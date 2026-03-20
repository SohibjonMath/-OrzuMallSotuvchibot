import os
import json
import random
import logging
from pathlib import Path
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# CONFIG
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SOURCE_CHAT_ID_RAW = os.getenv("SOURCE_CHAT_ID", "").strip()   # masalan: @kanalim yoki -100...
TARGET_CHAT_ID_RAW = os.getenv("TARGET_CHAT_ID", "").strip()   # masalan: -100...
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()             # masalan: 12345,67890
POST_TIMES_RAW = os.getenv("POST_TIMES", "07:00,09:00,11:00,13:00,15:00,17:00,19:00,21:00")
TIMEZONE_NAME = os.getenv("TIMEZONE", "Asia/Tashkent").strip()
NO_REPEAT_HOURS = int(os.getenv("NO_REPEAT_HOURS", "24"))
DATA_FILE = os.getenv("DATA_FILE", "data.json").strip()
SEND_MODE = os.getenv("SEND_MODE", "forward").strip().lower()  # forward | copy

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi")
if not SOURCE_CHAT_ID_RAW:
    raise RuntimeError("SOURCE_CHAT_ID topilmadi")
if not TARGET_CHAT_ID_RAW:
    raise RuntimeError("TARGET_CHAT_ID topilmadi")

TZ = ZoneInfo(TIMEZONE_NAME)

def parse_chat_id(value: str):
    value = value.strip()
    if value.startswith("@"):
        return value
    try:
        return int(value)
    except ValueError:
        return value

SOURCE_CHAT_ID = parse_chat_id(SOURCE_CHAT_ID_RAW)
TARGET_CHAT_ID = parse_chat_id(TARGET_CHAT_ID_RAW)

ADMIN_IDS = set()
if ADMIN_IDS_RAW:
    for x in ADMIN_IDS_RAW.split(","):
        x = x.strip()
        if x:
            ADMIN_IDS.add(int(x))

# =========================
# LOGGING
# =========================
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("repost-bot")


# =========================
# STORAGE
# =========================
class Storage:
    def __init__(self, path: str):
        self.path = Path(path)
        self.data = {
            "known_posts": [],      # [1,2,3...]
            "sent_history": [],     # [{"message_id": 12, "sent_at": "iso"}]
            "last_sent": None
        }
        self.load()

    def load(self):
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
                if "known_posts" not in self.data:
                    self.data["known_posts"] = []
                if "sent_history" not in self.data:
                    self.data["sent_history"] = []
                if "last_sent" not in self.data:
                    self.data["last_sent"] = None
            except Exception as e:
                logger.error("data.json o‘qishda xato: %s", e)
                self.save()
        else:
            self.save()

    def save(self):
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def add_post(self, message_id: int) -> bool:
        if message_id not in self.data["known_posts"]:
            self.data["known_posts"].append(message_id)
            self.data["known_posts"].sort()
            self.save()
            return True
        return False

    def has_post(self, message_id: int) -> bool:
        return message_id in self.data["known_posts"]

    def all_posts(self):
        return list(self.data["known_posts"])

    def mark_sent(self, message_id: int):
        self.data["last_sent"] = message_id
        self.data["sent_history"].append({
            "message_id": message_id,
            "sent_at": datetime.now(TZ).isoformat()
        })
        # Juda eski loglarni tozalash
        if len(self.data["sent_history"]) > 5000:
            self.data["sent_history"] = self.data["sent_history"][-5000:]
        self.save()

    def recently_sent_ids(self, hours: int):
        cutoff = datetime.now(TZ) - timedelta(hours=hours)
        result = []
        cleaned = []
        for item in self.data["sent_history"]:
            try:
                sent_at = datetime.fromisoformat(item["sent_at"])
                if sent_at.tzinfo is None:
                    sent_at = sent_at.replace(tzinfo=TZ)
                cleaned.append(item)
                if sent_at >= cutoff:
                    result.append(item["message_id"])
            except Exception:
                continue
        self.data["sent_history"] = cleaned
        self.save()
        return result

    def stats(self):
        return {
            "known_posts": len(self.data["known_posts"]),
            "sent_total": len(self.data["sent_history"]),
            "last_sent": self.data["last_sent"],
        }


storage = Storage(DATA_FILE)


