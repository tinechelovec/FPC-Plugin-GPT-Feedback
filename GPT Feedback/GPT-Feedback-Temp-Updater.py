from __future__ import annotations

import html
import logging
import os
import re
import shutil

import requests

NAME = "GPT-Feedback-Temp-Updater"
VERSION = "1.0.0"
DESCRIPTION = "Временный мини-плагин для обновления GPT Feedback командой /update_gpt_feedback."
CREDITS = "@tinechelovec"
UUID = "0ddfd580-1387-4f28-80f6-45fbfb9f55ac"
SETTINGS_PAGE = False

TARGET_NAME = "GPT Feedback"
TARGET_UUID = "461770a6-4460-4cf5-9eec-c41dc99fc64c"
UPDATE_URL = os.getenv(
    "GPT_FEEDBACK_UPDATE_URL",
    "https://raw.githubusercontent.com/tinechelovec/FPC-Plugin-GPT-Feedback/main/GPT%20Feedback/GPT%20Feedback.py",
).strip()

log = logging.getLogger(NAME)
_http = requests.Session()

def _h(value):
    return html.escape(str(value), quote=False)

def _send(bot, chat_id, text):
    try:
        return bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True)
    except TypeError:
        return bot.send_message(chat_id, text, parse_mode="HTML")

def _source_value(source, name):
    match = re.search(rf'(?m)^\s*{re.escape(name)}\s*=\s*(["\'])(.*?)\1\s*$', source or "")
    return match.group(2).strip() if match else None

def _version_key(value):
    numbers = [int(x) for x in re.findall(r"\d+", str(value or "0"))[:4]]
    return tuple(numbers + [0] * (4 - len(numbers)))

def _read(path):
    with open(path, "r", encoding="utf-8-sig") as file:
        return file.read()

def _is_target_source(source):
    if not source or "def init_cardinal" not in source:
        return False
    return _source_value(source, "NAME") == TARGET_NAME and _source_value(source, "UUID") == TARGET_UUID

def _candidate_dirs():
    here = os.path.dirname(os.path.abspath(__file__))
    cwd = os.path.abspath(os.getcwd())
    values = [
        here,
        cwd,
        os.path.join(cwd, "plugins"),
        os.path.join(cwd, "storage", "plugins"),
        os.path.dirname(here),
    ]
    result = []
    for path in values:
        path = os.path.abspath(path)
        if os.path.isdir(path) and path not in result:
            result.append(path)
    return result

def _find_plugin():
    explicit = os.getenv("GPT_FEEDBACK_PLUGIN_FILE", "").strip()
    self_file = os.path.abspath(__file__)
    checked = set()
    priority = []
    if explicit:
        priority.append(os.path.abspath(explicit))
    names = (
        "GPT Feedback.py",
        "GPT_Feedback.py",
        "GPT-Feedback.py",
        "GPT Feedback 1.4.py",
    )
    for directory in _candidate_dirs():
        priority.extend(os.path.join(directory, name) for name in names)
    for path in priority:
        if path in checked or path == self_file or not os.path.isfile(path):
            continue
        checked.add(path)
        try:
            if _is_target_source(_read(path)):
                return path
        except Exception:
            pass
    for directory in _candidate_dirs():
        try:
            for root, subdirs, files in os.walk(directory):
                if root.count(os.sep) - directory.count(os.sep) > 3:
                    subdirs[:] = []
                    continue
                for filename in files:
                    if not filename.lower().endswith(".py"):
                        continue
                    path = os.path.abspath(os.path.join(root, filename))
                    if path in checked or path == self_file:
                        continue
                    checked.add(path)
                    try:
                        if _is_target_source(_read(path)):
                            return path
                    except Exception:
                        pass
        except Exception:
            pass
    raise RuntimeError(
        "не найден установленный GPT Feedback. Можно указать путь через GPT_FEEDBACK_PLUGIN_FILE"
    )

