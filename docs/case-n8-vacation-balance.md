# Кейс №8: остатки дней отпуска (zup_gazaliev)

Отдельная задача для доработки бота / conf-doc / OData на базе `http://localhost/zup_gazaliev/odata/standard.odata`.

**Статус:** P0 закрыт · **diag Q8 (2026-06-07): +2 / +2** · см. §10

---

## 1. Формулировка

**Вопрос пользователя (блок 2, id=8):**

> Сколько дней отпуска осталось у сотрудников?

**Сценарий в каталоге:** `tests/scenarios/catalog.yaml` → `email-zup-vacation-balance` (status: `blocked`).

**Критерий успеха в `eval_conf_doc_block2.py`:**

- `expect_entity`: регистр/документ с подстрокой `Отпуск` в имени
- `expect_kind`: `data` — нужны фактические данные или осмысленный count
- Шкала: **+2** данные есть · **+1** верный entity, пусто/частично · **0** неверный entity · **−1** ошибка OData / parse

---

## 2. Модель данных ЗУП (как думать об остатке)

**Остаток = накоплено − израсходовано.**

| Сторона | OData (примеры) | Смысл |
|---------|-----------------|--------|
| Накоплено | `InformationRegister_НачальныеОстаткиОтпусков_RecordType`, `InformationRegister_ПоложенныеВидыЕжегодныхОтпусков_RecordType` | Ввод остатков, права (дней в год) |
| Израсходовано | `AccumulationRegister_ФактическиеОтпуска/Turnovers()` или `_RecordType` | Фактически использованные дни (`Document_Отпуск` и др.) |
| **Готовый остаток** | **`InformationRegister_АналитикаОстатковОтпусков`** | Поле **`ОстатокДней`** — результат расчёта (отчёт «Остатки отпусков») |

**AccumulationRegister:** сначала виртуальные таблицы из `$metadata` — `/Balance()`, `/Turnovers()`, `/BalanceAndTurnovers()`. На `zup_gazaliev` у `ФактическиеОтпуска` работает **`/Turnovers()`** (`КоличествоTurnover`), **`/Balance()` — нет** (оборотный регистр / VT не опубликована).

**Не путать:**

- `Document_Отпуск` — оформление отпуска (источник движений в `ФактическиеОтпуска`), не таблица остатков
- `InformationRegister_ОстаткиОтпусков` — **нет в OData** (404); нужен `АналитикаОстатковОтпусков`

---

## 3. Что было сломано (прогон 2026-06-07, до P0)

| Режим | Score | Типичная ошибка |
|-------|-------|-----------------|
| С conf-doc | −1 | IR-шапка + `ВидОтпуска_Key` → HTTP 400 |
| Без conf-doc | −1 | `/Balance()` + выдуманное `КоличествоBalance` → HTTP 400 |

---

## 4. Что реально есть в OData `zup_gazaliev`

Скрипт: `python scripts/probe_vacation_balance.py`
Образцы: `tests/artifacts/probe_vacation_sample.json`

### 4.1. Работает (HTTP 200)

| Entity | Назначение | Ключевые поля |
|--------|------------|---------------|
| **`InformationRegister_АналитикаОстатковОтпусков`** | **Остаток дней (primary)** | `Сотрудник_Key`, `ВидЕжегодногоОтпуска_Key`, `Дата`, **`ОстатокДней`** |
| `InformationRegister_НачальныеОстаткиОтпусков_RecordType` | Ввод начальных остатков | `КоличествоДней`, `ДатаОстатка` |
| `AccumulationRegister_ФактическиеОтпуска/Turnovers()` | Оборот (израсходовано) | `КоличествоTurnover` |
| `AccumulationRegister_ФактическиеОтпуска_RecordType` | Движения | `Количество`, `Recorder_Type=Document_Отпуск` |
| `InformationRegister_ПоложенныеВидыЕжегодныхОтпусков_RecordType` | Права (дней в год) | `КоличествоДнейВГод` |

### 4.2. Не работает / не то

| Entity / метод | HTTP | Примечание |
|----------------|------|------------|
| `AccumulationRegister_ФактическиеОтпуска/Balance()` | 400 | «Метод не найден» → бот fallback на `/Turnovers()` |
| `InformationRegister_ОстаткиОтпусков` | 404 | Имени нет; использовать `АналитикаОстатковОтпусков` |
| `InformationRegister_НачальныеОстаткиОтпусков` (шапка) | 400 | Поля среза → `_RecordType` (fallback в `query_executor`) |
| `InformationRegister_АналитикаОстатковОтпусков_RecordType` | 404 | Запрос к **шапке** IR |

---

## 5. Сделано (P0, 2026-06-07)

