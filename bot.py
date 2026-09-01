import os
import re
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
import hashlib
import logging

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

# ===== تكوين الأمان =====
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/tmp/bot_security.log'),
    ]
)
logger = logging.getLogger(__name__)

# تحميل التوكن من متغيرات البيئة (آمن)
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    # استخدام التوكن الجديد
    TOKEN = "8796179561:AAHtstdmYb3qXO67K32JKrX7cGIwMOQ7s4c"

if not TOKEN or TOKEN == "":
    raise RuntimeError("BOT_TOKEN is missing")

print(f"✅ Bot initialized securely")

# ===== قوائم الحماية =====
ADMIN_IDS = [1234567890]  # أضف ID الأدمن فقط
WHITELIST_USERS = set()
BLACKLIST_USERS = set()

# كلمات مفتاحية محظورة
BLOCKED_KEYWORDS = [
    "xxx", "18+", "adult", "porn", "sex", "naked", 
    "nsfw", "explicit", "adult content", "سكسي", 
    "18", "احدى عشر", "xxxxx", "xnxx"
]

# محدد معدل الرسائل
USER_LIMITS = {}
RATE_LIMIT_SECONDS = 5
MAX_REQUESTS_PER_HOUR = 50

# تتبع الأنشطة المريبة
SUSPICIOUS_ACTIVITY = {}
MAX_SUSPICION_SCORE = 10

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "tg_downloader_secure"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

USER_HISTORY = {}
MAX_HISTORY = 3

# ===== وظائف الأمان =====

def is_admin(user_id: int) -> bool:
    """التحقق من أن المستخدم مسؤول"""
    return user_id in ADMIN_IDS

def check_user_blocked(user_id: int) -> bool:
    """التحقق من حظر المستخدم"""
    return user_id in BLACKLIST_USERS

def increment_suspicion(user_id: int, points: int = 1):
    """زيادة درجة الشك للمستخدم"""
    if user_id not in SUSPICIOUS_ACTIVITY:
        SUSPICIOUS_ACTIVITY[user_id] = 0
    
    SUSPICIOUS_ACTIVITY[user_id] += points
    
    # إذا تجاوز الحد، احظر المستخدم
    if SUSPICIOUS_ACTIVITY[user_id] >= MAX_SUSPICION_SCORE:
        BLACKLIST_USERS.add(user_id)
        logger.warning(f"User {user_id} blacklisted - suspicion score {SUSPICIOUS_ACTIVITY[user_id]}")

def check_rate_limit(user_id: int) -> bool:
    """فحص حد معدل الرسائل"""
    now = datetime.now()
    
    if user_id not in USER_LIMITS:
        USER_LIMITS[user_id] = []
    
    # احذف الطلبات القديمة (أكثر من ساعة)
    USER_LIMITS[user_id] = [
        timestamp for timestamp in USER_LIMITS[user_id]
        if now - timestamp < timedelta(hours=1)
    ]
    
    # تحقق من حد الساعة
    if len(USER_LIMITS[user_id]) >= MAX_REQUESTS_PER_HOUR:
        increment_suspicion(user_id, 2)
        return False
    
    # أضف الطلب الحالي
    USER_LIMITS[user_id].append(now)
    return True

def scan_url_for_threats(url: str) -> bool:
    """فحص الرابط للمحتوى الخطر"""
    url_lower = url.lower()
    
    for keyword in BLOCKED_KEYWORDS:
        if keyword in url_lower:
            return True
    
    # فحص الرابط نفسه
    if "shortlink" in url_lower or "bit.ly" in url_lower or "tinyurl" in url_lower:
        # قد يكون رابط مريب
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
    return bool(
        re.match(
            r"^https?://",
            text.strip(),
            re.IGNORECASE,
        )
    )

def format_size(bytes_size):
    """تحويل الحجم لصيغة قابلة للقراءة"""
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
    """تحويل الثواني لصيغة دقائق وثواني"""
    if seconds is None:
        return "بدون معلومات"
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{minutes}:{secs:02d}"

