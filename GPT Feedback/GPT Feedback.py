from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Any, Optional
import os
import json
import re
import time
import logging
import hashlib
import requests
from datetime import datetime
from html import escape

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot.apihelper import ApiTelegramException

from FunPayAPI.types import MessageTypes
from FunPayAPI.updater.events import NewMessageEvent

if TYPE_CHECKING:
    from cardinal import Cardinal

NAME = "GPT Feedback"
VERSION = "1.2"
DESCRIPTION = "Отвечает на отзывы через GPT."
CREDITS = "@tinechelovec"
UUID = "461770a6-4460-4cf5-9eec-c41dc99fc64c"
SETTINGS_PAGE = True

logger = logging.getLogger(f"FPC.{__name__}")
PREFIX = "[GPT Feedback]"

BASE_URL = "https://api.zukijourney.com/v1"
DEFAULT_MODEL = "gpt-3.5-turbo"

INSTRUCTION_URL = "https://teletype.in/@tinechelovec/GPT-Feedback"

PLUGIN_FOLDER = "storage/plugins/gpt_feedback"
DATA_FILE = os.path.join(PLUGIN_FOLDER, "data.json")
STATE_FILE = os.path.join(PLUGIN_FOLDER, "state.json")
os.makedirs(PLUGIN_FOLDER, exist_ok=True)

if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, indent=4, ensure_ascii=False)

if not os.path.exists(STATE_FILE):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, indent=4, ensure_ascii=False)


ORDER_ID_REGEX = re.compile(r"#([A-Za-z0-9]+)")
MAX_ATTEMPTS = 3
MAX_CHARACTERS = 700
MIN_CHARACTERS = 30

DEFAULT_PROMPT_TEMPLATE = """
Привет! Ты - ИИ Ассистент в нашем интернет-магазине игровых ценностей.

Информация о покупателе и заказе:
{info_block}

Твоя задача:
- Ответить покупателю в доброжелательном тоне.
- Использовать много эмодзи.
- Обязательно учесть информацию о покупателе и заказе.
- Написать большой и развернутый ответ до 700 симвболо.
- Пожелать что-нибудь хорошее покупателю.

Важно:
- Не упоминать интернет-ресурсы.
- Не использовать оскорбления, ненормативную лексику, противозаконную или политическую информацию.
- НЕ ВЫДАВАТЬ ФРАГМЕНТЫ КОДА ИЛИ ЛИСТИНГИ КОДА.
- НЕ ИСПОЛЬЗОВАТЬ MARKDOWN / HTML / РАЗМЕТКУ.

В конце добавь строку: Спасибо за {rating} звезд и отзыв от {date} {time}!
""".strip()

try:
    import tg_bot.CBT as CBT
except Exception:
    class CBT:
        EDIT_PLUGIN = "PLUGIN_EDIT"
        PLUGIN_SETTINGS = "PLUGIN_SETTINGS"
        BACK = None

CBT_EDIT_PLUGIN = getattr(CBT, "EDIT_PLUGIN", "PLUGIN_EDIT")
CBT_PLUGIN_SETTINGS = getattr(CBT, "PLUGIN_SETTINGS", "PLUGIN_SETTINGS")
CBT_BACK = getattr(CBT, "BACK", None) or f"{UUID}:back"
CB_WELCOME = f"{UUID}:welcome"
CB_SETTINGS = f"{UUID}:settings"
CB_DELETE = f"{UUID}:delete"
CB_DELETE_YES = f"{UUID}:delete_yes"
CB_DELETE_NO = f"{UUID}:delete_no"
CB_TOGGLE = f"{UUID}:toggle"
CB_STARS = f"{UUID}:stars"
CB_STAR_TOGGLE = f"{UUID}:star"
CB_FIELDS = f"{UUID}:fields"
CB_FIELD_TOGGLE = f"{UUID}:field"
CB_APIKEY = f"{UUID}:apikey"
CB_TEST = f"{UUID}:test"
CB_CANCEL = f"{UUID}:cancel"
CBT_PLUGINS_LIST_OPEN = f"{getattr(CBT, 'PLUGINS_LIST', '44')}:0"

_fsm: Dict[int, Dict[str, Any]] = {}

def open_plugins_list(cardinal: "Cardinal", call):
    pass

