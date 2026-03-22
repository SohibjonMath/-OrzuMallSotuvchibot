import os
import json
import random
import logging
from pathlib import Path
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from functools import wraps

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# ENV / CONFIG
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SOURCE_CHAT_ID_RAW = os.getenv("SOURCE_CHAT_ID", "").strip()   # @kanal yoki -100...
TARGET_CHAT_ID_RAW = os.getenv("TARGET_CHAT_ID", "").strip()   # -100...
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()             # 12345,67890
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
if SEND_MODE not in {"forward", "copy"}:
    raise RuntimeError("SEND_MODE faqat 'forward' yoki 'copy' bo‘lishi kerak")

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
    level=logging.INFO,
)
logger = logging.getLogger("album-repost-bot")


# =========================
# STORAGE
# =========================
class Storage:
    """
    items:
      [
        {"id":"single_123","type":"single","message_ids":[123],"created_at":"..."},
        {"id":"album_999888","type":"album","message_ids":[120,121,122],"created_at":"..."}
      ]

    sent_history:
      [
        {"item_id":"album_999888","sent_at":"..."}
      ]
    """

    def __init__(self, path: str):
        self.path = Path(path)
        self.data = {
            "items": [],
            "sent_history": [],
            "last_sent_item_id": None,
        }
        self.load()

    def load(self):
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
                self.data.setdefault("items", [])
                self.data.setdefault("sent_history", [])
                self.data.setdefault("last_sent_item_id", None)
            except Exception as e:
                logger.error("Storage load xato: %s", e)
                self.save()
        else:
            self.save()

    def save(self):
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_item_by_id(self, item_id: str):
        for item in self.data["items"]:
            if item["id"] == item_id:
                return item
        return None

    def get_item_by_message_id(self, message_id: int):
        for item in self.data["items"]:
            if message_id in item.get("message_ids", []):
                return item
        return None

    def remove_item_by_id(self, item_id: str) -> bool:
        before = len(self.data["items"])
        self.data["items"] = [x for x in self.data["items"] if x["id"] != item_id]
        changed = len(self.data["items"]) != before
        if changed:
            self.save()
        return changed

    def add_single(self, message_id: int) -> bool:
        existing = self.get_item_by_message_id(message_id)
        if existing:
            return False

        self.data["items"].append(
            {
                "id": f"single_{message_id}",
                "type": "single",
                "message_ids": [message_id],
                "created_at": datetime.now(TZ).isoformat(),
            }
        )
        self.save()
        return True

    def upsert_album_message(self, media_group_id: str, message_id: int) -> bool:
        item_id = f"album_{media_group_id}"
        item = self.get_item_by_id(item_id)
        changed = False

        # Shu message ilgari single bo‘lib yozilgan bo‘lsa, o‘chirib yuboramiz.
        single_id = f"single_{message_id}"
        if self.get_item_by_id(single_id):
            self.remove_item_by_id(single_id)
            changed = True
            logger.info("Single o‘chirildi, chunki albumga tegishli: %s", single_id)

        if not item:
            item = {
                "id": item_id,
                "type": "album",
                "message_ids": [message_id],
                "created_at": datetime.now(TZ).isoformat(),
            }
            self.data["items"].append(item)
            self.save()
            return True

        if message_id not in item["message_ids"]:
            item["message_ids"].append(message_id)
            item["message_ids"] = sorted(set(item["message_ids"]))
            changed = True

        if changed:
            self.save()
        return changed

    def cleanup_single_duplicates(self) -> dict:
        album_message_ids = set()
        for item in self.data["items"]:
            if item.get("type") == "album":
                album_message_ids.update(item.get("message_ids", []))

        before = len(self.data["items"])
        removed = []
        kept = []

        for item in self.data["items"]:
            if item.get("type") == "single" and item.get("message_ids"):
                msg_id = item["message_ids"][0]
                if msg_id in album_message_ids:
                    removed.append(item["id"])
                    continue
            kept.append(item)

        self.data["items"] = kept
        if removed:
            self.save()

        return {
            "removed_count": len(removed),
            "items_before": before,
            "items_after": len(self.data["items"]),
            "removed_ids": removed[:20],
        }

    def all_items(self):
        return list(self.data["items"])

    def mark_sent(self, item_id: str):
        self.data["last_sent_item_id"] = item_id
        self.data["sent_history"].append(
            {
                "item_id": item_id,
                "sent_at": datetime.now(TZ).isoformat(),
            }
        )
        if len(self.data["sent_history"]) > 5000:
            self.data["sent_history"] = self.data["sent_history"][-5000:]
        self.save()

    def recently_sent_item_ids(self, hours: int):
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
                    result.append(item["item_id"])
            except Exception:
                continue

        self.data["sent_history"] = cleaned
        self.save()
        return result

    def stats(self):
        single_count = 0
        album_count = 0
        media_count = 0

        for item in self.data["items"]:
            if item["type"] == "single":
                single_count += 1
                media_count += 1
            elif item["type"] == "album":
                album_count += 1
                media_count += len(item["message_ids"])

        return {
            "items_total": len(self.data["items"]),
            "single_count": single_count,
            "album_count": album_count,
            "media_count": media_count,
            "sent_total": len(self.data["sent_history"]),
            "last_sent_item_id": self.data["last_sent_item_id"],
        }


