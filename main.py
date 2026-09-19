import os
import asyncio
import base64
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DEFAULT_CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID")
DEFAULT_CF_API_TOKEN = os.getenv("CF_API_TOKEN")

ADMIN_ID = 8394607974
CHANNEL_USERNAME = "@ByteTunnel"

IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"
PROMPT_MODEL = "@cf/meta/llama-3.2-3b-instruct"

DB_FILE = "byteimage.db"
BOT_TIMEZONE = os.getenv("BOT_TIMEZONE", "Asia/Tehran")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")


# =========================
# BOT CLOCK NAME
# =========================

async def bot_clock_task(bot):
    try:
        tz = ZoneInfo(BOT_TIMEZONE)
    except Exception:
        tz = timezone.utc

    last_name = None

    while True:
        try:
            current = datetime.now(tz)
            bot_name = f"ByteImage • {current:%H:%M}"

            if bot_name != last_name:
                try:
                    result = await bot.set_my_name(
                        name=bot_name
                    )

                    if result:
                        last_name = bot_name
                        print(f"✅ Bot name updated: {bot_name}")
                    else:
                        print(f"⚠️ Telegram rejected name update: {bot_name}")

                except Exception as e:
                    print(
                        "⚠️ BOT NAME UPDATE ERROR:",
                        repr(e)
                    )

            # دقیقاً تا شروع دقیقه بعد صبر می‌کنیم
            now_local = datetime.now(tz)
            delay = (
                60
                - now_local.second
                - (now_local.microsecond / 1_000_000)
            )

            await asyncio.sleep(max(2, delay))

        except asyncio.CancelledError:
            raise

        except Exception as e:
            print(
                "⚠️ BOT CLOCK LOOP ERROR:",
                repr(e)
            )
            await asyncio.sleep(10)


# =========================
# DATABASE
# =========================

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
            created_at TEXT,
            last_seen TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            first_name TEXT,
            prompt TEXT,
            request_type TEXT,
            created_at TEXT
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


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def save_user(user):
    if not user:
        return

    conn = db()
    current = now()

    conn.execute("""
        INSERT INTO users
        (user_id, username, first_name, blocked, created_at, last_seen)
        VALUES (?, ?, ?, 0, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name,
            last_seen=excluded.last_seen
    """, (
        user.id,
        user.username or "",
        user.first_name or "",
        current,
        current,
    ))

    conn.commit()
    conn.close()


