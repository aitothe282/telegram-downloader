import os
import re
import asyncio
import tempfile
from pathlib import Path

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


TOKEN = os.getenv("8796179561:AAGSQ0c65tS3eqIBfxi9_Az1h2i1ME_PU8M")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")


DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "tg_downloader"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً بيك ببوت التحميل.\n\n"
        "دز رابط الفيديو مباشرة، وأنا أطلعلك خيارات التحميل."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start - تشغيل البوت\n"
        "/help - المساعدة\n"
        "/cancel - إلغاء المهمة الحالية\n\n"
        "بالخاص: دز الرابط مباشرة."
    )


def is_url(text: str) -> bool:
    return bool(
        re.match(
            r"^https?://",
            text.strip(),
            re.IGNORECASE,
        )
    )


def get_info(url: str):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def build_formats(info):
    formats = []

    for height in [1080, 720, 480, 360]:
        formats.append(
            [
                InlineKeyboardButton(
                    f"{height}p",
                    callback_data=f"video|{height}",
                )
            ]
        )

    formats.append(
        [
            InlineKeyboardButton(
                "🎵 MP3",
                callback_data="audio|mp3",
            )
        ]
    )

    return InlineKeyboardMarkup(formats)


async def handle_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    url = update.message.text.strip()

    if not is_url(url):
        return

    await update.message.reply_text(
        "🔎 دا أجيب معلومات الفيديو..."
    )

    try:
        info = await asyncio.to_thread(
            get_info,
            url,
        )

        context.user_data["url"] = url

        title = info.get("title", "بدون عنوان")
        duration = info.get("duration")

        duration_text = ""

        if duration:
            minutes = duration // 60
            seconds = duration % 60
            duration_text = (
                f"\n⏱ {minutes}:{seconds:02d}"
            )

        text = (
            f"🎬 {title}"
            f"{duration_text}\n\n"
            "اختار الجودة:"
        )

        await update.message.reply_text(
            text,
            reply_markup=build_formats(info),
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ ما گدرت أجيب الفيديو.\n\n{e}"
        )


async def handle_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query
    await query.answer()

    url = context.user_data.get("url")

    if not url:
        await query.message.reply_text(
            "انتهت صلاحية الرابط. دزه مرة ثانية."
        )
        return

    action, value = query.data.split("|", 1)

    await query.message.reply_text(
        "⬇️ جاري التحميل..."
    )

    try:

        user_dir = (
            DOWNLOAD_DIR /
            str(query.from_user.id)
        )

        user_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        if action == "audio":

            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl":
                    str(user_dir / "%(title)s.%(ext)s"),
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "postprocessors": [
                    {
                        "key":
                            "FFmpegExtractAudio",
                        "preferredcodec":
                            "mp3",
                        "preferredquality":
                            "192",
                    }
                ],
            }

        else:

            height = int(value)

            ydl_opts = {
                "format":
                    f"bestvideo[height<={height}]+bestaudio/"
                    f"best[height<={height}]",
                "merge_output_format": "mp4",
                "outtmpl":
                    str(user_dir / "%(title)s.%(ext)s"),
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
            }

        def download():
            with yt_dlp.YoutubeDL(
                ydl_opts
            ) as ydl:

                ydl.download([url])

        await asyncio.to_thread(
            download
        )

        files = list(
            user_dir.iterdir()
        )

        if not files:
            raise RuntimeError(
                "No file was produced."
            )

        file_path = max(
            files,
            key=lambda p: p.stat().st_mtime,
        )

        await query.message.reply_text(
            "⬆️ جاري الإرسال..."
        )

        suffix = file_path.suffix.lower()

        if suffix == ".mp3":

            with open(
                file_path,
                "rb",
            ) as audio:

                await query.message.reply_audio(
                    audio=audio
                )

        else:

            with open(
                file_path,
                "rb",
            ) as video:

                await query.message.reply_video(
                    video=video,
                    supports_streaming=True,
                )

        try:
            file_path.unlink()
        except Exception:
            pass

    except Exception as e:

        await query.message.reply_text(
            f"❌ فشل التحميل.\n\n{e}"
        )


async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "إذا كانت هناك مهمة شغالة، سيتم دعم إلغائها في نسخة الـQueue الكاملة."
    )


def main():

    app = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    app.add_handler(
        CommandHandler(
            "help",
            help_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "cancel",
            cancel,
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            handle_callback,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT &
            ~filters.COMMAND,
            handle_url,
        )
    )

    print("Bot is running...")

    app.run_polling()


if __name__ == "__main__":
    main()
