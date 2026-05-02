#!/usr/bin/env python3
# odata-cfg-info.py v1.0 — 1C configuration info from OData $metadata with cache

import argparse
import base64
import json
import logging
import os
import sys
import time
import urllib.request
import urllib.error
from collections import OrderedDict
from xml.etree import ElementTree as ET

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

logger = logging.getLogger(__name__)

parser = argparse.ArgumentParser(description="Fetch 1C config info from OData $metadata")
parser.add_argument("-EnvFile", default="env.json", help="Path to env.json with credentials")
parser.add_argument("-EnvProfile", default="default", help="Profile name in env.json")
parser.add_argument("-ODataUrl", default="", help="Override odata_url from env.json")
parser.add_argument("-Mode", choices=["overview", "brief", "full"], default="overview")
parser.add_argument("-CacheDir", default=".cache", help="Directory for cached metadata")
parser.add_argument("-CacheTTL", type=int, default=3600, help="Cache TTL in seconds (0 = disable)")
parser.add_argument("-ForceRefresh", action="store_true", help="Ignore cache, fetch fresh metadata")
args = parser.parse_args()

# --- Type maps ---
PREFIX_TO_TYPE = OrderedDict([
    ("Catalog_",                    "Catalog"),
    ("Document_",                   "Document"),
    ("InformationRegister_",        "InformationRegister"),
    ("AccumulationRegister_",       "AccumulationRegister"),
    ("AccountingRegister_",         "AccountingRegister"),
    ("CalculationRegister_",        "CalculationRegister"),
    ("ChartOfCharacteristicTypes_", "ChartOfCharacteristicTypes"),
    ("ChartOfAccounts_",            "ChartOfAccounts"),
    ("ChartOfCalculationTypes_",    "ChartOfCalculationTypes"),
    ("Enum_",                       "Enum"),
    ("BusinessProcess_",            "BusinessProcess"),
    ("Task_",                       "Task"),
    ("ExchangePlan_",               "ExchangePlan"),
    ("Sequence_",                   "Sequence"),
    ("DocumentJournal_",            "DocumentJournal"),
])

TYPE_RU = {
    "Catalog":                    "Справочники",
    "Document":                   "Документы",
    "InformationRegister":        "Регистры сведений",
    "AccumulationRegister":       "Регистры накопления",
    "AccountingRegister":         "Регистры бухгалтерии",
    "CalculationRegister":        "Регистры расчёта",
    "ChartOfCharacteristicTypes": "ПВХ",
    "ChartOfAccounts":            "Планы счетов",
    "ChartOfCalculationTypes":    "ПВР",
    "Enum":                       "Перечисления",
    "BusinessProcess":            "Бизнес-процессы",
    "Task":                       "Задачи",
    "ExchangePlan":               "Планы обмена",
    "Sequence":                   "Последовательности",
    "DocumentJournal":            "Журналы документов",
}

TYPE_ORDER = list(PREFIX_TO_TYPE.values())

# --- Load credentials ---
env_file = args.EnvFile
if not os.path.isabs(env_file):
    env_file = os.path.join(os.getcwd(), env_file)

odata_url = args.ODataUrl.rstrip("/")
odata_user = ""
odata_password = ""

if os.path.isfile(env_file):
    with open(env_file, encoding="utf-8") as f:
        env = json.load(f)
    profile = env.get(args.EnvProfile, {})
    if not odata_url:
        odata_url = profile.get("odata_url", "").rstrip("/")
    odata_user = profile.get("odata_user", "")
    odata_password = profile.get("odata_password", "")
elif not odata_url:
    logger.error("env.json not found: %s", env_file)
    logger.error("Provide -ODataUrl or ensure env.json exists")
    sys.exit(1)

if not odata_url:
    logger.error("odata_url is not set")
    sys.exit(1)

metadata_url = odata_url + "/$metadata"

# --- Cache setup ---
cache_dir = args.CacheDir
if not os.path.isabs(cache_dir):
    cache_dir = os.path.join(os.getcwd(), cache_dir)

import hashlib
url_hash = hashlib.md5(odata_url.encode()).hexdigest()[:8]
cache_file = os.path.join(cache_dir, f"odata_metadata_{url_hash}.xml")

def load_from_cache():
    if not os.path.isfile(cache_file):
        return None
    if args.CacheTTL > 0:
        age = time.time() - os.path.getmtime(cache_file)
        if age > args.CacheTTL:
            return None
    with open(cache_file, encoding="utf-8") as f:
        return f.read()