def log_request(user, prompt, request_type):
    conn = db()

    conn.execute("""
        INSERT INTO requests
        (user_id, username, first_name, prompt, request_type, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        user.id,
        user.username or "",
        user.first_name or "",
        prompt,
        request_type,
        now(),
    ))

    conn.commit()
    conn.close()


def get_setting(key, default=None):
    conn = db()
    row = conn.execute(
        "SELECT value FROM settings WHERE key=?",
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
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, value))

    conn.commit()
    conn.close()


def cf_credentials():
    account_id = get_setting(
        "cf_account_id",
        DEFAULT_CF_ACCOUNT_ID
    )

    api_token = get_setting(
        "cf_api_token",
        DEFAULT_CF_API_TOKEN
    )

    return account_id, api_token


# =========================
# KEYBOARDS
# =========================

def main_keyboard(user_id):
    rows = [
        ["🎨 ساخت تصویر"],
        ["✨ ساخت پرامپت"],
        ["🔄 ساخت تصویر جدید"],
    ]

    if user_id == ADMIN_ID:
        rows.append(["👑 پنل مدیریت"])

    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True
    )


def admin_keyboard():
    return ReplyKeyboardMarkup([
        ["👥 کاربران", "📜 آخرین درخواست‌ها"],
        ["🔎 جستجوی کاربر", "📊 آمار کلی"],
        ["📈 مصرف امروز", "🚫 مدیریت کاربران"],
        ["⚙️ مدیریت API"],
        ["🔙 بازگشت"],
    ], resize_keyboard=True)


def api_keyboard():
    return ReplyKeyboardMarkup([
        ["🔑 تغییر Account ID"],
        ["🔐 تغییر API Token"],
        ["👁 وضعیت API"],
        ["🔙 بازگشت"],
    ], resize_keyboard=True)


# =========================
# FORCE JOIN
# =========================

async def is_joined(bot, user_id):
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

    except Exception:
        return False


async def join_message(update):
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📢 عضویت در کانال",
                url="https://t.me/ByteTunnel"
            )
        ],
        [
            InlineKeyboardButton(
                "✅ بررسی عضویت",
                callback_data="check_join"
            )
        ]
    ])

    text = (
        "🔒 برای استفاده از ByteImage ابتدا باید در کانال ما عضو شوی.\n\n"
        "📢 کانال: @ByteTunnel\n\n"
        "بعد از عضویت روی «✅ بررسی عضویت» بزن."
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=keyboard
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=keyboard
        )


async def ensure_joined(update):
    user = update.effective_user

    if user.id == ADMIN_ID:
        return True

    if await is_joined(update.get_bot(), user.id):
        return True

    await join_message(update)
    return False


# =========================
# CLOUDFLARE
# =========================

async def cloudflare_request(model, payload):
    account_id, api_token = cf_credentials()

    if not account_id:
        raise Exception("CF_ACCOUNT_ID تنظیم نشده است.")

    if not api_token:
        raise Exception("CF_API_TOKEN تنظیم نشده است.")

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{account_id}/ai/run/{model}"
    )

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            url,
            headers=headers,
            json=payload
        )

    try:
        data = response.json()
    except Exception:
        raise Exception(
            f"Cloudflare HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    if response.status_code >= 400 or not data.get("success"):
        raise Exception(
            f"Cloudflare HTTP {response.status_code}: "
            f"{data.get('errors', data)}"
        )

    return data.get("result", {})


# =========================
# IMAGE GENERATION
# =========================

async def create_image(prompt):
    result = await cloudflare_request(
        IMAGE_MODEL,
        {
            "prompt": prompt,
            "steps": 4
        }
    )

    image_data = result.get("image")

    if not image_data:
        raise Exception(
            "Cloudflare تصویر تولید کرد ولی داده تصویر دریافت نشد."
        )

    if isinstance(image_data, str):
        if image_data.startswith("data:image"):
            image_data = image_data.split(",", 1)[1]

        try:
            return base64.b64decode(image_data)
        except Exception:
            return image_data.encode()

    if isinstance(image_data, bytes):
        return image_data

    raise Exception("فرمت تصویر دریافتی نامعتبر است.")


# =========================
# PROMPT GENERATOR
# =========================

async def generate_prompt(description):
    system_prompt = """
You are ByteImage Prompt Engineer.

Your job is to convert the user's image description into ONE
high-quality English prompt for an AI image generation model.

The user may write in Persian, English, or mixed language.

Rules:
- Understand Persian naturally.
- Preserve the user's intended subject and meaning.
- Expand the description with useful visual details when appropriate.
- Add composition, lighting, camera, atmosphere, materials and realism
  only when they improve the requested image.
- Do not change the main subject.
- Do not invent unrelated objects or people.
- Do not explain anything.
- Do not use Markdown.
- Do not add quotation marks.
- Return ONLY the final English image prompt.
- Keep it detailed but practical.
"""

    user_prompt = f"""
Convert this user description into a professional English image prompt:

{description}
"""

    result = await cloudflare_request(
        PROMPT_MODEL,
        {
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt.strip()
                },
                {
                    "role": "user",
                    "content": user_prompt.strip()
                }
            ],
            "max_tokens": 300,
            "temperature": 0.65
        }
    )

    prompt = result.get("response")

    if not prompt:
        raise Exception(
            "مدل پرامپت پاسخی برنگرداند."
        )

    prompt = prompt.strip()

    if len(prompt) > 4000:
        prompt = prompt[:4000].rstrip()

    return prompt


# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)

    context.user_data.clear()

    if not await ensure_joined(update):
        return

    text = (
        "🎨 به ByteImage خوش آمدی!\n\n"
        "با این بات می‌تونی با استفاده از هوش مصنوعی، "
        "تصویر دلخواهت رو بسازی. 🖼️✨\n\n"
        "📌 امکانات:\n"
        "🎨 ساخت تصویر با پرامپت انگلیسی\n"
        "✨ تبدیل توضیح فارسی یا انگلیسی به پرامپت حرفه‌ای\n\n"
        "💡 اگر انگلیسی بلد نیستی، مشکلی نیست؛ "
        "از «✨ ساخت پرامپت» استفاده کن.\n\n"
        "👇 یکی از گزینه‌های پایین را انتخاب کن."
    )

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(user.id)
    )


# =========================
# IMAGE FLOW
# =========================

async def ask_image(update):
    update.message and await update.message.reply_text(
        "🎨 توضیح تصویرت را به انگلیسی بفرست.\n\n"
        "💡 مثال:\n"
        "A black sports car driving on a rainy city street at night, "
        "neon lights, cinematic, realistic"
    )


async def handle_image_prompt(update, context):
    prompt = update.message.text.strip()

    if len(prompt) < 3:
        await update.message.reply_text(
            "⚠️ لطفاً توضیح کامل‌تری برای تصویر بفرست."
        )
        return

    if len(prompt) > 4000:
        await update.message.reply_text(
            "⚠️ متن خیلی طولانی است. لطفاً کوتاه‌ترش کن."
        )
        return

    status = await update.message.reply_text(
        "🎨 در حال ساخت تصویر...\n⏳ چند لحظه صبر کن."
    )

    try:
        image = await create_image(prompt)

        log_request(
            update.effective_user,
            prompt,
            "text_to_image"
        )

        context.user_data["last_prompt"] = prompt

        await status.delete()

        await update.message.reply_photo(
            photo=image,
            caption=(
                "✅ تصویر با موفقیت ساخته شد.\n\n"
                "🔄 برای ساخت تصویر جدید، "
                "گزینه پایین را بزن."
            ),
            reply_markup=main_keyboard(
                update.effective_user.id
            )
        )

    except Exception as e:
        await status.edit_text(
            f"❌ خطا در ساخت تصویر:\n\n{str(e)[:1500]}"
        )


# =========================
# PROMPT FLOW
# =========================

async def ask_prompt_generation(update):
    await update.message.reply_text(
        "✨ توضیح تصویری که می‌خواهی را بفرست.\n\n"
        "🇮🇷 فارسی کاملاً پشتیبانی می‌شود.\n"
        "🇬🇧 انگلیسی هم می‌توانی بفرستی.\n\n"
        "مثال:\n"
        "یک ماشین اسپرت مشکی در خیابان بارانی شب، "
        "با نورهای نئونی و فضای سینمایی"
    )


async def handle_prompt_generation(update, context):
    description = update.message.text.strip()

    if len(description) < 3:
        await update.message.reply_text(
            "⚠️ لطفاً توضیح کامل‌تری بفرست."
        )
        return

    if len(description) > 4000:
        await update.message.reply_text(
            "⚠️ متن خیلی طولانی است. لطفاً کوتاه‌ترش کن."
        )
        return

    status = await update.message.reply_text(
        "✨ در حال ساخت پرامپت حرفه‌ای...\n⏳"
    )

    try:
        generated = await generate_prompt(description)

        log_request(
            update.effective_user,
            description,
            "prompt_generator"
        )

        context.user_data["generated_prompt"] = generated
        context.user_data["last_prompt"] = generated

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🎨 ساخت تصویر با این پرامپت",
                    callback_data="use_generated_prompt"
                )
            ],
            [
                InlineKeyboardButton(
                    "✨ ساخت پرامپت جدید",
                    callback_data="new_prompt"
                )
            ]
        ])

        await status.edit_text(
            "✨ پرامپت آماده شد:\n\n"
            f"{generated}",
            reply_markup=keyboard
        )

    except Exception as e:
        await status.edit_text(
            f"❌ خطا در ساخت پرامپت:\n\n{str(e)[:1500]}"
        )


# =========================
# CALLBACKS
# =========================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user

    if query.data == "check_join":
        if await is_joined(update.get_bot(), user.id):
            await query.edit_message_text(
                "✅ عضویت تأیید شد!\n\n"
                "حالا می‌توانی از ByteImage استفاده کنی.",
            )

            await context.bot.send_message(
                chat_id=user.id,
                text="👇 منوی اصلی:",
                reply_markup=main_keyboard(user.id)
            )
        else:
            await query.answer(
                "❌ هنوز عضویت شما تأیید نشده است.",
                show_alert=True
            )

        return

    if query.data == "use_generated_prompt":
        prompt = context.user_data.get("generated_prompt")

        if not prompt:
            await query.edit_message_text(
                "⚠️ پرامپت قبلی پیدا نشد. دوباره پرامپت بساز."
            )
            return

        await query.edit_message_text(
            "🎨 در حال ساخت تصویر...\n⏳"
        )

        try:
            image = await create_image(prompt)

            log_request(
                user,
                prompt,
                "generated_prompt_to_image"
            )

            context.user_data["last_prompt"] = prompt

            await context.bot.send_photo(
                chat_id=user.id,
                photo=image,
                caption="✅ تصویر با پرامپت ساخته‌شده آماده شد.",
                reply_markup=main_keyboard(user.id)
            )

        except Exception as e:
            await context.bot.send_message(
                chat_id=user.id,
                text=f"❌ خطا در ساخت تصویر:\n\n{str(e)[:1500]}",
                reply_markup=main_keyboard(user.id)
            )

        return

    if query.data == "new_prompt":
        await query.edit_message_text(
            "✨ توضیح تصویر جدیدت را بفرست.\n\n"
            "🇮🇷 فارسی یا 🇬🇧 انگلیسی، هر دو قابل استفاده هستند."
        )

        context.user_data["mode"] = "prompt_generator"
        return


# =========================
# ADMIN
# =========================

async def admin_only(update):
    return update.effective_user.id == ADMIN_ID


async def admin_panel(update):
    if not await admin_only(update):
        return

    await update.message.reply_text(
        "👑 پنل مدیریت ByteImage\n\n"
        "یکی از گزینه‌ها را انتخاب کن:",
        reply_markup=admin_keyboard()
    )


async def show_users(update):
    conn = db()

    total = conn.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    active = conn.execute("""
        SELECT COUNT(*)
        FROM users
        WHERE last_seen >= datetime('now', '-1 day')
    """).fetchone()[0]

    blocked = conn.execute("""
        SELECT COUNT(*)
        FROM users
        WHERE blocked = 1
    """).fetchone()[0]

    conn.close()

    await update.message.reply_text(
        "👥 کاربران\n\n"
        f"👤 کل کاربران: {total}\n"
        f"🟢 فعال در ۲۴ ساعت اخیر: {active}\n"
        f"🚫 مسدودشده: {blocked}"
    )


async def show_latest_requests(update):
    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM requests
        ORDER BY id DESC
        LIMIT 15
    """).fetchall()

    conn.close()

    if not rows:
        await update.message.reply_text(
            "📜 هنوز درخواستی ثبت نشده."
        )
        return

    for row in rows:
        username = (
            f"@{row['username']}"
            if row["username"]
            else "بدون یوزرنیم"
        )

        text = (
            "📜 درخواست\n\n"
            f"👤 {row['first_name'] or 'بدون نام'}\n"
            f"🔹 {username}\n"
            f"🆔 {row['user_id']}\n"
            f"🕐 {row['created_at']}\n\n"
            f"📝 درخواست:\n{row['prompt']}"
        )

        await update.message.reply_text(text[:4000])


