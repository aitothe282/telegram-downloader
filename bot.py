import os
import re
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
import hashlib
import logging
import json
from typing import Optional, Dict, List

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

# ===== إعدادات قاعدة البيانات =====
import sqlite3
from contextlib import contextmanager

DATABASE_PATH = "/tmp/bot_database.db"

@contextmanager
def get_db():
    """إدارة اتصال قاعدة البيانات"""
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
        
        # جدول المستخدمين
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            total_downloads INTEGER DEFAULT 0,
            is_premium BOOLEAN DEFAULT 0,
            is_banned BOOLEAN DEFAULT 0,
            suspicion_score INTEGER DEFAULT 0
        )
        """)
        
        # جدول التحميلات
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            title TEXT,
            source TEXT,
            file_size INTEGER,
            download_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'success',
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
        """)
        
        # جدول السجل الأمني
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS security_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            reason TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            details TEXT
        )
        """)
        
        # جدول معدل الطلبات
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS rate_limits (
            user_id INTEGER PRIMARY KEY,
            requests_today INTEGER DEFAULT 0,
            last_request TIMESTAMP,
            banned_until TIMESTAMP
        )
        """)
        
        conn.commit()

# ===== تكوين الأمان والتسجيل =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/tmp/bot_security.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# تحميل التوكن
TOKEN = os.getenv("BOT_TOKEN") or "8796179561:AAHtstdmYb3qXO67K32JKrX7cGIwMOQ7s4c"
ADMIN_IDS = [8770697660]  # ID المسؤول

# الإعدادات
BLOCKED_KEYWORDS = [
    "xxx", "18+", "adult", "porn", "sex", "naked", 
    "nsfw", "explicit", "adult content", "سكسي", "18", "xnxx"
]

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "tg_downloader_v2"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_REQUESTS_PER_DAY = 50
RATE_LIMIT_SECONDS = 3
MAX_FILE_SIZE = 2_000_000_000  # 2GB

# ===== قاعدة البيانات - الدوال =====

def add_user(user_id: int, username: str, first_name: str):
    """إضافة مستخدم جديد"""
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

def get_user_stats(user_id: int) -> Dict:
    """الحصول على إحصائيات المستخدم"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
        return dict(user) if user else None

def increment_downloads(user_id: int):
    """زيادة عدد التحميلات"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE users SET total_downloads = total_downloads + 1
        WHERE user_id = ?
        """, (user_id,))
        conn.commit()

def log_download(user_id: int, url: str, title: str, source: str, file_size: int, status: str = "success"):
    """تسجيل التحميل"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO downloads (user_id, url, title, source, file_size, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, url, title, source, file_size, status))
        conn.commit()

def log_security_event(user_id: int, action: str, reason: str, details: str = ""):
    """تسجيل حدث أمني"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO security_log (user_id, action, reason, details)
        VALUES (?, ?, ?, ?)
        """, (user_id, action, reason, details))
        conn.commit()

def is_user_banned(user_id: int) -> bool:
    """التحقق من حظر المستخدم"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return row and row[0]

def ban_user(user_id: int, reason: str):
    """حظر مستخدم"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
    log_security_event(user_id, "BAN", reason)
    logger.warning(f"User {user_id} banned: {reason}")

def check_rate_limit(user_id: int) -> bool:
    """فحص حد معدل الطلبات"""
    with get_db() as conn:
        cursor = conn.cursor()
        today = datetime.now().date()
        
        cursor.execute("""
        SELECT requests_today FROM rate_limits 
        WHERE user_id = ? AND DATE(last_request) = ?
        """, (user_id, today))
        
        row = cursor.fetchone()
        
        if row and row[0] >= MAX_REQUESTS_PER_DAY:
            return False
        
        # تحديث العداد
        cursor.execute("""
        INSERT OR REPLACE INTO rate_limits (user_id, requests_today, last_request)
        VALUES (?, 
                COALESCE((SELECT requests_today FROM rate_limits 
                         WHERE user_id = ? AND DATE(last_request) = ?) + 1, 1),
                CURRENT_TIMESTAMP)
        """, (user_id, user_id, today))
        
        conn.commit()
        return True

# ===== وظائف الأمان =====