def save_to_cache(content):
    os.makedirs(cache_dir, exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        f.write(content)

# --- Fetch metadata ---
def fetch_metadata():
    auth = base64.b64encode(f"{odata_user}:{odata_password}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        metadata_url,
        headers={
            "Authorization": f"Basic {auth}",
            "Accept": "application/xml",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        logger.error("HTTP %s fetching %s", e.code, metadata_url)
        sys.exit(1)
    except urllib.error.URLError as e:
        logger.error("Cannot connect to %s: %s", metadata_url, e.reason)
        sys.exit(1)

# --- Get XML content (cache or network) ---
xml_content = None
cache_used = False

if not args.ForceRefresh:
    xml_content = load_from_cache()
    if xml_content:
        cache_used = True
        cache_age = int(time.time() - os.path.getmtime(cache_file))
        cache_note = f" [кэш, {cache_age}с назад]"

if xml_content is None:
    xml_content = fetch_metadata()
    save_to_cache(xml_content)
    cache_note = " [получено из OData]"

# --- Parse EDMX ---
try:
    root = ET.fromstring(xml_content)
except ET.ParseError as e:
    logger.error("Failed to parse XML: %s", e)
    sys.exit(1)

# EDMX namespaces
NS_EDMX = "http://schemas.microsoft.com/ado/2007/06/edmx"
NS_EDM4 = "http://schemas.microsoft.com/ado/2009/11/edm"
NS_EDM3 = "http://schemas.microsoft.com/ado/2008/09/edm"

def find_ns(elem, tag, ns_candidates):
    for ns in ns_candidates:
        found = elem.find(f"{{{ns}}}{tag}")
        if found is not None:
            return found
    return elem.find(tag)

def findall_ns(elem, tag, ns_candidates):
    results = []
    for ns in ns_candidates:
        results = elem.findall(f"{{{ns}}}{tag}")
        if results:
            return results
    return elem.findall(tag)

NS_LIST = [NS_EDM4, NS_EDM3, ""]

# Find DataServices → Schema → EntityContainer
data_services = find_ns(root, "DataServices", [NS_EDMX])
if data_services is None:
    data_services = root

schema = find_ns(data_services, "Schema", NS_LIST)
if schema is None:
    logger.error("No Schema element found in $metadata")
    sys.exit(1)

entity_container = find_ns(schema, "EntityContainer", NS_LIST)
entity_sets = findall_ns(entity_container, "EntitySet", NS_LIST) if entity_container is not None else []

# --- Build counts by 1C type ---
type_counts = OrderedDict()
type_names = {}  # type -> list of object names

for es in entity_sets:
    name = es.get("Name", "")
    matched_type = None
    matched_obj_name = None
    for prefix, type_name in PREFIX_TO_TYPE.items():
        if name.startswith(prefix):
            matched_type = type_name
            matched_obj_name = name[len(prefix):]
            break
    if matched_type:
        if matched_type not in type_counts:
            type_counts[matched_type] = 0
            type_names[matched_type] = []
        type_counts[matched_type] += 1
        type_names[matched_type].append(matched_obj_name)

total_objects = sum(type_counts.values())
total_entity_sets = len(entity_sets)

# Try to get namespace (= configuration name hint)
namespace = schema.get("Namespace", "")
cfg_name = namespace  # Often something like "Конфигурация" or the actual name

# --- Output ---
lines = []
def out(text=""):
    lines.append(text)

dash = "\u2014"

if args.Mode == "brief":
    out(f"OData: {odata_url}{cache_note} | {total_objects} объектов ({total_entity_sets} сущностей)")

if args.Mode == "overview":
    out(f"=== OData конфигурация{cache_note} ===")
    out()
    out(f"URL:            {odata_url}")
    if cfg_name:
        out(f"Пространство:   {cfg_name}")
    out(f"Всего сущностей: {total_entity_sets}")
    out(f"Объектов 1С:    {total_objects}")
    out()
    out(f"--- Состав (опубликовано в OData: {total_objects} объектов) ---")
    out()
    max_len = max((len(TYPE_RU.get(t, t)) for t in type_counts), default=10)
    if max_len < 10:
        max_len = 10
    for type_name in TYPE_ORDER:
        if type_name in type_counts:
            ru = TYPE_RU.get(type_name, type_name)
            out(f"  {ru.ljust(max_len)}  {type_counts[type_name]}")

if args.Mode == "full":
    out(f"=== OData конфигурация{cache_note} ===")
    out()
    out(f"URL:             {odata_url}")
    if cfg_name:
        out(f"Пространство:    {cfg_name}")
    out(f"Кэш:             {cache_file}")
    out(f"Всего сущностей: {total_entity_sets}")
    out(f"Объектов 1С:     {total_objects}")
    out()
    out(f"--- Состав (опубликовано в OData: {total_objects} объектов) ---")
    out()
    for type_name in TYPE_ORDER:
        if type_name not in type_counts:
            continue
        ru = TYPE_RU.get(type_name, type_name)
        count = type_counts[type_name]
        out(f"  {ru} ({type_name}): {count}")
        for obj_name in sorted(type_names[type_name]):
            out(f"    {obj_name}")
    out()

    # Unknown prefixes
    unknown = []
    for es in entity_sets:
        name = es.get("Name", "")
        if not any(name.startswith(p) for p in PREFIX_TO_TYPE):
            unknown.append(name)
    if unknown:
        out(f"--- Прочие сущности ({len(unknown)}) ---")
        for u in unknown:
            out(f"  {u}")

print("\n".join(lines))
