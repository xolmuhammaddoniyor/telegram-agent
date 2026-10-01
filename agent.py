"""Telegram kanaliga jadval bo'yicha post joylaydigan AI agent.

Gemini matn yozadi, Gemini "Nano Banana" (gemini-2.5-flash-image) rasm
chizadi, so'ng post Telegram Bot API orqali kanalga yuboriladi.

Ishga tushirish:
    python agent.py                 # jadval bo'yicha doimiy ishlaydi
    python agent.py --now 0         # config.yaml'dagi 0-postni hozir yuboradi
    python agent.py --now 0 --dry-run   # Telegram'ga yubormay, output/ ga saqlaydi
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

log = logging.getLogger("telegram-agent")

BASE_DIR = Path(__file__).resolve().parent
CAPTION_LIMIT = 1024  # Telegram rasm izohi chegarasi
MESSAGE_LIMIT = 4096  # Telegram oddiy xabar chegarasi


@dataclass
class Settings:
    bot_token: str
    channel_id: str
    gemini_api_key: str
    image_model: str
    text_model: str

    @classmethod
    def from_env(cls, require_telegram: bool = True) -> "Settings":
        def get(name: str, required: bool = True) -> str:
            value = os.environ.get(name, "").strip()
            if required and not value:
                sys.exit(f"Xato: {name} muhit o'zgaruvchisi o'rnatilmagan (.env.example ga qarang).")
            return value

        return cls(
            bot_token=get("TELEGRAM_BOT_TOKEN", require_telegram),
            channel_id=get("TELEGRAM_CHANNEL_ID", require_telegram),
            gemini_api_key=get("GEMINI_API_KEY", required=False),
            image_model=os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image"),
            text_model=os.environ.get("GEMINI_TEXT_MODEL", "gemini-2.5-flash"),
        )


# ---------------------------------------------------------------- Gemini


class ContentGenerator:
    def __init__(self, settings: Settings, language: str, style: str):
        self.client = None
        if settings.gemini_api_key:
            from google import genai

            self.client = genai.Client(api_key=settings.gemini_api_key)
        self.settings = settings
        self.language = language
        self.style = style

    def write_text(self, topic: str) -> str:
        if self.client is None:
            raise RuntimeError("GEMINI_API_KEY yo'q: config.yaml da postga 'text' yozing yoki kalitni qo'shing")
        prompt = (
            f"Telegram kanal uchun {self.language} tilida post yoz.\n"
            f"Mavzu: {topic}\n"
            f"Uslub: {self.style}\n"
            f"Matn {CAPTION_LIMIT - 50} belgidan oshmasin. "
            "Faqat post matnini qaytar, boshqa izoh yozma. Markdown belgilarini ishlatma."
        )
        response = self.client.models.generate_content(
            model=self.settings.text_model, contents=prompt
        )
        text = (response.text or "").strip()
        if not text:
            raise RuntimeError("Gemini bo'sh matn qaytardi")
        return text

    def make_image(self, topic: str, image_prompt: str | None, aspect_ratio: str) -> bytes:
        if self.client is None:
            raise RuntimeError("GEMINI_API_KEY yo'q, rasm yaratilmaydi")
        from google.genai import types

        prompt = image_prompt or (
            f"Telegram kanal posti uchun chiroyli, yuqori sifatli illyustratsiya. "
            f"Mavzu: {topic}. Rasmda hech qanday yozuv yoki matn bo'lmasin."
        )
        response = self.client.models.generate_content(
            model=self.settings.image_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
            ),
        )
        for candidate in response.candidates or []:
            for part in (candidate.content.parts if candidate.content else None) or []:
                if part.inline_data and part.inline_data.data:
                    return part.inline_data.data
        raise RuntimeError("Gemini rasm qaytarmadi (ehtimol so'rov xavfsizlik filtridan o'tmadi)")


# ---------------------------------------------------------------- Telegram


class TelegramPublisher:
    def __init__(self, settings: Settings):
        self.base = f"https://api.telegram.org/bot{settings.bot_token}"
        self.channel_id = settings.channel_id

    def _call(self, method: str, data: dict, files: dict | None = None) -> dict:
        resp = requests.post(f"{self.base}/{method}", data=data, files=files, timeout=60)
        payload = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if not payload.get("ok"):
            # Tokenni logga chiqarmaslik uchun faqat tavsifni ko'rsatamiz
            raise RuntimeError(f"Telegram {method} xatosi: {payload.get('description', resp.status_code)}")
        return payload["result"]

    def publish(self, text: str, image: bytes | None) -> None:
        if image is None:
            self._call("sendMessage", {"chat_id": self.channel_id, "text": text[:MESSAGE_LIMIT]})
            return
        if len(text) <= CAPTION_LIMIT:
            self._call(
                "sendPhoto",
                {"chat_id": self.channel_id, "caption": text},
                files={"photo": ("post.png", image, "image/png")},
            )
        else:
            # Izoh juda uzun: avval rasm, keyin matn alohida xabar sifatida
            self._call("sendPhoto", {"chat_id": self.channel_id}, files={"photo": ("post.png", image, "image/png")})
            self._call("sendMessage", {"chat_id": self.channel_id, "text": text[:MESSAGE_LIMIT]})


# ---------------------------------------------------------------- Agent


class Agent:
    def __init__(self, config: dict, settings: Settings, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run
        self.generator = ContentGenerator(
            settings,
            language=config.get("language", "o'zbek"),
            style=config.get("style", ""),
        )
        self.publisher = None if dry_run else TelegramPublisher(settings)

    def run_post(self, index: int) -> bool:
        post = self.config["posts"][index]
        topic = post.get("topic", "")
        log.info("Post #%d tayyorlanmoqda: %s", index, topic)
        try:
            text = pick_text(post) or self.generator.write_text(topic)
            image = None
            if post.get("image", True):
                try:
                    image = self.generator.make_image(
                        topic, post.get("image_prompt"), post.get("aspect_ratio", "1:1")
                    )
                except Exception as exc:  # rasm chiqmasa ham matn baribir chiqsin
                    log.warning("Rasm yaratilmadi, post rasmsiz yuboriladi: %s", exc)

            if self.dry_run:
                self._save_locally(index, text, image)
            else:
                self.publisher.publish(text, image)
                log.info("Post #%d kanalga joylandi", index)
            return True
        except Exception:
            log.exception("Post #%d yuborilmadi", index)
            return False

    def _save_locally(self, index: int, text: str, image: bytes | None) -> None:
        out = BASE_DIR / "output"
        out.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (out / f"{stamp}-post{index}.txt").write_text(text, encoding="utf-8")
        if image:
            (out / f"{stamp}-post{index}.png").write_bytes(image)
        log.info("Sinov rejimi: post output/%s-post%d.* ga saqlandi", stamp, index)

    def schedule_forever(self) -> None:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger

        tz = self.config.get("timezone", "Asia/Tashkent")
        scheduler = BlockingScheduler(timezone=tz)
        for i, post in enumerate(self.config["posts"]):
            if post.get("every"):
                minutes = parse_every(post["every"])
                trigger = CronTrigger(minute=f"*/{minutes}", timezone=tz)
                qachon = f"har {minutes} daqiqada"
            else:
                hour, minute = parse_time(post["time"])
                trigger = CronTrigger(
                    hour=hour, minute=minute, day_of_week=post.get("days", "*"), timezone=tz
                )
                qachon = f"{post['time']} ({post.get('days', 'har kuni')})"
            scheduler.add_job(
                self.run_post, trigger, args=[i], id=f"post-{i}",
                misfire_grace_time=600, coalesce=True, max_instances=1,
            )
            log.info("Rejalashtirildi: #%d %s — %s", i, qachon, post.get("topic", ""))
        log.info("Agent ishga tushdi (%s). To'xtatish uchun Ctrl+C.", tz)
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            log.info("Agent to'xtatildi")


def pick_text(post: dict) -> str | None:
    """Tayyor matnni tanlaydi: 'text' bitta matn, 'texts' ro'yxatdan navbatdagisi.

    Har bir ishga tushish alohida jarayon bo'lgani uchun navbat vaqt bo'yicha
    hisoblanadi: ketma-ket postlarda har xil matn chiqadi.
    """
    texts = post.get("texts")
    if texts:
        if not isinstance(texts, list):
            sys.exit("Xato: 'texts' ro'yxat bo'lishi kerak")
        step = parse_every(post["every"]) if post.get("every") else 24 * 60
        slot = int(datetime.now(timezone.utc).timestamp()) // 60 // step
        return str(texts[slot % len(texts)])
    return post.get("text")


def parse_time(value: str) -> tuple[int, int]:
    try:
        hour, minute = (int(x) for x in str(value).split(":"))
    except ValueError:
        sys.exit(f"Xato: vaqt formati noto'g'ri: {value!r} (masalan \"09:30\")")
    if not (0 <= hour < 24 and 0 <= minute < 60):
        sys.exit(f"Xato: vaqt noto'g'ri: {value!r}")
    return hour, minute


def parse_every(value) -> int:
    """Takrorlanish oralig'ini daqiqalarda qaytaradi (60 ning bo'luvchisi)."""
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        sys.exit(f"Xato: 'every' butun son bo'lishi kerak: {value!r}")
    if not 1 <= minutes <= 60 or 60 % minutes:
        sys.exit(f"Xato: 'every' 60 ning bo'luvchisi bo'lsin (1,2,3,4,5,6,10,12,15,20,30,60): {value!r}")
    return minutes


DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def utc_cron(post: dict, tz: str) -> str:
    """Post vaqtini GitHub Actions uchun UTC cron ifodasiga aylantiradi."""
    if post.get("every"):
        return f"*/{parse_every(post['every'])} * * * *"
    hour, minute = parse_time(post["time"])
    offset = int(datetime.now(ZoneInfo(tz)).utcoffset().total_seconds() // 60)
    total = hour * 60 + minute - offset
    day_shift, total = divmod(total, 24 * 60)
    days = str(post.get("days", "*")).replace(" ", "").lower()
    if days == "*":
        dow = "*"
    else:
        nums = []
        for name in days.split(","):
            if name not in DAY_NAMES:
                sys.exit(f"Xato: kun nomi noto'g'ri: {name!r} ({','.join(DAY_NAMES)})")
            idx = (DAY_NAMES.index(name) + day_shift) % 7
            nums.append((idx + 1) % 7)  # cron: 0=yakshanba, 1=dushanba
        dow = ",".join(str(n) for n in sorted(set(nums)))
    return f"{total % 60} {total // 60} * * {dow}"


WORKFLOW_TEMPLATE = """# Bu fayl avtomatik yaratilgan: python agent.py --workflow > .github/workflows/post.yml
# config.yaml o'zgarsa, qayta yarating. GitHub cron vaqti UTC da va bir necha daqiqa kechikishi mumkin.
name: Telegram post

