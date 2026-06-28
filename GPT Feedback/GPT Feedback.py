from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Any, Optional
import os
import json
import re
import time
import logging
import hashlib
import shutil
import base64 as _b64
import threading
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
VERSION = "1.4"
DESCRIPTION = "Отвечает на отзывы через GPT."
CREDITS = "@tinechelovec"
UUID = "461770a6-4460-4cf5-9eec-c41dc99fc64c"
SETTINGS_PAGE = True

logger = logging.getLogger(f"FPC.{__name__}")
PREFIX = f"[{NAME}]"

INSTRUCTION_URL = "https://teletype.in/@tinechelovec/GPT-Feedback"
CREATOR_URL = "https://t.me/tinechelovec"
GROUP_URL = "https://t.me/dev_thc_chat"
CHANNEL_URL = "https://t.me/by_thc"
GITHUB_URL = "https://github.com/tinechelovec/FPC-Plugin-GPT-Feedback"
GITHUB_UPDATE_URL = os.getenv(
    "GPT_FEEDBACK_UPDATE_URL",
    "https://raw.githubusercontent.com/tinechelovec/FPC-Plugin-GPT-Feedback/main/GPT%20Feedback/GPT%20Feedback.py",
).strip()
UPDATE_TIMEOUT = float(os.getenv("GPT_FEEDBACK_UPDATE_TIMEOUT", "30"))

def _u(value: str) -> str:
    return _b64.b64decode(value.encode("ascii")).decode("utf-8")

def _branding_values():
    data = globals().get("_p")
    if not isinstance(data, (tuple, list)) or len(data) != 7:
        return ()
    try:
        values = tuple(_u(str(item)) for item in data)
    except Exception:
        return ()
    if not (
        values[0].startswith("@")
        and all(value.startswith("https://t.me/") for value in values[1:4])
        and values[4].startswith("https://")
        and values[5].startswith("https://github.com/")
        and values[6].startswith("https://raw.githubusercontent.com/")
    ):
        return ()
    return values

def _restore_branding():
    global CREDITS, CREATOR_URL, GROUP_URL, CHANNEL_URL
    global INSTRUCTION_URL, GITHUB_URL, GITHUB_UPDATE_URL
    values = _branding_values()
    if not values:
        return False
    CREDITS, CREATOR_URL, GROUP_URL, CHANNEL_URL, INSTRUCTION_URL, GITHUB_URL = values[:6]
    GITHUB_UPDATE_URL = os.getenv("GPT_FEEDBACK_UPDATE_URL", values[6]).strip()
    return True

IO_BASE_URL = os.getenv("IOINTELLIGENCE_BASE_URL", "https://api.intelligence.io.solutions/api/v1/")
IO_CHAT_URL = os.getenv("IOINTELLIGENCE_CHAT_URL", IO_BASE_URL.rstrip("/") + "/chat/completions")
IO_MODEL = os.getenv("IOINTELLIGENCE_MODEL", "meta-llama/Llama-3.3-70B-Instruct")
IO_MODELS_URL = os.getenv(
    "IOINTELLIGENCE_MODELS_URL",
    IO_BASE_URL.rstrip("/") + "/models",
)
IO_TIMEOUT = float(os.getenv("IOINTELLIGENCE_TIMEOUT", "45"))
IO_MODELS_TIMEOUT = float(os.getenv("IOINTELLIGENCE_MODELS_TIMEOUT", "20"))
IO_TEMPERATURE = float(os.getenv("IOINTELLIGENCE_TEMPERATURE", "0.2"))
MODEL_CACHE_TTL = int(os.getenv("GPT_FEEDBACK_MODEL_CACHE_TTL", "600"))
MODEL_ATTEMPTS_PER_MODEL = 2
MAX_FALLBACK_MODELS = 6
IO_API_KEY_ENV = (
    (os.getenv("IOINTELLIGENCE_API_KEY", "io-v2-eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJvd25lciI6IjEyMjFlYTM2LTlmZDgtNGQ3ZC1hMzY5LTMxMDZiMDk4ODMzOSIsImV4cCI6NDkzNjIwOTA4NX0.kQKFJ11wu3i5A_kRsVNIEUYs6SYNg5MRyiD52uXGsIL40LXGHFzxYst1Fl_57rODeN0FmHGD6qqTJuA0k-MpzQ") or "").strip()
    or (os.getenv("IONET_API_KEY", "") or "").strip()
)

DEFAULT_MODEL = IO_MODEL

PLUGIN_FOLDER = "storage/plugins/gpt_feedback"
DATA_FILE = os.path.join(PLUGIN_FOLDER, "data.json")
STATE_FILE = os.path.join(PLUGIN_FOLDER, "state.json")
LOG_FILE = os.path.join(PLUGIN_FOLDER, "plugin.log")
LOG_EXPORT_FILE = os.path.join(PLUGIN_FOLDER, "GPT_Feedback_logs.txt")
PENDING_FILE = os.path.join(PLUGIN_FOLDER, "pending_reviews.json")
os.makedirs(PLUGIN_FOLDER, exist_ok=True)

def _setup_file_logging():
    try:
        log_path = os.path.abspath(LOG_FILE)
        for handler in logger.handlers:
            if os.path.abspath(getattr(handler, "baseFilename", "")) == log_path:
                return

        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [GPT Feedback] %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    except Exception:
        pass

_setup_file_logging()

if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, indent=4, ensure_ascii=False)

if not os.path.exists(STATE_FILE):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, indent=4, ensure_ascii=False)

if not os.path.exists(PENDING_FILE):
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, indent=4, ensure_ascii=False)

ORDER_ID_REGEX = re.compile(r"#([A-Za-z0-9]+)")
MAX_ATTEMPTS = 5
MAX_CHARACTERS = 900
MIN_CHARACTERS = 30
RESPONSE_LENGTH_OPTIONS = tuple(range(200, 901, 100))

RESPONSE_STYLES = {
    "adaptive": (
        "🧠 Адаптивный",
        "Подстраивайся под самого покупателя: повторяй его уровень формальности, темп и настроение, "
        "но не копируй слова дословно. Сам выбирай обращение на «ты» или «Вы». "
        "Структуру ответа меняй от отзыва к отзыву.",
    ),
    "friendly": (
        "🙂 Дружелюбный",
        "Говори как доброжелательный владелец небольшого магазина. Обращайся на «ты», используй "
        "простые разговорные слова, тёплую благодарность и личное пожелание. Избегай канцелярита, "
        "официальных оборотов и холодных формулировок.",
    ),
    "professional": (
        "🤝 Деловой",
        "Говори как специалист службы поддержки. Всегда обращайся на «Вы». Используй полные, "
        "грамотные предложения и нейтральную деловую лексику. Структура: признание обратной связи → "
        "конкретная реакция на отзыв → спокойное завершение. Не используй сленг и фамильярность.",
    ),
    "youthful": (
        "🔥 Молодёжный",
        "Говори как современный продавец цифровых товаров. Обращайся на «ты», используй короткие "
        "энергичные фразы и умеренный актуальный сленг: «топ», «кайф», «круто», когда это уместно. "
        "Для оценок 1–2 звезды полностью убирай сленг и отвечай серьёзно.",
    ),
    "premium": (
        "✨ Премиальный",
        "Говори как персональный менеджер премиального сервиса. Всегда обращайся на «Вы». "
        "Используй спокойную, выразительную и аккуратную лексику, плавные предложения и сдержанную "
        "благодарность. Не используй сленг, дешёвый пафос, шаблонные возгласы и лишние восклицания.",
    ),
    "playful": (
        "🎭 Игривый",
        "Создавай лёгкий узнаваемый голос бренда: живые формулировки, одна небольшая шутка, игра слов "
        "или образ, связанный с товаром. Юмор разрешён только для положительных отзывов на 4–5 звёзд. "
        "При 1–3 звёздах отвечай бережно и серьёзно, без шуток.",
    ),
}

STYLE_SYSTEM_PROMPTS = {
    "adaptive": (
        "Ты - адаптивный автор ответов. Каждый раз зеркаль тон конкретного покупателя и меняй композицию. "
        "Не используй один и тот же шаблон начала или завершения."
    ),
    "friendly": (
        "Ты - тёплый и открытый владелец магазина. Твой голос разговорный, человечный и заботливый. "
        "Используй обращение на «ты», если только негативный отзыв не требует более осторожного тона."
    ),
    "professional": (
        "Ты - профессиональный менеджер поддержки. Всегда обращайся на «Вы», пиши официально, ясно и "
        "спокойно. Не используй разговорный сленг. Ответ должен ощущаться как деловая коммуникация."
    ),
    "youthful": (
        "Ты - энергичный продавец цифровых товаров с современным молодёжным голосом. Для положительных "
        "отзывов используй короткие фразы и лёгкий сленг. Для негатива мгновенно переходи на серьёзный тон."
    ),
    "premium": (
        "Ты - персональный менеджер премиального сервиса. Всегда обращайся на «Вы», пиши элегантно, "
        "сдержанно и индивидуально. Избегай дешёвых клише и чрезмерной эмоциональности."
    ),
    "playful": (
        "Ты - остроумный автор бренда. В положительных ответах добавляй одну уместную лёгкую шутку или "
        "метафору, связанную с товаром. Никогда не шути над жалобой, покупателем или низкой оценкой."
    ),
}

STYLE_TEMPERATURES = {
    "adaptive": 0.55,
    "friendly": 0.65,
    "professional": 0.25,
    "youthful": 0.8,
    "premium": 0.45,
    "playful": 0.9,
}