def get_info(url: str):
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
        if "Sign in" in str(e) or "bot" in str(e).lower():
            opts["cookiesfrombrowser"] = "chrome"
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        raise

def build_quality_keyboard():
    """بناء لوحة الجودة لليوتيوب وتويتر"""
    formats = []

    for height in [1080, 720, 480, 360]:
        formats.append(
            [
                InlineKeyboardButton(
                    f"📹 {height}p",
                    callback_data=f"video|{height}",
                )
            ]
        )

    formats.append([
        InlineKeyboardButton("🎵 MP3", callback_data="audio|mp3"),
    ])
    
    formats.append([
        InlineKeyboardButton("❌ إلغاء", callback_data="cancel_download"),
    ])

    return InlineKeyboardMarkup(formats)

def add_to_history(user_id, url, title):
    """إضافة الرابط للسجل (محدود جداً)"""
    if user_id not in USER_HISTORY:
        USER_HISTORY[user_id] = []
    
    # لا تحفظ الروابط الفعلية، فقط هاش
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:8]
    
    USER_HISTORY[user_id].append({
        "url_hash": url_hash,
        "title": title[:20] + "..." if len(title) > 20 else title,
        "timestamp": datetime.now()
    })
    
    USER_HISTORY[user_id] = USER_HISTORY[user_id][-MAX_HISTORY:]

