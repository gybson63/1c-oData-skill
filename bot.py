#!/usr/bin/env python3
"""Telegram-бот для запросов к 1С через OData + OpenAI-совместимый ИИ."""

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
from xml.etree import ElementTree as ET
import urllib.request
from pathlib import Path
from typing import Optional

from html import escape as _html_escape

from openai import AsyncOpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters


def _esc(text: str) -> str:
    """Escape special HTML chars for safe use in Telegram HTML parse mode."""
    return _html_escape(str(text))

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger("1c-bot")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(env_file: str = "env.json", profile: str = "default") -> dict:
    path = Path(env_file)
    if not path.exists():
        raise FileNotFoundError(f"Файл конфигурации не найден: {env_file}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    cfg = data.get(profile, {})
    # Fallback to env vars
    for key, env_var in [
        ("telegram_token", "TELEGRAM_TOKEN"),
        ("ai_api_key", "AI_API_KEY"),
        ("ai_base_url", "AI_BASE_URL"),
        ("ai_model", "AI_MODEL"),
        ("odata_url", "ODATA_URL"),
        ("odata_user", "ODATA_USER"),
        ("odata_password", "ODATA_PASSWORD"),
    ]:
        if not cfg.get(key) and os.environ.get(env_var):
            cfg[key] = os.environ[env_var]
    # Strip accidental "Bearer " prefix from api key
    if cfg.get("ai_api_key", "").startswith("Bearer "):
        cfg["ai_api_key"] = cfg["ai_api_key"][len("Bearer "):]
    # ai_rpm: max requests per minute to the AI (default 15 — Gemini free tier)
    cfg["ai_rpm"] = int(cfg.get("ai_rpm", 15))

    required = ["odata_url", "odata_user", "odata_password", "telegram_token", "ai_api_key", "ai_base_url", "ai_model"]
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        raise ValueError(f"Отсутствуют поля в конфигурации: {', '.join(missing)}")
    return cfg


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

SCRIPT_PATH = Path(__file__).parent / "skills" / "1cconfinfo" / "scripts" / "odata-cfg-info.py"
SUMMARY_CACHE_TTL = 3600  # seconds

# Prefixes that signal subtable rows or helper entities to exclude
_EXCLUDE_SUFFIXES = ("ПрисоединенныеФайлы",)
_EXCLUDE_PREFIXES = ("Удалить",)

# TYPE_RU mapping (mirrors odata-cfg-info.py)
TYPE_RU = {
    "Catalog": "Справочники",
    "Document": "Документы",
    "InformationRegister": "Регистры сведений",
    "AccumulationRegister": "Регистры накопления",
    "AccountingRegister": "Регистры бухгалтерии",
    "CalculationRegister": "Регистры расчёта",
    "ChartOfCharacteristicTypes": "ПВХ",
    "ChartOfAccounts": "Планы счетов",
    "ChartOfCalculationTypes": "ПВР",
    "Enum": "Перечисления",
    "BusinessProcess": "Бизнес-процессы",
    "Task": "Задачи",
    "ExchangePlan": "Планы обмена",
    "Sequence": "Последовательности",
    "DocumentJournal": "Журналы документов",
}

_TYPE_HEADER_RE = re.compile(r"^\s{2}(.+?)\s+\((\w+)\):\s+\d+")


def _should_exclude(name: str) -> bool:
    if "_" in name:
        return True
    for s in _EXCLUDE_SUFFIXES:
        if name.endswith(s):
            return True
    for p in _EXCLUDE_PREFIXES:
        if name.startswith(p):
            return True
    return False


def parse_full_output_to_dict(text: str) -> dict:
    """Parse -Mode full stdout into {TypeEN: [ObjectName, ...]} dict."""
    result: dict = {}
    current_type: Optional[str] = None
    for line in text.splitlines():
        header_match = _TYPE_HEADER_RE.match(line)
        if header_match:
            current_type = header_match.group(2)
            if current_type not in result:
                result[current_type] = []
            continue
        if current_type and line.startswith("    ") and line.strip():
            obj_name = line.strip()
            if not _should_exclude(obj_name):
                result[current_type].append(obj_name)
    return result


def _summary_cache_path(cache_dir: str) -> Path:
    return Path(cache_dir) / "metadata_summary.json"


# EDMX namespaces — mirrors odata-cfg-info.py
_NS_LIST = [
    "http://schemas.microsoft.com/ado/2009/11/edm",
    "http://schemas.microsoft.com/ado/2008/09/edm",
    "",
]

def _find_ns(elem, tag):
    for ns in _NS_LIST:
        found = elem.find(f"{{{ns}}}{tag}" if ns else tag)
        if found is not None:
            return found
    return None

def _findall_ns(elem, tag):
    for ns in _NS_LIST:
        results = elem.findall(f"{{{ns}}}{tag}" if ns else tag)
        if results:
            return results
    return []


def get_entity_fields(entity_name: str, odata_url: str, cache_dir: str) -> list[str]:
    """Parse cached $metadata XML and return property names for the given entity."""
    url_hash = hashlib.md5(odata_url.rstrip("/").encode()).hexdigest()[:8]
    cache_file = Path(cache_dir) / f"odata_metadata_{url_hash}.xml"
    if not cache_file.exists():
        return []
    try:
        root = ET.parse(str(cache_file)).getroot()
    except ET.ParseError:
        return []

    # Navigate EDMX → DataServices → Schema
    ns_edmx = "http://schemas.microsoft.com/ado/2007/06/edmx"
    data_services = root.find(f"{{{ns_edmx}}}DataServices") or root
    schema = _find_ns(data_services, "Schema")
    if schema is None:
        return []

    # EntityType name in 1C metadata is typically entity_name + "_Type"
    target_names = {entity_name, entity_name + "_Type"}
    for et in _findall_ns(schema, "EntityType"):
        if et.get("Name") in target_names:
            return [p.get("Name") for p in _findall_ns(et, "Property") if p.get("Name") is not None]  # type: ignore[return-value]

    return []


def get_metadata_summary(env_file: str, cache_dir: str = ".cache", force_refresh: bool = False) -> dict:
    """Return metadata dict {TypeEN: [names]}. Uses subprocess + file cache."""
    cache_path = _summary_cache_path(cache_dir)
    # Check cache freshness
    if not force_refresh and cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < SUMMARY_CACHE_TTL:
            log.debug("Metadata summary loaded from cache (%ds old)", int(age))
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)

    log.info("Fetching metadata via odata-cfg-info.py...")
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "-Mode", "full", "-EnvFile", env_file, "-CacheDir", cache_dir],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(f"odata-cfg-info.py завершился с ошибкой:\n{stderr}")

    meta = parse_full_output_to_dict(result.stdout)
    total = sum(len(v) for v in meta.values())
    log.info("Metadata loaded: %d objects across %d types", total, len(meta))

    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return meta


