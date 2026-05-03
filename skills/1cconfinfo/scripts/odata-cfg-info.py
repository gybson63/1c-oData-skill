#!/usr/bin/env python3
"""Получение информации о конфигурации 1С из OData $metadata.

Анализирует состав опубликованных объектов (справочники, документы,
регистры и т.д.) и выводит сводку в одном из трёх режимов:

- **brief**    — одна строка с количеством объектов
- **overview** — сводная таблица по типам (по умолчанию)
- **full**     — полный список всех объектов по типам

Использует :mod:`lib.metadata_parser` для парсинга XML
и :mod:`lib.exceptions` для типизированных ошибок.

Пример запуска::

    python skills/1cconfinfo/scripts/odata-cfg-info.py -Mode overview
    python skills/1cconfinfo/scripts/odata-cfg-info.py -Mode full -ForceRefresh
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from typing import Optional

# Pylance: reconfigure существует в CPython runtime, но не в type stubs
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)

# --- Добавить корень проекта в sys.path для доступа к lib/ ---
_PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# --- Импорт общей библиотеки ---
from bot_lib.exceptions import ConfigError, ODataHTTPError, ODataConnectionError
from bot_lib.metadata_parser import (
    PREFIX_TO_TYPE,
    TYPE_RU,
    TYPE_ORDER,
    classify_entity_sets,
)


# ═══════════════════════════════════════════════════════════════════════════
# ODataConfigInfo — анализ конфигурации 1С через $metadata
# ═══════════════════════════════════════════════════════════════════════════


class ODataConfigInfo:
    """Анализатор конфигурации 1С через OData ``$metadata``.

    Принимает строку XML метаданных, разбирает её и предоставляет
    три формата вывода: :meth:`get_brief`, :meth:`get_overview`, :meth:`get_full`.

    Args:
        metadata_xml: содержимое ``$metadata`` XML.
        cache_note:   пояснение о происхождении данных (кэш / сеть).

    Example::

        info = ODataConfigInfo(xml_content, cache_note=" [кэш, 120с назад]")
        print(info.get_overview())
    """

    def __init__(self, metadata_xml: str, cache_note: str = "") -> None:
        self._xml = metadata_xml
        self._cache_note = cache_note

        type_counts, type_names, entity_sets, namespace = classify_entity_sets(metadata_xml)

        self._type_counts: OrderedDict[str, int] = type_counts
        self._type_names: dict[str, list[str]] = type_names
        self._entity_sets = entity_sets
        self._namespace: Optional[str] = namespace or None

        self._total_objects = sum(type_counts.values())
        self._total_entity_sets = len(entity_sets)

    # --- Публичные свойства ---------------------------------------------------

    @property
    def total_objects(self) -> int:
        """Общее количество объектов 1С (без учёта неизвестных префиксов)."""
        return self._total_objects

    @property
    def total_entity_sets(self) -> int:
        """Общее количество EntitySet в ``$metadata``."""
        return self._total_entity_sets

    @property
    def namespace(self) -> Optional[str]:
        """Пространство имён (обычно — имя конфигурации)."""
        return self._namespace

    @property
    def type_counts(self) -> OrderedDict[str, int]:
        """Количество объектов по типам ``{type_name: count}``."""
        return self._type_counts

    @property
    def type_names(self) -> dict[str, list[str]]:
        """Имена объектов по типам ``{type_name: [obj_name, ...]}``."""
        return self._type_names

    # --- Форматы вывода -------------------------------------------------------

    def get_brief(self, odata_url: str = "") -> str:
        """Вернуть однострочную сводку.

        Args:
            odata_url: URL OData для отображения (необязательно).
        """
        url_part = f"OData: {odata_url}" if odata_url else "OData"
        return (
            f"{url_part}{self._cache_note} | "
            f"{self._total_objects} объектов ({self._total_entity_sets} сущностей)"
        )

    def get_overview(self, odata_url: str = "") -> str:
        """Вернуть краткий обзор — сводная таблица по типам.

        Args:
            odata_url: URL OData для отображения (необязательно).
        """
        lines: list[str] = []

        lines.append(f"=== OData конфигурация{self._cache_note} ===")
        lines.append("")
        if odata_url:
            lines.append(f"URL:            {odata_url}")
        if self._namespace is not None:
            lines.append(f"Пространство:   {self._namespace}")
        lines.append(f"Всего сущностей: {self._total_entity_sets}")
        lines.append(f"Объектов 1С:    {self._total_objects}")
        lines.append("")
        lines.append(f"--- Состав (опубликовано в OData: {self._total_objects} объектов) ---")
        lines.append("")

        max_len = max((len(TYPE_RU.get(t, t)) for t in self._type_counts), default=10)
        if max_len < 10:
            max_len = 10

        for type_name in TYPE_ORDER:
            if type_name in self._type_counts:
                ru = TYPE_RU.get(type_name, type_name)
                lines.append(f"  {ru.ljust(max_len)}  {self._type_counts[type_name]}")

        return "\n".join(lines)

    def get_full(self, odata_url: str = "", cache_file: str = "") -> str:
        """Вернуть полный отчёт — все объекты по типам.

        Args:
            odata_url:  URL OData для отображения (необязательно).
            cache_file: путь к файлу кэша для отображения (необязательно).
        """
        lines: list[str] = []

        lines.append(f"=== OData конфигурация{self._cache_note} ===")
        lines.append("")
        if odata_url:
            lines.append(f"URL:             {odata_url}")
        if self._namespace:
            lines.append(f"Пространство:    {self._namespace}")
        if cache_file:
            lines.append(f"Кэш:             {cache_file}")
        lines.append(f"Всего сущностей: {self._total_entity_sets}")
        lines.append(f"Объектов 1С:     {self._total_objects}")
        lines.append("")
        lines.append(f"--- Состав (опубликовано в OData: {self._total_objects} объектов) ---")
        lines.append("")

        for type_name in TYPE_ORDER:
            if type_name not in self._type_counts:
                continue
            ru = TYPE_RU.get(type_name, type_name)
            count = self._type_counts[type_name]
            lines.append(f"  {ru} ({type_name}): {count}")
            for obj_name in sorted(self._type_names[type_name]):
                lines.append(f"    {obj_name}")
        lines.append("")

        # Unknown prefixes
        unknown = self._get_unknown_entities()
        if unknown:
            lines.append(f"--- Прочие сущности ({len(unknown)}) ---")
            for u in unknown:
                lines.append(f"  {u}")

        return "\n".join(lines)

    # --- Приватные методы -----------------------------------------------------

    def _get_unknown_entities(self) -> list[str]:
        """Получить EntitySet с неизвестными префиксами."""
        unknown: list[str] = []
        for es in self._entity_sets:
            name = es.get("Name", "")
            if not any(name.startswith(p) for p in PREFIX_TO_TYPE):
                unknown.append(name)
        return unknown


# ═══════════════════════════════════════════════════════════════════════════
# MetadataCache — кэширование $metadata XML на диск
# ═══════════════════════════════════════════════════════════════════════════


class MetadataCache:
    """Файловый кэш для ``$metadata`` XML.

    Args:
        cache_dir: директория для хранения кэша.
        cache_ttl: время жизни кэша в секундах (0 = бессрочный).

    Example::

        cache = MetadataCache(".cache", ttl=3600)
        xml = cache.load(url)
        if xml is None:
            xml = fetch_metadata(url)
            cache.save(url, xml)
    """

    def __init__(self, cache_dir: str, ttl: int = 3600) -> None:
        self._dir = cache_dir
        self._ttl = ttl

    def _cache_path(self, odata_url: str) -> str:
        """Получить путь к файлу кэша для данного URL."""
        url_hash = hashlib.md5(odata_url.encode()).hexdigest()[:8]
        return os.path.join(self._dir, f"odata_metadata_{url_hash}.xml")

    def load(self, odata_url: str) -> Optional[str]:
        """Загрузить метаданные из кэша.

        Returns:
            Строка XML или ``None`` если кэш отсутствует / устарел.
        """
        path = self._cache_path(odata_url)
        if not os.path.isfile(path):
            return None

        if self._ttl > 0:
            age = time.time() - os.path.getmtime(path)
            if age > self._ttl:
                logger.info("Cache expired for %s (age=%.0fs, ttl=%ds)", odata_url, age, self._ttl)
                return None

        with open(path, encoding="utf-8") as f:
            content = f.read()
        logger.debug("Loaded metadata from cache: %s", path)
        return content

    def save(self, odata_url: str, content: str) -> str:
        """Сохранить метаданные в кэш.

        Returns:
            Путь к сохранённому файлу.
        """
        path = self._cache_path(odata_url)
        os.makedirs(self._dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.debug("Saved metadata to cache: %s", path)
        return path

    def get_age(self, odata_url: str) -> Optional[int]:
        """Получить возраст кэша в секундах или ``None`` если кэша нет."""
        path = self._cache_path(odata_url)
        if not os.path.isfile(path):
            return None
        return int(time.time() - os.path.getmtime(path))

    def get_path(self, odata_url: str) -> str:
        """Получить путь к файлу кэша (даже если он не существует)."""
        return self._cache_path(odata_url)


# ═══════════════════════════════════════════════════════════════════════════
# Fetch metadata from OData
# ═══════════════════════════════════════════════════════════════════════════


def fetch_metadata(
    odata_url: str,
    username: str = "",
    password: str = "",
    timeout: int = 30,
) -> str:
    """Получить ``$metadata`` XML по сети.

    Args:
        odata_url: базовый URL OData.
        username:  имя пользователя 1С (Basic Auth).
        password:  пароль пользователя 1С.
        timeout:   таймаут запроса в секундах.

    Returns:
        Строка с содержимым ``$metadata`` XML.

    Raises:
        ODataHTTPError:       при HTTP-ошибке (4xx, 5xx).
        ODataConnectionError: при ошибке соединения (timeout, DNS).
    """
    metadata_url = odata_url.rstrip("/") + "/$metadata"
    headers: dict[str, str] = {
        "Accept": "application/xml",
    }
    if username:
        auth = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {auth}"

    req = urllib.request.Request(metadata_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise ODataHTTPError(
            message=str(e),
            status_code=e.code or 0,
            url=metadata_url,
        ) from e
    except urllib.error.URLError as e:
        raise ODataConnectionError(
            f"Cannot connect to {metadata_url}: {e.reason}",
        ) from e


# ═══════════════════════════════════════════════════════════════════════════
# Конфигурация — загрузка env.json
# ═══════════════════════════════════════════════════════════════════════════


def load_env_config(
    env_file: str,
    profile: str = "default",
) -> dict:
    """Загрузить профиль из ``env.json``.

    Args:
        env_file: путь к файлу ``env.json``.
        profile:  имя профиля (ключ верхнего уровня).

    Returns:
        Словарь с данными профиля.

    Raises:
        ConfigError: если файл не найден.
    """
    if not os.path.isfile(env_file):
        raise ConfigError(f"env.json not found: {env_file}")

    with open(env_file, encoding="utf-8") as f:
        env = json.load(f)

    if profile not in env:
        logger.warning("Profile '%s' not found in %s, using empty config", profile, env_file)

    return env.get(profile, {})


# ═══════════════════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════════════════


def build_parser() -> argparse.ArgumentParser:
    """Создать парсер аргументов CLI."""
    parser = argparse.ArgumentParser(
        description="Fetch 1C config info from OData $metadata",
    )
    parser.add_argument(
        "-EnvFile", default="env.json",
        help="Path to env.json with credentials",
    )
    parser.add_argument(
        "-EnvProfile", default="default",
        help="Profile name in env.json",
    )
    parser.add_argument(
        "-ODataUrl", default="",
        help="Override odata_url from env.json",
    )
    parser.add_argument(
        "-Mode", choices=["overview", "brief", "full"], default="overview",
    )
    parser.add_argument(
        "-CacheDir", default=".cache",
        help="Directory for cached metadata",
    )
    parser.add_argument(
        "-CacheTTL", type=int, default=3600,
        help="Cache TTL in seconds (0 = disable)",
    )
    parser.add_argument(
        "-ForceRefresh", action="store_true",
        help="Ignore cache, fetch fresh metadata",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> str:
    """Точка входа CLI.

    Args:
        argv: аргументы командной строки (``None`` = ``sys.argv``).

    Returns:
        Строка с результатом (для удобства тестирования).

    Raises:
        SystemExit: при ошибке конфигурации (отсутствует URL/env.json).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    # --- Resolve paths ---
    env_file = args.EnvFile
    if not os.path.isabs(env_file):
        env_file = os.path.join(os.getcwd(), env_file)

    cache_dir = args.CacheDir
    if not os.path.isabs(cache_dir):
        cache_dir = os.path.join(os.getcwd(), cache_dir)

    # --- Load credentials ---
    odata_url = args.ODataUrl.rstrip("/")
    odata_user = ""
    odata_password = ""

    if os.path.isfile(env_file):
        try:
            profile_data = load_env_config(env_file, args.EnvProfile)
        except ConfigError:
            logger.error("Failed to read %s", env_file)
            profile_data = {}

        if not odata_url:
            odata_url = profile_data.get("odata_url", "").rstrip("/")
        odata_user = profile_data.get("odata_user", "")
        odata_password = profile_data.get("odata_password", "")
    elif not odata_url:
        logger.error("env.json not found: %s", env_file)
        logger.error("Provide -ODataUrl or ensure env.json exists")
        sys.exit(1)

    if not odata_url:
        logger.error("odata_url is not set")
        sys.exit(1)

    # --- Get XML content (cache or network) ---
    cache = MetadataCache(cache_dir, ttl=args.CacheTTL)

    xml_content: Optional[str] = None
    cache_note = ""

    if not args.ForceRefresh:
        xml_content = cache.load(odata_url)
        if xml_content is not None:
            age = cache.get_age(odata_url)
            cache_note = f" [кэш, {age}с назад]" if age is not None else " [кэш]"

    if xml_content is None:
        xml_content = fetch_metadata(odata_url, odata_user, odata_password)
        cache.save(odata_url, xml_content)
        cache_note = " [получено из OData]"

    # --- Parse and format ---
    info = ODataConfigInfo(xml_content, cache_note=cache_note)

    if args.Mode == "brief":
        result = info.get_brief(odata_url=odata_url)
    elif args.Mode == "full":
        result = info.get_full(
            odata_url=odata_url,
            cache_file=cache.get_path(odata_url),
        )
    else:
        result = info.get_overview(odata_url=odata_url)

    return result


if __name__ == "__main__":
    print(main())