storage = Storage(DATA_FILE)


# =========================
# HELPERS
# =========================
def is_admin(user_id: int) -> bool:
    return bool(ADMIN_IDS) and user_id in ADMIN_IDS


def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or not is_admin(user.id):
            if update.message:
                await update.message.reply_text("Bu buyruq faqat admin uchun.")
            return
        return await func(update, context)

    return wrapper



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



def is_source_chat(chat) -> bool:
    if isinstance(SOURCE_CHAT_ID, int):
        return chat.id == SOURCE_CHAT_ID
    username = f"@{chat.username}" if chat.username else None
    return username == SOURCE_CHAT_ID



def pick_random_item():
    items = storage.all_items()
    if not items:
        return None

    recent_ids = set(storage.recently_sent_item_ids(NO_REPEAT_HOURS))
    available = [item for item in items if item["id"] not in recent_ids]

    if not available:
        available = items[:]

    return random.choice(available) if available else None


# =========================
# SEND LOGIC
# =========================
async def send_item(context: ContextTypes.DEFAULT_TYPE, item: dict) -> bool:
    bot = context.bot
    item_type = item["type"]
    message_ids = sorted(set(item["message_ids"]))
    item_id = item["id"]

    try:
        if item_type == "album":
            if len(message_ids) < 2:
                logger.warning("Album item ichida 2 tadan kam message bor: %s", item_id)

            if SEND_MODE == "copy":
                await bot.copy_messages(
                    chat_id=TARGET_CHAT_ID,
                    from_chat_id=SOURCE_CHAT_ID,
                    message_ids=message_ids,
                )
            else:
                await bot.forward_messages(
                    chat_id=TARGET_CHAT_ID,
                    from_chat_id=SOURCE_CHAT_ID,
                    message_ids=message_ids,
                )
        else:
            message_id = message_ids[0]
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

        storage.mark_sent(item_id)
        logger.info("Yuborildi: %s | type=%s | ids=%s", item_id, item_type, message_ids)
        return True
    except Exception as e:
        logger.warning("Yuborishda xato: %s | %s", item_id, e)
        return False


# =========================
# COMMANDS
# =========================
@admin_only
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ FINAL PRO Album Repost Bot ishlayapti.\n\n"
        "Buyruqlar:\n"
        "/stats - statistika\n"
        "/postnow - hozir random repost\n"
        "/importsingles 1 500 - eski oddiy post ID larni qo‘shish\n"
        "/cleanupdupes - album ichiga tushib qolgan single larni tozalash\n"
        "/helpadmin - yordam\n\n"
        "Muhim:\n"
        "• Yangi albumlar 1 ta post sifatida saqlanadi\n"
        "• Album ichidagi rasmlar alohida single bo‘lib qolmaydi\n"
        "• Eski /importsingles noto‘g‘ri kiritilgan albumlarni Telegram API bilan orqaga qarab aniq tiklab bo‘lmaydi\n"
        f"• Rejim: {SEND_MODE}\n"
        f"• Vaqtlar: {POST_TIMES_RAW}\n"
        f"• Takrorlamaslik: {NO_REPEAT_HOURS} soat"
    )


@admin_only
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = storage.stats()
    await update.message.reply_text(
        "📊 Statistika\n\n"
        f"Jami item: {st['items_total']}\n"
        f"Oddiy postlar: {st['single_count']}\n"
        f"Albumlar: {st['album_count']}\n"
        f"Jami media birlik: {st['media_count']}\n"
        f"Jami yuborilgan: {st['sent_total']}\n"
        f"So‘nggi yuborilgan item: {st['last_sent_item_id']}\n"
        f"Rejim: {SEND_MODE}\n"
        f"Vaqtlar: {POST_TIMES_RAW}"
    )


@admin_only
async def postnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    item = pick_random_item()
    if not item:
        await update.message.reply_text(
            "Hali bazada post yo‘q.\n"
            "Botni source kanalga admin qiling va yangi postlarni yig‘diring."
        )
        return

    ok = await send_item(context, item)
    if ok:
        await update.message.reply_text(
            f"✅ Yuborildi\n"
            f"Type: {item['type']}\n"
            f"IDs: {sorted(set(item['message_ids']))}"
        )
    else:
        await update.message.reply_text("❌ Yuborib bo‘lmadi.")


