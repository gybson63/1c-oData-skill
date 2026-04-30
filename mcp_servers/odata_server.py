#!/usr/bin/env python3
"""MCP-сервер для работы с OData-интерфейсом 1С:Предприятие.

Предоставляет инструменты для:
- Получения метаданных (список сущностей, поля)
- Выполнения OData-запросов (с фильтрами, $select, $orderby)
- Подсчёта записей ($count / $inlinecount)
- Получения записи по Ref_Key
- Справки по синтаксису OData 1С

Credentials передаются через переменные окружения:
  ODATA_URL, ODATA_USER, ODATA_PASSWORD

Запуск:
  python mcp_servers/odata_server.py
"""

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
import urllib.request
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stderr,
)
log = logging.getLogger("1c-odata-mcp")

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

ODATA_URL = os.environ.get("ODATA_URL", "")
ODATA_USER = os.environ.get("ODATA_USER", "")
ODATA_PASSWORD = os.environ.get("ODATA_PASSWORD", "")
CACHE_DIR = os.environ.get("ODATA_CACHE_DIR", ".cache")
ENV_FILE = os.environ.get("ODATA_ENV_FILE", "env.json")

# Path to odata-cfg-info.py script
SCRIPT_PATH = Path(__file__).parent.parent / "skills" / "1cconfinfo" / "scripts" / "odata-cfg-info.py"

mcp = FastMCP("1c-odata", instructions="MCP-сервер для работы с OData-интерфейсом 1С:Предприятие")


# ---------------------------------------------------------------------------
# OData Reference Data
# ---------------------------------------------------------------------------

ODATA_REFERENCE: dict[str, str] = {
    "count": (
        "/$count — возвращает целое число (количество записей), а не массив объектов.\n"
        "URL: /<Entity>/$count?$format=json[&$filter=...]\n"
        "Пример: /Catalog_Сотрудники/$count?$format=json&$filter=DeletionMark eq false\n"
        "Ответ: plain integer, например 31\n"
        "НЕ используй $top и $select вместе с /$count — они игнорируются сервером 1С."
    ),
    "filter": (
        "$filter — фильтрация записей. Операторы: eq, ne, gt, ge, lt, le, and, or, not.\n"
        "Функции: contains(поле,'текст'), startswith(поле,'текст'), endswith(поле,'текст').\n"
        "Пример: DeletionMark eq false and contains(Description,'Иванов')\n"
        "Даты: Date ge datetime'2025-01-01T00:00:00' and Date le datetime'2025-12-31T23:59:59'\n"
        "Логические: Проведен eq true\n"
        "Ссылки: Организация/Description eq 'ООО Ромашка'  (слеш, не точка!)"
    ),
    "orderby": (
        "$orderby — сортировка. Формат: Поле asc | desc. Несколько: Поле1 desc, Поле2 asc.\n"
        "Примеры: Date desc   Number asc   СуммаДокумента desc\n"
        "Ссылочные поля НЕ поддерживают сортировку — только прямые реквизиты."
    ),
    "select": (
        "$select — выбор полей. Перечисли через запятую: Description,Code,ИНН\n"
        "Для справочника всегда включай: Description, Code.\n"
        "Для документа всегда включай: Number, Date.\n"
        "Служебные поля НЕ включай: Ref_Key, DataVersion, DeletionMark, Predefined, PredefinedDataName, IsFolder.\n"
        "Ссылочные поля: включай суффикс _Key, например Организация_Key — будет GUID."
    ),
    "top": (
        "$top — ограничение количества возвращаемых записей. Целое число.\n"
        "Пример: $top=10\n"
        "Максимум: 50. Не используй совместно с /$count."
    ),
    "skip": (
        "$skip — пропустить N первых записей (пагинация).\n"
        "Пример: $skip=20&$top=10 — вторая страница по 10 записей.\n"
        "1С OData v3 поддерживает $skip."
    ),
    "expand": (
        "$expand — раскрыть связанный объект inline.\n"
        "Пример: $expand=Организация — добавляет объект Организация со всеми полями вместо GUID.\n"
        "Используй осторожно: увеличивает объём ответа.\n"
        "Для нескольких: $expand=Организация,Сотрудник"
    ),
    "date_format": (
        "Формат дат в OData 1С (v3): datetime'YYYY-MM-DDT00:00:00'\n"
        "Примеры:\n"
        "  datetime'2025-01-01T00:00:00'  — начало дня\n"
        "  datetime'2025-12-31T23:59:59'  — конец дня\n"
        "Пустая дата 1С: datetime'0001-01-01T00:00:00' — означает «не задана», пропускай."
    ),
    "guid": (
        "GUID в OData 1С: guid'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'\n"
        "Пример фильтра по Ref_Key: Ref_Key eq guid'....'\n"
        "Поля _Key содержат GUID ссылки."
    ),
    "string_functions": (
        "Строковые функции OData 1С (v3):\n"
        "  contains(поле,'текст')       — содержит подстроку\n"
        "  startswith(поле,'текст')     — начинается с\n"
        "  endswith(поле,'текст')       — заканчивается на\n"
        "Регистронезависимый поиск в 1С не гарантирован."
    ),
    "navigation_properties": (
        "Навигационные свойства — связанные объекты через слеш в $filter:\n"
        "  Организация/Description eq 'ООО Ромашка'\n"
        "  Сотрудник/Code eq '000123'\n"
        "НЕ используй точку: Организация.Description — ошибка в 1С OData.\n"
        "В $select навигационные свойства НЕ поддерживаются."
    ),
    "inlinecount": (
        "$inlinecount=allpages — возвращает общее количество записей вместе с данными.\n"
        "Пример URL: /Catalog_Сотрудники?$top=10&$inlinecount=allpages&$format=json\n"
        "Ответ содержит: {\"odata.count\":\"31\",\"value\":[...]}\n"
        "Предпочитай /$count если нужно ТОЛЬКО число без записей."
    ),
}


