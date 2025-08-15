from __future__ import annotations
from typing import TYPE_CHECKING
import os
import json
import re
import time
import logging
from datetime import datetime

from g4f.client import Client
from FunPayAPI.types import MessageTypes
from FunPayAPI.updater.events import NewMessageEvent
from telebot.types import InlineKeyboardButton
from tg_bot import keyboards

if TYPE_CHECKING:
    from cardinal import Cardinal

NAME = "GPT Feedback"
VERSION = "1.0"
DESCRIPTION = "Отвечает на отзывы через GPT."
CREDITS = "@tinechelovec"
UUID = "461770a6-4460-4cf5-9eec-c41dc99fc64c"
SETTINGS_PAGE = False

logger = logging.getLogger(f"FPC.{__name__}")
PREFIX = "[GPT Feedback]"
def log(msg: str):
    logger.info(f"{PREFIX} {msg}")

PLUGIN_FOLDER = "storage/plugins/gpt_feedback"
DATA_FILE = os.path.join(PLUGIN_FOLDER, "data.json")
os.makedirs(PLUGIN_FOLDER, exist_ok=True)
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, indent=4, ensure_ascii=False)

MAX_ATTEMPTS = 5
MAX_CHARACTERS = 700
MIN_CHARACTERS = 50
ORDER_ID_REGEX = re.compile(r"#([A-Za-z0-9]+)")
client = Client()

PROMPT_TEMPLATE = """
Привет! Ты — ИИ-ассистент в магазине игровых ценностей.
Данные заказа:
    - Оценка: {rating} из 5
    - Отзыв: {text}

Составь дружелюбный ответ:
- Используй эмодзи.
- Пожелай что-то хорошее.
- Сделай шутку про покупку.
- В конце добавь: спасибо за {rating} звезд и отзыв от {date} {time}!
"""

def load_data() -> dict:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"Ошибка загрузки data.json: {e}")
        return {}

def save_data(data: dict):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        log(f"Ошибка сохранения data.json: {e}")

def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."

def build_prompt(order) -> str:
    rating = getattr(order.review, "stars", None) or "5"
    text = getattr(order.review, "text", None) or "Спасибо!"
    return PROMPT_TEMPLATE.format(
        rating=rating,
        text=text,
        date=datetime.now().strftime("%d.%m.%Y"),
        time=datetime.now().strftime("%H:%M:%S"),
    )

def generate_response(prompt: str) -> str:
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}]
            )
            text = " ".join(response.choices[0].message.content.strip().splitlines())
            if len(text) < MIN_CHARACTERS:
                log(f"Ответ GPT слишком короткий (попытка {attempt+1})")
                continue
            if len(text) > MAX_CHARACTERS:
                text = truncate(text, MAX_CHARACTERS)
            return text
        except Exception as e:
            log(f"Ошибка GPT: {e}")
            time.sleep(1)
    return "Спасибо за отзыв! 😊"

def parse_stars_args(text: str) -> list:
    parts = re.split(r"[\s,;]+", text.strip())
    stars = []
    for p in parts:
        if not p:
            continue
        try:
            n = int(p)
            if 1 <= n <= 5 and n not in stars:
                stars.append(n)
        except ValueError:
            continue
    return sorted(stars)

def cmd_toggle(message, cardinal: 'Cardinal'):
    uid = str(message.chat.id)
    data = load_data()
    user = data.get(uid, {"enabled": False, "stars": [5]})
    user["enabled"] = not user.get("enabled", False)
    data[uid] = user
    save_data(data)
    state = "включён" if user["enabled"] else "выключен"
    cardinal.telegram.bot.send_message(message.chat.id, f"✅ Плагин {state} для вашего чата.")
    log(f"Пользователь {uid} переключил плагин: {state}")