def logi(msg: str):
    logger.info(f"{PREFIX} INFO: {msg}")

def logw(msg: str):
    logger.warning(f"{PREFIX} WARNING: {msg}")

def loge(msg: str):
    logger.error(f"{PREFIX} ERROR: {msg}")

def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        loge(f"_load_json({path}) failed: {e}")
        return {}

def _save_json(path: str, data: dict):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        loge(f"_save_json({path}) failed: {e}")

def load_data() -> dict:
    return _load_json(DATA_FILE)

def save_data(data: dict):
    _save_json(DATA_FILE, data)

def load_state() -> dict:
    return _load_json(STATE_FILE)

def save_state(st: dict):
    _save_json(STATE_FILE, st)

def _default_config() -> dict:
    return {
        "enabled": False,
        "stars": [5],
        "api_key": "",
        "model": DEFAULT_MODEL,
        "fields": {
            "name": True,
            "item": True,
            "cost": True,
            "rating": True,
            "text": True,
        }
    }

def _mask_key(s: str) -> str:
    if not s:
        return "—"
    t = s.strip()
    if len(t) <= 10:
        return "****"
    return t[:4] + "…" + t[-4:]

def _get_config(data: dict) -> dict:
    if isinstance(data.get("global"), dict):
        cfg = data["global"]
        base = _default_config()
        base.update(cfg)
        base["fields"] = {**_default_config()["fields"], **(cfg.get("fields") or {})}
        stars = cfg.get("stars")
        if not isinstance(stars, list) or not stars:
            base["stars"] = [5]
        else:
            base["stars"] = sorted({int(x) for x in stars if str(x).isdigit() and 1 <= int(x) <= 5}) or [5]

        base.pop("prompt", None)
        return base

    for _, v in (data or {}).items():
        if isinstance(v, dict) and ("api_key" in v or "enabled" in v or "stars" in v):
            cfg = v
            base = _default_config()
            base.update(cfg)
            base["fields"] = {**_default_config()["fields"], **(cfg.get("fields") or {})}
            data["global"] = base
            save_data(data)
            base.pop("prompt", None)
            return base

    data["global"] = _default_config()
    save_data(data)
    return data["global"]

def _set_config(cfg: dict):
    data = load_data()
    cfg.pop("prompt", None)
    data["global"] = cfg
    save_data(data)

def _safe_edit(bot, chat_id: int, msg_id: int, text: str, kb=None):
    try:
        bot.edit_message_text(
            text,
            chat_id,
            msg_id,
            parse_mode="HTML",
            reply_markup=kb,
            disable_web_page_preview=True
        )
    except ApiTelegramException as e:
        if "message is not modified" in str(e).lower():
            return
        raise

def _try_delete(bot, chat_id: int, msg_id: int):
    try:
        bot.delete_message(chat_id, msg_id)
    except Exception:
        pass

def _notify(cardinal: "Cardinal", text: str):
    try:
        bot = cardinal.telegram.bot
        users = getattr(cardinal.telegram, "authorized_users", []) or []
        for uid in users:
            try:
                bot.send_message(int(uid), text, disable_web_page_preview=True)
            except Exception:
                pass
    except Exception as e:
        loge(f"_notify failed: {e}")

def _welcome_text(cfg: dict) -> str:
    stars = cfg.get("stars", [5]) or [5]
    model = cfg.get("model", DEFAULT_MODEL)
    return (
        "👋 <b>GPT Feedback</b>\n\n"
        f"Статус: {'✅ ВКЛ' if cfg.get('enabled') else '❌ ВЫКЛ'}\n"
        f"Звёзды: {', '.join(map(str, stars))}\n"
        f"API ключ: {'✅ Установлен' if (cfg.get('api_key') or '').strip() else '❌ Нет'} "
        f"<code>{_mask_key((cfg.get('api_key') or '').strip())}</code>\n\n"
        "Выбери действие:"
    )

def _welcome_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("⚙️ Настройки", callback_data=CB_SETTINGS),
        InlineKeyboardButton("📘 Инструкция", url=INSTRUCTION_URL),
    )
    kb.row(
        InlineKeyboardButton("🗑 Удалить плагин", callback_data=CB_DELETE),
    )
    kb.row(
        InlineKeyboardButton("🔙 К списку плагинов", callback_data=CBT_PLUGINS_LIST_OPEN)
    )
    return kb

