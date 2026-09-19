import os
import base64
import httpx

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID")
CF_API_TOKEN = os.getenv("CF_API_TOKEN")

TEXT_TO_IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"
IMAGE_TO_IMAGE_MODEL = "@cf/runwayml/stable-diffusion-v1-5-img2img"


# =========================
# Keyboard
# =========================

def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["🎨 ساخت تصویر", "🧠 تبدیل پرامپت به عکس"],
        ],
        resize_keyboard=True
    )


def image_result_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["🔄 دوباره بساز", "🎨 ساخت تصویر جدید"],
        ],
        resize_keyboard=True
    )


# =========================
# Helpers
# =========================

def contains_persian(text: str) -> bool:
    return any(
        "\u0600" <= char <= "\u06ff"
        for char in text
    )


def cloudflare_url(model: str) -> str:
    return (
        f"https://api.cloudflare.com/client/v4/"
        f"accounts/{CF_ACCOUNT_ID}/ai/run/{model}"
    )


# =========================
# Start
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "🎨 به ByteImage خوش آمدی!\n\n"
        "با این بات می‌تونی با استفاده از هوش مصنوعی، "
        "تصویر دلخواهت رو بسازی. 🖼️✨\n\n"
        "📌 نکته مهم:\n"
        "برای دریافت نتیجه بهتر، توضیح تصویر را به زبان انگلیسی ارسال کن.\n"
        "❌ در حال حاضر پرامپت فارسی پشتیبانی نمی‌شود.\n\n"
        "💡 مثال:\n"
        "A black sports car driving on a rainy city street at night, "
        "neon lights, cinematic, realistic\n\n"
        "⏳ بعد از ارسال توضیحت، چند لحظه صبر کن تا تصویر ساخته شود.\n\n"
        "👇 برای شروع، از منوی پایین گزینه موردنظر را انتخاب کن.",
        reply_markup=main_keyboard()
    )


# =========================
# Text To Image
# =========================

async def create_image(prompt: str) -> bytes:
    url = cloudflare_url(TEXT_TO_IMAGE_MODEL)

    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json",
    }

    data = {
        "prompt": prompt,
        "steps": 4,
    }

    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(
            url,
            headers=headers,
            json=data,
        )

    response.raise_for_status()

    result = response.json()

    if not result.get("success"):
        raise Exception(result.get("errors"))

    image = result["result"]["image"]

    return base64.b64decode(image)


# =========================
# Image To Image
# =========================

async def transform_image(
    image_bytes: bytes,
    prompt: str,
    strength: float = 0.65,
) -> bytes:

    url = cloudflare_url(IMAGE_TO_IMAGE_MODEL)

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json",
    }

    data = {
        "prompt": prompt,
        "image_b64": image_b64,
        "strength": strength,
        "num_steps": 20,
        "guidance": 7.5,
    }

    async with httpx.AsyncClient(timeout=240) as client:
        response = await client.post(
            url,
            headers=headers,
            json=data,
        )

    response.raise_for_status()

    result = response.json()

    if not result.get("success"):
        raise Exception(result.get("errors"))

    output = result.get("result")

    if not output:
        raise Exception("Cloudflare returned an empty result")

    # Cloudflare image response
    if isinstance(output, dict):
        image = output.get("image")

        if image:
            return base64.b64decode(image)

    # Fallback
    if isinstance(output, str):
        try:
            return base64.b64decode(output)
        except Exception:
            pass

    raise Exception("Invalid image response from Cloudflare")


# =========================
# Handle Photo
# =========================

async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.user_data.get("waiting_image"):
        await update.message.reply_text(
            "برای شروع، از منوی پایین یکی از گزینه‌ها را انتخاب کن.",
            reply_markup=main_keyboard()
        )
        return

    try:
        photo = update.message.photo[-1]

        telegram_file = await context.bot.get_file(
            photo.file_id
        )

        image_bytes = await telegram_file.download_as_bytearray()

        context.user_data["source_image"] = bytes(image_bytes)
        context.user_data["waiting_image"] = False
        context.user_data["waiting_edit_prompt"] = True

        await update.message.reply_text(
            "✍️ حالا پرامپت یا توضیحی که می‌خوای روی عکس اعمال بشه رو بفرست.\n\n"
            "📌 لطفاً پرامپت را به انگلیسی ارسال کن.\n\n"
            "💡 مثال:\n"
            "Add cinematic neon lights and make the scene look futuristic."
        )

    except Exception as e:
        print("PHOTO ERROR:", repr(e))

        await update.message.reply_text(
            "❌ دریافت عکس انجام نشد.\n"
            "لطفاً دوباره عکس را ارسال کن."
        )


# =========================
# Main Message Handler
# =========================