def cmd_setstars(message, cardinal: 'Cardinal'):
    args = message.text.replace("/gptfeedback_setstars", "", 1).strip()
    uid = str(message.chat.id)
    if not args:
        cardinal.telegram.bot.send_message(
            message.chat.id,
            "❗ Использование: /gptfeedback_setstars 5 4 3"
        )
        return
    stars = parse_stars_args(args)
    if not stars:
        cardinal.telegram.bot.send_message(message.chat.id, "❌ Укажите числа от 1 до 5.")
        return
    data = load_data()
    user = data.get(uid, {"enabled": False, "stars": [5]})
    user["stars"] = stars
    data[uid] = user
    save_data(data)
    cardinal.telegram.bot.send_message(message.chat.id, f"✅ Отвечаем на отзывы: {', '.join(map(str, stars))} звёзд.")
    log(f"Пользователь {uid} установил звёзды: {stars}")

def cmd_status(message, cardinal: 'Cardinal'):
    uid = str(message.chat.id)
    data = load_data()
    user = data.get(uid)
    if not user:
        cardinal.telegram.bot.send_message(message.chat.id, "ℹ️ Плагин выключен, звёзды: 5")
        return
    enabled = user.get("enabled", False)
    stars = user.get("stars", [5])
    text = f"🔧 Статус плагина: {'ВКЛ' if enabled else 'ВЫКЛ'}\n⭐ Звёзды: {', '.join(map(str, stars))}"
    cardinal.telegram.bot.send_message(message.chat.id, text)

def handle_feedback_event(cardinal: 'Cardinal', event: NewMessageEvent):
    if event.message.type not in (
        MessageTypes.NEW_FEEDBACK,
        MessageTypes.FEEDBACK_CHANGED,
        MessageTypes.FEEDBACK_DELETED
    ):
        return

    order_id_match = ORDER_ID_REGEX.search(str(event.message))
    if not order_id_match:
        return

    order_id = order_id_match.group(1)
    order = cardinal.account.get_order(order_id)
    data = load_data()
    enabled_users = [u for u, v in data.items() if v.get('enabled')]
    if not enabled_users:
        return

    if event.message.type == MessageTypes.FEEDBACK_DELETED:
        if order and getattr(order.review, "reply", None):
            cardinal.account.delete_review(order.id)
            log(f"Удалён ответ на удалённый отзыв: {order_id}")
        return

    if not order or not getattr(order, 'review', None):
        return

    stars = getattr(order.review, 'stars', 0) or 0
    try:
        stars_int = int(stars)
    except Exception:
        stars_int = 0

    allowed = any(stars_int in data.get(uid, {}).get('stars', [5]) for uid in enabled_users)
    if not allowed:
        if order.review.reply:
            cardinal.account.delete_review(order.id)
        return

    prompt = build_prompt(order)
    reply_text = generate_response(prompt)
    cardinal.account.send_review(order.id, text=reply_text, rating=stars_int)
    log(f"Автоответ на {stars_int}⭐ отзыв ({order_id}): {reply_text}")

def init_cardinal(cardinal: 'Cardinal'):
    tg = cardinal.telegram
    tg.msg_handler(lambda m: cmd_toggle(m, cardinal), commands=["gptfeedback_toggle"])
    tg.msg_handler(lambda m: cmd_setstars(m, cardinal), commands=["gptfeedback_setstars"])
    tg.msg_handler(lambda m: cmd_status(m, cardinal), commands=["gptfeedback_status"])

    cardinal.add_telegram_commands(UUID, [
        ("gptfeedback_toggle", "Включить/выключить автоматические ответы GPT", True),
        ("gptfeedback_setstars", "Установить звёзды (пример: /gptfeedback_setstars 4 5)", True),
        ("gptfeedback_status", "Показать настройки плагина", True),
    ])

    try:
        orig_edit_plugin = keyboards.edit_plugin
        def custom_edit_plugin(c, uuid, offset=0, ask_to_delete=False):
            kb = orig_edit_plugin(c, uuid, offset, ask_to_delete)
            if uuid == UUID:
                dev_btn = InlineKeyboardButton(text="👽 Разработчик", url=f"https://t.me/{CREDITS[1:]}")
                kb.keyboard.insert(0, [dev_btn])
            return kb
        keyboards.edit_plugin = custom_edit_plugin
    except Exception:
        pass

BIND_TO_PRE_INIT = [init_cardinal]
BIND_TO_NEW_MESSAGE = [handle_feedback_event]
BIND_TO_DELETE = None
