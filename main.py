import os
import base64
import httpx
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN")
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID")
CF_API_TOKEN = os.getenv("CF_API_TOKEN")

MODEL = "@cf/black-forest-labs/flux-1-schnell"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["🎨 ساخت تصویر"]]

    await update.message.reply_text(
        "🎨 به ByteImage خوش آمدی!\n\n"
        "برای ساخت تصویر روی دکمه زیر بزن.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True
        )
    )


async def create_image(prompt):
    url = (
        f"https://api.cloudflare.com/client/v4/"
        f"accounts/{CF_ACCOUNT_ID}/ai/run/{MODEL}"
    )

    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json"
    }

    data = {
        "prompt": prompt,
        "steps": 4
    }

    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(
            url,
            headers=headers,
            json=data
        )

    response.raise_for_status()

    result = response.json()

    if not result.get("success"):
        raise Exception(result.get("errors"))

    image = result["result"]["image"]

    return base64.b64decode(image)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "🎨 ساخت تصویر":
        context.user_data["waiting"] = True

        await update.message.reply_text(
            "🖼 توضیح تصویری که می‌خواهی را بفرست.\n\n"
            "مثال:\n"
            "یک ماشین اسپرت مشکی در خیابان بارانی شب، "
            "نورهای نئونی، واقع‌گرایانه"
        )
        return

    if not context.user_data.get("waiting"):
        await update.message.reply_text(
            "برای شروع روی «🎨 ساخت تصویر» بزن."
        )
        return

    context.user_data["waiting"] = False

    msg = await update.message.reply_text(
        "🎨 در حال ساخت تصویر...\n⏳ لطفاً صبر کن."
    )

    try:
        image = await create_image(text)

        await update.message.reply_photo(
            photo=image,
            caption="🎨 تصویر ساخته شد"
        )

        await msg.delete()

    except Exception as e:
        print("ERROR:", e)

        await msg.edit_text(
            "❌ ساخت تصویر انجام نشد.\n"
            "لطفاً دوباره امتحان کن."
        )


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN تنظیم نشده")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

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