def get_history_keyboard(user_id):
    """الحصول على لوحة السجل (محدودة)"""
    if user_id not in USER_HISTORY or not USER_HISTORY[user_id]:
        return None
    
    history = USER_HISTORY[user_id]
    keyboard = []
    
    # لا تعرض الروابط الفعلية
    for idx, item in enumerate(reversed(history)):
        keyboard.append([
            InlineKeyboardButton(
                f"📌 {item['title']}",
                callback_data=f"history_blocked"  # لا تربطه برابط فعلي
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton("🏠 الرئيسية", callback_data="start"),
    ])
    
    return InlineKeyboardMarkup(keyboard)

# ===== معالجات الأوامر =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # فحص الأمان الأساسي
    if check_user_blocked(user_id):
        logger.warning(f"Blocked user {user_id} tried to access")
        return
    
    if not check_rate_limit(user_id):
        await update.message.reply_text("⏳ طلبت الكثير من المرات. حاول لاحقاً")
        return
    
    keyboard = [
        [InlineKeyboardButton("ℹ️ المساعدة", callback_data="help")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🎬 مرحباً!\n\n"
        "📌 أرسل رابط الفيديو مباشرة\n\n"
        "🌍 المواقع المدعومة:\n"
        "YouTube • TikTok • Instagram • Facebook • Twitter",
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if check_user_blocked(user_id):
        return
    
    keyboard = [[InlineKeyboardButton("🏠 الرئيسية", callback_data="start")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📖 المساعدة:\n\n"
        "/start - البدء\n"
        "/help - المساعدة\n"
        "/cancel - إلغاء\n\n"
        "💡 نصائح:\n"
        "• YouTube و Twitter: اختر الجودة\n"
        "• باقي المواقع: تحميل فوري",
        reply_markup=reply_markup
    )

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # ===== فحوصات الأمان =====
    if check_user_blocked(user_id):
        logger.warning(f"Blocked user {user_id} attempted action")
        return
    
    if not check_rate_limit(user_id):
        await update.message.reply_text("⏳ حد أقصى من الطلبات")
        return
    
    url = update.message.text.strip()

    if not is_url(url):
        return

    # فحص المحتوى الخطر
    if scan_url_for_threats(url):
        increment_suspicion(user_id, 5)
        logger.warning(f"User {user_id} attempted to download blocked content")
        await update.message.reply_text("❌ محتوى غير مسموح")
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

        # أضف للسجل (بأمان)
        add_to_history(user_id, url, title)

        duration_text = format_duration(duration)
        filesize_text = format_size(filesize)

        if video_source in ["youtube", "twitter"]:
            text = (
                f"✅ تم العثور!\n\n"
                f"🎬 <b>{title[:40]}</b>\n"
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
                    "http_headers": {
                        "User-Agent": "Mozilla/5.0"
                    },
                }

                def download():
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([url])

                await asyncio.to_thread(download)

                files = list(user_dir.iterdir())

                if not files:
                    raise RuntimeError("فشل التحميل")

                file_path = max(files, key=lambda p: p.stat().st_mtime)
                file_size = format_size(file_path.stat().st_size)

                # التحقق من حجم الملف
                if file_path.stat().st_size > 2_000_000_000:  # 2GB
                    increment_suspicion(user_id, 1)
                    await status_msg.edit_text("❌ الملف كبير جداً")
                    file_path.unlink()
                    return

                await status_msg.edit_text(f"⬆️ جاري الإرسال...\n📦 {file_size}")

                suffix = file_path.suffix.lower()

                if suffix == ".mp3":
                    with open(file_path, "rb") as audio:
                        await update.message.reply_audio(audio=audio)
                elif suffix in [".jpg", ".png", ".gif", ".webp"]:
                    with open(file_path, "rb") as photo:
                        await update.message.reply_photo(photo=photo)
                else:
                    with open(file_path, "rb") as video:
                        await update.message.reply_video(
                            video=video,
                            supports_streaming=True
                        )

                file_path.unlink()

                try:
                    await status_msg.delete()
                except Exception:
                    pass

                logger.info(f"User {user_id} downloaded successfully")

            except Exception as e:
                logger.error(f"Download error for user {user_id}: {e}")
                await status_msg.edit_text("❌ فشل التحميل")

    except Exception as e:
        logger.error(f"Error for user {user_id}: {e}")
        await status_msg.edit_text("❌ خطأ في البحث")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    
    if check_user_blocked(user_id):
        return
    
    await query.answer()

    action_data = query.data

    if action_data == "help":
        keyboard = [[InlineKeyboardButton("🏠 الرئيسية", callback_data="start")]]
        await query.edit_message_text(
            "📖 المساعدة\n\n/start - البدء",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if action_data == "start":
        keyboard = [
            [InlineKeyboardButton("ℹ️ المساعدة", callback_data="help")],
        ]
        await query.edit_message_text(
            "🎬 مرحباً!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if action_data == "cancel_download":
        await query.edit_message_text("❌ تم الإلغاء")
        return

    if action_data == "history_blocked":
        await query.answer("❌ السجل محدود للأمان", show_alert=True)
        return

    # معالجة الجودة
    url = context.user_data.get("url")

    if not url:
        await query.answer("❌ انتهت صلاحية الرابط", show_alert=True)
        return

    if "|" not in action_data:
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
                "socket_timeout": 30,
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
                "socket_timeout": 30,
            }

        def download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

        await asyncio.to_thread(download)

        files = list(user_dir.iterdir())

        if not files:
            raise RuntimeError("فشل")

        file_path = max(files, key=lambda p: p.stat().st_mtime)
        file_size = format_size(file_path.stat().st_size)

        await status_msg.edit_text(f"⬆️ الإرسال...\n📦 {file_size}")

        suffix = file_path.suffix.lower()

        if suffix == ".mp3":
            with open(file_path, "rb") as audio:
                await status_msg.reply_to_message.reply_audio(audio=audio)
        else:
            with open(file_path, "rb") as video:
                await status_msg.reply_to_message.reply_video(
                    video=video,
                    supports_streaming=True
                )

        file_path.unlink()

        try:
            await status_msg.delete()
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Download error: {e}")
        await status_msg.edit_text("❌ فشل التحميل")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم الإلغاء")

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_url,
        )
    )

    print("🤖 Secure bot running...")
    logger.info("Bot started securely")
    app.run_polling()

if __name__ == "__main__":
    main()