def format_metadata_for_prompt(meta: dict, max_names: int = 30) -> str:
    """Compact text block for the AI system prompt.
    max_names controls how many object names are shown per type (reduce on token limit errors)."""
    lines = []
    priority = ["Catalog", "Document", "InformationRegister", "AccumulationRegister"]
    ordered_keys = [k for k in priority if k in meta] + [k for k in meta if k not in priority]
    for type_en in ordered_keys:
        names = meta[type_en]
        ru = TYPE_RU.get(type_en, type_en)
        if max_names == 0:
            lines.append(f"{type_en}_<Имя> [{ru}, {len(names)} шт.]")
        else:
            sample = ", ".join(names[:max_names])
            if len(names) > max_names:
                sample += f" ... (+{len(names) - max_names})"
            lines.append(f"{type_en}_<Имя> [{ru}, {len(names)} шт.]: {sample}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# OData HTTP
# ---------------------------------------------------------------------------

class ODataError(Exception):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


def make_auth_header(user: str, password: str) -> dict:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _strip_prefix(value: str, prefix: str) -> str:
    """Remove OData param prefix if present: '$filter=x' → 'x'."""
    return value[len(prefix):] if value.startswith(prefix) else value


def _qv(value: str, safe: str = "") -> str:
    """URL-encode an OData query parameter value.
    Never leaves spaces unencoded regardless of safe chars passed in."""
    encoded = urllib.parse.quote(value, safe=safe)
    return encoded.replace(" ", "%20")  # belt-and-suspenders: catch any leftover spaces


def build_odata_url(base_url: str, entity: str, filter_expr: Optional[str],
                    select: Optional[str], orderby: Optional[str], top: int) -> str:
    params = [f"$top={top}", "$format=json"]
    if filter_expr:
        fval = _strip_prefix(filter_expr, "$filter=")
        params.append("$filter=" + _qv(fval, safe="(),'/"))
    if select:
        sval = _strip_prefix(select, "$select=")
        sval = ",".join(f.strip() for f in sval.split(","))  # strip spaces around commas
        params.append("$select=" + _qv(sval, safe=",_"))
    if orderby:
        oval = _strip_prefix(orderby, "$orderby=")
        oval = ",".join(f.strip() for f in oval.split(","))
        params.append("$orderby=" + _qv(oval, safe=",_"))
    encoded_entity = _qv(entity)
    qs = "&".join(params)
    return f"{base_url.rstrip('/')}/{encoded_entity}?{qs}"


def _sanitize_url(url: str) -> str:
    """Final safety net: encode any bare spaces or control chars left in the URL."""
    # Split at '?' to avoid touching already-encoded path segments
    if "?" in url:
        path, qs = url.split("?", 1)
        qs = re.sub(r"[^\x21-\x7E]", lambda m: urllib.parse.quote(m.group()), qs)
        return f"{path}?{qs}"
    return url


def _sync_http_get(url: str, headers: dict) -> bytes:
    url = _sanitize_url(url)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="ignore")[:300]
        except Exception:
            pass
        raise ODataError(f"HTTP {e.code}: {e.reason} | {body}", status_code=e.code)
    except urllib.error.URLError as e:
        raise ODataError(f"Ошибка соединения: {e.reason}", status_code=0)


