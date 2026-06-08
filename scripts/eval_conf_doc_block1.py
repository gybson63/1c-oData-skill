#!/usr/bin/env python3
"""Прогон блока 1 чеклиста conf-doc (структура метаданных)."""

from __future__ import annotations

import re
import sys

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CFG = "ЗарплатаИУправлениеПерсоналомКОРП"
BASE = "http://localhost:8050"

QUESTIONS = [
    {
        "id": 1,
        "q": "Какие основные реквизиты у документа Отпуск в ЗУП?",
        "search": "отпуск документ реквизиты",
        "object_type": "Document",
        "target": "Отпуск",
    },
    {
        "id": 2,
        "q": "Чем документ Отпуск отличается от ОтпускаСотрудников?",
        "search": "отпуск отпуска сотрудников",
        "object_type": "Document",
        "targets": ["Отпуск", "ОтпускаСотрудников"],
    },
    {
        "id": 3,
        "q": "В каком объекте конфигурации хранится расчёт среднего заработка для отпуска?",
        "search": "средний заработок отпуск расчет",
        "object_type": None,
        "target": None,
    },
    {
        "id": 4,
        "q": "Какие реквизиты у документа Табель учёта рабочего времени для фильтрации по месяцу?",
        "search": "табель учета рабочего времени",
        "object_type": "Document",
        "target": "ТабельУчетаРабочегоВремени",
    },
    {
        "id": 5,
        "q": "Какие документы и регистры связаны с увольнением сотрудника?",
        "search": "увольнение сотрудника",
        "object_type": None,
        "target": None,
    },
]

PREFIX = {
    "Catalog": "Catalog_",
    "Document": "Document_",
    "InformationRegister": "InformationRegister_",
    "AccumulationRegister": "AccumulationRegister_",
    "Report": "Report_",
    "Enum": "Enum_",
}


def conf_search(client: httpx.Client, q: str, ot: str | None, k: int = 5) -> list[dict]:
    body: dict = {"query": q, "top_k": k, "configuration": CFG}
    if ot:
        body["object_type"] = ot
    r = client.post("/search", json=body, timeout=120)
    if not r.is_success:
        return []
    return r.json()


def conf_get(client: httpx.Client, ot: str, name: str) -> dict | None:
    r = client.get(f"/objects/{ot}/{name}", params={"configuration": CFG}, timeout=60)
    return r.json() if r.is_success else None


def conf_chunk(client: httpx.Client, ot: str, name: str, idx: int) -> str:
    r = client.get(f"/objects/{ot}/{name}/chunks/{idx}", params={"configuration": CFG}, timeout=60)
    if not r.is_success:
        return ""
    return r.json().get("text", "")


def list_objects_substring(client: httpx.Client, q: str, limit: int = 12) -> list[str]:
    words = [w.lower() for w in re.findall(r"[а-яёa-z0-9]+", q.lower()) if len(w) > 2]
    r = client.get("/objects", params={"configuration": CFG, "limit": 500}, timeout=60)
    if not r.is_success:
        return []
    hits: list[str] = []
    for o in r.json():
        name = o.get("name", "")
        syn = (o.get("synonym") or "").lower()
        blob = f"{name} {syn}".lower()
        if any(w in blob for w in words[:4]):
            pref = PREFIX.get(o.get("object_type", ""), "")
            hits.append(f"{pref}{name}")
    return hits[:limit]


def extract_attr_lines(text: str, max_lines: int = 8) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("|") and s.count("|") >= 3:
            lines.append(s[:140])
        elif s.startswith("- **") and "**" in s[2:]:
            lines.append(s[:140])
        if len(lines) >= max_lines:
            break
    return lines


def score_with_conf(has_target: bool, has_attrs: bool, top_relevant: bool) -> int:
    if has_target and has_attrs:
        return 2
    if has_target or top_relevant:
        return 1
    return 0


def score_without(hits: list[str], expected_prefixes: list[str] | None = None) -> int:
    if not hits:
        return -1
    if expected_prefixes:
        ok = sum(1 for h in hits if any(h.startswith(p) for p in expected_prefixes))
        if ok >= len(expected_prefixes):
            return 1
        if ok:
            return 0
        return -1
    # generic: many irrelevant hits
    if len(hits) >= 5:
        return 0
    return 1 if hits else -1