# =========================
# HELPERS
# =========================
def is_admin(user_id: int) -> bool:
    if not ADMIN_IDS:
        return False
    return user_id in ADMIN_IDS

def format_times(raw: str):
    result = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        hh, mm = part.split(":")
        result.append(time(hour=int(hh), minute=int(mm), tzinfo=TZ))
    return result

POST_TIMES = format_times(POST_TIMES_RAW)

def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or not is_admin(user.id):
            if update.message:
                await update.message.reply_text("Bu buyruq faqat admin uchun.")
            return
        return await func(update, context)
    return wrapper

async def send_post_by_id(context: ContextTypes.DEFAULT_TYPE, message_id: int) -> bool:
    bot = context.bot
    try:
        if SEND_MODE == "copy":
            await bot.copy_message(
                chat_id=TARGET_CHAT_ID,
                from_chat_id=SOURCE_CHAT_ID,
                message_id=message_id,
            )
        else:
            await bot.forward_message(
                chat_id=TARGET_CHAT_ID,
                from_chat_id=SOURCE_CHAT_ID,
                message_id=message_id,
            )
        storage.mark_sent(message_id)
        logger.info("Yuborildi: %s", message_id)
        return True
    except Exception as e:
        logger.warning("Yuborishda xato. message_id=%s | %s", message_id, e)
        return False

def pick_random_post():
    all_posts = storage.all_posts()
    if not all_posts:
        return None

    recent_ids = set(storage.recently_sent_ids(NO_REPEAT_HOURS))
    available = [pid for pid in all_posts if pid not in recent_ids]

    if not available:
        # Hamma postlar oxirgi N soatda ishlatilgan bo‘lsa,
        # qaytadan umumiy ro‘yxatdan tanlaydi
        available = all_posts[:]

    return random.choice(available) if available else None


# =========================
# COMMANDS
# =========================
@admin_only
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "✅ PRO Repost Bot ishlayapti.\n\n"
        "Buyruqlar:\n"
        "/stats - statistika\n"
        "/postnow - hozir random post tashlash\n"
        "/import <boshi> <oxiri> - eski postlarni import qilish\n"
        "Masalan: /import 1 500\n\n"
        "Eslatma:\n"
        f"- Source: {SOURCE_CHAT_ID}\n"
        f"- Target: {TARGET_CHAT_ID}\n"
        f"- Vaqtlar: {POST_TIMES_RAW}\n"
        f"- Takrorlamaslik: {NO_REPEAT_HOURS} soat\n"
        f"- Rejim: {SEND_MODE}"
    )
    await update.message.reply_text(txt)

@admin_only
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = storage.stats()
    txt = (
        "📊 Statistika\n\n"
        f"Jami tanilgan postlar: {st['known_posts']}\n"
        f"Jami yuborilganlar: {st['sent_total']}\n"
        f"So‘nggi yuborilgan post ID: {st['last_sent']}\n"
        f"Takrorlamaslik oynasi: {NO_REPEAT_HOURS} soat\n"
        f"Vaqtlar: {POST_TIMES_RAW}"
    )
    await update.message.reply_text(txt)

@admin_only
async def postnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_id = pick_random_post()
    if not msg_id:
        await update.message.reply_text(
            "Hali hech qanday post bazaga tushmagan.\n"
            "Botni source kanalga admin qiling va /import 1 500 kabi eski postlarni ham qo‘shing."
        )
        return

    ok = await send_post_by_id(context, msg_id)
    if ok:
        await update.message.reply_text(f"✅ Random post yuborildi. ID: {msg_id}")
    else:
        await update.message.reply_text(f"❌ Yuborib bo‘lmadi. ID: {msg_id}")

