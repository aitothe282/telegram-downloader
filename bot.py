import os
import re
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
import hashlib
import logging
import sqlite3
from contextlib import contextmanager
from urllib.parse import urlparse
import socket
import time
from collections import defaultdict

import yt_dlp
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ===== إعدادات السجل =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/tmp/bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ===== الثوابت =====
TOKEN = os.getenv("BOT_TOKEN") or "8796179561:AAHtstdmYb3qXO67K32JKrX7cGIwMOQ7s4c"
ADMIN_IDS = [8770697660]
MAX_FILE_SIZE = 50_000_000  # 50MB
DOWNLOAD_DIR = Path("/tmp/bot_downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = "/tmp/bot.db"

# نظام Queue البسيط
download_queue = asyncio.Queue()
MAX_CONCURRENT = 3
active_downloads = 0

# ===== Progress Tracking =====
progress_state = defaultdict(dict)
last_progress_update = defaultdict(float)
PROGRESS_UPDATE_INTERVAL = 2  # ثواني

# ===== قاعدة البيانات =====
@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_database():
    """إنشاء جداول قاعدة البيانات"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            total_downloads INTEGER DEFAULT 0,
            is_banned BOOLEAN DEFAULT 0
        )
        """)
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            title TEXT,
            source TEXT,
            file_size INTEGER,
            status TEXT DEFAULT 'success',
            downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
        """)
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS error_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            url TEXT,
            error_type TEXT,
            error_message TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS active_jobs (
            job_id TEXT PRIMARY KEY,
            user_id INTEGER,
            url TEXT,
            status TEXT,
            message_id INTEGER,
            chat_id INTEGER,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        conn.commit()
        logger.info("Database initialized")

# ===== وظائف قاعدة البيانات =====
def add_user(user_id: int, username: str, first_name: str):
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
            INSERT OR IGNORE INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
            """, (user_id, username, first_name))
            conn.commit()
        except Exception as e:
            logger.error(f"Error adding user: {e}")

def is_user_banned(user_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return row and row[0]

def increment_downloads(user_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE users SET total_downloads = total_downloads + 1
        WHERE user_id = ?
        """, (user_id,))
        conn.commit()

def log_download(user_id: int, url: str, title: str, source: str, file_size: int, status: str = "success"):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO downloads (user_id, url, title, source, file_size, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, url, title, source, file_size, status))
        conn.commit()

def log_error(user_id: int, url: str, error_type: str, error_message: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO error_log (user_id, url, error_type, error_message)
        VALUES (?, ?, ?, ?)
        """, (user_id, url, error_type, error_message))
        conn.commit()

def get_user_stats(user_id: int) -> dict:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
        return dict(user) if user else None

def get_admin_stats() -> dict:
    with get_db() as conn:
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) as count FROM users")
        total_users = cursor.fetchone()['count']
        
        cursor.execute("SELECT COUNT(*) as count FROM downloads")
        total_downloads = cursor.fetchone()['count']
        
        cursor.execute("SELECT COUNT(*) as count FROM users WHERE is_banned = 1")
        banned_users = cursor.fetchone()['count']
        
        cursor.execute("SELECT SUM(file_size) as total FROM downloads")
        total_size = cursor.fetchone()['total'] or 0
        
        return {
            'total_users': total_users,
            'total_downloads': total_downloads,
            'banned_users': banned_users,
            'total_size': total_size
        }

# ===== وظائف الأمان =====
def is_safe_url(url: str) -> bool:
    """فحص SSRF"""
    try:
        parsed = urlparse(url)
        
        if parsed.scheme not in ['http', 'https']:
            return False
        
        hostname = parsed.hostname or ''
        
        private_patterns = [
            '127.', '0.0.0.0', '192.168.', '10.',
            '172.', 'localhost', '169.254', '::1'
        ]
        
        for pattern in private_patterns:
            if hostname.startswith(pattern) or hostname == pattern:
                return False
        
        return True
    except Exception:
        return False

def safe_filename(filename: str) -> str:
    """منع Path Traversal"""
    safe = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
    safe = safe.replace('..', '').replace('/', '').replace('\\', '')
    return safe[:100]