@admin_only
async def importsingles_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Faqat eski oddiy postlar uchun.
    Album strukturasini tiklamaydi.
    """
    if len(context.args) != 2:
        await update.message.reply_text("Foydalanish: /importsingles 1 500")
        return

    try:
        start_id = int(context.args[0])
        end_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("ID lar son bo‘lishi kerak.")
        return

    if start_id <= 0 or end_id < start_id:
        await update.message.reply_text("Oraliq noto‘g‘ri.")
        return

    added = 0
    skipped = 0
    for msg_id in range(start_id, end_id + 1):
        if storage.add_single(msg_id):
            added += 1
        else:
            skipped += 1

    await update.message.reply_text(
        f"✅ importsingles tugadi\n"
        f"Qo‘shildi: {added}\n"
        f"O‘tkazib yuborildi: {skipped}\n\n"
        "Eslatma: bu faqat oddiy postlar uchun yaxshi.\n"
        "Eski albumlarni to‘liq tiklamaydi."
    )


@admin_only
async def cleanupdupes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = storage.cleanup_single_duplicates()
    await update.message.reply_text(
        "🧹 Tozalash tugadi\n\n"
        f"Oldin: {result['items_before']}\n"
        f"Keyin: {result['items_after']}\n"
        f"O‘chirilgan duplicate single: {result['removed_count']}\n"
        f"Namuna: {result['removed_ids']}"
    )


@admin_only
async def help_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛠 Admin yordam\n\n"
        "/start\n"
        "/stats\n"
        "/postnow\n"
        "/importsingles 1 500\n"
        "/cleanupdupes\n"
        "/helpadmin\n\n"
        "Qanday ishlaydi:\n"
        "1) Botni source kanalga admin qilasiz\n"
        "2) Yangi oddiy post bo‘lsa single sifatida saqlaydi\n"
        "3) Yangi album bo‘lsa media_group_id bilan 1 ta post qilib saqlaydi\n"
        "4) Album ichidagi har bir rasm alohida post bo‘lib ketmaydi\n"
        "5) Repost payti single yoki album bo‘lishiga qarab to‘g‘ri yuboradi\n\n"
        "Muhim:\n"
        "• /cleanupdupes album ichiga tushib qolgan duplicate single larni tozalaydi\n"
        "• /importsingles eski oddiy postlar uchun\n"
        "• Oldin noto‘g‘ri import qilingan eski albumlarni avtomatik 100% tiklab bo‘lmaydi"
    )


# =========================
# CAPTURE CHANNEL POSTS
# =========================
async def capture_channel_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.chat:
        return

    if not is_source_chat(msg.chat):
        return

    if msg.media_group_id:
        changed = storage.upsert_album_message(str(msg.media_group_id), msg.message_id)
        if changed:
            logger.info(
                "Album saqlandi/yangilandi | media_group_id=%s | message_id=%s",
                msg.media_group_id,
                msg.message_id,
            )
        return

    changed = storage.add_single(msg.message_id)
    if changed:
        logger.info("Single post saqlandi | message_id=%s", msg.message_id)


# =========================
# SCHEDULER
# =========================
async def scheduled_post(context: ContextTypes.DEFAULT_TYPE):
    item = pick_random_item()
    if not item:
        logger.warning("Bazadagi itemlar bo‘sh. Repost o‘tkazib yuborildi.")
        return

    ok = await send_item(context, item)
    if ok:
        return

    items = storage.all_items()
    random.shuffle(items)
    for alt in items[:20]:
        if alt["id"] == item["id"]:
            continue
        if await send_item(context, alt):
            return

    logger.warning("Fallback itemlar ham yuborilmadi.")


# =========================
# MAIN
# =========================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("postnow", postnow_cmd))
    app.add_handler(CommandHandler("importsingles", importsingles_cmd))
    app.add_handler(CommandHandler("cleanupdupes", cleanupdupes_cmd))
    app.add_handler(CommandHandler("helpadmin", help_admin_cmd))

    app.add_handler(
        MessageHandler(
            filters.ALL & filters.ChatType.CHANNEL,
            capture_channel_posts,
        )
    )

    for t in POST_TIMES:
        app.job_queue.run_daily(
            scheduled_post,
            time=t,
            name=f"post_{t.hour:02d}_{t.minute:02d}",
        )
        logger.info("Jadval qo‘shildi: %02d:%02d", t.hour, t.minute)

    logger.info("Bot ishga tushdi...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
