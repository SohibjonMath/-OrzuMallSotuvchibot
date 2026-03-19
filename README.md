# Railway Repost Bot

Bu bot Telegram kanal ichidagi postlarni random tarzda guruhga tashlab turadi.

## Vazifasi
- `POST1`, `POST2`, `POST3` ... ko'rinishida post ID larni ENV ga yozasiz
- bot har `INTERVAL_MINUTES` da ulardan random bittasini tanlaydi
- `MODE=forward` bo'lsa forward qiladi
- `MODE=copy` bo'lsa copy qiladi
- ketma-ket bir xil postni yubormaydi

## Fayllar
- `bot.py`
- `requirements.txt`
- `railway.json`
- `.env.example`

## Railway deploy
1. GitHub ga shu papkani yuklang
2. Railway da `New Project` → `Deploy from GitHub Repo`
3. Variables bo'limiga `.env.example` dagi env larni kiriting
4. Deploy bo'lgach bot avtomatik ishga tushadi

## Kerakli env lar
- `BOT_TOKEN`
- `TARGET_CHAT_ID`
- `SOURCE_CHAT_ID`
- `MODE`
- `INTERVAL_MINUTES`
- `POST1`, `POST2`, `POST3` ...

## Misol
```env
BOT_TOKEN=xxxxxxxx
TARGET_CHAT_ID=-1001234567890
SOURCE_CHAT_ID=@kanalnomi
MODE=forward
INTERVAL_MINUTES=30

POST1=15
POST2=18
POST3=22
POST4=30
```

## Muhim
- Bot manba kanalga admin qilingan bo'lsa yaxshi
- Bot target guruhga xabar yubora olishi kerak
- `POST1=15` degani kanal ichidagi 15-post ID si