def _download():
    if not UPDATE_URL.lower().startswith("https://"):
        raise RuntimeError("ссылка обновления должна использовать HTTPS")
    response = _http.get(
        UPDATE_URL,
        headers={
            "Accept": "text/plain, */*;q=0.1",
            "User-Agent": f"{NAME}/{VERSION}",
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = response.content or b""
    if len(payload) < 5000:
        raise RuntimeError(f"GitHub вернул слишком маленький файл ({len(payload)} байт)")
    if len(payload) > 5 * 1024 * 1024:
        raise RuntimeError("файл обновления слишком большой")
    try:
        source = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise RuntimeError(f"файл обновления не UTF-8: {error}") from error
    beginning = source[:700].lower()
    if "<html" in beginning or "<!doctype" in beginning:
        raise RuntimeError("вместо Python-файла скачалась HTML-страница")
    if not _is_target_source(source):
        raise RuntimeError("скачан не GPT Feedback или UUID файла не совпадает")
    missing = [
        item
        for item in ("def init_cardinal", "BIND_TO_PRE_INIT", "BIND_TO_NEW_MESSAGE")
        if item not in source
    ]
    if missing:
        raise RuntimeError("в скачанном файле отсутствует: " + ", ".join(missing))
    new_version = _source_value(source, "VERSION")
    if not new_version:
        raise RuntimeError("в скачанном файле не найдена VERSION")
    compile(source, UPDATE_URL, "exec")
    return source, new_version, payload

def _cleanup_pyc(path):
    try:
        base = os.path.splitext(os.path.basename(path))[0]
        cache = os.path.join(os.path.dirname(path), "__pycache__")
        if os.path.isdir(cache):
            for filename in os.listdir(cache):
                if filename.startswith(base + ".") and filename.endswith(".pyc"):
                    os.remove(os.path.join(cache, filename))
    except Exception:
        pass

def _atomic_replace(path, payload):
    temp_path = path + ".gpt_feedback_update_tmp"
    try:
        with open(temp_path, "wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        try:
            os.chmod(temp_path, os.stat(path).st_mode)
        except Exception:
            pass
        os.replace(temp_path, path)
        _cleanup_pyc(path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

def _delete_self():
    keep = os.getenv("GPT_FEEDBACK_UPDATER_KEEP_SELF", "0").strip().lower()
    if keep in {"1", "true", "yes", "on"}:
        return "оставлен по GPT_FEEDBACK_UPDATER_KEEP_SELF"
    self_file = os.path.abspath(__file__)
    try:
        _cleanup_pyc(self_file)
        os.remove(self_file)
        return "удалён"
    except Exception as error:
        return f"не удалён автоматически: {_h(error)}"

def _cmd_update(cardinal, message):
    bot = cardinal.telegram.bot
    chat_id = message.chat.id
    try:
        target = _find_plugin()
        old_source = _read(target)
        old_version = _source_value(old_source, "VERSION") or "не найдена"
        _send(
            bot,
            chat_id,
            "⏬ <b>Проверяю обновление GPT Feedback…</b>\n\n"
            f"Текущая версия: <code>{_h(old_version)}</code>",
        )
        _, new_version, new_payload = _download()
        _send(
            bot,
            chat_id,
            "📦 <b>Версии GPT Feedback</b>\n\n"
            f"Текущая: <code>{_h(old_version)}</code>\n"
            f"Новая: <code>{_h(new_version)}</code>",
        )
        if old_version != "не найдена" and _version_key(new_version) <= _version_key(old_version):
            _send(
                bot,
                chat_id,
                "✅ <b>Обновление не требуется.</b>\n\n"
                "Основной файл, настройки и updater не изменены.",
            )
            return
        backup_path = target + ".bak"
        shutil.copy2(target, backup_path)
        _atomic_replace(target, new_payload)
        self_status = _delete_self()
        _send(
            bot,
            chat_id,
            "✅ <b>GPT Feedback обновлён.</b>\n\n"
            f"Версия: <code>{_h(old_version)}</code> → <code>{_h(new_version)}</code>\n"
            f"Резервная копия: <code>{_h(backup_path)}</code>\n"
            "Настройки и статистика в <code>storage/plugins/gpt_feedback</code> сохранены.\n"
            f"Временный updater: <code>{self_status}</code>\n\n"
            "Теперь выполните: <code>/restart</code>",
        )
    except Exception as error:
        log.exception("GPT Feedback update failed")
        try:
            _send(
                bot,
                chat_id,
                "❌ <b>Не удалось обновить GPT Feedback.</b>\n\n"
                f"Ошибка: <code>{_h(error)}</code>\n\n"
                "Текущий основной файл и настройки не изменены.",
            )
        except Exception:
            pass

def init_cardinal(cardinal):
    try:
        cardinal.add_telegram_commands(
            UUID,
            [("update_gpt_feedback", "Обновить GPT Feedback", True)],
        )
    except Exception:
        pass
    cardinal.telegram.msg_handler(
        lambda message: _cmd_update(cardinal, message),
        commands=["update_gpt_feedback"],
    )
    log.info("GPT Feedback temporary updater loaded. Command: /update_gpt_feedback")

BIND_TO_PRE_INIT = [init_cardinal]
BIND_TO_NEW_MESSAGE = []
BIND_TO_NEW_ORDER = []
BIND_TO_DELETE = None