DEFAULT_PROMPT_TEMPLATE = """
Ты отвечаешь от лица продавца цифровых товаров на отзыв покупателя.
Сформируй персональный ответ, который опирается на доступные данные заказа, название и описание товара.

ВКЛЮЧЁННЫЕ ПРОДАВЦОМ ДАННЫЕ:
{info_block}

СЛУЖЕБНЫЕ НАСТРОЙКИ:
- Оценка для определения реакции: {rating} из 5.
- ОБЯЗАТЕЛЬНЫЙ объём: не меньше {min_length} и не больше {max_length} символов. Ответ короче {min_length} символов считается неправильным.
- Выбранная личность ответа: {style_instruction}
- Эмодзи: {emoji_instruction}
- Упоминание оценки: {rating_mention_instruction}
- Язык ответа: {language_instruction}

КАК РЕАГИРОВАТЬ НА ОЦЕНКУ:
{rating_instruction}

КАК АНАЛИЗИРОВАТЬ ТОВАР И ОТЗЫВ:
- Внимательно прочитай название товара и его описание, если они присутствуют во включённых данных.
- Определи, что именно купил человек: игровую валюту, аккаунт, подписку, Telegram Stars, услугу, цифровой ключ или другой товар.
- Учитывай назначение товара в пожелании. Не желай удачи в игре для подписки, VPN, Telegram Stars или неигрового товара.
- Не выдумывай характеристики, сроки, гарантии, бонусы и действия, которых нет во включённых данных.
- Если покупатель отметил конкретное преимущество, проблему, скорость, качество или удобство - ответь именно на это.
- Если отзыв пустой или состоит только из эмодзи, не придумывай детали: ориентируйся на оценку и товар.
- Если оценка высокая, но в тексте есть жалоба, обязательно признай её. Если оценка низкая, но текст положительный, мягко уточни, что можно улучшить.

СТРОГОЕ СООТВЕТСТВИЕ ПОЛЯМ:
- Используй только сведения, которые реально перечислены в блоке «Включённые продавцом данные».
- Если имя, стоимость, дата покупки, описание или другое поле отсутствует в этом блоке, не упоминай его и не делай вид, что знаешь его.
- Все правила ниже обязательны, если соответствующее значение реально получено из заказа:
{field_usage_instruction}
- Не превращай ответ в чек или перечень характеристик: соединяй обязательные сведения естественно.

ТРЕБОВАНИЯ:
- Пиши полностью и только на языке, указанном в настройке языка ответа, даже если остальные инструкции написаны по-русски. Без Markdown, HTML, списков и фрагментов кода.
- Ответ должен быть законченным, естественным и отличаться от шаблонных ответов.
- Не упоминай нейросеть, промпт, API, внутренние правила или настройки плагина.
- Не обещай возврат, компенсацию или исправление, которое ещё не выполнено.
- Не добавляй техническую подпись самостоятельно: подпись продавца будет добавлена программно после ответа.
- Не добавляй дату ответа или одинаковую финальную фразу ко всем отзывам.
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
CB_DETAILS = f"{UUID}:details"
CB_MODEL = f"{UUID}:model"
CB_MODEL_SELECT = f"{UUID}:model_select"
CB_MODEL_PAGE = f"{UUID}:model_page"
CB_MODEL_AUTO = f"{UUID}:model_auto"
CB_MODEL_REFRESH = f"{UUID}:model_refresh"
CB_LENGTH = f"{UUID}:length"
CB_LENGTH_SELECT = f"{UUID}:length_select"
CB_EMOJI = f"{UUID}:emoji"
CB_STYLE = f"{UUID}:style"
CB_STYLE_SELECT = f"{UUID}:style_select"
CB_SIGNATURE = f"{UUID}:signature"
CB_STATS = f"{UUID}:stats"
CB_LOGS = f"{UUID}:logs"
CB_INFO = f"{UUID}:info"
CB_INSTRUCTION_ACK = f"{UUID}:instruction_ack"
CB_UPDATE = f"{UUID}:update"
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
_model_menu_cache: Dict[int, list[str]] = {}
_models_api_cache: Dict[str, Any] = {"key_hash": "", "time": 0.0, "models": []}
_order_date_cache: Dict[str, str] = {}
_storage_lock = threading.RLock()

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

def load_pending() -> dict:
    with _storage_lock:
        data = _load_json(PENDING_FILE)
    return data if isinstance(data, dict) else {}

def save_pending(data: dict):
    with _storage_lock:
        _save_json(PENDING_FILE, data if isinstance(data, dict) else {})

def _stats_snapshot() -> dict:
    with _storage_lock:
        state = _load_json(STATE_FILE)
        stats = state.get("_stats") if isinstance(state, dict) else None
    base = {
        "sent": 0,
        "errors": 0,
        "queued": 0,
        "retried": 0,
        "retry_failed": 0,
        "by_stars": {str(i): 0 for i in range(1, 6)},
        "last_success_at": 0,
    }
    if isinstance(stats, dict):
        base.update(stats)
        base["by_stars"] = {**base["by_stars"], **(stats.get("by_stars") or {})}
    return base

def _stats_mutate(mutator):
    with _storage_lock:
        state = _load_json(STATE_FILE)
        if not isinstance(state, dict):
            state = {}
        stats = _stats_snapshot()
        mutator(stats)
        state["_stats"] = stats
        _save_json(STATE_FILE, state)

def _stats_record_success(stars: int, retried: bool = False):
    def mutate(stats: dict):
        stats["sent"] = int(stats.get("sent", 0)) + 1
        if retried:
            stats["retried"] = int(stats.get("retried", 0)) + 1
        by_stars = stats.setdefault("by_stars", {})
        key = str(max(1, min(5, int(stars))))
        by_stars[key] = int(by_stars.get(key, 0)) + 1
        stats["last_success_at"] = int(time.time())
    _stats_mutate(mutate)

def _stats_record_error(queued_new: bool = False, retry: bool = False):
    def mutate(stats: dict):
        stats["errors"] = int(stats.get("errors", 0)) + 1
        if queued_new:
            stats["queued"] = int(stats.get("queued", 0)) + 1
        if retry:
            stats["retry_failed"] = int(stats.get("retry_failed", 0)) + 1
    _stats_mutate(mutate)

def _queue_pending_review(order_id: str, stage: str, error: Any, force_notify: bool = False) -> bool:
    order_id = str(order_id or "").lstrip("#").strip()
    if not order_id:
        return False
    now = int(time.time())
    error_text = str(error or "Неизвестная ошибка").strip()[:1200]
    with _storage_lock:
        pending = _load_json(PENDING_FILE)
        if not isinstance(pending, dict):
            pending = {}
        previous = pending.get(order_id) if isinstance(pending.get(order_id), dict) else {}
        is_new = not bool(previous)
        should_notify = (
            force_notify
            or is_new
            or previous.get("error") != error_text
            or previous.get("stage") != stage
            or now - int(previous.get("last_notified_at", 0) or 0) >= 300
        )
        pending[order_id] = {
            "order_id": order_id,
            "stage": stage,
            "error": error_text,
            "created_at": int(previous.get("created_at", now) or now),
            "updated_at": now,
            "attempts": int(previous.get("attempts", 0) or 0) + 1,
            "last_notified_at": now if should_notify else int(previous.get("last_notified_at", 0) or 0),
        }
        _save_json(PENDING_FILE, pending)
    _stats_record_error(queued_new=is_new, retry=force_notify)
    return should_notify

def _remove_pending_review(order_id: str):
    order_id = str(order_id or "").lstrip("#").strip()
    with _storage_lock:
        pending = _load_json(PENDING_FILE)
        if isinstance(pending, dict) and order_id in pending:
            pending.pop(order_id, None)
            _save_json(PENDING_FILE, pending)

def _notify_order_error(cardinal: "Cardinal", order_id: str, stage: str, error: Any):
    message = (
        f"⚠️ {NAME}: ошибка обработки отзыва\n\n"
        f"Заказ: #{order_id}\n"
        f"Этап: {stage}\n"
        f"Ошибка: {str(error)[:900]}\n\n"
        "Заказ сохранён в очередь. После перезапуска Cardinal плагин автоматически попробует оставить ответ ещё раз."
    )
    _notify(cardinal, message)

def _default_config() -> dict:
    return {
        "enabled": False,
        "instruction_acknowledged": False,
        "stars": [5],
        "api_key": "",
        "model": DEFAULT_MODEL,
        "model_auto_fallback": True,
        "model_pool": [],
        "response_length": 500,
        "use_emojis": True,
        "response_style": "adaptive",
        "seller_signature": "",
        "fields": {
            "name": True,
            "item": True,
            "description": True,
            "cost": True,
            "rating": True,
            "text": True,
            "order_time": True,
        }
    }

def _normalize_config(cfg: Optional[dict]) -> dict:
    base = _default_config()
    if isinstance(cfg, dict):
        base.update(cfg)

    base["fields"] = {
        **_default_config()["fields"],
        **((cfg or {}).get("fields") or {}),
    }
    base["model"] = str(base.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    base["model_auto_fallback"] = bool(base.get("model_auto_fallback", True))
    base["use_emojis"] = bool(base.get("use_emojis", True))
    base["seller_signature"] = str(base.get("seller_signature") or "").strip()[:240]

    try:
        length = int(base.get("response_length", 500))
    except (TypeError, ValueError):
        length = 500
    base["response_length"] = min(RESPONSE_LENGTH_OPTIONS, key=lambda value: abs(value - length))

    style = str(base.get("response_style") or "adaptive").strip()
    if style == "concise":
        style = "adaptive"
    base["response_style"] = style if style in RESPONSE_STYLES else "adaptive"

    pool = base.get("model_pool") or []
    base["model_pool"] = list(dict.fromkeys(
        str(item).strip() for item in pool
        if isinstance(item, str) and item.strip()
    ))[:50]

    stars = base.get("stars")
    if not isinstance(stars, list) or not stars:
        base["stars"] = [5]
    else:
        base["stars"] = sorted({
            int(x) for x in stars
            if str(x).isdigit() and 1 <= int(x) <= 5
        }) or [5]

    base.pop("prompt", None)
    return base

def _get_config(data: dict) -> dict:
    if isinstance(data.get("global"), dict):
        return _normalize_config(data["global"])

    for _, value in (data or {}).items():
        if isinstance(value, dict) and ("api_key" in value or "enabled" in value or "stars" in value):
            base = _normalize_config(value)
            data["global"] = base
            save_data(data)
            return base

    data["global"] = _default_config()
    save_data(data)
    return data["global"]

def _set_config(cfg: dict):
    data = load_data()
    cfg.pop("prompt", None)
    data["global"] = cfg
    save_data(data)

def _mask_key(value: str) -> str:
    key = str(value or "").strip()
    if not key:
        return "-"
    if len(key) <= 10:
        return "********"
    return f"{key[:6]}…{key[-4:]}"

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

def _get_api_key(cfg: dict) -> str:
    k = (cfg.get("api_key") or "").strip()
    if k:
        return k
    return (IO_API_KEY_ENV or "").strip()

def _welcome_text(cfg: dict) -> str:
    _restore_branding()
    return (
        f"🧩 <b>Плагин:</b> <b>{NAME}</b>\n"
        f"📦 <b>Версия:</b> <code>{VERSION}</code>\n"
        f'👤 <b>Создатель:</b> <a href="{escape(CREATOR_URL, quote=True)}">'
        f"{escape(CREDITS)}</a>\n\n"
        "Выбери действие:"
    )

def _welcome_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("⚙️ Настройки", callback_data=CB_SETTINGS),
        InlineKeyboardButton("ℹ️ Информация", callback_data=CB_INFO),
    )
    kb.row(
        InlineKeyboardButton("🔄 Обновить", callback_data=CB_UPDATE),
        InlineKeyboardButton("🗑 Удалить плагин", callback_data=CB_DELETE),
    )
    kb.row(
        InlineKeyboardButton("🔙 К списку плагинов", callback_data=CBT_PLUGINS_LIST_OPEN)
    )
    return kb

def _first_settings_notice_text() -> str:
    return (
        "⚠️ <b>Перед использованием плагина</b>\n\n"
        "Сначала обязательно прочитайте инструкцию. В ней описаны настройка API-ключа, "
        "выбор оценок и полей, а также правильный запуск плагина.\n\n"
        "После ознакомления нажмите кнопку <b>«Прочитал»</b>."
    )

def _first_settings_notice_kb() -> InlineKeyboardMarkup:
    _restore_branding()
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("📖 Открыть инструкцию", url=INSTRUCTION_URL))
    kb.row(InlineKeyboardButton("✅ Прочитал", callback_data=CB_INSTRUCTION_ACK))
    kb.row(InlineKeyboardButton("◀️ Назад", callback_data=CB_WELCOME))
    return kb

def _info_text() -> str:
    return (
        "ℹ️ <b>Информация</b>\n\n"
        "Здесь находятся официальные ссылки GPT Feedback.\n\n"
        "• <b>Чат</b> - помощь и общение.\n"
        "• <b>Канал</b> - новости и обновления.\n"
        "• <b>Инструкция</b> - настройка и использование плагина.\n"
        "• <b>Мой Telegram</b> - связь с автором. (100 звёзд за сообщение)"
    )

def _info_kb() -> InlineKeyboardMarkup:
    _restore_branding()
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("💬 Чат", url=GROUP_URL),
        InlineKeyboardButton("📢 Канал", url=CHANNEL_URL),
    )
    kb.row(InlineKeyboardButton("📖 Инструкция", url=INSTRUCTION_URL))
    kb.row(InlineKeyboardButton("👤 Мой Telegram", url=CREATOR_URL))
    kb.row(InlineKeyboardButton("◀️ Назад", callback_data=CB_WELCOME))
    return kb

def open_information(cardinal: "Cardinal", call):
    _restore_branding()
    bot = cardinal.telegram.bot
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    _safe_edit(bot, call.message.chat.id, call.message.id, _info_text(), _info_kb())

def _acknowledge_instruction(cardinal: "Cardinal", call):
    cfg = _get_config(load_data())
    cfg["instruction_acknowledged"] = True
    _set_config(cfg)
    open_settings(cardinal, call)

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
    key = _get_api_key(cfg)
    key_state = "✅ задан" if key else "❌ не задан"
    return (
        "⚙️ <b>Настройки</b>\n\n"
        f"Статус: {'✅ ВКЛ' if cfg.get('enabled') else '❌ ВЫКЛ'}\n"
        f"Звёзды: {', '.join(map(str, stars))}\n"
        f"API ключ: <b>{key_state}</b> (<code>{_mask_key(key)}</code>)\n\n"
        "Настрой параметры ниже:"
    )

def _settings_kb(cfg: Optional[dict] = None) -> InlineKeyboardMarkup:
    cfg = cfg or _get_config(load_data())
    enabled = bool(cfg.get("enabled"))

    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton(
            "✅ Плагин включён" if enabled else "❌ Плагин выключен",
            callback_data=CB_TOGGLE,
        )
    )
    kb.row(InlineKeyboardButton("⭐ Настройка отзывов", callback_data=CB_STARS))
    kb.row(InlineKeyboardButton("🔑 API ключ", callback_data=CB_APIKEY))
    kb.row(InlineKeyboardButton("🛠 Детальные настройки", callback_data=CB_DETAILS))
    kb.row(InlineKeyboardButton("📊 Статистика", callback_data=CB_STATS))
    kb.row(InlineKeyboardButton("📜 Логи плагина", callback_data=CB_LOGS))
    kb.row(InlineKeyboardButton("🧪 Тест API", callback_data=CB_TEST))
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

    if not cfg.get("instruction_acknowledged", False):
        _safe_edit(
            bot,
            chat_id,
            msg_id,
            _first_settings_notice_text(),
            _first_settings_notice_kb(),
        )
        return

    _safe_edit(bot, chat_id, msg_id, _settings_text(cfg), _settings_kb(cfg))

def _style_label(style_key: str) -> str:
    return RESPONSE_STYLES.get(style_key, RESPONSE_STYLES["adaptive"])[0]

def _response_limit(cfg: Optional[dict]) -> int:
    try:
        value = int((cfg or {}).get("response_length", 500))
    except (TypeError, ValueError):
        value = 500
    return max(100, min(MAX_CHARACTERS, value))

def _response_range(cfg: Optional[dict]) -> tuple[int, int]:
    maximum = _response_limit(cfg)
    minimum = maximum - 100
    return minimum, maximum

def _details_text(cfg: dict) -> str:
    fields = cfg.get("fields") or {}
    enabled_fields = sum(1 for value in fields.values() if value)
    model = escape(str(cfg.get("model") or DEFAULT_MODEL))
    fallback = "✅ включён" if cfg.get("model_auto_fallback", True) else "❌ выключен"
    emojis = "✅ добавляются" if cfg.get("use_emojis", True) else "❌ запрещены"
    style = escape(_style_label(str(cfg.get("response_style") or "adaptive")))
    signature = str(cfg.get("seller_signature") or "").strip()
    signature_preview = escape(signature[:45] + ("…" if len(signature) > 45 else "")) if signature else "не задана"
    return (
        "🛠 <b>Детальные настройки</b>\n\n"
        f"Модель: <code>{model}</code>\n"
        f"Автопереход на резервную модель: <b>{fallback}</b>\n"
        f"Длина ответа: <b>{_response_range(cfg)[0]}–{_response_range(cfg)[1]} символов</b>\n"
        f"Эмодзи: <b>{emojis}</b>\n"
        f"Стиль: <b>{style}</b>\n"
        f"Язык: <b>автоматически по отзыву</b>\n"
        f"Подпись: <b>{signature_preview}</b>\n"
        f"Активных полей: <b>{enabled_fields}/{len(fields)}</b>\n\n"
        "Подпись добавляется отдельной строкой снизу и входит в выбранный лимит символов."
    )

def _details_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("🤖 Модель и автопереход", callback_data=CB_MODEL))
    kb.row(InlineKeyboardButton("📏 Длина ответа", callback_data=CB_LENGTH))
    kb.row(InlineKeyboardButton("😊 Эмодзи в ответах", callback_data=CB_EMOJI))
    kb.row(InlineKeyboardButton("🎨 Стиль сообщений", callback_data=CB_STYLE))
    kb.row(InlineKeyboardButton("✍️ Подпись продавца", callback_data=CB_SIGNATURE))
    kb.row(InlineKeyboardButton("🧾 Поля заказа и отзыва", callback_data=CB_FIELDS))
    kb.row(InlineKeyboardButton("◀️ Назад", callback_data=CB_SETTINGS))
    return kb

def _details_open(cardinal: "Cardinal", call):
    bot = cardinal.telegram.bot
    cfg = _get_config(load_data())
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    _safe_edit(bot, call.message.chat.id, call.message.id, _details_text(cfg), _details_kb())

def _length_text(cfg: dict) -> str:
    minimum, maximum = _response_range(cfg)
    return (
        "📏 <b>Длина ответа</b>\n\n"
        f"Сейчас выбрано: <b>от {minimum} до {maximum} символов</b>.\n"
        "Это строгий диапазон вместе с подписью продавца: слишком короткий ответ "
        "будет автоматически перегенерирован, а слишком длинный - аккуратно сокращён."
    )

def _length_kb(cfg: dict) -> InlineKeyboardMarkup:
    current = _response_limit(cfg)
    kb = InlineKeyboardMarkup()
    values = list(RESPONSE_LENGTH_OPTIONS)
    for index in range(0, len(values), 3):
        buttons = []
        for value in values[index:index + 3]:
            marker = "✅ " if value == current else ""
            minimum = value - 100
            buttons.append(InlineKeyboardButton(
                f"{marker}{minimum}–{value}",
                callback_data=f"{CB_LENGTH_SELECT}:{value}",
            ))
        kb.row(*buttons)
    kb.row(InlineKeyboardButton("◀️ Назад", callback_data=CB_DETAILS))
    return kb

def _length_open(cardinal: "Cardinal", call):
    bot = cardinal.telegram.bot
    cfg = _get_config(load_data())
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    _safe_edit(bot, call.message.chat.id, call.message.id, _length_text(cfg), _length_kb(cfg))

def _length_select(cardinal: "Cardinal", call, value: int):
    bot = cardinal.telegram.bot
    if value not in RESPONSE_LENGTH_OPTIONS:
        try:
            bot.answer_callback_query(call.id, "Недопустимая длина", show_alert=True)
        except Exception:
            pass
        return
    cfg = _get_config(load_data())
    cfg["response_length"] = value
    _set_config(cfg)
    try:
        bot.answer_callback_query(call.id, f"Длина: {value - 100}–{value} символов")
    except Exception:
        pass
    _safe_edit(bot, call.message.chat.id, call.message.id, _length_text(cfg), _length_kb(cfg))

def _emoji_toggle(cardinal: "Cardinal", call):
    bot = cardinal.telegram.bot
    cfg = _get_config(load_data())
    cfg["use_emojis"] = not bool(cfg.get("use_emojis", True))
    _set_config(cfg)
    state = "включены" if cfg["use_emojis"] else "выключены"
    try:
        bot.answer_callback_query(call.id, f"Эмодзи {state}")
    except Exception:
        pass
    _safe_edit(bot, call.message.chat.id, call.message.id, _details_text(cfg), _details_kb())

def _style_text(cfg: dict) -> str:
    current = str(cfg.get("response_style") or "adaptive")
    label, instruction = RESPONSE_STYLES.get(current, RESPONSE_STYLES["adaptive"])
    return (
        "🎨 <b>Стиль сообщений</b>\n\n"
        f"Текущий стиль: <b>{escape(label)}</b>\n\n"
        f"{escape(instruction)}"
    )

def _style_kb(cfg: dict) -> InlineKeyboardMarkup:
    current = str(cfg.get("response_style") or "adaptive")
    kb = InlineKeyboardMarkup()
    for key, (label, _) in RESPONSE_STYLES.items():
        marker = "✅ " if key == current else ""
        kb.row(InlineKeyboardButton(
            marker + label,
            callback_data=f"{CB_STYLE_SELECT}:{key}",
        ))
    kb.row(InlineKeyboardButton("◀️ Назад", callback_data=CB_DETAILS))
    return kb

def _style_open(cardinal: "Cardinal", call):
    bot = cardinal.telegram.bot
    cfg = _get_config(load_data())
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    _safe_edit(bot, call.message.chat.id, call.message.id, _style_text(cfg), _style_kb(cfg))

def _style_select(cardinal: "Cardinal", call, style_key: str):
    bot = cardinal.telegram.bot
    if style_key not in RESPONSE_STYLES:
        try:
            bot.answer_callback_query(call.id, "Неизвестный стиль", show_alert=True)
        except Exception:
            pass
        return
    cfg = _get_config(load_data())
    cfg["response_style"] = style_key
    _set_config(cfg)
    try:
        bot.answer_callback_query(call.id, f"Выбран стиль: {_style_label(style_key)}")
    except Exception:
        pass
    _safe_edit(bot, call.message.chat.id, call.message.id, _style_text(cfg), _style_kb(cfg))

def _signature_text(cfg: dict) -> str:
    signature = str(cfg.get("seller_signature") or "").strip()
    current = escape(signature) if signature else "не задана"
    return (
        "✍️ <b>Подпись продавца</b>\n\n"
        f"Текущая подпись:\n<code>{current}</code>\n\n"
        "Отправь новую подпись одним сообщением. Она будет добавляться отдельной строкой внизу каждого ответа.\n"
        "Чтобы убрать подпись, отправь один символ <code>-</code>. Максимум 240 символов."
    )

def _signature_start(cardinal: "Cardinal", call):
    bot = cardinal.telegram.bot
    chat_id = call.message.chat.id
    cfg = _get_config(load_data())
    _fsm[chat_id] = {
        "mode": "signature",
        "panel_chat_id": chat_id,
        "panel_msg_id": call.message.id,
    }
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    _safe_edit(bot, chat_id, call.message.id, _signature_text(cfg), _input_kb(CB_DETAILS))

def _statistics_text() -> str:
    stats = _stats_snapshot()
    pending_count = len(load_pending())
    by_stars = stats.get("by_stars") or {}
    last_at = int(stats.get("last_success_at", 0) or 0)
    last_text = datetime.fromtimestamp(last_at).strftime("%d.%m.%Y %H:%M") if last_at else "-"
    return (
        "📊 <b>Статистика GPT Feedback</b>\n\n"
        f"Успешно опубликовано: <b>{int(stats.get('sent', 0))}</b>\n"
        f"Ошибок обработки: <b>{int(stats.get('errors', 0))}</b>\n"
        f"Добавлено в очередь: <b>{int(stats.get('queued', 0))}</b>\n"
        f"Успешно после перезапуска: <b>{int(stats.get('retried', 0))}</b>\n"
        f"Неудачных повторов: <b>{int(stats.get('retry_failed', 0))}</b>\n"
        f"Сейчас ожидают повтора: <b>{pending_count}</b>\n"
        f"Последний успешный ответ: <b>{last_text}</b>\n\n"
        "По оценкам:\n"
        f"1⭐ - {int(by_stars.get('1', 0))} | 2⭐ - {int(by_stars.get('2', 0))} | 3⭐ - {int(by_stars.get('3', 0))}\n"
        f"4⭐ - {int(by_stars.get('4', 0))} | 5⭐ - {int(by_stars.get('5', 0))}"
    )

def _statistics_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("🔄 Обновить", callback_data=CB_STATS))
    kb.row(InlineKeyboardButton("◀️ Назад", callback_data=CB_SETTINGS))
    return kb

def _statistics_open(cardinal: "Cardinal", call):
    bot = cardinal.telegram.bot
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    _safe_edit(bot, call.message.chat.id, call.message.id, _statistics_text(), _statistics_kb())

def _model_is_text_candidate(model_id: str) -> bool:
    value = str(model_id or "").strip()
    low = value.lower()
    if not value:
        return False
    blocked = (
        "embedding", "rerank", "whisper", "speech", "audio", "tts",
        "image", "stable-diffusion", "flux", "moderation",
    )
    return not any(word in low for word in blocked)

def _model_sort_key(model_id: str) -> tuple[int, str]:
    low = model_id.lower()
    score = 0
    if "instruct" in low:
        score += 40
    if "chat" in low:
        score += 30
    if any(name in low for name in ("llama", "qwen", "mistral", "deepseek")):
        score += 20
    if "vision" in low:
        score -= 10
    if "coder" in low:
        score -= 5
    return (-score, low)

def _fetch_available_models(api_key: str, force: bool = False) -> list[str]:
    if not api_key:
        return []

    key_hash = hashlib.sha256(api_key.encode("utf-8", errors="ignore")).hexdigest()[:16]
    now = time.time()
    if (
        not force
        and _models_api_cache.get("key_hash") == key_hash
        and now - float(_models_api_cache.get("time") or 0) < MODEL_CACHE_TTL
    ):
        return list(_models_api_cache.get("models") or [])

    try:
        response = requests.get(
            IO_MODELS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            timeout=IO_MODELS_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") if isinstance(payload, dict) else payload
        models = []
        for row in rows or []:
            model_id = row.get("id") if isinstance(row, dict) else row
            model_id = str(model_id or "").strip()
            if _model_is_text_candidate(model_id):
                models.append(model_id)
        models = sorted(dict.fromkeys(models), key=_model_sort_key)
        _models_api_cache.update({"key_hash": key_hash, "time": now, "models": models})
        return models
    except Exception as error:
        logw(f"Не удалось получить список моделей: {error}")
        return list(_models_api_cache.get("models") or [])

def _remember_model_pool(cfg: dict, models: list[str]):
    clean = [m for m in dict.fromkeys(models) if _model_is_text_candidate(m)]
    if clean and clean != (cfg.get("model_pool") or []):
        cfg["model_pool"] = clean[:50]
        _set_config(cfg)

def _model_list_for_chat(cfg: dict, api_key: str, force: bool = False) -> list[str]:
    current = str(cfg.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    fetched = _fetch_available_models(api_key, force=force)
    if fetched:
        _remember_model_pool(cfg, fetched)
    pool = fetched or list(cfg.get("model_pool") or [])
    return list(dict.fromkeys([current, *pool]))

def _short_model_name(model_id: str, limit: int = 42) -> str:
    value = str(model_id or "")
    return value if len(value) <= limit else "…" + value[-(limit - 1):]

def _model_text(cfg: dict, models: list[str]) -> str:
    current = escape(str(cfg.get("model") or DEFAULT_MODEL))
    auto = "✅ включён" if cfg.get("model_auto_fallback", True) else "❌ выключен"
    return (
        "🤖 <b>Модель ИИ</b>\n\n"
        f"Текущая модель: <code>{current}</code>\n"
        f"Автопереход: <b>{auto}</b>\n"
        f"Доступно моделей: <b>{len(models)}</b>\n\n"
        "При исчерпании лимита, перегрузке или недоступности текущей модели "
        "плагин попробует другую модель из списка и сохранит её как основную."
    )

def _model_kb(cfg: dict, models: list[str], page: int = 0) -> InlineKeyboardMarkup:
    page_size = 7
    pages = max(1, (len(models) + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    start = page * page_size
    current = str(cfg.get("model") or DEFAULT_MODEL)

    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton(
            "✅ Автопереход включён" if cfg.get("model_auto_fallback", True)
            else "❌ Автопереход выключен",
            callback_data=CB_MODEL_AUTO,
        )
    )
    for index in range(start, min(start + page_size, len(models))):
        model_id = models[index]
        marker = "✅ " if model_id == current else "▫️ "
        kb.row(InlineKeyboardButton(
            marker + _short_model_name(model_id),
            callback_data=f"{CB_MODEL_SELECT}:{index}",
        ))

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️", callback_data=f"{CB_MODEL_PAGE}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data=f"{CB_MODEL_PAGE}:{page}"))
        if page + 1 < pages:
            nav.append(InlineKeyboardButton("➡️", callback_data=f"{CB_MODEL_PAGE}:{page + 1}"))
        kb.row(*nav)

    kb.row(InlineKeyboardButton("🔄 Обновить список API", callback_data=CB_MODEL_REFRESH))
    kb.row(InlineKeyboardButton("◀️ Назад", callback_data=CB_DETAILS))
    return kb

def _model_open(cardinal: "Cardinal", call, page: int = 0, force: bool = False):
    bot = cardinal.telegram.bot
    chat_id = call.message.chat.id
    cfg = _get_config(load_data())
    api_key = _get_api_key(cfg)

    try:
        bot.answer_callback_query(call.id, "Загружаю модели…" if force else None)
    except Exception:
        pass

    models = _model_list_for_chat(cfg, api_key, force=force)
    _model_menu_cache[chat_id] = models
    _safe_edit(bot, chat_id, call.message.id, _model_text(cfg, models), _model_kb(cfg, models, page))

_p = (
    "QHRpbmVjaGVsb3ZlYw==",
    "aHR0cHM6Ly90Lm1lL3RpbmVjaGVsb3ZlYw==",
    "aHR0cHM6Ly90Lm1lL2Rldl90aGNfY2hhdA==",
    "aHR0cHM6Ly90Lm1lL2J5X3RoYw==",
    "aHR0cHM6Ly90ZWxldHlwZS5pbi9AdGluZWNoZWxvdmVjL0dQVC1GZWVkYmFjaw==",
    "aHR0cHM6Ly9naXRodWIuY29tL3RpbmVjaGVsb3ZlYy9GUEMtUGx1Z2luLUdQVC1GZWVkYmFjaw==",
    "aHR0cHM6Ly9yYXcuZ2l0aHVidXNlcmNvbnRlbnQuY29tL3RpbmVjaGVsb3ZlYy9GUEMtUGx1Z2luLUdQVC1GZWVkYmFjay9tYWluL0dQVCUyMEZlZWRiYWNrL0dQVCUyMEZlZWRiYWNrLnB5",
)

def _model_select(cardinal: "Cardinal", call, index: int):
    bot = cardinal.telegram.bot
    chat_id = call.message.chat.id
    cfg = _get_config(load_data())
    models = _model_menu_cache.get(chat_id) or _model_list_for_chat(cfg, _get_api_key(cfg))
    if index < 0 or index >= len(models):
        try:
            bot.answer_callback_query(call.id, "Список моделей устарел. Обнови его.", show_alert=True)
        except Exception:
            pass
        return

    cfg["model"] = models[index]
    _set_config(cfg)
    try:
        bot.answer_callback_query(call.id, "Модель сохранена")
    except Exception:
        pass
    _safe_edit(bot, chat_id, call.message.id, _model_text(cfg, models), _model_kb(cfg, models, index // 7))

def _model_auto_toggle(cardinal: "Cardinal", call):
    cfg = _get_config(load_data())
    cfg["model_auto_fallback"] = not bool(cfg.get("model_auto_fallback", True))
    _set_config(cfg)
    _model_open(cardinal, call)

def _read_plugin_logs(max_lines: int = 35, max_chars: int = 3200) -> str:
    try:
        if not os.path.exists(LOG_FILE):
            return "Лог-файл пока не создан."
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as file:
            lines = file.readlines()
        if not lines:
            return "Лог-файл пока пуст."
        result = "".join(lines[-max_lines:]).strip()
        if len(result) > max_chars:
            result = "…" + result[-max_chars:]
        return result
    except Exception as error:
        return f"Не удалось прочитать логи: {error}"

def _prepare_logs_export() -> str:
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as source:
                content = source.read()
        else:
            content = "Лог-файл пока не создан.\n"
        with open(LOG_EXPORT_FILE, "w", encoding="utf-8", newline="\n") as target:
            target.write(content)
        return LOG_EXPORT_FILE
    except Exception as error:
        loge(f"Не удалось подготовить TXT логов: {error}")
        return ""

def _logs_text() -> str:
    logs = escape(_read_plugin_logs())
    return (
        "📜 <b>Логи плагина</b>\n\n"
        "Ниже показаны последние записи. Полный лог отправлен отдельным TXT-файлом.\n\n"
        f"<pre>{logs}</pre>"
    )

def _logs_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("🔄 Обновить и отправить TXT", callback_data=CB_LOGS))
    kb.row(InlineKeyboardButton("◀️ Назад", callback_data=CB_SETTINGS))
    return kb

def _logs_open(cardinal: "Cardinal", call):
    bot = cardinal.telegram.bot
    chat_id = call.message.chat.id
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    _safe_edit(bot, chat_id, call.message.id, _logs_text(), _logs_kb())

    export_path = _prepare_logs_export()
    if not export_path:
        return
    try:
        with open(export_path, "rb") as document:
            bot.send_document(
                chat_id,
                document,
                caption="📄 Полный лог GPT Feedback",
            )
    except Exception as error:
        loge(f"Не удалось отправить TXT логов: {error}")

def _fields_text(cfg: dict) -> str:
    f = cfg.get("fields") or {}
    def line(key: str, title: str) -> str:
        return f"{'✅' if f.get(key) else '❌'} {title}"
    return (
        "🧾 <b>Какие данные передавать модели</b>\n\n"
        f"{line('name', 'Имя покупателя')}\n"
        f"{line('item', 'Название товара')}\n"
        f"{line('description', 'Описание товара')}\n"
        f"{line('cost', 'Стоимость')}\n"
        f"{line('rating', 'Оценка - можно упоминать в тексте')}\n"
        f"{line('text', 'Текст отзыва')}\n"
        f"{line('order_time', 'Дата и время покупки')}\n\n"
        "Оценка всегда используется служебно для выбора правильной реакции. "
        "Если поле «Оценка» выключено, бот просто не будет писать её число в ответе."
    )

def _fields_kb(cfg: dict) -> InlineKeyboardMarkup:
    f = cfg.get("fields") or {}
    def btn(key: str, title: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            f"{'✅' if f.get(key) else '❌'} {title}",
            callback_data=f"{CB_FIELD_TOGGLE}:{key}",
        )
    kb = InlineKeyboardMarkup()
    kb.row(btn("name", "Имя"), btn("item", "Название"))
    kb.row(btn("description", "Описание"), btn("cost", "Стоимость"))
    kb.row(btn("rating", "Оценка"), btn("text", "Отзыв"))
    kb.row(btn("order_time", "Дата покупки"))
    kb.row(InlineKeyboardButton("◀️ Назад", callback_data=CB_DETAILS))
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
    key = _get_api_key(cfg)
    masked = _mask_key(key)
    return (
        "🔑 <b>API ключ IO Intelligence</b>\n\n"
        f"Текущий: <code>{masked}</code>\n\n"
        "Теперь отправь новый API-ключ <b>одним сообщением</b> в чат.\n"
        "Если оставить пустым - будет использован ключ из переменных окружения.\n"
        "Чтобы отменить - нажми ❌ Отменить."
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
            for k in ("api_key", "token", "key", "apikey", "io_api_key"):
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

    if mode == "signature":
        cfg["seller_signature"] = "" if text == "-" else text[:240]
        _set_config(cfg)
        _fsm.pop(chat_id, None)
        panel_msg_id = st.get("panel_msg_id")
        if panel_msg_id:
            _safe_edit(bot, chat_id, panel_msg_id, _details_text(cfg), _details_kb())
        return

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
            _safe_edit(bot, chat_id, panel_msg_id, _settings_text(cfg), _settings_kb(cfg))
        return

class _SafeDict(dict):
    def __missing__(self, key):
        return ""

def _hash_review(stars: Optional[int], text: Optional[str]) -> str:
    s = f"{stars or ''}|{(text or '').strip()}"
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()

def _first_nonempty_attr(obj, names) -> Any:
    if obj is None:
        return None
    for name in names:
        try:
            value = getattr(obj, name, None)
        except Exception:
            value = None
        if value is not None and str(value).strip():
            return value
    return None

def _format_order_datetime(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    if isinstance(value, (int, float)):
        try:
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y %H:%M")
        except Exception:
            return str(value).strip()
    return str(value).strip()

def _resolve_order_datetime(cardinal: "Cardinal", order, order_id: str) -> str:
    normalized_id = str(order_id or getattr(order, "id", "") or "").lstrip("#").strip()
    if not normalized_id:
        return ""

    cached = _order_date_cache.get(normalized_id)
    if cached:
        return cached

    direct_value = _first_nonempty_attr(order, (
        "_gpt_feedback_order_time", "date", "created_at", "date_created",
        "created", "order_date", "purchase_date", "timestamp",
    ))
    direct_result = _format_order_datetime(direct_value)
    if direct_result:
        _order_date_cache[normalized_id] = direct_result
        return direct_result

    account = getattr(cardinal, "account", None)
    get_sells = getattr(account, "get_sells", None)
    if not callable(get_sells):
        return ""

    shortcuts = []
    try:
        result = get_sells(id=normalized_id)
        if isinstance(result, tuple) and len(result) >= 2:
            shortcuts = list(result[1] or [])
    except Exception as error:
        logw(f"Не удалось получить дату заказа #{normalized_id} через get_sells(id=...): {error}")

    if not shortcuts:
        buyer_username = str(getattr(order, "buyer_username", "") or "").strip()
        if buyer_username:
            try:
                result = get_sells(buyer=buyer_username)
                if isinstance(result, tuple) and len(result) >= 2:
                    shortcuts = list(result[1] or [])
            except Exception as error:
                logw(f"Не удалось получить дату заказа #{normalized_id} через get_sells(buyer=...): {error}")

    for shortcut in shortcuts:
        shortcut_id = str(getattr(shortcut, "id", "") or "").lstrip("#").strip()
        if shortcut_id != normalized_id:
            continue
        value = _first_nonempty_attr(shortcut, ("date", "created_at", "timestamp"))
        formatted = _format_order_datetime(value)
        if formatted:
            _order_date_cache[normalized_id] = formatted
            try:
                setattr(order, "_gpt_feedback_order_time", formatted)
            except Exception:
                pass
            logi(f"Дата заказа #{normalized_id} получена: {formatted}")
            return formatted

    logw(f"Дата заказа #{normalized_id} не найдена в списке продаж")
    return ""

def _extract_order_fields(order) -> Dict[str, str]:
    review = getattr(order, "review", None)
    lot = getattr(order, "lot", None)
    offer = getattr(order, "offer", None)

    name = _first_nonempty_attr(order, ("buyer_username", "buyer", "customer_username"))
    if not name:
        name = _first_nonempty_attr(review, ("author", "username"))

    item = _first_nonempty_attr(order, (
        "title", "lot_title", "offer_title", "item_title", "description_title",
    ))
    if not item:
        item = _first_nonempty_attr(lot, ("title", "name")) or _first_nonempty_attr(offer, ("title", "name"))

    description = _first_nonempty_attr(order, (
        "description", "lot_description", "offer_description", "short_description",
        "full_description", "item_description",
    ))
    if not description:
        description = (
            _first_nonempty_attr(lot, ("description", "short_description"))
            or _first_nonempty_attr(offer, ("description", "short_description"))
        )

    cost = _first_nonempty_attr(order, ("sum", "price", "amount", "total", "cost"))
    currency = _first_nonempty_attr(order, ("currency", "currency_code"))
    if cost is not None and currency and str(currency).casefold() not in str(cost).casefold():
        cost = f"{cost} {currency}"

    rating = _first_nonempty_attr(review, ("stars", "rating"))
    text_value = _first_nonempty_attr(review, ("text", "message", "comment"))

    order_time_value = _first_nonempty_attr(order, (
        "_gpt_feedback_order_time", "date", "created_at", "date_created", "created",
        "order_date", "purchase_date", "timestamp",
    ))
    order_time = _format_order_datetime(order_time_value)

    return {
        "name": str(name or "").strip(),
        "item": str(item or "").strip(),
        "description": str(description or "").strip()[:1600],
        "cost": str(cost or "").strip(),
        "rating": str(rating or "").strip(),
        "text": str(text_value or "").strip(),
        "order_time": order_time,
    }

def _build_info_block(cfg: dict, order) -> str:
    fields = cfg.get("fields") or {}
    values = _extract_order_fields(order)
    lines = []

    mapping = (
        ("name", "Имя покупателя"),
        ("item", "Название товара"),
        ("description", "Описание товара"),
        ("cost", "Стоимость"),
        ("rating", "Оценка"),
        ("text", "Текст отзыва"),
        ("order_time", "Дата и время покупки"),
    )
    for key, title in mapping:
        if not fields.get(key):
            continue
        value = values.get(key, "")
        if key == "rating" and value:
            value = f"{value} из 5"
        if not value:
            value = "не указано в данных заказа"
        lines.append(f"- {title}: {value}")

    return "\n".join(lines) if lines else "- Продавец отключил все дополнительные поля."

def _rating_instruction(stars: int) -> str:
    if stars <= 1:
        return (
            "Оценка крайне низкая. Коротко извинись за неприятный опыт, признай конкретную проблему "
            "из текста и предложи покупателю написать продавцу, чтобы спокойно разобраться. "
            "Не изображай радость и не благодари за одну звезду как за подарок."
        )
    if stars == 2:
        return (
            "Покупатель в основном недоволен. Поблагодари за честную обратную связь, спокойно признай "
            "недочёт и покажи готовность разобраться. Не используй чрезмерно радостный тон."
        )
    if stars == 3:
        return (
            "Опыт покупателя средний. Ответь сбалансированно: отметь полезную обратную связь, конкретный "
            "минус или пожелание и покажи стремление стать лучше."
        )
    if stars == 4:
        return (
            "Покупатель скорее доволен. Тепло поблагодари, отреагируй на конкретную похвалу или замечание, "
            "не называй результат идеальным и мягко покажи желание заслужить максимальную оценку."
        )
    return (
        "Это максимальная оценка. Ответь заметно теплее и энергичнее, искренне поблагодари, подчеркни "
        "конкретную деталь из отзыва или назначение товара и пригласи обратиться снова."
    )

def _emoji_instruction(cfg: dict, stars: int) -> str:
    if not cfg.get("use_emojis", True):
        return "Не используй ни одного эмодзи."
    if stars <= 2:
        return "Допустим максимум один сдержанный эмодзи, только если он действительно уместен."
    if stars == 3:
        return "Используй 0–2 уместных эмодзи."
    if stars == 4:
        return "Используй 1–3 уместных эмодзи."
    return "Используй 2–5 уместных эмодзи, но без спама."

def _strip_emojis(text_value: str) -> str:
    emoji_pattern = re.compile(
        "["
        "\\U0001F1E0-\\U0001F1FF"
        "\\U0001F300-\\U0001FAFF"
        "\\U00002600-\\U000027BF"
        "\\U0000FE0F"
        "\\U0000200D"
        "]+",
        flags=re.UNICODE,
    )
    cleaned = emoji_pattern.sub("", str(text_value or ""))
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    return cleaned.strip()

def _field_usage_instruction(cfg: dict, values: Dict[str, str]) -> str:
    fields = cfg.get("fields") or {}
    rules = []
    if fields.get("name") and values.get("name"):
        rules.append("обратись к покупателю по имени или нику один раз")
    if fields.get("item") and values.get("item"):
        rules.append("естественно упомяни название товара или его понятное сокращение")
    if fields.get("description") and values.get("description"):
        rules.append("используй описание для понимания назначения товара и отрази одну уместную деталь, не копируя описание целиком")
    if fields.get("cost") and values.get("cost"):
        rules.append("один раз естественно упомяни стоимость, не превращая ответ в чек")
    if fields.get("rating") and values.get("rating"):
        rules.append("можно один раз упомянуть поставленную оценку или количество звёзд")
    if fields.get("text") and values.get("text"):
        rules.append("обязательно ответь на главный смысл и конкретные слова отзыва")
    if fields.get("order_time") and values.get("order_time"):
        rules.append("ОБЯЗАТЕЛЬНО назови дату покупки; точное время тоже укажи, если оно получено из заказа")
    if not rules:
        return "- Дополнительных обязательных упоминаний нет."
    return "\n".join(f"- {rule}." for rule in rules)

def _style_system_prompt(cfg: Optional[dict]) -> str:
    style_key = str((cfg or {}).get("response_style") or "adaptive")
    return STYLE_SYSTEM_PROMPTS.get(style_key, STYLE_SYSTEM_PROMPTS["adaptive"])

def _style_temperature(cfg: Optional[dict]) -> float:
    style_key = str((cfg or {}).get("response_style") or "adaptive")
    return STYLE_TEMPERATURES.get(style_key, STYLE_TEMPERATURES["adaptive"])

def _detect_review_language(review_text: str) -> tuple[str, str]:
    raw = str(review_text or "").strip()
    if not re.search(r"[A-Za-zА-Яа-яЁёІіЇїЄєҐґӘәӨөҮүҰұҚқҒғҢңҺһ一-龯ぁ-ゟ゠-ヿ가-힣\u0600-\u06FF]", raw):
        return "ru", "Russian"

    if re.search(r"[ぁ-ゟ゠-ヿ]", raw):
        return "ja", "Japanese"
    if re.search(r"[가-힣]", raw):
        return "ko", "Korean"
    if re.search(r"[一-龯]", raw):
        return "zh", "Chinese"
    if re.search(r"[\u0600-\u06FF]", raw):
        return "ar", "Arabic"

    low = raw.casefold()
    words = re.findall(r"[a-zà-ÿа-яёіїєґәөүұқғңһ]+", low)
    word_set = set(words)

    if re.search(r"[іїєґ]", low) or word_set & {"дякую", "дуже", "товар", "чудовий", "швидко", "продавець"}:
        return "uk", "Ukrainian"
    if re.search(r"[әөүұқғңһ]", low) or word_set & {"рахмет", "жақсы", "тауар", "керемет", "сатушы"}:
        return "kk", "Kazakh"
    if re.search(r"[а-яё]", low):
        return "ru", "Russian"

    vocabularies = {
        "en": {"hi", "hello", "thanks", "thank", "great", "good", "product", "very", "such", "quick", "fast", "perfect", "excellent", "everything", "seller", "delivery", "love", "nice"},
        "de": {"hallo", "danke", "sehr", "gut", "produkt", "schnell", "perfekt", "alles", "verkäufer", "lieferung", "toll"},
        "fr": {"bonjour", "merci", "très", "bon", "produit", "rapide", "parfait", "vendeur", "livraison", "excellent"},
        "es": {"hola", "gracias", "muy", "bueno", "producto", "rápido", "perfecto", "vendedor", "entrega", "excelente"},
        "it": {"ciao", "grazie", "molto", "buono", "prodotto", "veloce", "perfetto", "venditore", "consegna", "ottimo"},
        "pt": {"olá", "obrigado", "obrigada", "muito", "bom", "produto", "rápido", "perfeito", "vendedor", "entrega", "ótimo"},
        "tr": {"merhaba", "teşekkür", "çok", "iyi", "ürün", "hızlı", "mükemmel", "satıcı", "teslimat"},
        "pl": {"cześć", "dziękuję", "bardzo", "dobry", "produkt", "szybko", "świetny", "sprzedawca", "dostawa"},
    }
    names = {
        "en": "English", "de": "German", "fr": "French", "es": "Spanish",
        "it": "Italian", "pt": "Portuguese", "tr": "Turkish", "pl": "Polish",
    }
    scores = {code: len(word_set & vocabulary) for code, vocabulary in vocabularies.items()}
    best_code = max(scores, key=scores.get)
    if scores[best_code] > 0:
        return best_code, names[best_code]

    if re.search(r"[A-Za-z]", raw) and not re.search(r"[À-ÿ]", raw):
        return "en", "English"

    accent_hints = {
        "de": "äöüß", "fr": "àâçéèêëîïôûùüÿœ", "es": "áéíóúñü¿¡",
        "it": "àèéìíîòóùú", "pt": "ãõáâàçéêíóôú", "tr": "çğıöşü",
        "pl": "ąćęłńóśźż",
    }
    accent_scores = {code: sum(low.count(ch) for ch in chars) for code, chars in accent_hints.items()}
    best_code = max(accent_scores, key=accent_scores.get)
    if accent_scores[best_code] > 0:
        return best_code, names[best_code]
    return "en", "English"

def _language_instruction(language: tuple[str, str]) -> str:
    code, name = language
    if code == "ru":
        return "Ответь полностью на русском языке."
    return (
        f"Ответь полностью и только на языке {name}. Не переходи на русский язык, "
        "даже если название товара, системные инструкции или данные заказа написаны по-русски."
    )

def _language_system_prompt(language: tuple[str, str]) -> str:
    code, name = language
    if code == "ru":
        return "ОБЯЗАТЕЛЬНО: весь ответ должен быть только на русском языке."
    return (
        f"MANDATORY OUTPUT LANGUAGE: {name}. Write the entire seller reply only in {name}. "
        "Do not use Russian words or Russian sentences. Product names, usernames and exact values may remain unchanged."
    )

def _text_matches_language(text_value: str, language: tuple[str, str]) -> bool:
    code, _ = language
    text_value = str(text_value or "")
    latin = len(re.findall(r"[A-Za-zÀ-ÿ]", text_value))
    cyrillic = len(re.findall(r"[А-Яа-яЁёІіЇїЄєҐґӘәӨөҮүҰұҚқҒғҢңҺһ]", text_value))
    if code in {"en", "de", "fr", "es", "it", "pt", "tr", "pl"}:
        return latin >= max(8, cyrillic * 4)
    if code in {"ru", "uk", "kk"}:
        return cyrillic >= max(8, latin * 2)
    if code == "ja":
        return bool(re.search(r"[ぁ-ゟ゠-ヿ一-龯]", text_value))
    if code == "zh":
        return bool(re.search(r"[一-龯]", text_value))
    if code == "ko":
        return bool(re.search(r"[가-힣]", text_value))
    if code == "ar":
        return bool(re.search(r"[\u0600-\u06FF]", text_value))
    return True

def _effective_signature(cfg: Optional[dict], total_limit: Optional[int] = None) -> str:
    limit = total_limit or _response_limit(cfg)
    signature = str((cfg or {}).get("seller_signature") or "").strip()
    if not signature:
        return ""

    max_signature = max(0, limit - 40)
    return _cut_700_no_dots(signature, max_signature) if max_signature else ""

def _response_body_range(cfg: Optional[dict]) -> tuple[int, int]:
    total_minimum, total_maximum = _response_range(cfg)
    signature = _effective_signature(cfg, total_maximum)
    overhead = len(signature) + 1 if signature else 0
    body_maximum = max(40, total_maximum - overhead)
    body_minimum = max(40, total_minimum - overhead)
    return min(body_minimum, body_maximum), body_maximum

def _response_body_limit(cfg: Optional[dict]) -> int:
    return _response_body_range(cfg)[1]

def _append_seller_signature(text_value: str, cfg: Optional[dict]) -> str:
    total = _response_limit(cfg)
    signature = _effective_signature(cfg, total)
    if not signature:
        return _cut_700_no_dots(text_value, total)
    body = _cut_700_no_dots(text_value, max(40, total - len(signature) - 1))
    return f"{body}\n{signature}".strip()

class ResponseGenerationError(RuntimeError):
    pass

def build_prompt(cfg: dict, order, language: Optional[tuple[str, str]] = None) -> str:
    review = getattr(order, "review", None)
    values = _extract_order_fields(order)
    try:
        stars = int(getattr(review, "stars", None) or values.get("rating") or 5)
    except (TypeError, ValueError):
        stars = 5
    stars = max(1, min(5, stars))

    lower, limit = _response_body_range(cfg)
    language = language or _detect_review_language(values.get("text", ""))
    style_key = str(cfg.get("response_style") or "adaptive")
    style_instruction = RESPONSE_STYLES.get(style_key, RESPONSE_STYLES["adaptive"])[1]
    rating_is_visible = bool((cfg.get("fields") or {}).get("rating"))

    mapping = _SafeDict({
        "info_block": _build_info_block(cfg, order),
        "rating": str(stars),
        "rating_instruction": _rating_instruction(stars),
        "min_length": str(lower),
        "max_length": str(limit),
        "style_instruction": style_instruction,
        "emoji_instruction": _emoji_instruction(cfg, stars),
        "language_instruction": _language_instruction(language),
        "rating_mention_instruction": (
            "Можно естественно упомянуть оценку, если это полезно."
            if rating_is_visible else
            "Не называй количество звёзд и не упоминай число оценки в готовом ответе."
        ),
        "field_usage_instruction": _field_usage_instruction(cfg, values),
    })

    try:
        return DEFAULT_PROMPT_TEMPLATE.format_map(mapping)
    except Exception as error:
        loge(f"build_prompt format failed: {error}")
        return DEFAULT_PROMPT_TEMPLATE + "\n\n" + mapping["info_block"]

def _cut_700_no_dots(text: str, limit: int = MAX_CHARACTERS) -> str:
    if text is None:
        return ""
    t = str(text).strip()
    if len(t) <= limit:
        return t
    cut_point = t.rfind(" ", 0, limit)
    return t[:cut_point] if cut_point != -1 else t[:limit]

def _should_switch_model(status_code: int, error_text: str) -> bool:
    low = str(error_text or "").lower()
    if status_code in {404, 408, 409, 429, 500, 502, 503, 504}:
        return True
    trigger_words = (
        "rate limit", "quota", "token limit", "tokens limit", "limit reached",
        "insufficient", "exhausted", "capacity", "overloaded", "unavailable",
        "model not found", "model is not available", "temporarily unavailable",
    )
    return status_code in {400, 402, 403} and any(word in low for word in trigger_words)

def _build_model_chain(cfg: Optional[dict], api_key: str, primary_model: str) -> list[str]:
    primary = str(primary_model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    if not cfg or not cfg.get("model_auto_fallback", True):
        return [primary]

    pool = list(cfg.get("model_pool") or [])
    live_models = _fetch_available_models(api_key)
    if live_models:
        pool = live_models
        _remember_model_pool(cfg, live_models)

    candidates = [primary]
    for model_id in pool:
        if model_id != primary and _model_is_text_candidate(model_id):
            candidates.append(model_id)
        if len(candidates) >= 1 + MAX_FALLBACK_MODELS:
            break
    return list(dict.fromkeys(candidates))

def generate_response(
    prompt: str,
    api_key: str,
    model: str,
    cfg: Optional[dict] = None,
    language: Optional[tuple[str, str]] = None,
) -> str:
    if not api_key:
        raise ResponseGenerationError("API-ключ не настроен")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    models = _build_model_chain(cfg, api_key, model)
    last_error = ""
    language = language or ("ru", "Russian")
    minimum_length, response_limit = _response_body_range(cfg)
    best_short_answer = ""

    for model_index, current_model in enumerate(models):
        previous_short_answer = ""
        for attempt in range(1, MODEL_ATTEMPTS_PER_MODEL + 1):
            messages = [
                {"role": "system", "content": _style_system_prompt(cfg)},
                {"role": "system", "content": _language_system_prompt(language)},
                {"role": "user", "content": prompt},
            ]
            if previous_short_answer:
                messages.extend([
                    {"role": "assistant", "content": previous_short_answer},
                    {
                        "role": "user",
                        "content": (
                            f"Rewrite the reply. It is too short. The final reply must contain from "
                            f"{minimum_length} to {response_limit} characters, must remain natural, "
                            f"and must be written only in {language[1]}. Do not explain the rewrite."
                        ),
                    },
                ])
            payload = {
                "model": current_model,
                "messages": messages,
                "temperature": _style_temperature(cfg),
                "max_tokens": max(180, min(900, response_limit + 180)),
            }
            try:
                resp = requests.post(IO_CHAT_URL, headers=headers, json=payload, timeout=IO_TIMEOUT)
                if resp.status_code >= 400:
                    body = (resp.text or "")[:500]
                    last_error = f"HTTP {resp.status_code}: {body}"
                    switch_model = _should_switch_model(resp.status_code, body)
                    logw(
                        f"IO model={current_model} HTTP {resp.status_code} "
                        f"attempt={attempt}: {body[:250]}"
                    )
                    if switch_model:
                        break
                    if resp.status_code in {401, 403}:
                        raise ResponseGenerationError(last_error)
                    time.sleep(1)
                    continue

                data = resp.json()
                content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                content = (content or "").strip()
                if cfg is not None and not cfg.get("use_emojis", True):
                    content = _strip_emojis(content)

                if not _text_matches_language(content, language):
                    last_error = f"ответ сгенерирован не на языке {language[1]}"
                    previous_short_answer = ""
                    logw(f"Model {current_model}: {last_error}, attempt={attempt}")
                    continue

                if len(content) < minimum_length:
                    last_error = (
                        f"слишком короткий ответ: {len(content)} символов, "
                        f"требуется минимум {minimum_length}"
                    )
                    if len(content) > len(best_short_answer):
                        best_short_answer = content
                    previous_short_answer = content
                    logw(f"Model {current_model}: {last_error}, attempt={attempt}")
                    continue

                if cfg is not None and current_model != str(cfg.get("model") or DEFAULT_MODEL):
                    old_model = str(cfg.get("model") or DEFAULT_MODEL)
                    cfg["model"] = current_model
                    cfg["model_pool"] = list(dict.fromkeys([
                        current_model,
                        old_model,
                        *(cfg.get("model_pool") or []),
                    ]))[:50]
                    _set_config(cfg)
                    logi(f"Автопереход модели: {old_model} -> {current_model}")

                content = _cut_700_no_dots(content, response_limit)
                if len(content) < minimum_length:
                    last_error = (
                        f"после сокращения осталось {len(content)} символов, "
                        f"требуется минимум {minimum_length}"
                    )
                    previous_short_answer = content
                    continue
                return content

            except ResponseGenerationError:
                raise
            except Exception as error:
                last_error = str(error)
                loge(f"IO request model={current_model} attempt={attempt}: {error}")
                if attempt < MODEL_ATTEMPTS_PER_MODEL:
                    time.sleep(1)

        if model_index + 1 < len(models):
            logw(f"Переключаюсь с модели {current_model} на {models[model_index + 1]}")

    if best_short_answer:
        last_error = (
            f"модели не смогли соблюсти минимальную длину {minimum_length}; "
            f"лучший ответ - {len(best_short_answer)} символов"
        )
    loge(f"Все модели недоступны: {last_error or 'неизвестная ошибка'}")
    raise ResponseGenerationError(last_error or "Все доступные модели недоступны")

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

    _safe_edit(bot, chat_id, call.message.id, _settings_text(cfg), _settings_kb(cfg))

def _test_api(cardinal: "Cardinal", call):
    bot = cardinal.telegram.bot
    chat_id = call.message.chat.id

    cfg = _get_config(load_data())
    api_key = _get_api_key(cfg)
    model = cfg.get("model", DEFAULT_MODEL)

    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    if not api_key:
        try:
            bot.answer_callback_query(call.id, "Сначала задай API ключ.", show_alert=True)
        except Exception:
            pass
        return

    prompt = (
        "Определи язык отзыва и ответь на нём в выбранном стиле. "
        "Отзыв: 'Everything was delivered very quickly, thank you!'"
    )
    try:
        ans = generate_response(prompt, api_key, model, cfg, ("en", "English"))
        ans = _append_seller_signature(ans, cfg)
        actual_model = escape(str(cfg.get("model") or model))
        bot.send_message(
            chat_id,
            f"🧪 <b>Тест API</b>\nМодель: <code>{actual_model}</code>\n\n{escape(ans)}",
            parse_mode="HTML",
        )
    except Exception as error:
        bot.send_message(chat_id, f"❌ Тест API не пройден:\n{error}")

def _extract_source_value(source: str, name: str) -> Optional[str]:
    pattern = rf'(?m)^\s*{re.escape(name)}\s*=\s*(["\'])(.*?)\1\s*$'
    match = re.search(pattern, source)
    return match.group(2).strip() if match else None

def _version_parts(version: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", str(version or ""))
    return tuple(int(x) for x in numbers) or (0,)

def _compare_versions(left: str, right: str) -> int:
    a = list(_version_parts(left))
    b = list(_version_parts(right))
    length = max(len(a), len(b))
    a.extend([0] * (length - len(a)))
    b.extend([0] * (length - len(b)))
    return (a > b) - (a < b)

def _update_plugin(cardinal: "Cardinal", call):
    _restore_branding()
    bot = cardinal.telegram.bot
    chat_id = call.message.chat.id
    msg_id = call.message.id
    temp_path = None

    try:
        bot.answer_callback_query(call.id, "Проверяю обновление…")
    except Exception:
        pass

    _safe_edit(
        bot,
        chat_id,
        msg_id,
        "🔄 <b>Обновление плагина</b>\n\nПроверяю последнюю версию на GitHub…",
    )

    try:
        response = requests.get(
            GITHUB_UPDATE_URL,
            timeout=UPDATE_TIMEOUT,
            headers={"User-Agent": f"{NAME}/{VERSION}"},
        )
        response.raise_for_status()
        source = response.content.decode("utf-8-sig")

        remote_name = _extract_source_value(source, "NAME")
        remote_uuid = _extract_source_value(source, "UUID")
        remote_version = _extract_source_value(source, "VERSION")

        if remote_name != NAME or remote_uuid != UUID or not remote_version:
            raise ValueError("полученный файл не похож на GPT Feedback")

        compile(source, GITHUB_UPDATE_URL, "exec")

        version_cmp = _compare_versions(remote_version, VERSION)
        if version_cmp == 0:
            _safe_edit(
                bot,
                chat_id,
                msg_id,
                f"✅ Установлена актуальная версия <code>{VERSION}</code>.\n\nОбновление не требуется.",
                _welcome_kb(),
            )
            return

        if version_cmp < 0:
            _safe_edit(
                bot,
                chat_id,
                msg_id,
                f"ℹ️ Установленная версия <code>{VERSION}</code> новее версии "
                f"<code>{remote_version}</code> на GitHub.\n\nФайл не был заменён.",
                _welcome_kb(),
            )
            return

        plugin_path = os.path.abspath(__file__)
        if not os.path.isfile(plugin_path):
            raise FileNotFoundError(f"не найден файл плагина: {plugin_path}")

        temp_path = f"{plugin_path}.update.{os.getpid()}.tmp"
        backup_path = f"{plugin_path}.bak"

        with open(temp_path, "w", encoding="utf-8", newline="\n") as file:
            file.write(source)
            file.flush()
            os.fsync(file.fileno())

        shutil.copy2(plugin_path, backup_path)
        os.replace(temp_path, plugin_path)
        temp_path = None

        logi(f"Плагин обновлён: {VERSION} -> {remote_version}")
        _safe_edit(
            bot,
            chat_id,
            msg_id,
            f"✅ <b>Плагин обновлён!</b>\n\n"
            f"Версия: <code>{VERSION}</code> → <code>{remote_version}</code>\n\n"
            "Перезапусти Cardinal, чтобы новая версия начала работать. "
            "Настройки и история ответов сохранены.",
            _welcome_kb(),
        )

    except Exception as error:
        loge(f"Update failed: {error}")
        _safe_edit(
            bot,
            chat_id,
            msg_id,
            "❌ <b>Не удалось обновить плагин.</b>\n\n"
            f"Ошибка: <code>{escape(str(error))}</code>\n\n"
            f"Репозиторий: {GITHUB_URL}",
            _welcome_kb(),
        )
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except Exception:
                pass

def _delete_menu_text() -> str:
    return (
        "🗑 <b>Удаление плагина</b>\n\n"
        f"Ты точно хочешь удалить <b>{NAME}</b>?\n"
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

def _delete_plugin_path(cardinal: "Cardinal") -> str:
    plugin = (getattr(cardinal, "plugins", None) or {}).get(UUID)
    paths = [getattr(plugin, "path", "") if plugin else "", globals().get("__file__", "")]
    for path in paths:
        if not path:
            continue
        absolute = os.path.abspath(path)
        if not os.path.isfile(absolute):
            continue
        try:
            with open(absolute, "r", encoding="utf-8", errors="ignore") as file:
                if UUID not in file.read(30000):
                    continue
        except Exception:
            pass
        return absolute
    return ""

def _unregister_deleted_plugin(cardinal: "Cardinal"):
    plugins = getattr(cardinal, "plugins", None)
    if isinstance(plugins, dict):
        plugins.pop(UUID, None)
    changed = {}
    for attr in ("disabled_plugins", "pinned_plugins"):
        values = getattr(cardinal, attr, None)
        if isinstance(values, list):
            while UUID in values:
                values.remove(UUID)
            changed[attr] = values
    try:
        from Utils import cardinal_tools
        if "disabled_plugins" in changed:
            cardinal_tools.cache_disabled_plugins(changed["disabled_plugins"])
        if "pinned_plugins" in changed:
            cardinal_tools.cache_pinned_plugins(changed["pinned_plugins"])
    except Exception:
        pass

def _delete_try(cardinal: "Cardinal", call):
    bot = cardinal.telegram.bot
    try:
        bot.answer_callback_query(call.id, "Удаляю плагин…")
    except Exception:
        pass

    path = _delete_plugin_path(cardinal)
    if not path:
        _safe_edit(
            bot,
            call.message.chat.id,
            call.message.id,
            "❌ <b>Не найден файл плагина.</b>\n\nПерезапусти Cardinal и попробуй удалить его через общий список плагинов.",
            _welcome_kb(),
        )
        return

    try:
        os.remove(path)
    except Exception as first_error:
        renamed = path + ".deleted"
        try:
            if os.path.exists(renamed):
                os.remove(renamed)
            os.replace(path, renamed)
            try:
                os.remove(renamed)
            except Exception:
                pass
        except Exception as second_error:
            _safe_edit(
                bot,
                call.message.chat.id,
                call.message.id,
                "❌ <b>Не удалось удалить файл плагина.</b>\n\n"
                f"Путь: <code>{escape(path)}</code>\n"
                f"Ошибка: <code>{escape(str(second_error or first_error))}</code>",
                _welcome_kb(),
            )
            return

    _unregister_deleted_plugin(cardinal)
    try:
        bot.edit_message_text(
            "✅ <b>GPT Feedback удалён.</b>\n\nФайл плагина удалён. Перезапусти Cardinal, чтобы полностью очистить старые Telegram-обработчики.",
            call.message.chat.id,
            call.message.id,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        pass

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

def _normalize_username(value: Any) -> str:
    return str(value or "").strip().lstrip("@").casefold()

def _is_own_purchase(cardinal: "Cardinal", order) -> bool:
    account = getattr(cardinal, "account", None)

    account_id = getattr(account, "id", None)
    buyer_id = getattr(order, "buyer_id", None)
    if account_id is not None and buyer_id is not None:
        try:
            if int(account_id) == int(buyer_id):
                return True
        except (TypeError, ValueError):
            if str(account_id) == str(buyer_id):
                return True

    account_username = _normalize_username(getattr(account, "username", None))
    buyer_username = _normalize_username(getattr(order, "buyer_username", None))
    return bool(account_username and buyer_username and account_username == buyer_username)

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
        _notify(cardinal, f"❌ {NAME}: не смог удалить ответ для заказа #{order_id}: {e}")

def _send_or_edit_reply(
    cardinal: "Cardinal",
    order_id: str,
    stars: int,
    text: str,
    limit: int = MAX_CHARACTERS,
):
    cardinal.account.send_review(
        order_id=order_id,
        rating=int(stars),
        text=_cut_700_no_dots(text, limit),
    )
    logi(f"✅ send_review({order_id}) OK")

def _process_feedback_order(
    cardinal: "Cardinal",
    order_id: str,
    msg_type=None,
    from_retry: bool = False,
) -> bool:
    order_id = str(order_id or "").lstrip("#").strip()
    stage = "подготовка"
    try:
        cfg = _get_config(load_data())
        if not cfg.get("enabled"):
            return False

        stage = "проверка API-ключа"
        api_key = _get_api_key(cfg)
        if not api_key:
            raise RuntimeError("API-ключ не настроен")

        stage = "получение заказа"
        order = cardinal.account.get_order(order_id)
        if not order:
            raise RuntimeError("FunPay не вернул данные заказа")

        if _is_own_purchase(cardinal, order):
            state = load_state()
            if order_id in state:
                state.pop(order_id, None)
                save_state(state)
            _remove_pending_review(order_id)
            logi(f"Пропущен собственный покупательский отзыв по заказу #{order_id}")
            return True

        if (cfg.get("fields") or {}).get("order_time"):
            _resolve_order_datetime(cardinal, order, order_id)

        state = load_state()
        previous = state.get(order_id) if isinstance(state.get(order_id), dict) else None
        previous_fp = (previous or {}).get("review_fp")

        if msg_type == getattr(MessageTypes, "FEEDBACK_DELETED", None):
            if previous:
                _delete_our_reply(cardinal, order_id)
                state.pop(order_id, None)
                save_state(state)
            _remove_pending_review(order_id)
            return True

        if not _review_exists(order):
            if previous:
                _delete_our_reply(cardinal, order_id)
                state.pop(order_id, None)
                save_state(state)
            _remove_pending_review(order_id)
            return True

        review = getattr(order, "review", None)
        stars = int(getattr(review, "stars", 5) or 5)
        fingerprint = _buyer_review_fingerprint(order)

        if previous_fp and previous_fp == fingerprint and not from_retry:
            _remove_pending_review(order_id)
            return True

        allowed = cfg.get("stars", [5]) or [5]
        if stars not in allowed:
            if previous:
                _delete_our_reply(cardinal, order_id)
                state.pop(order_id, None)
                save_state(state)
            _remove_pending_review(order_id)
            return True

        stage = "определение языка и генерация ответа"
        order_values = _extract_order_fields(order)
        response_language = _detect_review_language(order_values.get("text", ""))
        prompt = build_prompt(cfg, order, response_language)
        reply_text = generate_response(
            prompt,
            api_key,
            cfg.get("model", DEFAULT_MODEL),
            cfg,
            response_language,
        )
        if not cfg.get("use_emojis", True):
            reply_text = _strip_emojis(reply_text)
        reply_text = _append_seller_signature(reply_text, cfg)

        stage = "отправка ответа на FunPay"
        _send_or_edit_reply(cardinal, order_id, stars, reply_text, _response_limit(cfg))

        state = load_state()
        state[order_id] = {
            "review_fp": fingerprint,
            "stars": stars,
            "updated_at": int(time.time()),
        }
        save_state(state)
        _remove_pending_review(order_id)
        _stats_record_success(stars, retried=from_retry)

        if from_retry:
            _notify(cardinal, f"✅ {NAME}: повторная отправка для заказа #{order_id} выполнена успешно.")
        return True

    except Exception as error:
        should_notify = _queue_pending_review(order_id, stage, error, force_notify=from_retry)
        loge(f"Заказ #{order_id}, этап '{stage}': {error}")
        if should_notify:
            _notify_order_error(cardinal, order_id, stage, error)
        return False

def _retry_pending_reviews(cardinal: "Cardinal"):

    time.sleep(3)
    pending = load_pending()
    if not pending:
        return
    logi(f"Повторная обработка очереди: {len(pending)} заказ(ов)")
    for order_id in list(pending.keys()):
        _process_feedback_order(cardinal, order_id, from_retry=True)
        time.sleep(1)

def handle_feedback_event(cardinal: "Cardinal", event: NewMessageEvent):
    order_id = ""
    try:
        msg_type = getattr(event.message, "type", None)
        if not _should_handle_event_type(msg_type):
            return

        order_id = _get_order_id_from_event(event) or ""
        if not order_id:
            logw("Не нашёл order_id по regex #(...). Проверь формат event.message.")
            return

        _process_feedback_order(cardinal, order_id, msg_type=msg_type, from_retry=False)

    except Exception as error:
        loge(f"handle_feedback_event crashed: {error}")
        if order_id:
            should_notify = _queue_pending_review(order_id, "непредвиденная ошибка обработчика", error)
            if should_notify:
                _notify_order_error(cardinal, order_id, "непредвиденная ошибка обработчика", error)
        else:
            _notify(cardinal, f"❌ {NAME}: непредвиденная ошибка обработчика: {error}")

def init_cardinal(cardinal: "Cardinal"):
    _restore_branding()
    tg = cardinal.telegram
    tg.msg_handler(lambda m: open_welcome(cardinal, m), commands=["gptfeedback_menu"])
    tg.msg_handler(lambda m: _handle_fsm(m, cardinal), func=lambda m: m.chat.id in _fsm)
    tg.cbq_handler(lambda c: open_welcome(cardinal, c), func=lambda c:
                   c.data.startswith(f"{CBT_EDIT_PLUGIN}:{UUID}")
                   or c.data.startswith(f"{CBT_PLUGIN_SETTINGS}:{UUID}")
                   or c.data == CB_WELCOME)
    tg.cbq_handler(lambda c: open_settings(cardinal, c), func=lambda c: c.data == CB_SETTINGS)
    tg.cbq_handler(lambda c: _details_open(cardinal, c), func=lambda c: c.data == CB_DETAILS)
    tg.cbq_handler(lambda c: _length_open(cardinal, c), func=lambda c: c.data == CB_LENGTH)
    tg.cbq_handler(
        lambda c: _length_select(cardinal, c, int(c.data.split(":")[-1])),
        func=lambda c: c.data.startswith(f"{CB_LENGTH_SELECT}:"),
    )
    tg.cbq_handler(lambda c: _emoji_toggle(cardinal, c), func=lambda c: c.data == CB_EMOJI)
    tg.cbq_handler(lambda c: _style_open(cardinal, c), func=lambda c: c.data == CB_STYLE)
    tg.cbq_handler(
        lambda c: _style_select(cardinal, c, c.data.split(":")[-1]),
        func=lambda c: c.data.startswith(f"{CB_STYLE_SELECT}:"),
    )
    tg.cbq_handler(lambda c: _signature_start(cardinal, c), func=lambda c: c.data == CB_SIGNATURE)
    tg.cbq_handler(lambda c: _statistics_open(cardinal, c), func=lambda c: c.data == CB_STATS)
    tg.cbq_handler(lambda c: _model_open(cardinal, c), func=lambda c: c.data == CB_MODEL)
    tg.cbq_handler(lambda c: _model_open(cardinal, c, force=True), func=lambda c: c.data == CB_MODEL_REFRESH)
    tg.cbq_handler(lambda c: _model_auto_toggle(cardinal, c), func=lambda c: c.data == CB_MODEL_AUTO)
    tg.cbq_handler(
        lambda c: _model_open(cardinal, c, int(c.data.split(":")[-1])),
        func=lambda c: c.data.startswith(f"{CB_MODEL_PAGE}:"),
    )
    tg.cbq_handler(
        lambda c: _model_select(cardinal, c, int(c.data.split(":")[-1])),
        func=lambda c: c.data.startswith(f"{CB_MODEL_SELECT}:"),
    )
    tg.cbq_handler(lambda c: _logs_open(cardinal, c), func=lambda c: c.data == CB_LOGS)
    tg.cbq_handler(lambda c: open_information(cardinal, c), func=lambda c: c.data == CB_INFO)
    tg.cbq_handler(lambda c: _acknowledge_instruction(cardinal, c), func=lambda c: c.data == CB_INSTRUCTION_ACK)
    tg.cbq_handler(lambda c: _update_plugin(cardinal, c), func=lambda c: c.data == CB_UPDATE)
    tg.cbq_handler(lambda c: _delete_open(cardinal, c), func=lambda c: c.data == CB_DELETE)
    tg.cbq_handler(lambda c: _delete_try(cardinal, c), func=lambda c: c.data == CB_DELETE_YES)
    tg.cbq_handler(lambda c: _delete_no(cardinal, c), func=lambda c: c.data == CB_DELETE_NO)
    tg.cbq_handler(lambda c: _toggle(cardinal, c), func=lambda c: c.data == CB_TOGGLE)
    tg.cbq_handler(lambda c: _stars_open(cardinal, c), func=lambda c: c.data == CB_STARS)
    tg.cbq_handler(lambda c: _fields_open(cardinal, c), func=lambda c: c.data == CB_FIELDS)
    tg.cbq_handler(lambda c: _apikey_start(cardinal, c), func=lambda c: c.data == CB_APIKEY)
    tg.cbq_handler(lambda c: _test_api(cardinal, c), func=lambda c: c.data == CB_TEST)
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
    except Exception as error:
        logw(f"add_telegram_commands failed: {error}")

    threading.Thread(
        target=_retry_pending_reviews,
        args=(cardinal,),
        daemon=True,
        name="GPTFeedbackRetry",
    ).start()

    logi("✅ GPT Feedback запущен")

BIND_TO_PRE_INIT = [init_cardinal]
BIND_TO_NEW_MESSAGE = [handle_feedback_event]
BIND_TO_DELETE = None