def main() -> None:
    rows: list[dict] = []
    with httpx.Client(base_url=BASE, timeout=120) as client:
        for item in QUESTIONS:
            print("=" * 70)
            print(f"#{item['id']} {item['q']}")
            print("-" * 70)

            results = conf_search(client, item["search"], item.get("object_type"), 5)
            print("С conf-doc (search top-5):")
            for r in results:
                sc = r.get("score", 0)
                print(f"  {r['object_type']}.{r['name']} score={sc:.3f} | {r.get('synonym', '')}")

            targets = item.get("targets") or ([item["target"]] if item.get("target") else [])
            attr_lines: list[str] = []
            has_target = False
            for t in targets:
                if not t:
                    continue
                ot = item.get("object_type") or (results[0]["object_type"] if results else "Document")
                card = conf_get(client, ot, t)
                if card:
                    has_target = True
                    print(
                        f"  Карточка {ot}.{t}: реквизитов={card.get('attributes_count')}, "
                        f"ТЧ={card.get('tabular_sections_count')}, чанков={len(card.get('chunks', []))}"
                    )
                    for idx in (1, 0):
                        ch = conf_chunk(client, ot, t, idx)
                        attr_lines = extract_attr_lines(ch)
                        if attr_lines:
                            break
                    if attr_lines:
                        print("  Фрагмент структуры:")
                        for a in attr_lines:
                            print("   ", a)

            if not targets and results:
                r0 = results[0]
                card = conf_get(client, r0["object_type"], r0["name"])
                if card:
                    has_target = True
                    print(f"  Карточка {r0['object_type']}.{r0['name']}: реквизитов={card.get('attributes_count')}")

            sub = list_objects_substring(client, item["q"])
            print("Без conf-doc (подстрока в имени, как search_entities):")
            if sub:
                for s in sub:
                    print(" ", s)
            else:
                print("  (пусто)")

            top_relevant = False
            if item["id"] == 1:
                top_relevant = any(r["name"] == "Отпуск" for r in results)
            elif item["id"] == 2:
                top_relevant = {r["name"] for r in results} >= {"Отпуск", "ОтпускаСотрудников"}
            elif item["id"] == 3:
                top_relevant = any("средн" in (r.get("synonym") or "").lower() or "Средн" in r["name"] for r in results)
            elif item["id"] == 4:
                top_relevant = any(r["name"] == "ТабельУчетаРабочегоВремени" for r in results)
            elif item["id"] == 5:
                top_relevant = any(r["name"] == "Увольнение" for r in results)

            sw = score_with_conf(has_target, bool(attr_lines), top_relevant)
            if item["id"] == 2:
                swo = score_without(sub, ["Document_Отпуск", "Document_ОтпускаСотрудников"])
            elif item["id"] == 3:
                swo = score_without(sub, None)
                if not any("средн" in h.lower() for h in sub):
                    swo = -1
            else:
                exp = None
                if item["id"] == 1:
                    exp = ["Document_Отпуск"]
                elif item["id"] == 4:
                    exp = ["Document_ТабельУчетаРабочегоВремени"]
                elif item["id"] == 5:
                    exp = ["Document_Увольнение"]
                swo = score_without(sub, exp)

            rows.append(
                {
                    "id": item["id"],
                    "with": sw,
                    "without": swo,
                    "delta": sw - swo,
                    "note": f"search:{len(results)} hits, substring:{len(sub)}",
                }
            )
            print(f"\nБаллы: с conf-doc={sw}, без={swo}, Δ={sw - swo}\n")

    print("=" * 70)
    print("СВОДКА")
    print("| № | С conf-doc | Без | Δ |")
    print("|---|------------|-----|---|")
    tw = tw0 = 0
    for r in rows:
        print(f"| {r['id']} | {r['with']} | {r['without']} | {r['delta']:+d} |")
        tw += r["with"]
        tw0 += r["without"]
    print(f"| **Итого** | **{tw}** | **{tw0}** | **{tw - tw0:+d}** |")


if __name__ == "__main__":
    main()