async def show_stats(update):
    conn = db()

    users = conn.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    requests = conn.execute(
        "SELECT COUNT(*) FROM requests"
    ).fetchone()[0]

    images = conn.execute("""
        SELECT COUNT(*)
        FROM requests
        WHERE request_type IN (
            'text_to_image',
            'generated_prompt_to_image'
        )
    """).fetchone()[0]

    prompts = conn.execute("""
        SELECT COUNT(*)
        FROM requests
        WHERE request_type = 'prompt_generator'
    """).fetchone()[0]

    conn.close()

    await update.message.reply_text(
        "📊 آمار کلی\n\n"
        f"👥 کاربران: {users}\n"
        f"📨 کل درخواست‌ها: {requests}\n"
        f"🎨 تصاویر ساخته‌شده: {images}\n"
        f"✨ پرامپت‌های ساخته‌شده: {prompts}"
    )


async def show_today_usage(update):
    conn = db()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    total = conn.execute("""
        SELECT COUNT(*)
        FROM requests
        WHERE substr(created_at, 1, 10) = ?
    """, (today,)).fetchone()[0]

    images = conn.execute("""
        SELECT COUNT(*)
        FROM requests
        WHERE substr(created_at, 1, 10) = ?
        AND request_type IN (
            'text_to_image',
            'generated_prompt_to_image'
        )
    """, (today,)).fetchone()[0]

    prompts = conn.execute("""
        SELECT COUNT(*)
        FROM requests
        WHERE substr(created_at, 1, 10) = ?
        AND request_type = 'prompt_generator'
    """, (today,)).fetchone()[0]

    conn.close()

    await update.message.reply_text(
        "📈 مصرف امروز\n\n"
        f"📨 کل درخواست‌ها: {total}\n"
        f"🎨 ساخت تصویر: {images}\n"
        f"✨ ساخت پرامپت: {prompts}"
    )


