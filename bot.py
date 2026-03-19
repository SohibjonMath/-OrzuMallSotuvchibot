import asyncio
import os
import random
from typing import List

from telegram import Bot
from telegram.error import TelegramError


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or str(value).strip() == "":
        raise RuntimeError(f"ENV topilmadi: {name}")
    return str(value).strip()


BOT_TOKEN = env("BOT_TOKEN")
TARGET_CHAT_ID = env("TARGET_CHAT_ID")
SOURCE_CHAT_ID = env("SOURCE_CHAT_ID")
MODE = os.getenv("MODE", "forward").strip().lower()
INTERVAL_MINUTES = int(os.getenv("INTERVAL_MINUTES", "30"))

if MODE not in {"forward", "copy"}:
    raise RuntimeError("MODE faqat 'forward' yoki 'copy' bo'lishi kerak.")

if INTERVAL_MINUTES < 1:
    raise RuntimeError("INTERVAL_MINUTES kamida 1 bo'lishi kerak.")

INTERVAL_SECONDS = INTERVAL_MINUTES * 60


def load_posts(limit: int = 500) -> List[int]:
    posts: List[int] = []
    for i in range(1, limit + 1):
        raw = os.getenv(f"POST{i}")
        if raw is None or str(raw).strip() == "":
            continue
        raw = str(raw).strip()
        if not raw.isdigit():
            raise RuntimeError(f"POST{i} raqam bo'lishi kerak. Hozirgi qiymat: {raw}")
        posts.append(int(raw))

    if not posts:
        raise RuntimeError("Kamida bitta POST1, POST2 ... kiritilishi shart.")
    return posts


POSTS = load_posts()
bot = Bot(token=BOT_TOKEN)
_last_post_id: int | None = None


async def send_one_random_post() -> None:
    global _last_post_id

    available = POSTS[:]
    if _last_post_id is not None and len(available) > 1 and _last_post_id in available:
        available.remove(_last_post_id)

    post_id = random.choice(available)

    try:
        if MODE == "copy":
            await bot.copy_message(
                chat_id=TARGET_CHAT_ID,
                from_chat_id=SOURCE_CHAT_ID,
                message_id=post_id,
            )
        else:
            await bot.forward_message(
                chat_id=TARGET_CHAT_ID,
                from_chat_id=SOURCE_CHAT_ID,
                message_id=post_id,
            )

        _last_post_id = post_id
        print(f"✅ Yuborildi | mode={MODE} | post_id={post_id}", flush=True)

    except TelegramError as e:
        print(f"❌ Telegram xato | post_id={post_id} | {e}", flush=True)
    except Exception as e:
        print(f"❌ Noma'lum xato | post_id={post_id} | {e}", flush=True)


async def main() -> None:
    print("🚀 Railway repost bot ishga tushdi", flush=True)
    print(f"📦 Jami postlar: {len(POSTS)}", flush=True)
    print(f"⏱ Interval: {INTERVAL_MINUTES} minut", flush=True)
    print(f"📥 Source: {SOURCE_CHAT_ID}", flush=True)
    print(f"📤 Target: {TARGET_CHAT_ID}", flush=True)
    print(f"🔁 Mode: {MODE}", flush=True)

    while True:
        await send_one_random_post()
        await asyncio.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