# ---------------------------------------------------------------------------
# Type mapping
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

class ODataError(Exception):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


def _make_auth_header(user: str, password: str) -> dict:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _strip_prefix(value: str, prefix: str) -> str:
    return value[len(prefix):] if value.startswith(prefix) else value


def _qv(value: str, safe: str = "") -> str:
    encoded = urllib.parse.quote(value, safe=safe)
    return encoded.replace(" ", "%20")


def _sanitize_url(url: str) -> str:
    if "?" in url:
        path, qs = url.split("?", 1)
        qs = re.sub(r"[^\x21-\x7E]", lambda m: urllib.parse.quote(m.group()), qs)
        return f"{path}?{qs}"
    return url


def _http_get(url: str, headers: dict) -> bytes:
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


# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------

def _build_query_url(base_url: str, entity: str, filter_expr: Optional[str],
                     select: Optional[str], orderby: Optional[str],
                     top: int = 20, skip: int = 0) -> str:
    params = [f"$top={top}", "$format=json"]
    if skip > 0:
        params.append(f"$skip={skip}")
    if filter_expr:
        fval = _strip_prefix(filter_expr, "$filter=")
        params.append("$filter=" + _qv(fval, safe="(),'/"))
    if orderby:
        oval = _strip_prefix(orderby, "$orderby=")
        oval = ",".join(f.strip() for f in oval.split(","))
        params.append("$orderby=" + _qv(oval, safe=",_"))
    if select:
        sval = _strip_prefix(select, "$select=")
        sval = ",".join(f.strip() for f in sval.split(","))
        params.append("$select=" + _qv(sval, safe=",_"))
    encoded_entity = _qv(entity)
    qs = "&".join(params)
    return f"{base_url.rstrip('/')}/{encoded_entity}?{qs}"


def _build_count_url(base_url: str, entity: str, filter_expr: Optional[str]) -> str:
    params = ["$format=json"]
    if filter_expr:
        fval = _strip_prefix(filter_expr, "$filter=")
        params.append("$filter=" + _qv(fval, safe="(),'/"))
    encoded_entity = _qv(entity)
    qs = "&".join(params)
    return f"{base_url.rstrip('/')}/{encoded_entity}/$count?{qs}"


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------