async def ask_user_search(update, context):
    context.user_data["mode"] = "admin_search"

    await update.message.reply_text(
        "🔎 آیدی عددی یا یوزرنیم کاربر را بفرست."
    )


async def search_user(update, context):
    value = update.message.text.strip()

    conn = db()

    if value.startswith("@"):
        value = value[1:]

    if value.isdigit():
        rows = conn.execute("""
            SELECT *
            FROM users
            WHERE user_id = ?
        """, (int(value),)).fetchall()
    else:
        rows = conn.execute("""
            SELECT *
            FROM users
            WHERE username LIKE ?
        """, (value,)).fetchall()

    conn.close()

    if not rows:
        await update.message.reply_text(
            "❌ کاربری پیدا نشد."
        )
        return

    for row in rows:
        await update.message.reply_text(
            "👤 اطلاعات کاربر\n\n"
            f"🆔 {row['user_id']}\n"
            f"👤 {row['first_name'] or 'بدون نام'}\n"
            f"🔹 @{row['username'] or 'ندارد'}\n"
            f"📅 ورود: {row['created_at']}\n"
            f"🕐 آخرین فعالیت: {row['last_seen']}\n"
            f"🚫 مسدود: {'بله' if row['blocked'] else 'خیر'}"
        )


async def user_management(update):
    await update.message.reply_text(
        "🚫 مدیریت کاربران\n\n"
        "فعلاً بخش مشاهده وضعیت کاربران فعال است.\n"
        "مدیریت مسدودسازی در نسخه بعدی اضافه می‌شود."
    )