async def execute_odata_query(odata_url: str, auth_header: dict,
                               entity: str, filter_expr: Optional[str],
                               select: Optional[str], orderby: Optional[str],
                               top: int = 20) -> tuple:
    loop = asyncio.get_event_loop()
    url = build_odata_url(odata_url, entity, filter_expr, select, orderby, top)
    log.info("OData GET: %s", url)
    try:
        raw = await loop.run_in_executor(None, _sync_http_get, url, auth_header)
    except ODataError as e:
        if e.status_code == 400:
            if select or orderby:
                log.warning("OData 400, повтор без $select/$orderby")
                url2 = build_odata_url(odata_url, entity, filter_expr, None, None, top)
                log.info("OData GET (fallback): %s", url2)
                try:
                    raw = await loop.run_in_executor(None, _sync_http_get, url2, auth_header)
                except ODataError as e2:
                    if e2.status_code == 400 and filter_expr:
                        # filter тоже невалиден — пробуем без него
                        log.warning("OData 400 снова, повтор без $filter")
                        url3 = build_odata_url(odata_url, entity, None, None, None, top)
                        log.info("OData GET (fallback no filter): %s", url3)
                        raw = await loop.run_in_executor(None, _sync_http_get, url3, auth_header)
                    else:
                        raise e2
            elif filter_expr:
                log.warning("OData 400 в $filter, повтор без $filter")
                url2 = build_odata_url(odata_url, entity, None, None, None, top)
                log.info("OData GET (fallback no filter): %s", url2)
                raw = await loop.run_in_executor(None, _sync_http_get, url2, auth_header)
            else:
                raise
        else:
            raise
    data = json.loads(raw.decode("utf-8"))
    records = data.get("value", [])
    return records, None


# ---------------------------------------------------------------------------
# AI (OpenAI-compatible protocol)
# ---------------------------------------------------------------------------

class QueryError(Exception):
    pass

class TokenLimitError(Exception):
    """Request rejected because prompt is too large (HTTP 413 / token limit)."""
    pass


