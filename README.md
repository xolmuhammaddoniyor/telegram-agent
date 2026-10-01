# Telegram kanal uchun AI agent

Agent siz belgilagan vaqtlarda Telegram kanalingizga post joylaydi:

1. Gemini post matnini yozadi (yoki siz tayyor matn berasiz).
2. Gemini **Nano Banana** (`gemini-2.5-flash-image`) postga rasm chizadi.
3. Rasm va matn bot orqali kanalga yuboriladi.

## 1. Kerakli kalitlar

| O'zgaruvchi | Qayerdan olinadi |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram'da [@BotFather](https://t.me/BotFather) → `/newbot` |
| `TELEGRAM_CHANNEL_ID` | Kanal `@username`i yoki `-100...` raqamli ID |
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/apikey) |

Botni kanalga **administrator** qilib qo'shing va unga "Post joylash" huquqini bering.

## 2. O'rnatish

```bash
cd telegram-agent
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # so'ng .env ichiga kalitlarni yozing
```

`.env` faylini hech kimga yubormang va git'ga qo'shmang (`.gitignore` buni oldini oladi).

## 3. Jadvalni sozlash

`config.yaml` faylini tahrirlang:

```yaml
timezone: Asia/Tashkent
posts:
  - time: "09:00"                 # har kuni soat 9:00 da
    topic: "Motivatsion fikr"
  - time: "13:00"
    days: mon,wed,fri             # faqat dush, chor, juma
    topic: "AI bo'yicha maslahat"
  - time: "19:30"
    topic: "Kitob tavsiyasi"
    image_prompt: "Stol ustida ochiq kitob va choy, fotorealistik"
  - time: "21:00"
    text: "Tayyor matn — AI faqat rasm chizadi"
    topic: "Tungi shahar"
  - time: "22:00"
    topic: "Kun xulosasi"
    image: false                  # rasmsiz post
```

Qo'shimcha maydonlar: `aspect_ratio` (`"1:1"`, `"16:9"`, `"9:16"`, `"4:3"` ...).
Umumiy uslub va til `style` va `language` maydonlarida.

## 4. Ishga tushirish

```bash
# Sinov: Telegram'ga yubormaydi, output/ papkasiga saqlaydi (faqat GEMINI_API_KEY kerak)
python agent.py --now 0 --dry-run

# 0-postni hozirning o'zida kanalga yuborish
python agent.py --now 0

# Jadval bo'yicha doimiy ishlash
python agent.py
```

Agent doimiy ishlashi uchun kompyuter yoki server yoniq turishi kerak.
Serverda fon rejimida ishlatish uchun eng oddiy yo'l:

```bash
nohup python agent.py > agent.log 2>&1 &
```

## 5. GitHub Actions orqali bepul ishlatish (server shart emas)

Kompyuter yoqiq turmasa ham agent ishlashi uchun GitHub o'zi jadval bo'yicha uni ishga tushiradi.

1. Kodni GitHub'dagi **private** repoga joylang.
2. Repoda **Settings → Secrets and variables → Actions → New repository secret** orqali uchta secret qo'shing:
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `GEMINI_API_KEY`.
3. Sinov: **Actions → Telegram post → Run workflow**, post raqamini kiriting.

`config.yaml` dagi vaqtlar o'zgarsa, workflow faylini qayta yarating va push qiling:

```bash
python agent.py --workflow > .github/workflows/post.yml
```

GitHub jadvali ba'zan 5-15 daqiqa kechikadi. Aniq daqiqada chiqishi muhim bo'lsa, `python agent.py` ni serverda ishlating.

## Xatolar

- Rasm yaratilmasa (masalan, xavfsizlik filtri sababli), post rasmsiz yuboriladi va logda ogohlantirish chiqadi.
- Matn 1024 belgidan uzun bo'lsa, avval rasm, keyin matn alohida xabar bo'lib chiqadi.
- `Telegram sendPhoto xatosi: chat not found` — kanal ID noto'g'ri yoki bot kanalga admin qilinmagan.
- Model nomlari o'zgarsa, `.env` da `GEMINI_IMAGE_MODEL` va `GEMINI_TEXT_MODEL` ni almashtiring.