def format_size(bytes_size):
    if bytes_size is None:
        return "?"
    if bytes_size < 1024:
        return f"{bytes_size}B"
    elif bytes_size < 1024 ** 2:
        return f"{bytes_size / 1024:.1f}KB"
    elif bytes_size < 1024 ** 3:
        return f"{bytes_size / (1024 ** 2):.1f}MB"
    else:
        return f"{bytes_size / (1024 ** 3):.2f}GB"

def format_duration(seconds):
    if seconds is None:
        return "?"
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{minutes}:{secs:02d}"

def get_video_source(url: str) -> str:
    """تحديد المنصة"""
    url_lower = url.lower()
    
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    elif "twitter.com" in url_lower or "x.com" in url_lower:
        return "twitter"
    elif "instagram.com" in url_lower or "instagr.am" in url_lower:
        return "instagram"
    elif "tiktok.com" in url_lower:
        return "tiktok"
    elif "facebook.com" in url_lower or "fb.watch" in url_lower:
        return "facebook"
    else:
        return "other"

def is_url(text: str) -> bool:
    return bool(re.match(r"^https?://", text.strip(), re.IGNORECASE))

# ===== Progress Bar =====
def create_progress_bar(percent: int, width: int = 10) -> str:
    """إنشاء progress bar نصي"""
    filled = int(width * percent / 100)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {percent}%"

class DownloadProgress:
    def __init__(self, user_id: int, message=None):
        self.user_id = user_id
        self.message = message
        self.last_update = 0
        self.total_bytes = 0
        self.downloaded_bytes = 0
    
    def hook(self, d):
        """هوك yt-dlp للتقدم"""
        if d['status'] == 'downloading':
            self.downloaded_bytes = d.get('downloaded_bytes', 0)
            self.total_bytes = d.get('total_bytes', 0) or d.get('total_bytes_estimate', 0)
            
            if self.total_bytes > 0:
                percent = int(100 * self.downloaded_bytes / self.total_bytes)
                current_time = time.time()
                
                # تحديث كل PROGRESS_UPDATE_INTERVAL ثواني فقط
                if current_time - self.last_update >= PROGRESS_UPDATE_INTERVAL:
                    self.last_update = current_time
                    speed = d.get('speed', 0)
                    eta = d.get('eta', 0)
                    
                    progress_state[self.user_id] = {
                        'percent': percent,
                        'downloaded': self.downloaded_bytes,
                        'total': self.total_bytes,
                        'speed': speed,
                        'eta': eta
                    }

# ===== Error Mapping =====
ERROR_MAPPING = {
    "Sign in to confirm": {
        "message": "❌ الفيديو يتطلب تسجيل دخول YouTube",
        "retryable": False,
    },
    "Video unavailable": {
        "message": "❌ الفيديو غير متاح",
        "retryable": True,
    },
    "Age restricted": {
        "message": "❌ الفيديو مخصص للبالغين",
        "retryable": False,
    },
    "Private video": {
        "message": "❌ الفيديو خاص ولا يمكن تحميله",
        "retryable": False,
    },
    "HTTP Error 404": {
        "message": "❌ الرابط غير صحيح",
        "retryable": False,
    },
    "HTTP Error 403": {
        "message": "❌ الموقع رفض الوصول",
        "retryable": True,
    },
    "HTTP Error 429": {
        "message": "⏸ الموقع رفض الطلب، حاول لاحقاً",
        "retryable": True,
    },
    "timed out": {
        "message": "⏱ انتهت مهلة الانتظار، حاول مرة أخرى",
        "retryable": True,
    },
    "instaloader": {
        "message": "❌ فشل تحميل المنشور",
        "retryable": True,
    },
}

def map_error(error_msg: str) -> dict:
    """تحويل رسالة الخطأ"""
    error_lower = error_msg.lower()
    
    for key, info in ERROR_MAPPING.items():
        if key.lower() in error_lower:
            return info
    
    return {
        "message": "❌ حدث خطأ، حاول مرة أخرى",
        "retryable": True,
    }