_EXCLUDE_SUFFIXES = ("ПрисоединенныеФайлы",)
_EXCLUDE_PREFIXES = ("Удалить",)
_TYPE_HEADER_RE = re.compile(r"^\s{2}(.+?)\s+\((\w+)\):\s+\d+")

_NS_LIST = [
    "http://schemas.microsoft.com/ado/2009/11/edm",
    "http://schemas.microsoft.com/ado/2008/09/edm",
    "",
]


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


def _parse_full_output(text: str) -> dict:
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


SUMMARY_CACHE_TTL = 3600


def _get_metadata_summary(force_refresh: bool = False) -> dict:
    """Return metadata dict {TypeEN: [names]} with file cache."""
    cache_path = Path(CACHE_DIR) / "metadata_summary.json"
    if not force_refresh and cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < SUMMARY_CACHE_TTL:
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)

    if not SCRIPT_PATH.exists():
        raise RuntimeError(f"Скрипт не найден: {SCRIPT_PATH}")

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "-Mode", "full", "-EnvFile", ENV_FILE, "-CacheDir", CACHE_DIR],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"odata-cfg-info.py error:\n{result.stderr.strip()}")

    meta = _parse_full_output(result.stdout)

    Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return meta


def _get_entity_fields_from_cache(entity_name: str) -> list[str]:
    """Parse cached $metadata XML and return property names for the given entity."""
    url_hash = hashlib.md5(ODATA_URL.rstrip("/").encode()).hexdigest()[:8]
    cache_file = Path(CACHE_DIR) / f"odata_metadata_{url_hash}.xml"
    if not cache_file.exists():
        return []
    try:
        root = ET.parse(str(cache_file)).getroot()
    except ET.ParseError:
        return []

    ns_edmx = "http://schemas.microsoft.com/ado/2007/06/edmx"
    data_services = root.find(f"{{{ns_edmx}}}DataServices") or root
    schema = _find_ns(data_services, "Schema")
    if schema is None:
        return []

    target_names = {entity_name, entity_name + "_Type"}
    for et in _findall_ns(schema, "EntityType"):
        if et.get("Name") in target_names:
            return [p.get("Name") for p in _findall_ns(et, "Property") if p.get("Name") is not None]

    return []


# ---------------------------------------------------------------------------
# OData operations (sync, called via MCP tools)
# ---------------------------------------------------------------------------

def _do_odata_query(entity: str, filter_expr: Optional[str],
                    select: Optional[str], orderby: Optional[str],
                    top: int = 20, skip: int = 0) -> list[dict]:
    """Execute OData query with 400 fallback strategy."""
    auth = _make_auth_header(ODATA_USER, ODATA_PASSWORD)

    url = _build_query_url(ODATA_URL, entity, filter_expr, select, orderby, top, skip)
    log.info("OData GET: %s", url)

    try:
        raw = _http_get(url, auth)
    except ODataError as e:
        if e.status_code == 400:
            if select or orderby:
                log.warning("OData 400, retry without $select/$orderby")
                url2 = _build_query_url(ODATA_URL, entity, filter_expr, None, None, top, skip)
                try:
                    raw = _http_get(url2, auth)
                except ODataError as e2:
                    if e2.status_code == 400 and filter_expr:
                        log.warning("OData 400 again, retry without $filter")
                        url3 = _build_query_url(ODATA_URL, entity, None, None, None, top, skip)
                        raw = _http_get(url3, auth)
                    else:
                        raise
            elif filter_expr:
                log.warning("OData 400 in $filter, retry without $filter")
                url2 = _build_query_url(ODATA_URL, entity, None, None, None, top, skip)
                raw = _http_get(url2, auth)
            else:
                raise
        else:
            raise

    data = json.loads(raw.decode("utf-8"))
    return data.get("value", [])


