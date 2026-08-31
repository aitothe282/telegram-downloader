import os
import re
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime

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


# تحميل التوكن من متغيرات البيئة
TOKEN = os.getenv("BOT_TOKEN") or "8796179561:AAGSQ0c65tS3eqIBfxi9_Az1h2i1ME_PU8M"

if not TOKEN or TOKEN == "":
    raise RuntimeError("BOT_TOKEN is missing")

print(f"✅ Bot Token loaded successfully")

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "tg_downloader"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# تخزين آخر الروابط
USER_HISTORY = {}
MAX_HISTORY = 5


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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("ℹ️ المساعدة", callback_data="help")],
        [InlineKeyboardButton("📝 آخر الروابط", callback_data="history")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🎬 أهلاً بيك ببوت التحميل!\n\n"
        "📌 كيفية الاستخدام:\n"
        "• أرسل رابط الفيديو مباشرة\n"
        "• اختار الجودة (لليوتيوب وتويتر فقط)\n"
        "• انتظر التحميل والإرسال\n\n"
        "🌍 المواقع المدعومة:\n"
        "YouTube • TikTok • Instagram • Facebook • Twitter • و1000+ موقع آخر",
        reply_markup=reply_markup
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🏠 الرئيسية", callback_data="start")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📖 المساعدة:\n\n"
        "/start - إعادة تشغيل البوت\n"
        "/help - عرض هذه الرسالة\n"
        "/cancel - إلغاء المهمة الحالية\n\n"
        "💡 نصائح:\n"
        "• YouTube و Twitter: اختر الجودة\n"
        "• Instagram و TikTok: جودة عالية تلقائية\n"
        "• MP3 للموسيقى فقط",
        reply_markup=reply_markup
    )


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
    if bytes_size < 1024:
        return f"{bytes_size} B"
    elif bytes_size < 1024 ** 2:
        return f"{bytes_size / 1024:.1f} KB"
    elif bytes_size < 1024 ** 3:
        return f"{bytes_size / (1024 ** 2):.1f} MB"
    else:
        return f"{bytes_size / (1024 ** 3):.1f} GB"


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
    """إضافة الرابط للسجل"""
    if user_id not in USER_HISTORY:
        USER_HISTORY[user_id] = []
    
    USER_HISTORY[user_id].append({
        "url": url,
        "title": title[:30] + "..." if len(title) > 30 else title,
        "timestamp": datetime.now()
    })
    
    # الاحتفاظ بآخر 5 روابط فقط
    USER_HISTORY[user_id] = USER_HISTORY[user_id][-MAX_HISTORY:]