def open_welcome(cardinal: "Cardinal", call_or_msg):
    data = load_data()
    cfg = _get_config(data)
    bot = cardinal.telegram.bot

    if hasattr(call_or_msg, "message"):
        chat_id = call_or_msg.message.chat.id
        msg_id = call_or_msg.message.id
        try:
            bot.answer_callback_query(call_or_msg.id)
        except Exception:
            pass
        _safe_edit(bot, chat_id, msg_id, _welcome_text(cfg), _welcome_kb())
    else:
        chat_id = call_or_msg.chat.id
        bot.send_message(chat_id, _welcome_text(cfg), parse_mode="HTML", reply_markup=_welcome_kb(), disable_web_page_preview=True)

def _settings_text(cfg: dict) -> str:
    stars = cfg.get("stars", [5]) or [5]
    return (
        "⚙️ <b>Настройки GPT Feedback</b>\n\n"
        f"Статус: {'✅ ВКЛ' if cfg.get('enabled') else '❌ ВЫКЛ'}\n"
        f"Звёзды: {', '.join(map(str, stars))}\n"
        f"API ключ: {'✅ Установлен' if (cfg.get('api_key') or '').strip() else '❌ Нет'} "
        f"<code>{_mask_key((cfg.get('api_key') or '').strip())}</code>\n\n"
        "Настрой параметры ниже:"
    )

def _settings_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("🔛 Вкл/Выкл", callback_data=CB_TOGGLE),
        InlineKeyboardButton("⭐ Звёзды", callback_data=CB_STARS),
    )
    kb.row(
        InlineKeyboardButton("🧾 Поля", callback_data=CB_FIELDS),
        InlineKeyboardButton("🔑 API ключ", callback_data=CB_APIKEY),
    )
    kb.row(
        InlineKeyboardButton("🧪 Тест API", callback_data=CB_TEST),
    )
    kb.row(InlineKeyboardButton("◀️ Назад", callback_data=CB_WELCOME))
    return kb

def open_settings(cardinal: "Cardinal", call):
    bot = cardinal.telegram.bot
    chat_id = call.message.chat.id
    msg_id = call.message.id
    cfg = _get_config(load_data())

    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    _safe_edit(bot, chat_id, msg_id, _settings_text(cfg), _settings_kb())

def _fields_text(cfg: dict) -> str:
    f = cfg.get("fields") or {}
    def line(k: str, title: str) -> str:
        return f"{'✅' if f.get(k) else '❌'} {title}"
    return (
        "🧾 <b>Какие поля вставлять в промпт</b>\n\n"
        f"{line('name','Имя покупателя')}\n"
        f"{line('item','Товар')}\n"
        f"{line('cost','Стоимость')}\n"
        f"{line('rating','Оценка (звёзды)')}\n"
        f"{line('text','Текст отзыва')}\n\n"
        "Нажимай чтобы включить/выключить:"
    )