STEP1_SYSTEM = """\
Ты — помощник для запросов к базе 1С:Предприятие через OData REST API.
Верни ТОЛЬКО валидный JSON (без markdown, без пояснений вне JSON):
{{"entity":"...", "filter":"...", "select":"...", "orderby":"...", "top":20, "explanation":"..."}}

Правила построения запроса:
- "entity" — полное имя с префиксом из списка ниже: Catalog_Имя, Document_Имя и т.д.
- Используй ТОЛЬКО объекты из списка. Если нужного нет — выбери наиболее подходящий.
- Всегда добавляй в filter: DeletionMark eq false (если пользователь не просит удалённые).
- Даты: datetime'YYYY-MM-DDT00:00:00'
- Поиск по наименованию: contains(Description,'текст')
- "top" не более 50. Используй null для неиспользуемых полей.

Контекст диалога:
- Если пользователь говорит «покажи реквизиты», «все поля», «что внутри», «покажи подробнее» БЕЗ указания конкретного объекта — используй entity из предыдущего сообщения ассистента в истории.
- Если пользователь пишет «этот документ», «эта запись», «тот же», «предыдущий» — работай с тем же entity, что был в предыдущем запросе.
- «Покажи все реквизиты» без уточнения = select:null (вернуть все поля) для последнего entity из истории.

Правила $select — что включать:
- Для справочника (Catalog_): всегда включай Description, Code.
- Для документа (Document_): всегда включай Number, Date. Добавляй ключевые реквизиты шапки (Организация_Key, Контрагент_Key, СуммаДокумента и т.п.).
- Если пользователь просит «все поля» или «все реквизиты» — select:null (не задавай select).
- НЕ включай в select: Ref_Key, DataVersion, DeletionMark, Predefined, PredefinedDataName, IsFolder — они служебные.

Правила работы со ссылочными полями 1С:
- Поля-ссылки имеют суффикс _Key и содержат GUID, например: Организация_Key, Сотрудник_Key.
- Чтобы фильтровать по свойству связанного объекта — убери суффикс _Key и добавь /Свойство.
  Примеры фильтра: Организация/Description eq 'Название', Сотрудник/Code eq '000123'
- НЕ используй точку (Organization.Description) — только слеш (Организация/Description).
- НЕ переводи имена полей на английский — используй кириллические имена из схемы.
- Для поиска по наименованию связанного объекта: contains(Организация/Description,'текст')

Доступные объекты 1С:
{metadata}
"""

STEP2_SYSTEM = """\
Ты — помощник, отвечающий на вопросы о данных 1С:Предприятие.
Отвечай на русском языке. Используй HTML-теги Telegram для оформления:
  <b>жирный</b> — для заголовков, названий, итоговых значений
  <i>курсив</i> — для подписей, пояснений
  <code>моноширинный</code> — для кодов, номеров документов
Для списков используй символ «•» и перенос строки.
UUID (поля с суффиксом _Key) не показывай — они не нужны пользователю.
Служебные поля не показывай: DataVersion, DeletionMark, LineNumber, Predefined, IsFolder.
Для справочника: основное поле — <b>Наименование</b>; Code — если информативен, выводи как <code>Код</code>.
Для документа: основная строка — <b>№Номер</b> от ДД.ММ.ГГГГ, затем контрагент и сумма.
Даты выводи в формате ДД.ММ.ГГГГ; пустую дату (0001-01-01) пропускай.
Если поле — вложенный объект с Description — показывай его Description, не весь объект.
Если данных нет — скажи об этом прямо.
Если записей больше одной — покажи итоговую строку: <b>Всего: N</b>.
Не используй символы &, <, > вне HTML-тегов — экранируй их как &amp; &lt; &gt;.
Будь лаконичен.
"""


class RateLimiter:
    """Minimum interval between requests based on rpm setting."""
    def __init__(self, rpm: int):
        self._min_interval = 60.0 / max(rpm, 1)
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                log.debug("Rate limiter: ожидание %.1fs перед запросом к ИИ", wait)
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()


_rate_limiter: Optional[RateLimiter] = None


