
import json
import logging
import os
import random
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    MessageReactionHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("OrzuMallMultiBot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# ---------- FORWARD SETTINGS ----------
TARGET_CHANNEL = os.getenv("TARGET_CHANNEL", "").strip()
TZ = os.getenv("TZ", "Asia/Tashkent").strip()
POST_HOUR = int(os.getenv("POST_HOUR", "7"))
POST_MINUTE = int(os.getenv("POST_MINUTE", "30"))
SOURCE_CHAT_ID = os.getenv("SOURCE_CHAT_ID", "").strip()
SOURCE_MESSAGE_ID = int(os.getenv("SOURCE_MESSAGE_ID", "0"))

# ---------- CONTEST SETTINGS ----------
CONTEST_CHAT_ID = os.getenv("CONTEST_CHAT_ID", "").strip()
MORNING_HOUR = int(os.getenv("MORNING_HOUR", "6"))
MORNING_MINUTE = int(os.getenv("MORNING_MINUTE", "0"))
WINNER_HOUR = int(os.getenv("WINNER_HOUR", "20"))
WINNER_MINUTE = int(os.getenv("WINNER_MINUTE", "0"))

# ---------- INVITE RACE SETTINGS ----------
INVITE_CHAT_ID = os.getenv("INVITE_CHAT_ID", CONTEST_CHAT_ID).strip()
TOP_HOUR = int(os.getenv("TOP_HOUR", "21"))
TOP_MINUTE = int(os.getenv("TOP_MINUTE", "0"))

ADMIN_USER_IDS = [
    int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip()
]

DISCOUNT_PERCENT = int(os.getenv("DISCOUNT_PERCENT", "25"))
DISCOUNT_MAX_SUM = int(os.getenv("DISCOUNT_MAX_SUM", "1000000"))
DISCOUNT_HOURS = int(os.getenv("DISCOUNT_HOURS", "72"))
COOLDOWN_DAYS = int(os.getenv("WINNER_COOLDOWN_DAYS", "30"))

STATE_DIR = Path(os.getenv("STATE_DIR", "./data"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "contest_state.json"

def main_menu_keyboard(user_id: int = 0) -> InlineKeyboardMarkup:
    is_user_admin = user_id in ADMIN_USER_IDS

    if is_user_admin:
        rows = [
            [
                InlineKeyboardButton("🎁 Musobaqa posti", callback_data="postnow"),
                InlineKeyboardButton("🏆 G‘olib tanlash", callback_data="drawnow"),
            ],
            [
                InlineKeyboardButton("📊 Bugungi holat", callback_data="today"),
                InlineKeyboardButton("💰 Faol skidkalar", callback_data="discounts"),
            ],
            [
                InlineKeyboardButton("🔗 Mening referralim", callback_data="myref"),
                InlineKeyboardButton("🥇 TOP 20", callback_data="top20"),
            ],
            [
                InlineKeyboardButton("⚙️ Status", callback_data="status"),
                InlineKeyboardButton("ℹ️ Yordam", callback_data="help"),
            ],
        ]
    else:
        rows = [
            [
                InlineKeyboardButton("🔗 Mening referralim", callback_data="myref"),
                InlineKeyboardButton("🥇 TOP 20", callback_data="top20"),
            ],
            [
                InlineKeyboardButton("ℹ️ Yordam", callback_data="help"),
            ],
        ]

    return InlineKeyboardMarkup(rows)

HELP_TEXT = """🤖 <b>OrzuMall Xabarchi</b>

Kerakli bo‘limni pastdagi tugmalardan tanlang. Admin va foydalanuvchi menyusi alohida.

<b>Asosiy funksiyalar:</b>
• Kanalga avtomatik forward post
• 06:00 da contest post
• 20:00 da random g‘olib
• 25% skidka nazorati
• Referral orqali TOP 20

👇 Tugmalardan foydalaning"""

def tz_now() -> datetime:
    return datetime.now(ZoneInfo(TZ))

def format_money(n: int) -> str:
    return f"{n:,}".replace(",", " ")

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.exception("State o‘qishda xato: %s", e)
    return {
        "current_post_id": None,
        "current_post_date": None,
        "participants": [],
        "participants_meta": {},
        "discounts": {},
        "winner_history": {},
        "invite_links": {},
        "invite_joins": [],
    }

STATE = load_state()

def save_state() -> None:
    STATE_FILE.write_text(
        json.dumps(STATE, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def mention_html(user_id: int, label: str) -> str:
    safe = label.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<a href="tg://user?id={user_id}">{safe}</a>'

def display_name_for(uid: int) -> str:
    meta = STATE.get("participants_meta", {}).get(str(uid), {})
    username = meta.get("username")
    first_name = meta.get("first_name")
    if username:
        return f"@{username}"
    if first_name:
        return first_name
    return str(uid)


def label_with_you(label: str, target_uid: str | int, viewer_uid: int | None) -> str:
    try:
        if viewer_uid is not None and int(target_uid) == int(viewer_uid):
            return f"{label} (SIZ)"
    except Exception:
        pass
    return label

def user_label(user) -> str:
    if getattr(user, "username", None):
        return f"@{user.username}"
    return getattr(user, "first_name", None) or str(getattr(user, "id", ""))

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

async def send_text(update: Update, text: str, **kwargs):
    if update.callback_query:
        await update.callback_query.message.reply_text(text, **kwargs)
    elif update.message:
        await update.message.reply_text(text, **kwargs)

async def send_or_edit_menu(update: Update, text: str):
    user = update.effective_user
    markup = main_menu_keyboard(user.id if user else 0)
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
            return
        except Exception:
            pass
    if update.message:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    elif update.callback_query:
        await update.callback_query.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            disable_web_page_preview=True,
        )

def cleanup_expired_discounts() -> None:
    now = tz_now()
    discounts = STATE.get("discounts", {})
    to_delete = []
    for uid, item in discounts.items():
        expires_at = item.get("expires_at")
        used = item.get("used", False)
        if used:
            to_delete.append(uid)
            continue
        try:
            dt = datetime.fromisoformat(expires_at)
            if dt <= now:
                to_delete.append(uid)
        except Exception:
            to_delete.append(uid)
    for uid in to_delete:
        discounts.pop(uid, None)
    if to_delete:
        save_state()

def cleanup_old_invite_events() -> None:
    now = tz_now()
    keep = []
    for item in STATE.get("invite_joins", []):
        try:
            ts = datetime.fromisoformat(item["ts"])
            if ts > now - timedelta(days=7):
                keep.append(item)
        except Exception:
            pass
    if len(keep) != len(STATE.get("invite_joins", [])):
        STATE["invite_joins"] = keep
        save_state()

def already_recent_winner(user_id: int) -> bool:
    raw = STATE.get("winner_history", {}).get(str(user_id))
    if not raw:
        return False
    try:
        dt = datetime.fromisoformat(raw)
        return dt > (tz_now() - timedelta(days=COOLDOWN_DAYS))
    except Exception:
        return False

def contest_post_text() -> str:
    return (
        "🎁 <b>BUGUNGI MUSOBAQA BOSHLANDI!</b>\n\n"
        f"Bugun 1 ta omadli ishtirokchi:\n"
        f"🔥 <b>{DISCOUNT_PERCENT}% SKIDKA</b> yutadi!\n\n"
        "<b>Qatnashish shartlari:</b>\n"
        "1️⃣ Shu postga <b>reaksiya</b> qoldiring\n"
        "2️⃣ Guruhda qoling\n\n"
        f"⏰ <b>Natija bugun {WINNER_HOUR:02d}:{WINNER_MINUTE:02d} da</b> e'lon qilinadi\n\n"
        "📌 <b>Skidka shartlari:</b>\n"
        "• Faqat <b>1 martalik</b>\n"
        f"• Maksimal <b>{format_money(DISCOUNT_MAX_SUM)} so'mgacha</b> bo'lgan xaridlar uchun amal qiladi\n"
        f"• Skidka <b>{DISCOUNT_HOURS} soat</b> amal qiladi\n"
        "• Random tarzda 1 ta ishtirokchi tanlanadi\n\n"
        "💬 Omad hammaga!"
    )

def winner_post_text(uid: int) -> str:
    label = display_name_for(uid)
    return (
        "🏆 <b>BUGUNGI G‘OLIB ANIQLANDI!</b>\n\n"
        f"🎉 Tabriklaymiz: {mention_html(uid, label)}\n\n"
        f"Sizga <b>{DISCOUNT_PERCENT}% skidka</b> taqdim etildi.\n\n"
        "📌 <b>Shartlar:</b>\n"
        "• Faqat <b>1 martalik</b>\n"
        f"• Maksimal <b>{format_money(DISCOUNT_MAX_SUM)} so'mgacha</b> bo'lgan xaridlar uchun\n"
        f"• <b>{DISCOUNT_HOURS} soat</b> amal qiladi\n\n"
        "📩 Buyurtma uchun admin bilan bog‘laning."
    )

def invite_top5_text(window_hours: int = 24) -> str:
    now = tz_now()
    items = []
    seen_pairs = set()

    for item in STATE.get("invite_joins", []):
        try:
            ts = datetime.fromisoformat(item["ts"])
        except Exception:
            continue
        if ts <= now - timedelta(hours=window_hours):
            continue

        key = (str(item["inviter_id"]), str(item["joined_id"]))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        items.append(item)

    score = {}
    labels = {}
    for item in items:
        inviter_id = str(item["inviter_id"])
        score[inviter_id] = score.get(inviter_id, 0) + 1
        labels[inviter_id] = item.get("inviter_label") or inviter_id

    ranking = sorted(score.items(), key=lambda x: x[1], reverse=True)[:5]
    if not ranking:
        return (
            "📊 <b>OXIRGI 24 SOAT BO‘YICHA TOP 20</b>\n\n"
            "Hozircha natija yo‘q.\n\n"
            "💡 Eng yaxshi usul: <b>/myref</b> tugmasi orqali shaxsiy taklif havolangizni olib tarqating."
        )

    lines = ["📊 <b>OXIRGI 24 SOAT BO‘YICHA TOP 20</b>\n"]
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    for i, (uid, cnt) in enumerate(ranking):
        lines.append(f"{medals[i]} {labels.get(uid, uid)} — <b>{cnt} ta</b> odam")
    lines.append("\n🎁 Sovrinlarni admin belgilaydi.")
    return "\n".join(lines)

def register_invite_join(inviter_id: int, inviter_label: str, joined_id: int, joined_label: str, source: str) -> None:
    if inviter_id == joined_id:
        return

    now_iso = tz_now().isoformat()
    key = (str(inviter_id), str(joined_id))

    for item in reversed(STATE.get("invite_joins", [])):
        if (str(item.get("inviter_id")), str(item.get("joined_id"))) == key:
            try:
                ts = datetime.fromisoformat(item["ts"])
                if ts > tz_now() - timedelta(days=1):
                    return
            except Exception:
                pass

    STATE.setdefault("invite_joins", []).append({
        "ts": now_iso,
        "inviter_id": inviter_id,
        "joined_id": joined_id,
        "source": source,
        "joined_label": joined_label,
        "inviter_label": inviter_label,
    })
    save_state()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_or_edit_menu(update, HELP_TEXT)

async def help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_or_edit_menu(update, HELP_TEXT)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cleanup_expired_discounts()
    cleanup_old_invite_events()
    text = (
        "<b>⚙️ Joriy sozlamalar</b>\n\n"
        f"• TARGET_CHANNEL: <code>{TARGET_CHANNEL or 'kiritilmagan'}</code>\n"
        f"• SOURCE_CHAT_ID: <code>{SOURCE_CHAT_ID or 'kiritilmagan'}</code>\n"
        f"• SOURCE_MESSAGE_ID: <code>{SOURCE_MESSAGE_ID if SOURCE_MESSAGE_ID else 'kiritilmagan'}</code>\n"
        f"• Forward vaqti: <b>{POST_HOUR:02d}:{POST_MINUTE:02d}</b>\n"
        f"• CONTEST_CHAT_ID: <code>{CONTEST_CHAT_ID or 'kiritilmagan'}</code>\n"
        f"• Contest posti: <b>{MORNING_HOUR:02d}:{MORNING_MINUTE:02d}</b>\n"
        f"• Winner vaqti: <b>{WINNER_HOUR:02d}:{WINNER_MINUTE:02d}</b>\n"
        f"• INVITE_CHAT_ID: <code>{INVITE_CHAT_ID or 'kiritilmagan'}</code>\n"
        f"• TOP 20 e'lon: <b>{TOP_HOUR:02d}:{TOP_MINUTE:02d}</b>\n"
        f"• TZ: <b>{TZ}</b>\n"
        f"• Faol skidkalar: <b>{len(STATE.get('discounts', {}))}</b>\n"
        f"• Invite eventlar: <b>{len(STATE.get('invite_joins', []))}</b>"
    )
    await send_text(update, text, parse_mode=ParseMode.HTML)

# ---------- FORWARD PART ----------
async def do_forward(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not TARGET_CHANNEL or not SOURCE_CHAT_ID or not SOURCE_MESSAGE_ID:
        return
    try:
        await context.bot.forward_message(
            chat_id=TARGET_CHANNEL,
            from_chat_id=SOURCE_CHAT_ID,
            message_id=SOURCE_MESSAGE_ID,
        )
    except Exception as e:
        logger.exception("Forward xatoligi: %s", e)

async def testforward(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_text(update, "Test forward yuborilmoqda...")
    await do_forward(context)

async def debug_ids(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg:
        return
    lines = [f"CURRENT_CHAT_ID: {msg.chat.id}", f"CURRENT_MESSAGE_ID: {msg.message_id}"]
    if msg.forward_origin:
        origin = msg.forward_origin
        lines.append(f"FORWARD_ORIGIN_TYPE: {type(origin).__name__}")
        if hasattr(origin, "chat") and origin.chat:
            lines.append(f"ORIGIN_CHAT_ID: {origin.chat.id}")
        if hasattr(origin, "message_id"):
            lines.append(f"ORIGIN_MESSAGE_ID: {origin.message_id}")
    logger.info("DEBUG_FORWARD_INFO:\n%s", "\n".join(lines))
    await send_text(update, "Forward info logga yozildi.")

# ---------- CONTEST PART ----------
async def contest_post(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not CONTEST_CHAT_ID:
        return
    cleanup_expired_discounts()
    STATE["participants"] = []
    STATE["participants_meta"] = {}
    STATE["current_post_date"] = tz_now().date().isoformat()
    msg = await context.bot.send_message(
        chat_id=CONTEST_CHAT_ID,
        text=contest_post_text(),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    STATE["current_post_id"] = msg.message_id
    save_state()

async def reaction_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        reaction = update.message_reaction
        if not reaction:
            return
        if str(reaction.chat.id) != str(CONTEST_CHAT_ID):
            return
        current_post_id = STATE.get("current_post_id")
        if not current_post_id or reaction.message_id != current_post_id:
            return
        actor = reaction.user
        if not actor or actor.is_bot:
            return
        uid = actor.id
        participants = set(STATE.get("participants", []))
        participants.add(uid)
        STATE["participants"] = list(participants)
        STATE.setdefault("participants_meta", {})[str(uid)] = {
            "username": actor.username,
            "first_name": actor.first_name,
        }
        save_state()
    except Exception as e:
        logger.exception("Reaction handler xatoligi: %s", e)

async def draw_winner(context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not CONTEST_CHAT_ID:
        return False

    cleanup_expired_discounts()
    participant_ids = [int(x) for x in STATE.get("participants", [])]
    eligible = [uid for uid in participant_ids if not already_recent_winner(uid)]

    if not participant_ids:
        await context.bot.send_message(
            chat_id=CONTEST_CHAT_ID,
            text="Bugun hali hech kim reaksiya qoldirmadi. Ertaga yana urinib ko‘ring 🙂",
        )
        return False

    if not eligible:
        await context.bot.send_message(
            chat_id=CONTEST_CHAT_ID,
            text="Bugungi ishtirokchilar orasida cooldown sababli g‘olib topilmadi. Ertaga yana urinib ko‘ring 🙂",
        )
        return False

    winner_id = random.choice(eligible)
    now = tz_now()
    expires_at = now + timedelta(hours=DISCOUNT_HOURS)

    STATE.setdefault("discounts", {})[str(winner_id)] = {
        "user_id": winner_id,
        "label": display_name_for(winner_id),
        "discount_percent": DISCOUNT_PERCENT,
        "max_amount": DISCOUNT_MAX_SUM,
        "issued_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "used": False,
    }
    STATE.setdefault("winner_history", {})[str(winner_id)] = now.isoformat()
    save_state()

    await context.bot.send_message(
        chat_id=CONTEST_CHAT_ID,
        text=winner_post_text(winner_id),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    return True

# ---------- INVITE RACE PART ----------
async def myref(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    if not INVITE_CHAT_ID:
        await send_text(update, "❌ INVITE_CHAT_ID kiritilmagan.")
        return

    old = STATE.get("invite_links", {}).get(str(user.id))
    if old and old.get("invite_link"):
        await send_text(
            update,
            f"🔗 <b>Sizning taklif havolangiz:</b>\n\n<code>{old['invite_link']}</code>\n\n"
            "Shu havola orqali kirganlar sizga yoziladi.",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        link = await context.bot.create_chat_invite_link(
            chat_id=INVITE_CHAT_ID,
            name=f"ref_{user.id}",
            creates_join_request=False,
        )
        STATE.setdefault("invite_links", {})[str(user.id)] = {
            "invite_link": link.invite_link,
            "created_at": tz_now().isoformat(),
            "name": getattr(link, "name", None),
        }
        save_state()
        await send_text(
            update,
            f"🔗 <b>Sizning taklif havolangiz:</b>\n\n<code>{link.invite_link}</code>\n\n"
            "Shu havola orqali kirganlar sizga yoziladi.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.exception("myref xatoligi: %s", e)
        await send_text(
            update,
            "Taklif havolasini yaratib bo‘lmadi. Botda guruh uchun invite huquqi bo‘lishi kerak."
        )

async def new_members_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or str(msg.chat.id) != str(INVITE_CHAT_ID):
        return
    if not msg.new_chat_members:
        return

    inviter = msg.from_user
    for member in msg.new_chat_members:
        if member.is_bot:
            continue

        if inviter and inviter.id != member.id and not inviter.is_bot:
            register_invite_join(
                inviter_id=inviter.id,
                inviter_label=user_label(inviter),
                joined_id=member.id,
                joined_label=user_label(member),
                source="direct_add",
            )

async def chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cmu = update.chat_member
    if not cmu or str(cmu.chat.id) != str(INVITE_CHAT_ID):
        return

    old_status = getattr(cmu.old_chat_member, "status", None)
    new_status = getattr(cmu.new_chat_member, "status", None)
    joined_now = old_status in ("left", "kicked") and new_status in ("member", "administrator", "restricted")
    if not joined_now:
        return

    joined_user = cmu.new_chat_member.user
    if not joined_user or joined_user.is_bot:
        return

    inv_link = getattr(cmu, "invite_link", None)
    if inv_link and getattr(inv_link, "name", None):
        name = inv_link.name or ""
        if name.startswith("ref_"):
            try:
                inviter_id = int(name.split("_", 1)[1])
                if inviter_id != joined_user.id:
                    register_invite_join(
                        inviter_id=inviter_id,
                        inviter_label=display_name_for(inviter_id),
                        joined_id=joined_user.id,
                        joined_label=user_label(joined_user),
                        source="personal_link",
                    )
                    return
            except Exception:
                pass

async def post_top5(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not INVITE_CHAT_ID:
        return
    cleanup_old_invite_events()
    await context.bot.send_message(
        chat_id=INVITE_CHAT_ID,
        text=invite_top20_text(24),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

async def top20(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cleanup_old_invite_events()
    viewer_uid = update.effective_user.id if update.effective_user else None
    await send_text(
        update,
        invite_top20_text(24, viewer_uid),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

# ---------- ADMIN/INFO ----------
async def postnow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user and ADMIN_USER_IDS and not is_admin(update.effective_user.id):
        await send_text(update, "Bu bo‘lim faqat admin uchun.")
        return
    await contest_post(context)
    await send_text(update, "✅ Contest posti yuborildi.")

async def drawnow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user and ADMIN_USER_IDS and not is_admin(update.effective_user.id):
        await send_text(update, "Bu bo‘lim faqat admin uchun.")
        return
    ok = await draw_winner(context)
    if ok:
        await send_text(update, "✅ G‘olib tanlandi.")
    else:
        await send_text(update, "⚠️ G‘olib tanlab bo‘lmadi.")

async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cleanup_expired_discounts()
    participants = STATE.get("participants", [])
    current_post_id = STATE.get("current_post_id")
    current_post_date = STATE.get("current_post_date") or "yo‘q"
    text = (
        "📌 <b>Bugungi contest holati</b>\n\n"
        f"• Sana: <b>{current_post_date}</b>\n"
        f"• Post ID: <code>{current_post_id or 'yo‘q'}</code>\n"
        f"• Ishtirokchilar soni: <b>{len(participants)}</b>\n"
    )
    if participants:
        preview = ", ".join(display_name_for(int(uid)) for uid in participants[:10])
        text += f"\nIshtirokchilar: {preview}"
        if len(participants) > 10:
            text += " ..."
    await send_text(update, text, parse_mode=ParseMode.HTML)

async def discounts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or (ADMIN_USER_IDS and not is_admin(update.effective_user.id)):
        await send_text(update, "Bu bo‘lim faqat admin uchun.")
        return
    cleanup_expired_discounts()
    items = STATE.get("discounts", {})
    if not items:
        await send_text(update, "Faol skidkalar yo‘q.")
        return

    now = tz_now()
    lines = ["📊 <b>Faol skidkalar</b>\n"]
    for uid, item in items.items():
        exp = datetime.fromisoformat(item["expires_at"])
        remain = exp - now
        total_sec = int(remain.total_seconds())
        if total_sec <= 0:
            continue
        hours = total_sec // 3600
        minutes = (total_sec % 3600) // 60
        label = item.get("label") or display_name_for(int(uid))
        lines.append(
            f"• {mention_html(int(uid), label)}\n"
            f"  └ {item['discount_percent']}% | max {format_money(item['max_amount'])} so'm | {hours} soat {minutes} daqiqa qoldi"
        )
    await send_text(
        update,
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data
    admin_buttons = {"postnow", "drawnow", "today", "discounts", "status"}
    user = update.effective_user
    if data in admin_buttons and (not user or user.id not in ADMIN_USER_IDS):
        await query.answer("Bu bo‘lim faqat admin uchun.", show_alert=True)
        return
    if data == "postnow":
        await postnow(update, context)
    elif data == "drawnow":
        await drawnow(update, context)
    elif data == "today":
        await today(update, context)
    elif data == "discounts":
        await discounts(update, context)
    elif data == "myref":
        await myref(update, context)
    elif data == "top20":
        await top20(update, context)
    elif data == "status":
        await status(update, context)
    elif data == "help":
        await help_menu(update, context)

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN topilmadi. Railway Variables ga qo‘ying.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_menu))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("testforward", testforward))
    app.add_handler(CommandHandler("postnow", postnow))
    app.add_handler(CommandHandler("drawnow", drawnow))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("discounts", discounts))
    app.add_handler(CommandHandler("myref", myref))
    app.add_handler(CommandHandler("top20", top20))
    app.add_handler(CallbackQueryHandler(button_handler))

    app.add_handler(MessageHandler(filters.FORWARDED, debug_ids))
    app.add_handler(MessageReactionHandler(reaction_handler))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_members_handler))
    app.add_handler(ChatMemberHandler(chat_member_handler, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatJoinRequestHandler(lambda u, c: None))

    jq = app.job_queue
    if jq is None:
        logger.warning("JobQueue yo‘q. requirements.txt ichida python-telegram-bot[job-queue] bo‘lishi kerak.")
    else:
        jq.run_daily(
            do_forward,
            time=time(hour=POST_HOUR, minute=POST_MINUTE, tzinfo=ZoneInfo(TZ)),
            name="daily_forward_post",
        )
        jq.run_daily(
            contest_post,
            time=time(hour=MORNING_HOUR, minute=MORNING_MINUTE, tzinfo=ZoneInfo(TZ)),
            name="daily_contest_post",
        )
        jq.run_daily(
            draw_winner,
            time=time(hour=WINNER_HOUR, minute=WINNER_MINUTE, tzinfo=ZoneInfo(TZ)),
            name="daily_contest_winner",
        )
        jq.run_daily(
            post_top5,
            time=time(hour=TOP_HOUR, minute=TOP_MINUTE, tzinfo=ZoneInfo(TZ)),
            name="daily_top5_post",
        )

    logger.info("Bot ishga tushdi.")
    app.run_polling(close_loop=False, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