# =========================
# API MANAGEMENT
# =========================

async def api_panel(update):
    await update.message.reply_text(
        "⚙️ مدیریت API\n\n"
        "BOT_TOKEN فقط از Railway Variables مدیریت می‌شود.\n"
        "از این بخش فقط تنظیمات Cloudflare تغییر می‌کند.",
        reply_markup=api_keyboard()
    )


async def ask_account_id(update, context):
    context.user_data["mode"] = "set_account_id"

    await update.message.reply_text(
        "🔑 Account ID جدید Cloudflare را بفرست."
    )


async def ask_api_token(update, context):
    context.user_data["mode"] = "set_api_token"

    await update.message.reply_text(
        "🔐 API Token جدید Cloudflare را بفرست.\n\n"
        "⚠️ توکن فقط ذخیره می‌شود و در پیام عمومی نمایش داده نمی‌شود."
    )


async def show_api_status(update):
    account_id, token = cf_credentials()

    masked = "تنظیم نشده"

    if token:
        if len(token) > 8:
            masked = token[:4] + "••••••••" + token[-4:]
        else:
            masked = "••••••••"

    await update.message.reply_text(
        "⚙️ وضعیت API\n\n"
        f"🆔 Account ID: "
        f"{'تنظیم شده' if account_id else 'تنظیم نشده'}\n"
        f"🔐 API Token: {masked}\n\n"
        f"🖼 Image Model:\n{IMAGE_MODEL}\n\n"
        f"✨ Prompt Model:\n{PROMPT_MODEL}"
    )


# =========================
# MESSAGE ROUTER
# =========================