async def _ai_request(client: AsyncOpenAI, **kwargs):
    """Call chat.completions.create with client-side rate limiting and retry on 429."""
    import openai as _openai
    if _rate_limiter:
        await _rate_limiter.acquire()
    delays = [30, 60, 120]
    for attempt, delay in enumerate(delays + [None]):
        try:
            return await client.chat.completions.create(**kwargs)
        except _openai.RateLimitError:
            if delay is None:
                raise QueryError("Превышен лимит запросов к ИИ. Подождите минуту и попробуйте снова.")
            log.warning("Rate limit (429), повтор через %ds (попытка %d/%d)", delay, attempt + 1, len(delays))
            await asyncio.sleep(delay)
        except _openai.APIConnectionError as e:
            if delay is None:
                raise QueryError("Нет соединения с ИИ. Проверьте интернет и попробуйте снова.")
            log.warning("Ошибка соединения с ИИ (%s), повтор через %ds (попытка %d/%d)", e, delay, attempt + 1, len(delays))
            await asyncio.sleep(min(delay, 10))
        except _openai.AuthenticationError:
            raise QueryError("Ошибка авторизации в ИИ. Проверьте ai_api_key в env.json.")
        except _openai.APIStatusError as e:
            if e.status_code == 413 or (e.status_code == 400 and "too large" in str(e).lower()):
                raise TokenLimitError(str(e))
            provider_msg = None
            try:
                body = e.body if isinstance(e.body, dict) else {}
                provider_msg = body.get("error", {}).get("message")
            except Exception:
                pass
            if provider_msg:
                raise QueryError(f"Ошибка от провайдера ИИ (HTTP {e.status_code}): {provider_msg}")
            raise


async def ai_build_query(client: AsyncOpenAI, model: str,
                          user_message: str, metadata_text: str,
                          history: list[dict] | None = None) -> dict:
    """Step 1: AI returns OData query params as JSON.
    On TokenLimitError automatically retries with progressively smaller metadata."""
    # Levels of metadata reduction on token limit errors: 30 → 10 → 5 → 0 (names only)
    meta_levels = [metadata_text]
    for max_names in (10, 5, 0):
        meta_levels.append(format_metadata_for_prompt(_metadata_cache, max_names))

    for meta in meta_levels:
        system = STEP1_SYSTEM.format(metadata=meta)
        for attempt in range(2):
            extra = "" if attempt == 0 else " Верни ТОЛЬКО JSON без какого-либо текста до или после."
            messages = [{"role": "system", "content": system + extra}]
            if history:
                messages.extend(history)
            messages.append({"role": "user", "content": user_message})
            try:
                response = await _ai_request(
                    client,
                    model=model,
                    messages=messages,
                    temperature=0,
                    max_tokens=512,
                )
            except TokenLimitError:
                approx = len(system) // 4
                log.warning("Промпт слишком большой (~%d токенов), уменьшаю метаданные", approx)
                break  # go to next meta level
            text = response.choices[0].message.content or ""
            text = text.strip()
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            log.warning("AI Step 1 attempt %d: не удалось распарсить JSON: %s", attempt + 1, text[:200])
        else:
            # inner loop exhausted without break — JSON parse failed twice, not a size issue
            raise QueryError("Не удалось сформировать OData-запрос. Попробуйте переформулировать вопрос.")

    raise QueryError("Промпт слишком большой даже с минимальными метаданными. Попробуйте позже.")