| # | Изменение | Файл |
|---|-----------|------|
| ✓ | Секция «Остатки отпусков» в hint | `bot/config_hint.md` |
| ✓ | Буст conf-doc: `АналитикаОстатковОтпусков`, `ФактическиеОтпуска`, … | `bot/agents/odata/conf_doc_context.py` |
| ✓ | Fallback `Balance()` → `Turnovers()` | `query_executor.py`, `odata_query_utils.py` |
| ✓ | Fallback IR-шапка → `_RecordType` | `query_executor.py` |
| ✓ | Hint «Метод не найден» | `error_handler.py` |
| ✓ | Unit-тесты fallback | `tests/test_odata_vt_fallback.py` |

---

## 6. Эталонный запрос (zup_gazaliev)

**Primary — текущий остаток в днях:**

```
GET /InformationRegister_АналитикаОстатковОтпусков
  ?$top=50
  &$select=Сотрудник_Key,ВидЕжегодногоОтпуска_Key,Дата,ОстатокДней
  &$orderby=Дата desc
```

**Fallback — расходная нога (обороты, не «осталось»):**

```
GET /AccumulationRegister_ФактическиеОтпуска/Turnovers()
  ?$top=50
  &$select=Сотрудник_Key,ВидЕжегодногоОтпуска_Key,КоличествоTurnover
```

**Fallback — ввод начальных остатков (не текущий баланс):**

```
GET /InformationRegister_НачальныеОстаткиОтпусков_RecordType
  ?$top=50
  &$select=Сотрудник_Key,ВидЕжегодногоОтпуска_Key,КоличествоДней,ДатаОстатка
  &$orderby=ДатаОстатка desc
```

Без `$expand`. Поле вида отпуска: **`ВидЕжегодногоОтпуска_Key`**.

---

## 7. План работ (остаток)

### 7.1. Исследование

- [x] `probe_vacation_balance.py` — Аналитика, Turnovers, Balance
- [x] conf-doc: `АналитикаОстатковОтпусков` (score ~0.94 по прямому запросу)
- [x] Прогон `diag_block2_q8.py` — **+2 / +2**, entity `InformationRegister_АналитикаОстатковОтпусков`
- [ ] Прогон полного `eval_conf_doc_block2.py` (блок 2 целиком)

### 7.2. Код (опционально)

- [ ] Валидатор: отклонять `КоличествоBalance`, `ВидОтпуска_Key` без metadata
- [ ] Analytics: начислено − списано (multi-query)
- [ ] Сценарий `email-zup-vacation-balance`: `active` / `degraded`

---

## 8. Команды

```powershell
cd C:\ПервыйБИТ\ИИ\1c-oData-skill

# Probe OData
python scripts/probe_vacation_balance.py

# Только вопрос №8 (~2–4 мин)
python scripts/diag_block2_q8.py

# Полный блок 2 (~9–11 мин)
python scripts/eval_conf_doc_block2.py
```

**Конфиг:** `env.json` → `agents.odata.odata_url` = `http://localhost/zup_gazaliev/odata/standard.odata`
**conf-doc:** `ЗарплатаИУправлениеПерсоналомКОРП` на `:8050`

---

## 9. Критерии приёмки

| # | Критерий | P0 |
|---|----------|-----|
| A | eval №8: score ≥ 0 (нет HTTP 400) | проверить diag/eval |
| B | Entity с «Отпуск», предпочтительно `АналитикаОстатковОтпусков` | hint + conf-doc |
| C | `$select` только из metadata | fallback VT / RecordType |
| D | Не выдавать начальные остатки 2021 за «текущий» без пояснения | hint |
| E | `probe_vacation_balance.py` актуален | ✓ |
| F | `config_hint.md` обновлён | ✓ |

---

## 10. История наблюдений

| Дата | С conf-doc | Без | Примечание |
|------|------------|-----|------------|
| 2026-06-07 | −1 | −1 | IR шапка / Balance() 400 |
| 2026-06-07 | **+2** | **+2** | P0 + diag: `АналитикаОстатковОтпусков`, `ОстатокДней`, HTTP 200, 50 записей |

*После eval обновить scores и §10.*

---

## 11. Связанные файлы

| Файл | Роль |
|------|------|
| `scripts/eval_conf_doc_block2.py` | Оценка id=8 |
| `scripts/diag_block2_q8.py` | Быстрая диагностика Q8 |
| `scripts/probe_vacation_balance.py` | Probe OData |
| `bot/config_hint.md` | Секция «Остатки отпусков» |
| `bot/agents/odata/conf_doc_context.py` | Буст vacation balance |
| `bot/agents/odata/query_executor.py` | Fallback VT / RecordType |
| `docs/conf-doc-evaluation-checklist.md` | §8 чеклиста |