async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    save_user(user)

    if not await ensure_joined(update):
        return

    mode = context.user_data.get("mode")

    # ADMIN MODES
    if user.id == ADMIN_ID:
        if mode == "admin_search":
            context.user_data.pop("mode", None)
            await search_user(update, context)
            return

        if mode == "set_account_id":
            value = text.strip()

            if len(value) < 10:
                await update.message.reply_text(
                    "❌ Account ID نامعتبر است."
                )
                return

            set_setting("cf_account_id", value)

            context.user_data.pop("mode", None)

            await update.message.reply_text(
                "✅ Account ID با موفقیت تغییر کرد.",
                reply_markup=api_keyboard()
            )
            return

        if mode == "set_api_token":
            value = text.strip()

            if len(value) < 10:
                await update.message.reply_text(
                    "❌ API Token نامعتبر است."
                )
                return

            set_setting("cf_api_token", value)

            context.user_data.pop("mode", None)

            await update.message.reply_text(
                "✅ API Token با موفقیت تغییر کرد.",
                reply_markup=api_keyboard()
            )
            return

    # NORMAL MODES

    if mode == "image":
        context.user_data.pop("mode", None)
        await handle_image_prompt(update, context)
        return

    if mode == "prompt_generator":
        context.user_data.pop("mode", None)
        await handle_prompt_generation(update, context)
        return

    # MAIN BUTTONS

    if text == "🎨 ساخت تصویر":
        context.user_data["mode"] = "image"
        await ask_image(update)
        return

    if text == "✨ ساخت پرامپت":
        context.user_data["mode"] = "prompt_generator"
        await ask_prompt_generation(update)
        return

    if text == "🔄 ساخت تصویر جدید":
        last_prompt = context.user_data.get("last_prompt")

        if last_prompt:
            status = await update.message.reply_text(
                "🔄 در حال ساخت تصویر جدید..."
            )

            try:
                image = await create_image(last_prompt)

                log_request(
                    user,
                    last_prompt,
                    "generated_prompt_to_image"
                )

                await status.delete()

                await update.message.reply_photo(
                    photo=image,
                    caption="✅ تصویر جدید ساخته شد.",
                    reply_markup=main_keyboard(user.id)
                )

            except Exception as e:
                await status.edit_text(
                    f"❌ خطا:\n\n{str(e)[:1500]}"
                )
        else:
            context.user_data["mode"] = "image"
            await ask_image(update)

        return

    # ADMIN BUTTONS

    if user.id == ADMIN_ID:

        if text == "👑 پنل مدیریت":
            await admin_panel(update)
            return

        if text == "👥 کاربران":
            await show_users(update)
            return

        if text == "📜 آخرین درخواست‌ها":
            await show_latest_requests(update)
            return

        if text == "🔎 جستجوی کاربر":
            await ask_user_search(update, context)
            return

        if text == "📊 آمار کلی":
            await show_stats(update)
            return

        if text == "📈 مصرف امروز":
            await show_today_usage(update)
            return

        if text == "🚫 مدیریت کاربران":
            await user_management(update)
            return

        if text == "⚙️ مدیریت API":
            await api_panel(update)
            return

        if text == "🔑 تغییر Account ID":
            await ask_account_id(update, context)
            return

        if text == "🔐 تغییر API Token":
            await ask_api_token(update, context)
            return

        if text == "👁 وضعیت API":
            await show_api_status(update)
            return

        if text == "🔙 بازگشت":
            context.user_data.clear()

            await update.message.reply_text(
                "👇 منوی اصلی:",
                reply_markup=main_keyboard(user.id)
            )
            return

    await update.message.reply_text(
        "👇 از منوی پایین یکی از گزینه‌ها را انتخاب کن.",
        reply_markup=main_keyboard(user.id)
    )


# =========================
# ERROR
# =========================

async def error_handler(update, context):
    print(
        "ERROR:",
        repr(context.error)
    )


# =========================
# MAIN
# =========================

def main():
    init_db()

    async def post_init(application):
        application.create_task(
            bot_clock_task(application.bot)
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CallbackQueryHandler(callbacks)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_router
        )
    )

    app.add_error_handler(error_handler)

    print("ByteImageBot is running...")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