def _do_odata_count(entity: str, filter_expr: Optional[str] = None) -> int:
    """Count records. Tries /$count first, falls back to $inlinecount."""
    auth = _make_auth_header(ODATA_USER, ODATA_PASSWORD)

    # Attempt 1: /$count
    url = _build_count_url(ODATA_URL, entity, filter_expr)
    log.info("OData COUNT: %s", url)
    try:
        raw = _http_get(url, auth)
        text = raw.decode("utf-8").strip().strip('"')
        try:
            return int(text)
        except ValueError:
            parsed = json.loads(raw.decode("utf-8"))
            if isinstance(parsed, int):
                return parsed
            return int(parsed.get("value", 0))
    except ODataError as e:
        if e.status_code not in (404, 400):
            raise
        log.warning("/$count returned %d, trying $inlinecount", e.status_code)

    # Attempt 2: $inlinecount=allpages
    params = ["$top=1", "$format=json", "$inlinecount=allpages"]
    if filter_expr:
        fval = _strip_prefix(filter_expr, "$filter=")
        params.append("$filter=" + _qv(fval, safe="(),'/"))
    encoded_entity = _qv(entity)
    url2 = f"{ODATA_URL.rstrip('/')}/{encoded_entity}?{'&'.join(params)}"
    log.info("OData INLINECOUNT: %s", url2)
    raw2 = _http_get(url2, auth)
    data = json.loads(raw2.decode("utf-8"))
    for key in ("odata.count", "__count", "odata.totalCount"):
        if key in data:
            return int(data[key])
    raise ODataError("Не удалось получить количество записей: сервер не вернул odata.count")