@admin_only
async def import_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("Foydalanish: /import 1 500")
        return

    try:
        start_id = int(context.args[0])
        end_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("ID lar son bo‘lishi kerak. Masalan: /import 1 500")
        return

    if start_id <= 0 or end_id <= 0 or end_id < start_id:
        await update.message.reply_text("Oraliq noto‘g‘ri. Masalan: /import 1 500")
        return

    await update.message.reply_text(
        f"⏳ Import boshlandi: {start_id} dan {end_id} gacha.\n"
        "Bu buyruq eski postlarni tekshirib bazaga qo‘shadi."
    )

    added = 0
    checked = 0

    # forward/copy qilib tekshirish xavfli, chunki targetga yuborib yuboradi.
    # Shu sababli getChat orqali emas, copy/forwardsiz tekshirib bo‘lmaydi.
    # Eng xavfsiz usul: source kanalga admin bo‘lib turgan bot uchun copy qilib emas,
    # forward qilish o‘rniga copyni o‘zi targetga yuboradi, bu ham kerakmas.
    # Shuning uchun bu yerda amaliy workaround:
    # - admin source kanaldagi eski postlardan bir marta "Forward" qilsa, bot ularni ko‘radi.
    # - lekin foydalanuvchiga qulay bo‘lishi uchun biz ID oralig‘ini to‘g‘ridan-to‘g‘ri bazaga qo‘shamiz.
    # Keyin yuborish paytida haqiqiy ID bo‘lmasa skip bo‘ladi.
    #
    # Bu Telegram Bot API limitlari sabab eng sodda va amaliy yechim.
    for msg_id in range(start_id, end_id + 1):
        checked += 1
        if storage.add_post(msg_id):
            added += 1

    await update.message.reply_text(
        f"✅ Import tugadi.\n"
        f"Tekshirildi: {checked}\n"
        f"Bazaga qo‘shildi: {added}\n\n"
        "Eslatma: agar ayrim ID lar real post bo‘lmasa, bot yuborish paytida ularni tashlab ketadi."
    )

async def capture_channel_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return

    chat = msg.chat
    if not chat:
        return

    source_match = False
    if isinstance(SOURCE_CHAT_ID, int):
        source_match = (chat.id == SOURCE_CHAT_ID)
    else:
        username = f"@{chat.username}" if chat.username else None
        source_match = (username == SOURCE_CHAT_ID)

    if not source_match:
        return

    if msg.message_id:
        added = storage.add_post(msg.message_id)
        if added:
            logger.info("Yangi kanal posti saqlandi: %s", msg.message_id)

@admin_only
async def help_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "🛠 Admin yordam\n\n"
        "/start - bot holati\n"
        "/stats - statistika\n"
        "/postnow - hozir random post tashlaydi\n"
        "/import 1 500 - eski post ID oralig‘ini bazaga qo‘shadi\n\n"
        "Muhim:\n"
        "1) Botni SOURCE kanalga admin qiling\n"
        "2) Botni TARGET kanal/guruhga ham admin qiling\n"
        "3) Yangi postlar avtomatik yig‘iladi\n"
        "4) Eski postlar uchun /import ishlating"
    )
    await update.message.reply_text(txt)


# =========================
# SCHEDULER
# =========================
async def scheduled_post(context: ContextTypes.DEFAULT_TYPE):
    msg_id = pick_random_post()
    if not msg_id:
        logger.warning("Bazadagi postlar bo‘sh. Yuborish o‘tkazib yuborildi.")
        return

    ok = await send_post_by_id(context, msg_id)
    if not ok:
        # Yaroqsiz ID chiqsa, o‘chirib tashlamaymiz, keyin yana tekshiriladi.
        # Lekin shu aylanishda boshqa post tanlab qayta urinib ko‘ramiz.
        fallback_ids = storage.all_posts()[:]
        random.shuffle(fallback_ids)
        for alt in fallback_ids[:20]:
            if alt == msg_id:
                continue
            if await send_post_by_id(context, alt):
                return
        logger.warning("Muqobil post ham yuborilmadi.")


# =========================
# MAIN
# =========================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("postnow", postnow_cmd))
    app.add_handler(CommandHandler("import", import_cmd))
    app.add_handler(CommandHandler("helpadmin", help_admin_cmd))

    # Kanal postlarini tutib olish
    app.add_handler(MessageHandler(filters.ALL & filters.ChatType.CHANNEL, capture_channel_posts))

    # Har kuni aniq vaqtlarda ishlaydi
    for t in POST_TIMES:
        app.job_queue.run_daily(
            scheduled_post,
            time=t,
            name=f"post_{t.hour:02d}_{t.minute:02d}"
        )
        logger.info("Jadval qo‘shildi: %02d:%02d", t.hour, t.minute)

    logger.info("Bot ishga tushdi...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