async def message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    text = update.message.text.strip()

    # -------------------------
    # Image-to-image start
    # -------------------------

    if text == "🧠 تبدیل پرامپت به عکس":
        context.user_data.clear()
        context.user_data["waiting_image"] = True

        await update.message.reply_text(
            "🖼️ عکس موردنظرت رو بفرست."
        )
        return

    # -------------------------
    # Text-to-image start
    # -------------------------

    if text == "🎨 ساخت تصویر":
        context.user_data.clear()
        context.user_data["waiting"] = True

        await update.message.reply_text(
            "🖼 توضیح تصویری که می‌خواهی را بفرست.\n\n"
            "📌 پرامپت را به انگلیسی ارسال کن.\n\n"
            "💡 مثال:\n"
            "A black sports car driving on a rainy city street at night, "
            "neon lights, cinematic, realistic"
        )
        return

    # -------------------------
    # New image
    # -------------------------

    if text == "🎨 ساخت تصویر جدید":
        context.user_data.clear()
        context.user_data["waiting"] = True

        await update.message.reply_text(
            "🖼 توضیح تصویر جدید را به انگلیسی بفرست."
        )
        return

    # -------------------------
    # Repeat last image edit
    # -------------------------

    if text == "🔄 دوباره بساز":
        source_image = context.user_data.get("source_image")
        last_prompt = context.user_data.get("last_edit_prompt")

        if not source_image or not last_prompt:
            await update.message.reply_text(
                "❌ تصویر قبلی برای ساخت مجدد پیدا نشد."
            )
            return

        msg = await update.message.reply_text(
            "🧠 در حال ساخت نسخه جدید...\n"
            "⏳ لطفاً صبر کن."
        )

        try:
            image = await transform_image(
                source_image,
                last_prompt,
                strength=0.65
            )

            await update.message.reply_photo(
                photo=image,
                caption="🧠 تصویر جدید ساخته شد",
                reply_markup=image_result_keyboard()
            )

            await msg.delete()

        except Exception as e:
            print("REPEAT ERROR:", repr(e))

            await msg.edit_text(
                "❌ ساخت تصویر انجام نشد.\n"
                "لطفاً دوباره امتحان کن."
            )

        return

    # -------------------------
    # Image edit prompt
    # -------------------------

    if context.user_data.get("waiting_edit_prompt"):

        if contains_persian(text):
            await update.message.reply_text(
                "❌ فعلاً پرامپت فارسی پشتیبانی نمی‌شود.\n\n"
                "لطفاً توضیحت را به انگلیسی ارسال کن."
            )
            return

        source_image = context.user_data.get("source_image")

        if not source_image:
            context.user_data.clear()

            await update.message.reply_text(
                "❌ عکس قبلی پیدا نشد.\n"
                "لطفاً دوباره گزینه «🧠 تبدیل پرامپت به عکس» را انتخاب کن."
            )
            return

        context.user_data["waiting_edit_prompt"] = False
        context.user_data["last_edit_prompt"] = text

        msg = await update.message.reply_text(
            "🧠 در حال تغییر تصویر...\n"
            "⏳ ممکنه کمی زمان ببره."
        )

        try:
            image = await transform_image(
                source_image,
                text,
                strength=0.65
            )

            await update.message.reply_photo(
                photo=image,
                caption="🧠 تصویر با موفقیت ساخته شد",
                reply_markup=image_result_keyboard()
            )

            await msg.delete()

        except Exception as e:
            print("IMG2IMG ERROR:", repr(e))

            await msg.edit_text(
                "❌ تبدیل تصویر انجام نشد.\n\n"
                "ممکنه سرویس Cloudflare موقتاً خطا داده باشه.\n"
                "لطفاً دوباره امتحان کن."
            )

        return

    # -------------------------
    # Text-to-image prompt
    # -------------------------

    if context.user_data.get("waiting"):

        if contains_persian(text):
            await update.message.reply_text(
                "❌ فعلاً پرامپت فارسی پشتیبانی نمی‌شود.\n\n"
                "لطفاً توضیح تصویر را به انگلیسی ارسال کن."
            )
            return

        context.user_data["waiting"] = False

        msg = await update.message.reply_text(
            "🎨 در حال ساخت تصویر...\n"
            "⏳ لطفاً صبر کن."
        )

        try:
            image = await create_image(text)

            await update.message.reply_photo(
                photo=image,
                caption="🎨 تصویر ساخته شد",
                reply_markup=main_keyboard()
            )

            await msg.delete()

        except Exception as e:
            print("TEXT2IMAGE ERROR:", repr(e))

            await msg.edit_text(
                "❌ ساخت تصویر انجام نشد.\n"
                "لطفاً دوباره امتحان کن."
            )

        return

    # -------------------------
    # Default
    # -------------------------

    await update.message.reply_text(
        "برای شروع یکی از گزینه‌های منوی پایین را انتخاب کن.",
        reply_markup=main_keyboard()
    )


# =========================
# Main
# =========================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN تنظیم نشده")

    if not CF_ACCOUNT_ID:
        raise RuntimeError("CF_ACCOUNT_ID تنظیم نشده")

    if not CF_API_TOKEN:
        raise RuntimeError("CF_API_TOKEN تنظیم نشده")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        MessageHandler(
            filters.PHOTO,
            handle_photo
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_handler
        )
    )

    print("🤖 ByteImageBot is running...")

    app.run_polling()


if __name__ == "__main__":
    main()