def is_admin(user_id: int) -> bool:
    """التحقق من المسؤول"""
    return user_id in ADMIN_IDS

def scan_url_for_threats(url: str) -> bool:
    """فحص الرابط للمحتوى الخطر"""
    url_lower = url.lower()
    
    for keyword in BLOCKED_KEYWORDS:
        if keyword in url_lower:
            return True
    
    if "shortlink" in url_lower or "bit.ly" in url_lower or "tinyurl" in url_lower:
        return True
    
    return False

def get_video_source(url: str) -> str:
    """تحديد مصدر الفيديو"""
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
    """التحقق من كون النص رابط"""
    return bool(re.match(r"^https?://", text.strip(), re.IGNORECASE))

def format_size(bytes_size):
    """تحويل الحجم"""
    if bytes_size is None:
        return "بدون معلومات"
    if bytes_size < 1024:
        return f"{bytes_size} B"
    elif bytes_size < 1024 ** 2:
        return f"{bytes_size / 1024:.1f} KB"
    elif bytes_size < 1024 ** 3:
        return f"{bytes_size / (1024 ** 2):.1f} MB"
    else:
        return f"{bytes_size / (1024 ** 3):.2f} GB"

def format_duration(seconds):
    """تحويل المدة"""
    if seconds is None:
        return "بدون معلومات"
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{minutes}:{secs:02d}"

def get_info(url: str):
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
        if "Sign in" in str(e):
            opts["cookiesfrombrowser"] = "chrome"
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        raise

def build_quality_keyboard():
    """بناء لوحة الجودة"""
    formats = []
    for height in [1080, 720, 480, 360]:
        formats.append([
            InlineKeyboardButton(f"📹 {height}p", callback_data=f"video|{height}")
        ])
    
    formats.append([InlineKeyboardButton("🎵 MP3", callback_data="audio|mp3")])
    formats.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel_download")])
    
    return InlineKeyboardMarkup(formats)