async def ai_format_response(client: AsyncOpenAI, model: str,
                              user_message: str, records: list,
                              total: Optional[int], entity: str) -> str:
    """Step 2: AI formats raw records into human-readable Russian text."""
    count_note = ""
    if len(records) == 0:
        records_text = "[]"
    else:
        shown = records[:20]
        records_text = json.dumps(shown, ensure_ascii=False, indent=2)
        if len(records) > 20 or total:
            actual = total or len(records)
            count_note = f"\n\n(Показаны первые {len(shown)} из {actual} записей)"

    prompt = (
        f"Вопрос пользователя: {user_message}\n"
        f"Объект 1С: {entity}\n"
        f"Результаты запроса:\n{records_text}{count_note}"
    )
    response = await _ai_request(
        client,
        model=model,
        messages=[
            {"role": "system", "content": _master_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=1024,
    )
    return (response.choices[0].message.content or "").strip()


# ---------------------------------------------------------------------------
# Telegram handlers
# ---------------------------------------------------------------------------

# Bot state (set in main)
_cfg: dict = {}
_ai_client: Optional[AsyncOpenAI] = None
_metadata_cache: dict = {}
_metadata_text: str = ""
_env_file: str = "env.json"
_cache_dir: str = ".cache"
_master_prompt: str = STEP2_SYSTEM
_master_prompt_file: Path = Path("master_prompt.md")

# Conversation history per chat: chat_id → [{"role": ..., "content": ...}, ...]
_history: dict[int, list[dict]] = {}
HISTORY_MAX_TURNS = 6  # last N user+assistant pairs


def load_master_prompt() -> str:
    if _master_prompt_file.exists():
        text = _master_prompt_file.read_text(encoding="utf-8").strip()
        log.info("Мастер-промпт загружен из %s (%d символов)", _master_prompt_file, len(text))
        return text
    log.info("master_prompt.md не найден, используется промпт по умолчанию")
    return STEP2_SYSTEM


async def _load_metadata(force: bool = False) -> None:
    global _metadata_cache, _metadata_text, _master_prompt
    _master_prompt = load_master_prompt()
    loop = asyncio.get_event_loop()
    meta = await loop.run_in_executor(
        None, get_metadata_summary, _env_file, _cache_dir, force
    )
    _metadata_cache = meta
    _metadata_text = format_metadata_for_prompt(meta)
    total = sum(len(v) for v in meta.values())
    approx_tokens = len(_metadata_text) // 4
    log.info("Metadata ready: %d objects, ~%d токенов в промпте", total, approx_tokens)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat:
        _history.pop(update.effective_chat.id, None)
    total = sum(len(v) for v in _metadata_cache.values())
    text = (
        "👋 <b>Привет!</b> Я умею отвечать на вопросы о данных вашей базы <b>1С:Предприятие</b>.\n\n"
        f"📦 Загружено объектов: <b>{total}</b>\n\n"
        "<b>Примеры запросов:</b>\n"
        "  • Покажи список организаций\n"
        "  • Сколько контрагентов в базе?\n"
        "  • Найди сотрудников с фамилией Иванов\n"
        "  • Последние 10 документов\n\n"
        "<b>Команды:</b>\n"
        "  /refresh — обновить список объектов из 1С"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def handle_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🔄 Обновляю метаданные из 1С...", parse_mode="HTML")
    try:
        await _load_metadata(force=True)
        total = sum(len(v) for v in _metadata_cache.values())
        await update.message.reply_text(f"✅ Готово. Загружено объектов: <b>{total}</b>", parse_mode="HTML")
    except Exception as e:
        log.exception("Ошибка обновления метаданных")
        await update.message.reply_text(f"❌ <b>Ошибка обновления:</b> {_esc(str(e))}", parse_mode="HTML")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = (update.message.text or "").strip()
    if not user_text:
        return

    log.info("User %s: %s", update.effective_user.id, user_text[:100])
    await update.message.chat.send_action("typing")

    chat_id = update.effective_chat.id if update.effective_chat else 0
    history = _history.get(chat_id, [])

    try:
        # Step 1: build OData query (с историей диалога)
        query = await ai_build_query(_ai_client, _cfg["ai_model"], user_text, _metadata_text, history)
        log.info("Query: %s", query)

        entity = query.get("entity", "")
        if not entity:
            await update.message.reply_text("⚠️ Не смог определить нужный объект в базе 1С. Попробуйте уточнить запрос.", parse_mode="HTML")
            return

        # Validate entity against known metadata
        matched = False
        for type_en, names in _metadata_cache.items():
            prefix = f"{type_en}_"
            if entity.startswith(prefix):
                obj_name = entity[len(prefix):]
                if obj_name in names:
                    matched = True
                    break
        if not matched:
            log.warning("Entity '%s' not in metadata", entity)

        top = min(int(query.get("top") or 20), 50)

        # Validate $select against real fields from cached $metadata
        select = query.get("select")
        orderby = query.get("orderby")
        fields = await asyncio.get_event_loop().run_in_executor(
            None, get_entity_fields, entity, _cfg["odata_url"], _cache_dir
        )
        if fields:
            log.info("Fields for %s: %s", entity, fields)
            if select:
                raw_select = select[len("$select="):] if select.startswith("$select=") else select
                valid = [f.strip() for f in raw_select.split(",") if f.strip() in fields]
                select = ",".join(valid) if valid else None
                if select != raw_select:
                    log.info("$select скорректирован: %s → %s", raw_select, select)
            if orderby:
                raw_orderby = orderby[len("$orderby="):] if orderby.startswith("$orderby=") else orderby
                field_name = raw_orderby.split()[0]
                if field_name not in fields:
                    log.info("$orderby '%s' не найден в полях, убираем", field_name)
                    orderby = None

        auth = make_auth_header(_cfg["odata_user"], _cfg["odata_password"])

        # Step 2: execute OData
        records, total = await execute_odata_query(
            odata_url=_cfg["odata_url"],
            auth_header=auth,
            entity=entity,
            filter_expr=query.get("filter"),
            select=select,
            orderby=orderby,
            top=top,
        )

        # Step 3: format response
        answer = await ai_format_response(
            _ai_client, _cfg["ai_model"], user_text, records, total, entity
        )

        # Сохранить ход диалога: вопрос + краткий контекст ответа для следующего запроса
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": f'{{"entity":"{entity}","explanation":"{query.get("explanation","")}"}}' })
        _history[chat_id] = history[-(HISTORY_MAX_TURNS * 2):]  # оставить последние N пар

    except ODataError as e:
        log.error("OData error: %s (status=%s)", e, e.status_code)
        if e.status_code == 401:
            answer = "🔒 <b>Ошибка авторизации в 1С.</b> Проверьте логин и пароль."
        elif e.status_code == 404:
            answer = "🔍 <b>Объект не найден в OData.</b> Возможно, он не опубликован в базе 1С."
        elif e.status_code >= 500:
            answer = "🛑 <b>Ошибка сервера 1С.</b> Попробуйте позже."
        else:
            answer = f"❌ <b>Не удалось подключиться к 1С:</b> {_esc(str(e))}"
    except QueryError as e:
        answer = f"⚠️ {_esc(str(e))}"
    except Exception:
        log.exception("Unexpected error")
        answer = "💥 Произошла непредвиденная ошибка. Попробуйте позже."

    # Truncate if needed (Telegram limit: 4096 chars)
    if len(answer) > 4000:
        answer = answer[:4000] + "... (сообщение сокращено)"

    await update.message.reply_text(answer, parse_mode="HTML")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("PTB error", exc_info=context.error)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global _cfg, _ai_client, _env_file, _cache_dir

    parser = argparse.ArgumentParser(description="1С OData Telegram Bot")
    parser.add_argument("--env-file", default="env.json")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--cache-dir", default=".cache")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.getLogger().setLevel(args.log_level)

    _env_file = args.env_file
    _cache_dir = args.cache_dir

    global _rate_limiter
    _cfg = load_config(args.env_file, args.profile)
    _ai_client = AsyncOpenAI(api_key=_cfg["ai_api_key"], base_url=_cfg["ai_base_url"], max_retries=0)
    _rate_limiter = RateLimiter(rpm=_cfg["ai_rpm"])
    log.info("Rate limiter: %d rpm (интервал %.1fs)", _cfg["ai_rpm"], 60.0 / _cfg["ai_rpm"])

    # Load metadata synchronously at startup
    log.info("Загрузка метаданных 1С...")
    try:
        asyncio.run(_load_metadata(force=False))
    except Exception as e:
        log.error("Не удалось загрузить метаданные при старте: %s", e)
        log.warning("Бот запускается без метаданных. Используйте /refresh после устранения ошибки.")

    app = ApplicationBuilder().token(_cfg["telegram_token"]).build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("refresh", handle_refresh))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    log.info("Бот запущен. Нажмите Ctrl+C для остановки.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