on:
  schedule:
{schedules}
  workflow_dispatch:
    inputs:
      post:
        description: "Hozir yuboriladigan post raqami (0 dan boshlanadi)"
        required: true
        default: "0"

jobs:
  post:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt
      - name: Post yuborish
        env:
          TELEGRAM_BOT_TOKEN: ${{{{ secrets.TELEGRAM_BOT_TOKEN }}}}
          TELEGRAM_CHANNEL_ID: ${{{{ secrets.TELEGRAM_CHANNEL_ID }}}}
          GEMINI_API_KEY: ${{{{ secrets.GEMINI_API_KEY }}}}
          CRON: ${{{{ github.event.schedule }}}}
          POST: ${{{{ inputs.post }}}}
        run: |
          if [ -n "$CRON" ]; then
            python agent.py --cron "$CRON"
          else
            python agent.py --now "$POST"
          fi
"""


def make_workflow(config: dict) -> str:
    tz = config.get("timezone", "Asia/Tashkent")
    crons = sorted({utc_cron(p, tz) for p in config["posts"]})
    return WORKFLOW_TEMPLATE.format(schedules="\n".join(f'    - cron: "{c}"' for c in crons))


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    if not config.get("posts"):
        sys.exit(f"Xato: {path} faylida 'posts' ro'yxati bo'sh")
    for post in config["posts"]:
        if post.get("every"):
            parse_every(post["every"])
        elif "time" in post:
            parse_time(post["time"])
        else:
            sys.exit(f"Xato: har bir postda 'time' yoki 'every' bo'lishi kerak: {post}")
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram kanal uchun AI post agenti")
    parser.add_argument("--config", default=str(BASE_DIR / "config.yaml"))
    parser.add_argument("--now", type=int, metavar="N", help="N-postni hozir yuborish (0 dan boshlanadi)")
    parser.add_argument("--dry-run", action="store_true", help="Telegram'ga yubormay, output/ ga saqlash")
    parser.add_argument("--cron", metavar="EXPR", help="Shu UTC cron ifodasiga mos postlarni yuborish (GitHub Actions uchun)")
    parser.add_argument("--workflow", action="store_true", help="GitHub Actions workflow faylini chiqarish")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config(Path(args.config))
    if args.workflow:
        print(make_workflow(config), end="")
        return
    settings = Settings.from_env(require_telegram=not args.dry_run)
    agent = Agent(config, settings, dry_run=args.dry_run)

    if args.now is not None:
        if not 0 <= args.now < len(config["posts"]):
            sys.exit(f"Xato: post raqami 0..{len(config['posts']) - 1} oralig'ida bo'lishi kerak")
        if not agent.run_post(args.now):
            sys.exit(1)
    elif args.cron:
        tz = config.get("timezone", "Asia/Tashkent")
        matches = [i for i, p in enumerate(config["posts"]) if utc_cron(p, tz) == args.cron.strip()]
        if not matches:
            sys.exit(f"Xato: {args.cron!r} ga mos post topilmadi (workflow faylini qayta yarating)")
        results = [agent.run_post(i) for i in matches]
        if not all(results):
            sys.exit(1)
    else:
        agent.schedule_forever()


if __name__ == "__main__":
    main()