# ===== معالجات الأوامر =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر البداية"""
    user_id = update.effective_user.id
    
    if is_user_banned(user_id):
        logger.warning(f"Banned user {user_id} tried to access")
        return
    
    # إضافة المستخدم
    add_user(user_id, update.effective_user.username, update.effective_user.first_name)
    
    if not check_rate_limit(user_id):
        await update.message.reply_text("⏳ تجاوزت الحد الأقصى من الطلبات اليومية (50)")
        return
    
    keyboard = [
        [InlineKeyboardButton("ℹ️ المساعدة", callback_data="help")],
        [InlineKeyboardButton("📊 إحصائياتي", callback_data="stats")],
    ]
    
    await update.message.reply_text(
        "🎬 مرحباً! أنا بوت تحميل الفيديوهات الآمن 🔒\n\n"
        "📌 أرسل رابط الفيديو مباشرة\n\n"
        "🌍 المواقع المدعومة:\n"
        "YouTube • TikTok • Instagram • Facebook • Twitter\n\n"
        "⚡ محمي بنظام أمان عالي جداً",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر المساعدة"""
    user_id = update.effective_user.id
    
    if is_user_banned(user_id):
        return
    
    keyboard = [[InlineKeyboardButton("🏠 الرئيسية", callback_data="start")]]
    
    await update.message.reply_text(
        "📖 المساعدة:\n\n"
        "/start - البدء\n"
        "/help - المساعدة\n"
        "/stats - إحصائياتي\n"
        "/admin - لوحة تحكم (للمسؤولين فقط)\n\n"
        "💡 نصائح:\n"
        "• اختر الجودة للـ YouTube و Twitter\n"
        "• التحميل فوري من المواقع الأخرى\n"
        "• الحد الأقصى: 50 تحميل يومياً\n"
        "• حجم الملف الأقصى: 2GB",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لوحة تحكم المسؤول"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ غير مصرح")
        return
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        # إحصائيات عامة
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM downloads")
        total_downloads = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
        banned_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT SUM(file_size) FROM downloads")
        total_size = cursor.fetchone()[0] or 0
    
    stats_text = (
        "📊 لوحة التحكم:\n\n"
        f"👥 إجمالي المستخدمين: {total_users}\n"
        f"📥 إجمالي التحميلات: {total_downloads}\n"
        f"🚫 المستخدمون المحظورون: {banned_users}\n"
        f"💾 إجمالي البيانات: {format_size(total_size)}\n\n"
        "🔐 النظام آمن وعامل بكفاءة عالية"
    )
    
    await update.message.reply_text(stats_text)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إحصائيات المستخدم"""
    user_id = update.effective_user.id
    
    if is_user_banned(user_id):
        return
    
    user_stats = get_user_stats(user_id)
    
    if user_stats:
        text = (
            "📊 إحصائياتك:\n\n"
            f"👤 المستخدم: {user_stats['first_name']}\n"
            f"📥 التحميلات: {user_stats['total_downloads']}\n"
            f"📅 انضمت في: {user_stats['joined_at']}\n"
            f"💎 الحالة: {'Premium ⭐' if user_stats['is_premium'] else 'مجاني'}"
        )
    else:
        text = "❌ لا توجد بيانات"
    
    keyboard = [[InlineKeyboardButton("🏠 الرئيسية", callback_data="start")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الروابط"""
    user_id = update.effective_user.id
    
    # فحوصات الأمان
    if is_user_banned(user_id):
        logger.warning(f"Banned user {user_id} tried to download")
        return
    
    if not check_rate_limit(user_id):
        await update.message.reply_text("⏳ الحد الأقصى من الطلبات اليومية (50)")
        return
    
    url = update.message.text.strip()
    
    if not is_url(url):
        return
    
    # فحص المحتوى الخطر
    if scan_url_for_threats(url):
        ban_user(user_id, "محاولة تحميل محتوى ممنوع")
        await update.message.reply_text("❌ محتوى ممنوع! تم حظرك.")
        return
    
    status_msg = await update.message.reply_text("🔎 جاري البحث...")
    video_source = get_video_source(url)
    
    try:
        info = await asyncio.to_thread(get_info, url)
        
        context.user_data["url"] = url
        context.user_data["source"] = video_source
        
        title = info.get("title", "بدون عنوان")
        duration = info.get("duration")
        uploader = info.get("uploader", "بدون معلومات")
        filesize = info.get("filesize")
        
        duration_text = format_duration(duration)
        filesize_text = format_size(filesize)
        
        if video_source in ["youtube", "twitter"]:
            text = (
                f"✅ تم العثور!\n\n"
                f"🎬 <b>{title[:40]}</b>\n"
                f"👤 {uploader[:30]}\n"
                f"⏱ {duration_text}\n"
                f"📦 {filesize_text}\n\n"
                f"📊 اختر الجودة:"
            )
            
            await status_msg.edit_text(
                text,
                reply_markup=build_quality_keyboard(),
                parse_mode="HTML"
            )
        else:
            # حمل فوري
            await status_msg.edit_text("⬇️ جاري التحميل...")
            
            try:
                user_dir = DOWNLOAD_DIR / str(user_id)
                user_dir.mkdir(parents=True, exist_ok=True)
                
                ydl_opts = {
                    "format": "bestvideo+bestaudio/best",
                    "merge_output_format": "mp4",
                    "outtmpl": str(user_dir / "%(title)s.%(ext)s"),
                    "noplaylist": True,
                    "quiet": True,
                    "no_warnings": True,
                    "socket_timeout": 30,
                }
                
                def download():
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([url])
                
                await asyncio.to_thread(download)
                
                files = list(user_dir.iterdir())
                
                if not files:
                    raise RuntimeError("فشل التحميل")
                
                file_path = max(files, key=lambda p: p.stat().st_mtime)
                file_size = file_path.stat().st_size
                
                if file_size > MAX_FILE_SIZE:
                    log_download(user_id, url, title, video_source, file_size, "failed_size")
                    await status_msg.edit_text("❌ الملف كبير جداً (أكثر من 2GB)")
                    file_path.unlink()
                    return
                
                await status_msg.edit_text(f"⬆️ جاري الإرسال...\n📦 {format_size(file_size)}")
                
                suffix = file_path.suffix.lower()
                
                if suffix == ".mp3":
                    with open(file_path, "rb") as audio:
                        await update.message.reply_audio(audio=audio)
                elif suffix in [".jpg", ".png", ".gif", ".webp"]:
                    with open(file_path, "rb") as photo:
                        await update.message.reply_photo(photo=photo)
                else:
                    with open(file_path, "rb") as video:
                        await update.message.reply_video(video=video, supports_streaming=True)
                
                # تسجيل التحميل الناجح
                log_download(user_id, url, title, video_source, file_size, "success")
                increment_downloads(user_id)
                
                file_path.unlink()
                
                try:
                    await status_msg.delete()
                except:
                    pass
                
                logger.info(f"User {user_id} downloaded: {title} ({format_size(file_size)})")
                
            except Exception as e:
                logger.error(f"Download error: {e}")
                log_download(user_id, url, title, video_source, 0, "error")
                await status_msg.edit_text("❌ فشل التحميل")
    
    except Exception as e:
        logger.error(f"Error: {e}")
        log_security_event(user_id, "ERROR", str(e))
        await status_msg.edit_text("❌ خطأ في البحث")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الأزرار"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    if is_user_banned(user_id):
        return
    
    await query.answer()
    
    action_data = query.data
    
    if action_data == "help":
        keyboard = [[InlineKeyboardButton("🏠 الرئيسية", callback_data="start")]]
        await query.edit_message_text(
            "📖 المساعدة - استخدام آمن وسهل",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    if action_data == "stats":
        await query.message.reply_text("جاري تحميل الإحصائيات...")
        await stats_command(query.message, context)
        return
    
    if action_data == "start":
        keyboard = [
            [InlineKeyboardButton("ℹ️ المساعدة", callback_data="help")],
            [InlineKeyboardButton("📊 إحصائياتي", callback_data="stats")],
        ]
        await query.edit_message_text("🎬 مرحباً!", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    if action_data == "cancel_download":
        await query.edit_message_text("❌ تم الإلغاء")
        return
    
    # معالجة الجودة
    url = context.user_data.get("url")
    
    if not url or "|" not in action_data:
        return
    
    action, value = action_data.split("|", 1)
    
    await query.edit_message_text("⬇️ جاري التحميل...")
    status_msg = query.message
    
    try:
        user_dir = DOWNLOAD_DIR / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        
        if action == "audio":
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": str(user_dir / "%(title)s.%(ext)s"),
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }
        else:
            height = int(value)
            ydl_opts = {
                "format": f"bestvideo[height<={height}]+bestaudio/best[height<={height}]",
                "merge_output_format": "mp4",
                "outtmpl": str(user_dir / "%(title)s.%(ext)s"),
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
            }
        
        def download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        
        await asyncio.to_thread(download)
        
        files = list(user_dir.iterdir())
        
        if not files:
            raise RuntimeError("فشل")
        
        file_path = max(files, key=lambda p: p.stat().st_mtime)
        file_size = file_path.stat().st_size
        
        await status_msg.edit_text(f"⬆️ الإرسال...\n📦 {format_size(file_size)}")
        
        suffix = file_path.suffix.lower()
        
        if suffix == ".mp3":
            with open(file_path, "rb") as audio:
                await status_msg.reply_to_message.reply_audio(audio=audio)
        else:
            with open(file_path, "rb") as video:
                await status_msg.reply_to_message.reply_video(video=video, supports_streaming=True)
        
        log_download(user_id, url, "تحميل", context.user_data.get("source", "unknown"), file_size)
        increment_downloads(user_id)
        
        file_path.unlink()
        
        try:
            await status_msg.delete()
        except:
            pass
    
    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text("❌ فشل")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر الإلغاء"""
    await update.message.reply_text("❌ تم الإلغاء")

def main():
    """تشغيل البوت"""
    # إنشاء قاعدة البيانات
    init_database()
    logger.info("Database initialized")
    
    app = Application.builder().token(TOKEN).build()
    
    # معالجات الأوامر
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("cancel", cancel))
    
    # معالجات أخرى
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    
    print("🤖 Secure Bot V2 Started!")
    print("✅ Database: Active")
    print("🔒 Security: High")
    print("📊 Monitoring: Active")
    logger.info("Bot started successfully")
    
    app.run_polling()

if __name__ == "__main__":
    main()