# ===== yt-dlp Configuration =====
def get_ydl_opts(quality: int = None, audio_only: bool = False):
    """إعدادات yt-dlp"""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        },
        "noplaylist": True,
    }
    
    if audio_only:
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
    elif quality:
        opts["format"] = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]"
        opts["merge_output_format"] = "mp4"
    else:
        opts.update({
            "format": "bv*+ba/best",
            "merge_output_format": "mp4",
        })
    
    return opts

def get_info(url: str) -> dict:
    """الحصول على معلومات الفيديو"""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        },
    }
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        logger.error(f"Error extracting info: {e}")
        raise

def build_quality_keyboard():
    """لوحة الجودة"""
    formats = [
        [InlineKeyboardButton("1080p", callback_data="video|1080")],
        [InlineKeyboardButton("720p", callback_data="video|720")],
        [InlineKeyboardButton("480p", callback_data="video|480")],
        [InlineKeyboardButton("🎵 صوت فقط", callback_data="audio|mp3")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_download")],
    ]
    return InlineKeyboardMarkup(formats)

# ===== Job Queue Worker =====
async def download_worker():
    """Worker لمعالجة التنزيلات من Queue"""
    global active_downloads
    
    while True:
        try:
            job = await download_queue.get()
            
            # انتظر إذا كانت التنزيلات المتزامنة في الحد الأقصى
            while active_downloads >= MAX_CONCURRENT:
                await asyncio.sleep(1)
            
            active_downloads += 1
            
            try:
                await process_download_job(job)
            finally:
                active_downloads -= 1
                download_queue.task_done()
        
        except Exception as e:
            logger.error(f"Worker error: {e}")
            await asyncio.sleep(1)

async def process_download_job(job):
    """معالجة وظيفة تحميل واحدة"""
    try:
        user_id = job['user_id']
        url = job['url']
        source = job['source']
        quality = job.get('quality')
        audio_only = job.get('audio_only', False)
        
        user_dir = DOWNLOAD_DIR / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        
        # إعدادات yt-dlp مع progress hook
        ydl_opts = get_ydl_opts(quality=quality, audio_only=audio_only)
        ydl_opts["outtmpl"] = str(user_dir / "%(title)s.%(ext)s")
        
        progress = DownloadProgress(user_id)
        ydl_opts["progress_hooks"] = [progress.hook]
        
        # التحميل
        logger.info(f"Downloading for user {user_id}: {url}")
        
        def download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        
        await asyncio.to_thread(download)
        
        # البحث عن الملف
        files = list(user_dir.iterdir())
        if not files:
            job['status'] = 'error'
            job['error'] = 'فشل التحميل'
            return
        
        file_path = max(files, key=lambda p: p.stat().st_mtime)
        file_size = file_path.stat().st_size
        
        # فحص الحجم
        if file_size > MAX_FILE_SIZE:
            log_download(user_id, url, "video", source, file_size, "rejected_size")
            job['status'] = 'error'
            job['error'] = f'الملف أكبر من 50MB'
            file_path.unlink()
            return
        
        job['status'] = 'ready'
        job['file_path'] = file_path
        job['file_size'] = file_size
        
    except Exception as e:
        logger.error(f"Download job error: {e}")
        job['status'] = 'error'
        job['error'] = map_error(str(e))['message']

# ===== معالجات الأوامر =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if is_user_banned(user_id):
        return
    
    add_user(user_id, update.effective_user.username, update.effective_user.first_name)
    
    keyboard = [
        [InlineKeyboardButton("ℹ️ المساعدة", callback_data="help")],
        [InlineKeyboardButton("📊 إحصائياتي", callback_data="stats")],
    ]
    
    await update.message.reply_text(
        "🎬 *مرحباً!*\n\n"
        "أنا بوت تحميل الفيديوهات الآمن والسريع\n\n"
        "📌 *كيفية الاستخدام:*\n"
        "أرسل رابط الفيديو مباشرة\n\n"
        "🌍 *المواقع المدعومة:*\n"
        "YouTube • TikTok • Instagram\n"
        "Facebook • Twitter • و1000+ موقع",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if is_user_banned(user_id):
        return
    
    keyboard = [[InlineKeyboardButton("🏠 الرئيسية", callback_data="start")]]
    
    await update.message.reply_text(
        "📖 *المساعدة*\n\n"
        "/start - البدء\n"
        "/help - المساعدة\n"
        "/stats - إحصائياتي\n\n"
        "💡 *نصائح:*\n"
        "• YouTube و Twitter: اختر الجودة\n"
        "• باقي المواقع: تحميل فوري\n"
        "• الحد الأقصى: 50 MB\n"
        "• Progress bar يعدل كل ثانيتين",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if is_user_banned(user_id):
        return
    
    user_stats = get_user_stats(user_id)
    
    if user_stats:
        text = (
            f"📊 *إحصائياتك*\n\n"
            f"👤 اسمك: {user_stats['first_name']}\n"
            f"📥 عدد التحميلات: {user_stats['total_downloads']}\n"
            f"📅 انضمت في: {user_stats['joined_at']}"
        )
    else:
        text = "❌ لا توجد بيانات"
    
    keyboard = [[InlineKeyboardButton("🏠 الرئيسية", callback_data="start")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لوحة تحكم المسؤول"""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ غير مصرح")
        return
    
    stats = get_admin_stats()
    
    text = (
        f"📊 *لوحة التحكم*\n\n"
        f"👥 إجمالي المستخدمين: {stats['total_users']}\n"
        f"📥 إجمالي التحميلات: {stats['total_downloads']}\n"
        f"🚫 المستخدمون المحظورون: {stats['banned_users']}\n"
        f"💾 إجمالي البيانات: {format_size(stats['total_size'])}\n\n"
        f"⏳ التحميلات النشطة: {active_downloads}/{MAX_CONCURRENT}"
    )
    
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الروابط"""
    user_id = update.effective_user.id
    
    if is_user_banned(user_id):
        return
    
    url = update.message.text.strip()
    
    if not is_url(url):
        return
    
    # فحص الأمان
    if not is_safe_url(url):
        logger.warning(f"User {user_id} tried unsafe URL: {url}")
        await update.message.reply_text("❌ الرابط غير آمن")
        return
    
    status_msg = await update.message.reply_text("🔎 جاري البحث...")
    video_source = get_video_source(url)
    
    try:
        logger.info(f"Fetching info for {video_source}: {url}")
        info = await asyncio.to_thread(get_info, url)
        
        context.user_data["url"] = url
        context.user_data["source"] = video_source
        
        title = info.get("title", "بدون عنوان")
        duration = info.get("duration")
        uploader = info.get("uploader", "بدون معلومات")
        filesize = info.get("filesize")
        
        # فحص الحجم مسبقاً
        if filesize and filesize > MAX_FILE_SIZE:
            log_download(user_id, url, title, video_source, filesize, "rejected_size")
            await status_msg.edit_text(
                f"❌ *الملف كبير جداً*\n\n"
                f"الحجم المتوقع: {format_size(filesize)}\n"
                f"الحد الأقصى: {format_size(MAX_FILE_SIZE)}",
                parse_mode="Markdown"
            )
            return
        
        duration_text = format_duration(duration)
        filesize_text = format_size(filesize)
        
        # عرض معلومات مختلفة حسب المنصة
        if video_source in ["youtube", "twitter"]:
            info_text = (
                f"✅ *تم العثور!*\n\n"
                f"🎬 {safe_filename(title[:40])}\n"
                f"👤 {safe_filename(uploader[:30])}\n"
                f"⏱ {duration_text}\n"
                f"📦 {filesize_text}\n\n"
                f"📊 *اختر الجودة:*"
            )
            
            await status_msg.edit_text(
                info_text,
                reply_markup=build_quality_keyboard(),
                parse_mode="Markdown"
            )
        else:
            # تحميل فوري
            await status_msg.edit_text("⏳ جاري الإضافة إلى الطابور...")
            
            job = {
                'user_id': user_id,
                'url': url,
                'source': video_source,
                'title': title,
                'status': 'queued',
                'message': status_msg,
                'update': update
            }
            
            await download_queue.put(job)
    
    except Exception as e:
        error_msg = str(e)
        error_info = map_error(error_msg)
        
        logger.error(f"Error for user {user_id}: {error_msg}")
        log_error(user_id, url, type(e).__name__, error_msg)
        
        await status_msg.edit_text(error_info["message"], parse_mode="Markdown")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الأزرار"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    if is_user_banned(user_id):
        return
    
    await query.answer()
    
    action_data = query.data
    
    # الأوامر العامة
    if action_data == "help":
        keyboard = [[InlineKeyboardButton("🏠 الرئيسية", callback_data="start")]]
        await query.edit_message_text(
            "📖 *المساعدة*\n\nأرسل رابط الفيديو وسأحمله لك! 🎬",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return
    
    if action_data == "stats":
        user_stats = get_user_stats(user_id)
        if user_stats:
            text = (
                f"📊 *إحصائياتك*\n\n"
                f"👤 {user_stats['first_name']}\n"
                f"📥 {user_stats['total_downloads']} تحميلة"
            )
        else:
            text = "❌ لا توجد بيانات"
        
        keyboard = [[InlineKeyboardButton("🏠 الرئيسية", callback_data="start")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return
    
    if action_data == "start":
        keyboard = [
            [InlineKeyboardButton("ℹ️ المساعدة", callback_data="help")],
            [InlineKeyboardButton("📊 إحصائياتي", callback_data="stats")],
        ]
        await query.edit_message_text(
            "🎬 *مرحباً!*\n\nأرسل رابط الفيديو",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return
    
    if action_data == "cancel_download":
        await query.edit_message_text("❌ تم الإلغاء")
        return
    
    # معالجة الجودة
    if "|" in action_data:
        action, value = action_data.split("|", 1)
        
        url = context.user_data.get("url")
        source = context.user_data.get("source")
        
        if not url:
            await query.answer("❌ انتهت صلاحية الرابط", show_alert=True)
            return
        
        await query.edit_message_text("⏳ جاري الإضافة إلى الطابور...")
        
        job = {
            'user_id': user_id,
            'url': url,
            'source': source,
            'title': 'video',
            'status': 'queued',
            'message': query.message,
            'update': update
        }
        
        if action == "audio":
            job['audio_only'] = True
        elif action == "video":
            job['quality'] = int(value)
        
        await download_queue.put(job)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم الإلغاء")

async def periodic_progress_update():
    """تحديث Progress bar دوري"""
    app = context.application
    
    while True:
        try:
            for user_id, state in progress_state.items():
                if state.get('percent'):
                    percent = state['percent']
                    downloaded = state.get('downloaded', 0)
                    total = state.get('total', 0)
                    speed = state.get('speed', 0)
                    
                    progress_bar = create_progress_bar(percent)
                    
                    speed_text = f"{format_size(speed)}/s" if speed > 0 else "..."
                    
                    text = (
                        f"⬇️ *جاري التحميل*\n\n"
                        f"{progress_bar}\n\n"
                        f"📦 {format_size(downloaded)} / {format_size(total)}\n"
                        f"⚡ {speed_text}"
                    )
        except:
            pass
        
        await asyncio.sleep(2)

def main():
    """تشغيل البوت"""
    init_database()
    logger.info("🤖 Bot starting...")
    
    app = Application.builder().token(TOKEN).build()
    
    # معالجات الأوامر
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("cancel", cancel))
    
    # معالجات الرسائل والأزرار
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    
    # تشغيل worker
    async def start_worker(app):
        asyncio.create_task(download_worker())
        logger.info("✅ Download worker started")
    
    app.post_init = start_worker
    
    print("✅ Bot is running...")
    print("🔒 Security: SSRF + Path Traversal")
    print("📊 Database: SQLite")
    print("⚡ Format: bestvideo+bestaudio")
    print("📦 Max size: 50MB")
    print(f"🔄 Concurrent downloads: {MAX_CONCURRENT}")
    print("📈 Progress bar: Every 2 seconds")
    
    app.run_polling()

if __name__ == "__main__":
    main()