def get_history_keyboard(user_id):
    """الحصول على لوحة السجل"""
    if user_id not in USER_HISTORY or not USER_HISTORY[user_id]:
        return None
    
    history = USER_HISTORY[user_id]
    keyboard = []
    
    for idx, item in enumerate(reversed(history)):
        keyboard.append([
            InlineKeyboardButton(
                f"📌 {item['title']}",
                callback_data=f"history_url|{idx}"
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton("🏠 الرئيسية", callback_data="start"),
    ])
    
    return InlineKeyboardMarkup(keyboard)


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if not is_url(url):
        return

    status_msg = await update.message.reply_text("🔎 جاري البحث عن الفيديو...")

    try:
        info = await asyncio.to_thread(get_info, url)

        context.user_data["url"] = url
        
        title = info.get("title", "بدون عنوان")
        duration = info.get("duration")
        uploader = info.get("uploader", "بدون معلومات")
        video_source = get_video_source(url)

        # إضافة للسجل
        add_to_history(update.effective_user.id, url, title)

        duration_text = format_duration(duration)
        
        text = (
            f"✅ تم العثور على الفيديو!\n\n"
            f"🎬 <b>{title[:50]}</b>...\n"
            f"👤 <i>من: {uploader[:30]}</i>\n"
            f"⏱ المدة: {duration_text}\n\n"
        )

        # تحديد ما إذا كان نحتاج لعرض خيارات الجودة
        if video_source in ["youtube", "twitter"]:
            text += "📊 اختار الجودة المطلوبة:"
            keyboard = build_quality_keyboard()
        else:
            text += f"⚡ جودة عالية تلقائية ({video_source})"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("⬇️ تحميل", callback_data="auto_quality")],
                [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_download")],
            ])
            # حفظ مصدر الفيديو
            context.user_data["source"] = video_source

        # تحديث الرسالة القديمة بدل إرسال واحدة جديدة
        await status_msg.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        await status_msg.edit_text(
            "❌ لم أستطع الوصول للفيديو\n\n"
            "💡 تأكد من:\n"
            "• الرابط صحيح\n"
            "• الفيديو متاح للتحميل\n"
            "• الاتصال بالإنترنت"
        )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action_data = query.data

    # معالجة الأوامر الخاصة
    if action_data == "help":
        keyboard = [[InlineKeyboardButton("🏠 الرئيسية", callback_data="start")]]
        await query.edit_message_text(
            "📖 المساعدة:\n\n"
            "/start - إعادة تشغيل البوت\n"
            "/help - عرض هذه الرسالة\n"
            "/cancel - إلغاء المهمة الحالية\n\n"
            "💡 نصائح:\n"
            "• YouTube و Twitter: اختر الجودة\n"
            "• Instagram و TikTok: جودة عالية تلقائية\n"
            "• MP3 للموسيقى فقط",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if action_data == "history":
        history_keyboard = get_history_keyboard(update.effective_user.id)
        if history_keyboard:
            await query.edit_message_text(
                "📝 آخر الروابط المحملة:",
                reply_markup=history_keyboard
            )
        else:
            await query.answer("❌ لا توجد روابط سابقة", show_alert=True)
        return

    if action_data == "start":
        keyboard = [
            [InlineKeyboardButton("ℹ️ المساعدة", callback_data="help")],
            [InlineKeyboardButton("📝 آخر الروابط", callback_data="history")],
        ]
        await query.edit_message_text(
            "🎬 أهلاً بيك ببوت التحميل!\n\n"
            "📌 كيفية الاستخدام:\n"
            "• أرسل رابط الفيديو مباشرة\n"
            "• اختار الجودة (لليوتيوب وتويتر فقط)\n"
            "• انتظر التحميل والإرسال\n\n"
            "🌍 المواقع المدعومة:\n"
            "YouTube • TikTok • Instagram • Facebook • Twitter • و1000+ موقع آخر",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if action_data == "cancel_download":
        await query.edit_message_text("❌ تم الإلغاء")
        return

    # معالجة الروابط من السجل
    if action_data.startswith("history_url|"):
        idx = int(action_data.split("|")[1])
        user_id = update.effective_user.id
        
        if user_id in USER_HISTORY and idx < len(USER_HISTORY[user_id]):
            url = list(reversed(USER_HISTORY[user_id]))[idx]["url"]
            context.user_data["url"] = url
            context.user_data["from_history"] = True
            
            status_msg = await query.edit_message_text("🔎 جاري البحث...")
            
            try:
                info = await asyncio.to_thread(get_info, url)
                title = info.get("title", "بدون عنوان")
                duration = info.get("duration")
                uploader = info.get("uploader", "بدون معلومات")
                video_source = get_video_source(url)
                
                duration_text = format_duration(duration)
                
                text = (
                    f"✅ تم العثور على الفيديو!\n\n"
                    f"🎬 <b>{title[:50]}</b>...\n"
                    f"👤 <i>من: {uploader[:30]}</i>\n"
                    f"⏱ المدة: {duration_text}\n\n"
                )

                if video_source in ["youtube", "twitter"]:
                    text += "📊 اختار الجودة المطلوبة:"
                    keyboard = build_quality_keyboard()
                else:
                    text += f"⚡ جودة عالية تلقائية ({video_source})"
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬇️ تحميل", callback_data="auto_quality")],
                        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_download")],
                    ])
                    context.user_data["source"] = video_source
                
                await status_msg.edit_text(
                    text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            except Exception as e:
                await status_msg.edit_text("❌ حدث خطأ")
        return

    # معالجة الجودة التلقائية (Instagram, TikTok, إلخ)
    if action_data == "auto_quality":
        url = context.user_data.get("url")
        video_source = context.user_data.get("source", "other")

        if not url:
            await query.answer("❌ انتهت صلاحية الرابط", show_alert=True)
            return

        await query.edit_message_text("⬇️ جاري التحميل...\n⏳ قد يستغرق دقائق")

        try:
            user_dir = Path(tempfile.gettempdir()) / "tg_downloader" / str(query.from_user.id)
            user_dir.mkdir(parents=True, exist_ok=True)

            # تحميل بجودة عالية تلقائية
            ydl_opts = {
                "format": "best",
                "outtmpl": str(user_dir / "%(title)s.%(ext)s"),
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "socket_timeout": 30,
                "http_headers": {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
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

            await query.edit_message_text(f"⬆️ جاري الإرسال...\n📦 الحجم: {file_size}")

            suffix = file_path.suffix.lower()

            if suffix == ".mp3":
                with open(file_path, "rb") as audio:
                    await query.message.reply_audio(audio=audio)
            else:
                with open(file_path, "rb") as video:
                    await query.message.reply_video(
                        video=video,
                        supports_streaming=True
                    )

            try:
                file_path.unlink()
            except Exception:
                pass

            # حذف رسالة الإرسال بعد وصول المقطع
            await query.delete_message()

        except Exception as e:
            await query.edit_message_text(
                "❌ فشل التحميل\n\n"
                "💡 تأكد من:\n"
                "• الرابط صحيح\n"
                "• الفيديو متاح للتحميل\n"
                "• الاتصال بالإنترنت"
            )
        return

    # معالجة اختيار الجودة (YouTube و Twitter)
    url = context.user_data.get("url")

    if not url:
        await query.answer("❌ انتهت صلاحية الرابط. أرسله مرة أخرى", show_alert=True)
        return

    action, value = action_data.split("|", 1)

    # تحديث الرسالة لإظهار الحالة
    await query.edit_message_text("⬇️ جاري التحميل...\n⏳ قد يستغرق دقائق")

    try:
        user_dir = Path(tempfile.gettempdir()) / "tg_downloader" / str(query.from_user.id)
        user_dir.mkdir(parents=True, exist_ok=True)

        if action == "audio":
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": str(user_dir / "%(title)s.%(ext)s"),
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "socket_timeout": 30,
                "http_headers": {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                },
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
                "http_headers": {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
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

        await query.edit_message_text(f"⬆️ جاري الإرسال...\n📦 الحجم: {file_size}")

        suffix = file_path.suffix.lower()

        if suffix == ".mp3":
            with open(file_path, "rb") as audio:
                await query.message.reply_audio(audio=audio)
        else:
            with open(file_path, "rb") as video:
                await query.message.reply_video(
                    video=video,
                    supports_streaming=True
                )

        try:
            file_path.unlink()
        except Exception:
            pass

        # حذف رسالة الإرسال بعد وصول المقطع
        await query.delete_message()

    except Exception as e:
        await query.edit_message_text(
            "❌ فشل التحميل\n\n"
            "💡 تأكد من:\n"
            "• الرابط صحيح\n"
            "• الفيديو متاح للتحميل\n"
            "• الاتصال بالإنترنت"
        )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم إلغاء المهمة الحالية")


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

    print("🤖 Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