def _do_get_record(entity: str, ref_key: str, select: Optional[str] = None) -> Optional[dict]:
    """Get a single record by Ref_Key (GUID)."""
    auth = _make_auth_header(ODATA_USER, ODATA_PASSWORD)
    filter_expr = f"Ref_Key eq guid'{ref_key}'"
    url = _build_query_url(ODATA_URL, entity, filter_expr, select, None, top=1)
    log.info("OData GET record: %s", url)
    raw = _http_get(url, auth)
    data = json.loads(raw.decode("utf-8"))
    records = data.get("value", [])
    return records[0] if records else None


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def odata_list_entities(force_refresh: bool = False) -> str:
    """Список всех опубликованных сущностей 1С через OData.

    Возвращает JSON с группировкой по типам:
    Catalog, Document, InformationRegister и т.д.
    Каждый тип содержит массив имён объектов (без префикса).

    Args:
        force_refresh: Принудительно обновить кэш метаданных (по умолчанию False).
    """
    try:
        meta = _get_metadata_summary(force_refresh=force_refresh)
    except Exception as e:
        return json.dumps({"error": f"Ошибка получения метаданных: {e}"}, ensure_ascii=False)

    total = sum(len(v) for v in meta.values())
    result = {
        "total_objects": total,
        "types": {},
    }
    for type_en, names in meta.items():
        type_ru = TYPE_RU.get(type_en, type_en)
        result["types"][type_en] = {
            "name_ru": type_ru,
            "count": len(names),
            "entities": names,
        }
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def odata_get_entity_fields(entity_name: str) -> str:
    """Поля конкретной сущности 1С из кэша $metadata.

    Возвращает список свойств (реквизитов) для указанного объекта.

    Args:
        entity_name: Полное имя сущности, например Catalog_Сотрудники или Document_РеализацияТоваровУслуг.
    """
    fields = _get_entity_fields_from_cache(entity_name)
    if fields:
        return json.dumps({
            "entity": entity_name,
            "fields": fields,
            "count": len(fields),
        }, ensure_ascii=False, indent=2)
    return json.dumps({
        "entity": entity_name,
        "fields": [],
        "error": f"Поля для '{entity_name}' не найдены в кэше $metadata. Вызовите odata_list_entities(force_refresh=true) для обновления.",
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def odata_query(
    entity: str,
    filter_expr: str = "",
    select: str = "",
    orderby: str = "",
    top: int = 20,
    skip: int = 0,
) -> str:
    """Выполнить OData-запрос к 1С.

    Возвращает массив записей в формате JSON.
    При ошибке 400 автоматически повторяет запрос без $select/$orderby/$filter.

    Args:
        entity: Имя сущности с префиксом, например Catalog_Сотрудники, Document_ЗаявкаНаОтпуск.
        filter_expr: Условие фильтра ($filter), например "DeletionMark eq false and contains(Description,'Иванов')".
        select: Список полей через запятую ($select), например "Ref_Key,Description,Code".
        orderby: Сортировка ($orderby), например "Description asc".
        top: Количество записей (максимум 50, по умолчанию 20).
        skip: Пропустить N записей (пагинация, по умолчанию 0).
    """
    top = min(max(top, 1), 50)
    _filter = filter_expr if filter_expr else None
    _select = select if select else None
    _orderby = orderby if orderby else None

    try:
        records = _do_odata_query(entity, _filter, _select, _orderby, top, skip)
        return json.dumps({
            "entity": entity,
            "count": len(records),
            "value": records,
        }, ensure_ascii=False, indent=2)
    except ODataError as e:
        return json.dumps({
            "error": str(e),
            "status_code": e.status_code,
            "entity": entity,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Неожиданная ошибка: {e}"}, ensure_ascii=False, indent=2)


@mcp.tool()
def odata_count(entity: str, filter_expr: str = "") -> str:
    """Подсчитать количество записей сущности 1С через OData.

    Использует /$count, при ошибке — fallback на $inlinecount=allpages.

    Args:
        entity: Имя сущности с префиксом, например Catalog_Сотрудники.
        filter_expr: Условие фильтра ($filter), например "DeletionMark eq false".
    """
    _filter = filter_expr if filter_expr else None
    try:
        count_val = _do_odata_count(entity, _filter)
        return json.dumps({
            "entity": entity,
            "count": count_val,
            "filter": filter_expr or None,
        }, ensure_ascii=False, indent=2)
    except ODataError as e:
        return json.dumps({
            "error": str(e),
            "status_code": e.status_code,
            "entity": entity,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Неожиданная ошибка: {e}"}, ensure_ascii=False, indent=2)


@mcp.tool()
def odata_get_record(entity: str, ref_key: str, select: str = "") -> str:
    """Получить конкретную запись 1С по Ref_Key (GUID) через OData.

    Args:
        entity: Имя сущности с префиксом, например Catalog_Сотрудники.
        ref_key: UUID записи (Ref_Key), например '12345678-1234-1234-1234-123456789012'.
        select: Список полей через запятую (опционально).
    """
    _select = select if select else None
    try:
        record = _do_get_record(entity, ref_key, _select)
        if record:
            return json.dumps(record, ensure_ascii=False, indent=2)
        return json.dumps({
            "error": f"Запись не найдена: {entity} с Ref_Key={ref_key}",
        }, ensure_ascii=False, indent=2)
    except ODataError as e:
        return json.dumps({
            "error": str(e),
            "status_code": e.status_code,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Неожиданная ошибка: {e}"}, ensure_ascii=False, indent=2)


@mcp.tool()
def odata_reference(topic: str) -> str:
    """Справка по синтаксису OData для 1С:Предприятие.

    Возвращает документацию по запрошенной теме: $count, $filter, $select, $orderby,
    $top, $skip, $expand, формат дат, GUID, строковые функции, навигационные свойства.

    Args:
        topic: Тема справки. Доступные темы: count, filter, orderby, select, top, skip,
               expand, date_format, guid, string_functions, navigation_properties, inlinecount.
    """
    doc = ODATA_REFERENCE.get(topic)
    if doc:
        return doc
    available = ", ".join(ODATA_REFERENCE.keys())
    return f"Тема '{topic}' не найдена. Доступные темы: {available}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not ODATA_URL:
        log.error("ODATA_URL не задан. Установите переменную окружения или передайте через env в конфигурации MCP.")
        sys.exit(1)
    log.info("1C OData MCP Server starting: %s", ODATA_URL)
    mcp.run(transport="stdio")