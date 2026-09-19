import os
import base64
import sqlite3
from datetime import datetime, timezone

import httpx

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ENV_CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID")
ENV_CF_API_TOKEN = os.getenv("CF_API_TOKEN")

ADMIN_ID = 8394607974
CHANNEL_USERNAME = "@ByteTunnel"
CHANNEL_URL = "https://t.me/ByteTunnel"

TEXT_TO_IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"
IMAGE_TO_IMAGE_MODEL = "@cf/runwayml/stable-diffusion-v1-5-img2img"

DB_FILE = "byteimage.db"


# =========================================================
# DATABASE
# =========================================================

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            blocked INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            last_seen TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT,
            prompt TEXT NOT NULL,
            request_type TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.commit()
    conn.close()


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def register_user(user):
    if not user:
        return

    now = now_text()

    conn = db()

    conn.execute("""
        INSERT INTO users (
            user_id,
            username,
            first_name,
            created_at,
            last_seen
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name,
            last_seen = excluded.last_seen
    """, (
        user.id,
        user.username or "",
        user.first_name or "",
        now,
        now,
    ))

    conn.commit()
    conn.close()


def is_blocked(user_id):
    conn = db()

    row = conn.execute(
        "SELECT blocked FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    conn.close()

    return bool(row and row["blocked"])


def add_request(user, prompt, request_type):
    conn = db()

    conn.execute("""
        INSERT INTO requests (
            user_id,
            username,
            first_name,
            prompt,
            request_type,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        user.id,
        user.username or "",
        user.first_name or "",
        prompt,
        request_type,
        now_text(),
    ))

    conn.commit()
    conn.close()


def get_setting(key, default=None):
    conn = db()

    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (key,)
    ).fetchone()

    conn.close()

    if row:
        return row["value"]

    return default


def set_setting(key, value):
    conn = db()

    conn.execute("""
        INSERT INTO settings(key, value)
        VALUES (?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value = excluded.value
    """, (key, value))

    conn.commit()
    conn.close()


def get_cf_account_id():
    return get_setting("CF_ACCOUNT_ID") or ENV_CF_ACCOUNT_ID


def get_cf_api_token():
    return get_setting("CF_API_TOKEN") or ENV_CF_API_TOKEN


# =========================================================
# KEYBOARDS
# =========================================================

def main_keyboard(user_id=None):
    rows = [
        ["🎨 ساخت تصویر", "🧠 تبدیل پرامپت به عکس"],
        ["🔄 ساخت تصویر جدید"],
    ]

    if user_id == ADMIN_ID:
        rows.append(["👑 پنل مدیریت"])

    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True
    )


def admin_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["👥 کاربران", "📜 آخرین درخواست‌ها"],
            ["🔎 جستجوی کاربر", "📊 آمار کلی"],
            ["📈 مصرف امروز", "🚫 مدیریت کاربران"],
            ["⚙️ مدیریت API"],
            ["🔙 بازگشت"],
        ],
        resize_keyboard=True
    )


def join_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📢 عضویت در کانال",
                url=CHANNEL_URL
            )
        ],
        [
            InlineKeyboardButton(
                "✅ بررسی عضویت",
                callback_data="check_join"
            )
        ]
    ])


def result_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["🔄 دوباره بساز", "🎨 ساخت تصویر جدید"],
            ["🧠 تبدیل پرامپت به عکس"],
        ],
        resize_keyboard=True
    )


# =========================================================
# FORCE JOIN
# =========================================================

async def check_membership(user_id, bot):
    try:
        member = await bot.get_chat_member(
            CHANNEL_USERNAME,
            user_id
        )

        return member.status in (
            "member",
            "administrator",
            "creator"
        )

    except Exception as e:
        print("JOIN CHECK ERROR:", repr(e))
        return False


async def require_join(update, context):
    user = update.effective_user

    if not user:
        return False

    if user.id == ADMIN_ID:
        return True

    if is_blocked(user.id):
        if update.effective_message:
            await update.effective_message.reply_text(
                "🚫 دسترسی شما به این بات مسدود شده است."
            )
        return False

    ok = await check_membership(
        user.id,
        context.bot
    )

    if ok:
        return True

    if update.effective_message:
        await update.effective_message.reply_text(
            "📢 برای استفاده از ByteImage ابتدا باید عضو کانال ما شوی.\n\n"
            "بعد از عضویت روی «✅ بررسی عضویت» بزن.",
            reply_markup=join_keyboard()
        )

    return False


# =========================================================
# HELPERS
# =========================================================

def contains_persian(text):
    return any(
        "\u0600" <= char <= "\u06ff"
        for char in text
    )


def cloudflare_url(model):
    account_id = get_cf_account_id()

    return (
        "https://api.cloudflare.com/client/v4/"
        f"accounts/{account_id}/ai/run/{model}"
    )


def decode_image_result(result):
    output = result.get("result")

    if isinstance(output, dict):
        image = output.get("image")

        if isinstance(image, str):
            return base64.b64decode(image)

        if isinstance(image, list):
            return bytes(image)

    if isinstance(output, str):
        try:
            return base64.b64decode(output)
        except Exception:
            pass

    if isinstance(output, list):
        return bytes(output)

    raise Exception("Cloudflare returned an invalid image response")


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    register_user(user)

    context.user_data.clear()

    if not await require_join(update, context):
        return

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
        "👇 برای شروع یکی از گزینه‌های پایین را انتخاب کن.",
        reply_markup=main_keyboard(user.id)
    )


# =========================================================
# TEXT TO IMAGE
# =========================================================

async def create_image(prompt):
    account_id = get_cf_account_id()
    api_token = get_cf_api_token()

    if not account_id or not api_token:
        raise Exception("Cloudflare API configuration missing")

    url = cloudflare_url(TEXT_TO_IMAGE_MODEL)

    headers = {
        "Authorization": f"Bearer {api_token}",
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
            json=data
        )

    response.raise_for_status()

    result = response.json()

    if not result.get("success"):
        raise Exception(result.get("errors"))

    return decode_image_result(result)


# =========================================================
# IMAGE TO IMAGE
# =========================================================

async def transform_image(image_bytes, prompt):
    account_id = get_cf_account_id()
    api_token = get_cf_api_token()

    if not account_id or not api_token:
        raise Exception("Cloudflare API configuration missing")

    url = cloudflare_url(IMAGE_TO_IMAGE_MODEL)

    image_b64 = base64.b64encode(image_bytes).decode()

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    data = {
        "prompt": prompt,
        "image_b64": image_b64,
        "strength": 0.65,
        "num_steps": 20,
        "guidance": 7.5,
    }

    async with httpx.AsyncClient(timeout=240) as client:
        response = await client.post(
            url,
            headers=headers,
            json=data
        )

    response.raise_for_status()

    result = response.json()

    if not result.get("success"):
        raise Exception(result.get("errors"))

    return decode_image_result(result)


# =========================================================
# PHOTO HANDLER
# =========================================================

async def handle_photo(update, context):
    user = update.effective_user

    register_user(user)

    if not await require_join(update, context):
        return

    if not context.user_data.get("waiting_image"):
        await update.message.reply_text(
            "برای شروع گزینه «🧠 تبدیل پرامپت به عکس» را انتخاب کن.",
            reply_markup=main_keyboard(user.id)
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
            "لطفاً دوباره امتحان کن."
        )


# =========================================================
# ADMIN PANEL
# =========================================================

async def admin_panel(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    context.user_data.clear()

    await update.message.reply_text(
        "👑 پنل مدیریت ByteImage\n\n"
        "یکی از گزینه‌های زیر را انتخاب کن.",
        reply_markup=admin_keyboard()
    )


async def show_users(update, context):
    conn = db()

    rows = conn.execute("""
        SELECT user_id, username, first_name, blocked, last_seen
        FROM users
        ORDER BY last_seen DESC
        LIMIT 30
    """).fetchall()

    total = conn.execute(
        "SELECT COUNT(*) AS c FROM users"
    ).fetchone()["c"]

    conn.close()

    if not rows:
        await update.message.reply_text(
            "👥 هنوز کاربری ثبت نشده.",
            reply_markup=admin_keyboard()
        )
        return

    text = f"👥 کاربران\n\nتعداد کل: {total}\n\n"

    for i, row in enumerate(rows, 1):
        name = row["first_name"] or "-"
        username = (
            f"@{row['username']}"
            if row["username"]
            else "-"
        )

        status = "🚫" if row["blocked"] else "🟢"

        text += (
            f"{i}. {status} {name}\n"
            f"🆔 {row['user_id']}\n"
            f"👤 {username}\n"
            f"🕐 {row['last_seen']}\n\n"
        )

    await update.message.reply_text(
        text,
        reply_markup=admin_keyboard()
    )


async def show_requests(update, context):
    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM requests
        ORDER BY id DESC
        LIMIT 20
    """).fetchall()

    conn.close()

    if not rows:
        await update.message.reply_text(
            "📜 هنوز درخواستی ثبت نشده.",
            reply_markup=admin_keyboard()
        )
        return

    for row in rows:
        name = row["first_name"] or "-"
        username = (
            f"@{row['username']}"
            if row["username"]
            else "-"
        )

        await update.message.reply_text(
            f"👤 {name}\n"
            f"👤 {username}\n"
            f"🆔 {row['user_id']}\n"
            f"📝 {row['prompt']}\n"
            f"🕐 {row['created_at']}\n"
            f"🔹 نوع: {row['request_type']}"
        )

    await update.message.reply_text(
        "👑 پنل مدیریت",
        reply_markup=admin_keyboard()
    )


async def show_stats(update, context):
    conn = db()

    users = conn.execute(
        "SELECT COUNT(*) AS c FROM users"
    ).fetchone()["c"]

    requests = conn.execute(
        "SELECT COUNT(*) AS c FROM requests"
    ).fetchone()["c"]

    images = conn.execute("""
        SELECT COUNT(*) AS c
        FROM requests
        WHERE request_type = 'text_to_image'
    """).fetchone()["c"]

    edits = conn.execute("""
        SELECT COUNT(*) AS c
        FROM requests
        WHERE request_type = 'image_to_image'
    """).fetchone()["c"]

    blocked = conn.execute("""
        SELECT COUNT(*) AS c
        FROM users
        WHERE blocked = 1
    """).fetchone()["c"]

    conn.close()

    await update.message.reply_text(
        "📊 آمار کلی\n\n"
        f"👥 کاربران: {users}\n"
        f"📜 کل درخواست‌ها: {requests}\n"
        f"🎨 ساخت تصویر: {images}\n"
        f"🧠 تبدیل عکس: {edits}\n"
        f"🚫 کاربران مسدود: {blocked}",
        reply_markup=admin_keyboard()
    )


async def show_today_usage(update, context):
    today = datetime.now().strftime("%Y-%m-%d")

    conn = db()

    total = conn.execute("""
        SELECT COUNT(*) AS c
        FROM requests
        WHERE created_at LIKE ?
    """, (today + "%",)).fetchone()["c"]

    text_images = conn.execute("""
        SELECT COUNT(*) AS c
        FROM requests
        WHERE created_at LIKE ?
        AND request_type = 'text_to_image'
    """, (today + "%",)).fetchone()["c"]

    edits = conn.execute("""
        SELECT COUNT(*) AS c
        FROM requests
        WHERE created_at LIKE ?
        AND request_type = 'image_to_image'
    """, (today + "%",)).fetchone()["c"]

    conn.close()

    await update.message.reply_text(
        "📈 مصرف امروز\n\n"
        f"📊 کل درخواست‌ها: {total}\n"
        f"🎨 ساخت تصویر: {text_images}\n"
        f"🧠 تبدیل عکس: {edits}",
        reply_markup=admin_keyboard()
    )


async def ask_search_user(update, context):
    context.user_data["admin_search"] = True

    await update.message.reply_text(
        "🔎 آیدی عددی کاربر را ارسال کن.",
        reply_markup=admin_keyboard()
    )


async def search_user(update, context, text):
    try:
        user_id = int(text)
    except ValueError:
        await update.message.reply_text(
            "❌ آیدی باید عددی باشد.",
            reply_markup=admin_keyboard()
        )
        return

    conn = db()

    user = conn.execute("""
        SELECT *
        FROM users
        WHERE user_id = ?
    """, (user_id,)).fetchone()

    requests = conn.execute("""
        SELECT *
        FROM requests
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 10
    """, (user_id,)).fetchall()

    conn.close()

    if not user:
        await update.message.reply_text(
            "❌ کاربر پیدا نشد.",
            reply_markup=admin_keyboard()
        )
        return

    name = user["first_name"] or "-"
    username = (
        f"@{user['username']}"
        if user["username"]
        else "-"
    )

    text_out = (
        f"👤 {name}\n"
        f"👤 {username}\n"
        f"🆔 {user['user_id']}\n"
        f"🚦 وضعیت: "
        f"{'🚫 مسدود' if user['blocked'] else '🟢 فعال'}\n"
        f"🕐 آخرین فعالیت: {user['last_seen']}\n\n"
        f"📜 آخرین درخواست‌ها:\n\n"
    )

    if requests:
        for row in requests:
            text_out += (
                f"📝 {row['prompt']}\n"
                f"🕐 {row['created_at']}\n\n"
            )
    else:
        text_out += "هنوز درخواستی ثبت نشده."

    await update.message.reply_text(
        text_out,
        reply_markup=admin_keyboard()
    )


async def user_management(update, context):
    context.user_data["admin_manage"] = True

    await update.message.reply_text(
        "🚫 مدیریت کاربران\n\n"
        "برای مسدود کردن یا رفع مسدودی، آیدی عددی کاربر را ارسال کن.",
        reply_markup=admin_keyboard()
    )


async def manage_user(update, context, text):
    try:
        user_id = int(text)
    except ValueError:
        await update.message.reply_text(
            "❌ آیدی باید عددی باشد.",
            reply_markup=admin_keyboard()
        )
        return

    if user_id == ADMIN_ID:
        await update.message.reply_text(
            "❌ ادمین اصلی قابل مسدود شدن نیست.",
            reply_markup=admin_keyboard()
        )
        return

    conn = db()

    row = conn.execute("""
        SELECT blocked
        FROM users
        WHERE user_id = ?
    """, (user_id,)).fetchone()

    if not row:
        conn.close()

        await update.message.reply_text(
            "❌ کاربر پیدا نشد.",
            reply_markup=admin_keyboard()
        )
        return

    new_status = 0 if row["blocked"] else 1

    conn.execute("""
        UPDATE users
        SET blocked = ?
        WHERE user_id = ?
    """, (new_status, user_id))

    conn.commit()
    conn.close()

    await update.message.reply_text(
        (
            "🚫 کاربر مسدود شد."
            if new_status
            else
            "✅ مسدودی کاربر برداشته شد."
        ),
        reply_markup=admin_keyboard()
    )


async def api_panel(update, context):
    account = get_cf_account_id()
    token = get_cf_api_token()

    masked = "تنظیم نشده"

    if token:
        if len(token) > 8:
            masked = (
                token[:4]
                + "••••••••"
                + token[-4:]
            )
        else:
            masked = "••••••••"

    await update.message.reply_text(
        "⚙️ مدیریت API\n\n"
        f"🆔 CF Account ID:\n{account or 'تنظیم نشده'}\n\n"
        f"🔑 CF API Token:\n{masked}\n\n"
        "برای تغییر Account ID گزینه مربوطه را انتخاب کن.",
        reply_markup=ReplyKeyboardMarkup(
            [
                ["🆔 تغییر Account ID"],
                ["🔑 تغییر API Token"],
                ["🔙 بازگشت"],
            ],
            resize_keyboard=True
        )
    )


# =========================================================
# ADMIN MESSAGE ROUTER
# =========================================================

async def admin_message_router(update, context, text):
    if update.effective_user.id != ADMIN_ID:
        return False

    if context.user_data.get("admin_search"):
        context.user_data["admin_search"] = False
        await search_user(update, context, text)
        return True

    if context.user_data.get("admin_manage"):
        context.user_data["admin_manage"] = False
        await manage_user(update, context, text)
        return True

    if context.user_data.get("waiting_account_id"):
        context.user_data["waiting_account_id"] = False

        set_setting("CF_ACCOUNT_ID", text.strip())

        await update.message.reply_text(
            "✅ CF Account ID ذخیره شد.",
            reply_markup=admin_keyboard()
        )
        return True

    if context.user_data.get("waiting_api_token"):
        context.user_data["waiting_api_token"] = False

        set_setting("CF_API_TOKEN", text.strip())

        await update.message.reply_text(
            "✅ CF API Token ذخیره شد.\n\n"
            "🔐 مقدار Token نمایش داده نمی‌شود.",
            reply_markup=admin_keyboard()
        )
        return True

    if text == "👥 کاربران":
        await show_users(update, context)
        return True

    if text == "📜 آخرین درخواست‌ها":
        await show_requests(update, context)
        return True

    if text == "🔎 جستجوی کاربر":
        await ask_search_user(update, context)
        return True

    if text == "📊 آمار کلی":
        await show_stats(update, context)
        return True

    if text == "📈 مصرف امروز":
        await show_today_usage(update, context)
        return True

    if text == "🚫 مدیریت کاربران":
        await user_management(update, context)
        return True

    if text == "⚙️ مدیریت API":
        await api_panel(update, context)
        return True

    if text == "🆔 تغییر Account ID":
        context.user_data["waiting_account_id"] = True

        await update.message.reply_text(
            "🆔 CF Account ID جدید را ارسال کن.",
            reply_markup=admin_keyboard()
        )
        return True

    if text == "🔑 تغییر API Token":
        context.user_data["waiting_api_token"] = True

        await update.message.reply_text(
            "🔑 CF API Token جدید را ارسال کن.",
            reply_markup=admin_keyboard()
        )
        return True

    return False


# =========================================================
# CALLBACK
# =========================================================

async def callback_handler(update, context):
    query = update.callback_query

    await query.answer()

    if query.data != "check_join":
        return

    user = query.from_user

    register_user(user)

    if await check_membership(user.id, context.bot):
        await query.edit_message_text(
            "✅ عضویت شما تأیید شد.\n\n"
            "حالا می‌تونی از ByteImage استفاده کنی."
        )

        await context.bot.send_message(
            chat_id=user.id,
            text="🎨 منوی اصلی آماده است.",
            reply_markup=main_keyboard(user.id)
        )

    else:
        await query.answer(
            "❌ هنوز عضو کانال نیستی.",
            show_alert=True
        )


# =========================================================
# MAIN MESSAGE HANDLER
# =========================================================

async def message_handler(update, context):
    user = update.effective_user

    register_user(user)

    text = update.message.text.strip()

    # Admin routing
    if user.id == ADMIN_ID:
        if await admin_message_router(update, context, text):
            return

    # Admin panel
    if text == "👑 پنل مدیریت":
        await admin_panel(update, context)
        return

    if text == "🔙 بازگشت":
        context.user_data.clear()

        await update.message.reply_text(
            "🏠 منوی اصلی",
            reply_markup=main_keyboard(user.id)
        )
        return

    # All user functionality requires membership
    if not await require_join(update, context):
        return

    # -----------------------------------------------------
    # Image to image
    # -----------------------------------------------------

    if text == "🧠 تبدیل پرامپت به عکس":
        context.user_data.clear()
        context.user_data["waiting_image"] = True

        await update.message.reply_text(
            "🖼️ عکس موردنظرت رو بفرست."
        )
        return

    # -----------------------------------------------------
    # Text to image
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # New image
    # -----------------------------------------------------

    if text == "🎨 ساخت تصویر جدید":
        context.user_data.clear()
        context.user_data["waiting"] = True

        await update.message.reply_text(
            "🖼 توضیح تصویر جدید را به انگلیسی بفرست."
        )
        return

    # -----------------------------------------------------
    # Repeat
    # -----------------------------------------------------

    if text == "🔄 دوباره بساز":
        source_image = context.user_data.get("source_image")
        last_prompt = context.user_data.get("last_edit_prompt")

        if not source_image or not last_prompt:
            await update.message.reply_text(
                "❌ تصویر قبلی برای ساخت مجدد پیدا نشد.",
                reply_markup=main_keyboard(user.id)
            )
            return

        msg = await update.message.reply_text(
            "🧠 در حال ساخت نسخه جدید...\n"
            "⏳ لطفاً صبر کن."
        )

        try:
            image = await transform_image(
                source_image,
                last_prompt
            )

            add_request(
                user,
                last_prompt,
                "image_to_image"
            )

            await update.message.reply_photo(
                photo=image,
                caption="🧠 تصویر جدید ساخته شد",
                reply_markup=result_keyboard()
            )

            await msg.delete()

        except Exception as e:
            print("REPEAT ERROR:", repr(e))

            await msg.edit_text(
                "❌ ساخت تصویر انجام نشد.\n"
                "لطفاً دوباره امتحان کن."
            )

        return

    # -----------------------------------------------------
    # Image edit prompt
    # -----------------------------------------------------

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
                "دوباره «🧠 تبدیل پرامپت به عکس» را انتخاب کن."
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
                text
            )

            add_request(
                user,
                text,
                "image_to_image"
            )

            await update.message.reply_photo(
                photo=image,
                caption="🧠 تصویر با موفقیت ساخته شد",
                reply_markup=result_keyboard()
            )

            await msg.delete()

        except Exception as e:
            print("IMG2IMG ERROR:", repr(e))

            await msg.edit_text(
                "❌ تبدیل تصویر انجام نشد.\n\n"
                "لطفاً دوباره امتحان کن."
            )

        return

    # -----------------------------------------------------
    # Text image prompt
    # -----------------------------------------------------

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

            add_request(
                user,
                text,
                "text_to_image"
            )

            await update.message.reply_photo(
                photo=image,
                caption="🎨 تصویر ساخته شد",
                reply_markup=result_keyboard()
            )

            await msg.delete()

        except Exception as e:
            print("TEXT2IMAGE ERROR:", repr(e))

            await msg.edit_text(
                "❌ ساخت تصویر انجام نشد.\n"
                "لطفاً دوباره امتحان کن."
            )

        return

    await update.message.reply_text(
        "برای شروع یکی از گزینه‌های منوی پایین را انتخاب کن.",
        reply_markup=main_keyboard(user.id)
    )


# =========================================================
# MAIN
# =========================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN تنظیم نشده")

    if not ENV_CF_ACCOUNT_ID:
        print("⚠️ CF_ACCOUNT_ID از Railway تنظیم نشده؛ ممکن است از پنل تنظیم شود.")

    if not ENV_CF_API_TOKEN:
        print("⚠️ CF_API_TOKEN از Railway تنظیم نشده؛ ممکن است از پنل تنظیم شود.")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        MessageHandler(
            filters.PHOTO,
            handle_photo
        )
    )

    app.add_handler(
        __import__(
            "telegram.ext",
            fromlist=["CallbackQueryHandler"]
        ).CallbackQueryHandler(
            callback_handler
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_handler
        )
    )

    print("🤖 ByteImageBot FULL VERSION is running...")

    app.run_polling()


if __name__ == "__main__":
    main()