def _fields_kb(cfg: dict) -> InlineKeyboardMarkup:
    f = cfg.get("fields") or {}
    def btn(k: str, title: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(f"{'✅' if f.get(k) else '❌'} {title}", callback_data=f"{CB_FIELD_TOGGLE}:{k}")
    kb = InlineKeyboardMarkup()
    kb.row(btn("name", "Имя"), btn("item", "Товар"))
    kb.row(btn("cost", "Стоимость"), btn("rating", "Оценка"))
    kb.row(btn("text", "Отзыв"))
    kb.row(InlineKeyboardButton("◀️ Назад", callback_data=CB_SETTINGS))
    return kb

def _fields_open(cardinal: "Cardinal", call):
    bot = cardinal.telegram.bot
    chat_id = call.message.chat.id
    msg_id = call.message.id

    cfg = _get_config(load_data())

    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    _safe_edit(bot, chat_id, msg_id, _fields_text(cfg), _fields_kb(cfg))

def _field_toggle(cardinal: "Cardinal", call, field_name: str):
    bot = cardinal.telegram.bot
    chat_id = call.message.chat.id
    msg_id = call.message.id

    cfg = _get_config(load_data())
    fields = cfg.get("fields") or {}
    if field_name not in fields:
        try:
            bot.answer_callback_query(call.id, "Неизвестное поле.")
        except Exception:
            pass
        return

    fields[field_name] = not bool(fields.get(field_name))
    cfg["fields"] = fields
    _set_config(cfg)

    try:
        bot.answer_callback_query(call.id, f"{field_name}: {'ON' if fields[field_name] else 'OFF'}")
    except Exception:
        pass

    _safe_edit(bot, chat_id, msg_id, _fields_text(cfg), _fields_kb(cfg))

def _stars_text(cfg: dict) -> str:
    stars = cfg.get("stars", [5]) or [5]
    return (
        "⭐ <b>Ответы на какие оценки?</b>\n\n"
        f"Сейчас включено: <b>{', '.join(map(str, stars))}</b>\n\n"
        "Нажимай на звёзды чтобы включать/выключать:"
    )

def _stars_kb(cfg: dict) -> InlineKeyboardMarkup:
    current = set(cfg.get("stars", [5]) or [5])

    def sbtn(n: int) -> InlineKeyboardButton:
        on = n in current
        return InlineKeyboardButton(f"{'✅' if on else '⬜'} {n}⭐", callback_data=f"{CB_STAR_TOGGLE}:{n}")

    kb = InlineKeyboardMarkup()
    kb.row(sbtn(1), sbtn(2), sbtn(3))
    kb.row(sbtn(4), sbtn(5))
    kb.row(InlineKeyboardButton("◀️ Назад", callback_data=CB_SETTINGS))
    return kb

def _stars_open(cardinal: "Cardinal", call):
    bot = cardinal.telegram.bot
    chat_id = call.message.chat.id
    msg_id = call.message.id
    cfg = _get_config(load_data())

    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    _safe_edit(bot, chat_id, msg_id, _stars_text(cfg), _stars_kb(cfg))

def _star_toggle(cardinal: "Cardinal", call, n: int):
    bot = cardinal.telegram.bot
    chat_id = call.message.chat.id
    msg_id = call.message.id

    cfg = _get_config(load_data())
    stars = set(cfg.get("stars", [5]) or [5])

    if n in stars:
        if len(stars) == 1:
            try:
                bot.answer_callback_query(call.id, "Нельзя выключить последнюю звезду.", show_alert=True)
            except Exception:
                pass
            return
        stars.remove(n)
    else:
        stars.add(n)

    cfg["stars"] = sorted(stars)
    _set_config(cfg)

    try:
        bot.answer_callback_query(call.id, f"Звёзды: {', '.join(map(str, cfg['stars']))}")
    except Exception:
        pass

    _safe_edit(bot, chat_id, msg_id, _stars_text(cfg), _stars_kb(cfg))

def _apikey_screen_text(cfg: dict) -> str:
    masked = _mask_key((cfg.get("api_key") or "").strip())
    return (
        "🔑 <b>API ключ</b>\n\n"
        f"Текущий: <code>{masked}</code>\n\n"
        "Теперь отправь новый API-ключ <b>одним сообщением</b> в чат.\n"
        "Чтобы отменить — нажми ❌ Отменить."
    )

def _input_kb(return_cb: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("❌ Отменить", callback_data=CB_CANCEL),
        InlineKeyboardButton("◀️ Назад", callback_data=return_cb),
    )
    return kb

def _apikey_start(cardinal: "Cardinal", call):
    bot = cardinal.telegram.bot
    chat_id = call.message.chat.id
    msg_id = call.message.id
    cfg = _get_config(load_data())

    _fsm[chat_id] = {"mode": "apikey", "panel_chat_id": chat_id, "panel_msg_id": msg_id, "return": "settings"}

    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    _safe_edit(bot, chat_id, msg_id, _apikey_screen_text(cfg), _input_kb(CB_SETTINGS))

def _parse_key_text(raw_text: str) -> str:
    s = (raw_text or "").strip()
    if not s:
        return ""
    if s.startswith("{") and s.endswith("}"):
        try:
            obj = json.loads(s)
            for k in ("api_key", "token", "key", "apikey"):
                v = obj.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        except Exception:
            pass
    for line in s.splitlines():
        line = line.strip()
        if line:
            return line
    return s

def _fsm_cancel(cardinal: "Cardinal", call):
    bot = cardinal.telegram.bot
    chat_id = call.message.chat.id
    _fsm.pop(chat_id, None)

    try:
        bot.answer_callback_query(call.id, "Отменено.")
    except Exception:
        pass

    open_settings(cardinal, call)

def _handle_fsm(message, cardinal: "Cardinal"):
    chat_id = message.chat.id
    if chat_id not in _fsm:
        return

    st = _fsm.get(chat_id) or {}
    mode = st.get("mode")
    text = (getattr(message, "text", None) or "").strip()

    bot = cardinal.telegram.bot
    _try_delete(bot, chat_id, message.id)

    if not text:
        return

    cfg = _get_config(load_data())

    if mode == "apikey":
        key = _parse_key_text(text)
        if not key:
            panel_msg_id = st.get("panel_msg_id")
            if panel_msg_id:
                _safe_edit(bot, chat_id, panel_msg_id, _apikey_screen_text(cfg), _input_kb(CB_SETTINGS))
            return

        cfg["api_key"] = key
        _set_config(cfg)
        _fsm.pop(chat_id, None)

        panel_msg_id = st.get("panel_msg_id")
        if panel_msg_id:
            _safe_edit(bot, chat_id, panel_msg_id, _settings_text(cfg), _settings_kb())
        return

class _SafeDict(dict):
    def __missing__(self, key):
        return ""

def _hash_review(stars: Optional[int], text: Optional[str]) -> str:
    s = f"{stars or ''}|{(text or '').strip()}"
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()

def _extract_order_fields(order) -> Dict[str, str]:
    review = getattr(order, "review", None)
    name = str(getattr(order, "buyer_username", "") or "") or str(getattr(review, "author", "") or "")
    item = str(getattr(order, "title", "") or "")
    cost = str(getattr(order, "sum", "") or getattr(order, "price", "") or "")
    rating = str(getattr(review, "stars", "") or "")
    text = str(getattr(review, "text", "") or "")

    return {"name": name, "item": item, "cost": cost, "rating": rating, "text": text}

def _build_info_block(cfg: dict, order) -> str:
    f = cfg.get("fields") or {}
    vals = _extract_order_fields(order)

    lines = []
    if f.get("name"):
        lines.append(f"- Имя: {vals.get('name','')}".strip())
    if f.get("item"):
        lines.append(f"- Товар: {vals.get('item','')}".strip())
    if f.get("cost"):
        c = vals.get("cost", "")
        lines.append(f"- Стоимость: {c} рублей".strip() if c else "- Стоимость: ")
    if f.get("rating"):
        lines.append(f"- Оценка: {vals.get('rating','')} из 5".strip())
    if f.get("text"):
        lines.append(f"- Отзыв: {vals.get('text','')}".strip())
    if not lines:
        lines = ["- (поля выключены в настройках)"]
    return "\n".join(lines)

def build_prompt(cfg: dict, order) -> str:
    review = getattr(order, "review", None)
    vals = _extract_order_fields(order)

    info_block = _build_info_block(cfg, order)
    prompt_tpl = DEFAULT_PROMPT_TEMPLATE

    mapping = _SafeDict({
        "info_block": info_block,
        "name": vals.get("name", ""),
        "item": vals.get("item", ""),
        "cost": vals.get("cost", ""),
        "rating": str(getattr(review, "stars", "") or vals.get("rating", "") or ""),
        "text": str(getattr(review, "text", "") or vals.get("text", "") or ""),
        "date": datetime.now().strftime("%d.%m.%Y"),
        "time": datetime.now().strftime("%H:%M:%S"),
    })

    try:
        return prompt_tpl.format_map(mapping)
    except Exception as e:
        loge(f"build_prompt format failed: {e}")
        return prompt_tpl + "\n\n" + info_block

def _cut_700_no_dots(text: str, limit: int = MAX_CHARACTERS) -> str:
    if text is None:
        return ""
    t = str(text).strip()
    if len(t) <= limit:
        return t
    cut_point = t.rfind(" ", 0, limit)
    return t[:cut_point] if cut_point != -1 else t[:limit]

def generate_response(prompt: str, api_key: str, model: str) -> str:
    if not api_key:
        return "❌ API-ключ не настроен. Открой меню и укажи ключ."

    url = f"{BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model or DEFAULT_MODEL, "messages": [{"role": "user", "content": prompt}]}

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code != 200:
                logw(f"ZukiJourney HTTP {resp.status_code} (attempt {attempt}): {resp.text[:300]}")
                time.sleep(1)
                continue

            data = resp.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            content = (content or "").strip()

            if len(content) < MIN_CHARACTERS:
                logw(f"Model ответ слишком короткий (len={len(content)}) attempt {attempt}")
                continue

            return _cut_700_no_dots(content, MAX_CHARACTERS)

        except Exception as e:
            loge(f"ZukiJourney request error (attempt {attempt}): {e}")
            time.sleep(1)

    return "Спасибо за отзыв! 😊"

def _toggle(cardinal: "Cardinal", call):
    bot = cardinal.telegram.bot
    chat_id = call.message.chat.id

    cfg = _get_config(load_data())
    cfg["enabled"] = not bool(cfg.get("enabled"))
    _set_config(cfg)

    try:
        bot.answer_callback_query(call.id, f"Плагин {'включён' if cfg['enabled'] else 'выключен'}")
    except Exception:
        pass

    _safe_edit(bot, chat_id, call.message.id, _settings_text(cfg), _settings_kb())

def _test_api(cardinal: "Cardinal", call):
    bot = cardinal.telegram.bot
    chat_id = call.message.chat.id

    cfg = _get_config(load_data())
    api_key = (cfg.get("api_key") or "").strip()
    model = cfg.get("model", DEFAULT_MODEL)

    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    if not api_key:
        try:
            bot.answer_callback_query(call.id, "Сначала задай API ключ (🔑 API ключ).", show_alert=True)
        except Exception:
            pass
        return

    prompt = "Сгенерируй короткий дружелюбный ответ покупателю на отзыв: 'всё супер'. 1-2 предложения, с эмодзи."
    ans = generate_response(prompt, api_key, model)
    bot.send_message(chat_id, f"🧪 Тест API:\n\n{ans}")

def _delete_menu_text() -> str:
    return (
        "🗑 <b>Удаление плагина</b>\n\n"
        "Ты точно хочешь удалить <b>GPT Feedback</b>?\n"
        "Это действие может быть необратимым."
    )

def _delete_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("✅ Да, удалить", callback_data=CB_DELETE_YES),
        InlineKeyboardButton("❌ Нет", callback_data=CB_DELETE_NO),
    )
    kb.row(InlineKeyboardButton("◀️ Назад", callback_data=CB_WELCOME))
    return kb

