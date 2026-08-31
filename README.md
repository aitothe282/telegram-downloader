# Telegram Video Downloader Bot 🎬

بوت تيليجرام لتحميل الفيديوهات والموسيقى من معظم المواقع بجودات مختلفة.

## المميزات ✨

- ✅ تحميل الفيديوهات بجودات مختلفة (1080p, 720p, 480p, 360p)
- ✅ استخراج الموسيقى بصيغة MP3
- ✅ واجهة عربية سهلة الاستخدام
- ✅ دعم معظم مواقع الفيديو
- ✅ مدعوم بـ Docker للنشر السهل

## المتطلبات 📋

- Python 3.12 أو أحدث
- FFmpeg
- Telegram Bot Token

## التثبيت 🚀

### الطريقة الأولى: تشغيل مباشر

```bash
# استنساخ المشروع
git clone https://github.com/aitothe282/telegram-downloader.git
cd telegram-downloader

# تثبيت المتطلبات
pip install -r requirements.txt

# تعيين متغير البيئة
export BOT_TOKEN="your_bot_token_here"

# تشغيل البوت
python bot.py
```

### الطريقة الثانية: Docker

```bash
# بناء الـ Image
docker build -t telegram-downloader .

# تشغيل الـ Container
docker run -e BOT_TOKEN="your_bot_token_here" telegram-downloader
```

### الطريقة الثالثة: Docker Compose

```bash
# قم بتعديل BOT_TOKEN في ملف .env
# ثم شغل:
docker-compose up -d
```

## الاستخدام 💻

1. ابدأ المحادثة مع البوت بـ `/start`
2. أرسل رابط الفيديو
3. اختر الجودة أو صيغة التحميل
4. انتظر حتى ينتهي التحميل والإرسال

## الأوامر المتاحة 🎯

| الأمر | الوصف |
|------|-------|
| `/start` | بدء استخدام البوت |
| `/help` | عرض المساعدة |
| `/cancel` | إلغاء المهمة الحالية |

## البيئات المدعومة 🌍

- YouTube
- Instagram
- TikTok
- Facebook
- Twitter
- وأكثر من 1000 موقع آخر

## الترخيص 📄

هذا المشروع مرخص تحت MIT License

## المساهمة 🤝

أي تحسينات أو تصحيحات مرحب بها!

## المؤلف 👨‍💻

aitothe282