def _delete_open(cardinal: "Cardinal", call):
    bot = cardinal.telegram.bot
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    _safe_edit(bot, call.message.chat.id, call.message.id, _delete_menu_text(), _delete_menu_kb())

def _delete_try(cardinal: "Cardinal", call):
    bot = cardinal.telegram.bot
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    ok = False
    err = None

    candidates = [
        (cardinal, "delete_plugin"),
        (cardinal, "remove_plugin"),
        (cardinal, "uninstall_plugin"),
        (cardinal, "unload_plugin"),
        (getattr(cardinal, "plugins", None), "delete_plugin"),
        (getattr(cardinal, "plugins", None), "remove_plugin"),
        (getattr(cardinal, "plugin_manager", None), "delete_plugin"),
        (getattr(cardinal, "plugin_manager", None), "remove_plugin"),
        (getattr(cardinal, "plugin_manager", None), "unload_plugin"),
    ]

    for obj, method in candidates:
        try:
            if obj is None:
                continue
            fn = getattr(obj, method, None)
            if callable(fn):
                fn(UUID)
                ok = True
                break
        except Exception as e:
            err = e

    if ok:
        try:
            bot.edit_message_text(
                "✅ Плагин удалён.\n\nЕсли он всё ещё виден в меню — перезапусти Cardinal.",
                call.message.chat.id,
                call.message.id,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except Exception:
            pass
        return

    text = (
        "❌ Не смог удалить плагин автоматически (в твоём Cardinal нет подходящего метода).\n\n"
        "Удаление вручную:\n"
        "1) Открой Cardinal → Плагины\n"
        "2) Найди <b>GPT Feedback</b>\n"
        "3) Нажми <b>Удалить</b>\n\n"
        f"Ошибка (если была): <code>{escape(str(err)) if err else '—'}</code>"
    )
    _safe_edit(bot, call.message.chat.id, call.message.id, text, _welcome_kb())

def _delete_no(cardinal: "Cardinal", call):
    try:
        cardinal.telegram.bot.answer_callback_query(call.id, "Отменено.")
    except Exception:
        pass
    open_welcome(cardinal, call)

def _go_main_menu(cardinal: "Cardinal", call):
    bot = cardinal.telegram.bot
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    for attr in ("open_main_menu", "show_main_menu", "menu", "open_menu", "start_menu", "home"):
        fn = getattr(cardinal.telegram, attr, None) or getattr(cardinal, attr, None)
        if callable(fn):
            try:
                fn(call.message.chat.id)
                return
            except Exception:
                pass

    open_welcome(cardinal, call)

def _should_handle_event_type(msg_type) -> bool:
    types = {MessageTypes.NEW_FEEDBACK, MessageTypes.FEEDBACK_CHANGED}
    fd = getattr(MessageTypes, "FEEDBACK_DELETED", None)
    if fd is not None:
        types.add(fd)
    return msg_type in types

def _get_order_id_from_event(event: NewMessageEvent) -> Optional[str]:
    raw = str(event.message)
    m = ORDER_ID_REGEX.search(raw)
    if not m:
        return None
    return m.group(1)

def _review_exists(order) -> bool:
    review = getattr(order, "review", None)
    if not review:
        return False
    stars = getattr(review, "stars", None)
    text = getattr(review, "text", None)
    if stars is None and (text is None or str(text).strip() == ""):
        return False
    return True

def _buyer_review_fingerprint(order) -> str:
    review = getattr(order, "review", None)
    stars = getattr(review, "stars", None) if review else None
    text = getattr(review, "text", None) if review else None
    return _hash_review(stars, text)

def _delete_our_reply(cardinal: "Cardinal", order_id: str):
    try:
        cardinal.account.delete_review(order_id)
        logi(f"✅ delete_review({order_id}) OK")
    except Exception as e:
        loge(f"delete_review({order_id}) failed: {e}")
        _notify(cardinal, f"❌ GPT Feedback: не смог удалить ответ для заказа #{order_id}: {e}")

def _send_or_edit_reply(cardinal: "Cardinal", order_id: str, stars: int, text: str):
    try:
        cardinal.account.send_review(order_id=order_id, rating=int(stars), text=_cut_700_no_dots(text, MAX_CHARACTERS))
        logi(f"✅ send_review({order_id}) OK")
    except Exception as e:
        loge(f"send_review({order_id}) failed: {e}")
        _notify(cardinal, f"❌ GPT Feedback: не смог отправить/обновить ответ для заказа #{order_id}: {e}")

def handle_feedback_event(cardinal: "Cardinal", event: NewMessageEvent):
    try:
        msg_type = getattr(event.message, "type", None)
        if not _should_handle_event_type(msg_type):
            return

        order_id = _get_order_id_from_event(event)
        if not order_id:
            logw("Не нашёл order_id по regex #(...). Проверь формат event.message.")
            return

        cfg = _get_config(load_data())

        if not cfg.get("enabled"):
            return

        api_key = (cfg.get("api_key") or "").strip()
        if not api_key:
            _notify(cardinal, "❌ GPT Feedback: нет API ключа. Открой меню и задай ключ.")
            return

        try:
            order = cardinal.account.get_order(order_id)
        except Exception as e:
            loge(f"get_order({order_id}) failed: {e}")
            return

        if not order:
            return

        st = load_state()
        prev = st.get(order_id) if isinstance(st.get(order_id), dict) else None
        prev_fp = (prev or {}).get("review_fp")

        if msg_type == getattr(MessageTypes, "FEEDBACK_DELETED", None):
            if prev:
                _delete_our_reply(cardinal, order_id)
                st.pop(order_id, None)
                save_state(st)
            return

        if not _review_exists(order):
            if prev:
                _delete_our_reply(cardinal, order_id)
                st.pop(order_id, None)
                save_state(st)
            return

        review = getattr(order, "review", None)
        stars = int(getattr(review, "stars", 5) or 5)
        review_text = (getattr(review, "text", "") or "").strip()
        fp = _buyer_review_fingerprint(order)

        if prev_fp and prev_fp == fp:
            return

        allowed = cfg.get("stars", [5]) or [5]
        if stars not in allowed:
            if prev:
                _delete_our_reply(cardinal, order_id)
                st.pop(order_id, None)
                save_state(st)
            return

        prompt = build_prompt(cfg, order)
        reply_text = generate_response(prompt, api_key, cfg.get("model", DEFAULT_MODEL))
        reply_text = _cut_700_no_dots(reply_text, MAX_CHARACTERS)

        _send_or_edit_reply(cardinal, order_id, stars, reply_text)

        st[order_id] = {"review_fp": fp, "stars": stars, "updated_at": int(time.time())}
        save_state(st)

    except Exception as e:
        loge(f"handle_feedback_event crashed: {e}")
        _notify(cardinal, f"❌ GPT Feedback crashed: {e}")

def init_cardinal(cardinal: "Cardinal"):
    tg = cardinal.telegram
    tg.msg_handler(lambda m: open_welcome(cardinal, m), commands=["gptfeedback_menu"])
    tg.msg_handler(lambda m: _handle_fsm(m, cardinal), func=lambda m: m.chat.id in _fsm)
    tg.cbq_handler(lambda c: open_welcome(cardinal, c), func=lambda c:
                   c.data.startswith(f"{CBT_EDIT_PLUGIN}:{UUID}")
                   or c.data.startswith(f"{CBT_PLUGIN_SETTINGS}:{UUID}")
                   or c.data == CB_WELCOME)
    tg.cbq_handler(lambda c: open_settings(cardinal, c), func=lambda c: c.data == CB_SETTINGS)
    tg.cbq_handler(lambda c: _delete_open(cardinal, c), func=lambda c: c.data == CB_DELETE)
    tg.cbq_handler(lambda c: _delete_try(cardinal, c), func=lambda c: c.data == CB_DELETE_YES)
    tg.cbq_handler(lambda c: _delete_no(cardinal, c), func=lambda c: c.data == CB_DELETE_NO)
    tg.cbq_handler(lambda c: _toggle(cardinal, c), func=lambda c: c.data == CB_TOGGLE)
    tg.cbq_handler(lambda c: _stars_open(cardinal, c), func=lambda c: c.data == CB_STARS)
    tg.cbq_handler(lambda c: _fields_open(cardinal, c), func=lambda c: c.data == CB_FIELDS)
    tg.cbq_handler(lambda c: _apikey_start(cardinal, c), func=lambda c: c.data == CB_APIKEY)
    tg.cbq_handler(lambda c: _test_api(cardinal, c), func=lambda c: c.data == CB_TEST)
    tg.cbq_handler(lambda c: open_welcome(cardinal, c), func=lambda c: c.data == CB_WELCOME)
    tg.cbq_handler(lambda c: open_settings(cardinal, c), func=lambda c: c.data == CB_SETTINGS)
    tg.cbq_handler(lambda c: _star_toggle(cardinal, c, int(c.data.split(":")[-1])),
                   func=lambda c: c.data.startswith(f"{CB_STAR_TOGGLE}:"))
    tg.cbq_handler(lambda c: _field_toggle(cardinal, c, c.data.split(":")[-1]),
                   func=lambda c: c.data.startswith(f"{CB_FIELD_TOGGLE}:"))
    tg.cbq_handler(lambda c: _fsm_cancel(cardinal, c), func=lambda c: c.data == CB_CANCEL)
    tg.cbq_handler(lambda c: _go_main_menu(cardinal, c), func=lambda c: c.data == CBT_BACK)

    try:
        cardinal.add_telegram_commands(UUID, [
            ("gptfeedback_menu", "Открыть меню GPT Feedback", True),
        ])
    except Exception as e:
        logw(f"add_telegram_commands failed: {e}")

    logi("✅ GPT Feedback запущен")


BIND_TO_PRE_INIT = [init_cardinal]
BIND_TO_NEW_MESSAGE = [handle_feedback_event]
BIND_TO_DELETE = None
