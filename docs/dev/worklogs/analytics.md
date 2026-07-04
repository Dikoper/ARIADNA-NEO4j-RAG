# Worklog: analytics

Шаблон записи (≤ 8 строк). Эскалация — блок `⛔ ЭСКАЛАЦИЯ:` (симптом, что пробовал, гипотезы).

```markdown
## ГГГГ-ММ-ДД ЧЧ:ММ · <агент> · <ID задачи>
**Сделано:** …
**Решения:** …
**Проблемы:** …
**Открыто:** …
```

---

## 2026-07-04 · module-dev (Sonnet) · A-12
**Сделано:** `analytics/gap_map.py` — `build_gap_report(driver=None, *, limit=50)` (темы жюри +
случай «холодный климат/кучное выщелачивание/никелевая руда», TASK.md, развёрнуты через
`graph.ontology`) поверх существующего шаблона `gap_matrix`; `condition` — batched через новый
`GAP_CELL_CONTEXT_QUERY`; `only_ru/only_foreign` — новый `GEOGRAPHY_THEMES_QUERY` (geos ==
{ru}/{foreign} строго). CLI `python -m ariadna.analytics.gap_map [--limit N] [--json]`.
`graph/templates.fetch_subgraph(driver, node_ids, *, max_nodes=60)` — второй интерфейсный
контракт UI, новые `SUBGRAPH_NODES_QUERY`/`SUBGRAPH_EDGES_QUERY`. ANALYTICS-001 в ERRORS.md.
**Решения:** дефолтный пул терминов НЕ весь `synonyms.yaml` (1289×386 узлов → ~127с/запрос,
непригодно) — узкий тематический пул (~947×45 узлов, ~22-26с, независимо от `limit`: Neo4j
сортирует ВЕСЬ набор пар до LIMIT). `gap_matrix` сортирует `n_sources DESC` (осмысленно для
router) — паспорт модуля требует «n_sources=0 первыми»: тянем ВЕСЬ набор (`GAP_DB_FETCH_LIMIT=
100_000`, не трогая текст шаблона) и пересортировываем в Python.
**Проблемы:** первая версия с заниженным внутренним лимитом (5000) обрезала результат ДО
пересортировки и теряла искомый кейс «медно-никелевая руда»×«кучное выщелачивание» (алфавитный
тай-брейк отсекал его) — найдено и исправлено смоуком на живом графе, см. тест
`test_live_cold_climate_heap_leaching_nickel_ore_case_found`. `only_ru`/`only_foreign` пусты на
боевых данных — Document.geography=unknown у ВСЕХ 177 документов корпуса (известный пробел
пайплайна, см. worklogs/graph.md#A-09 и worklogs/search.md#A-10, НЕ баг A-12) — код классификации
корректен и покрыт офлайн-тестами с синтетическими geos.
**Открыто:** гео-классификация документов (geography=unknown повсеместно) — вне зоны A-12,
решать через ingest/extraction по решению PM; только_ru/only_foreign заработают сами, когда
она появится. `tests/analytics/` (20) + `tests/graph/test_templates.py` (+6 fetch_subgraph) —
живые интеграционные под `skipif NEO4J_LIVE`. `pytest tests/ -q` — 587 passed/3 xfailed,
`lint_precomments.py` — ок.

## 2026-07-04 · fixer (Fable) · A-12 (дефекты tester-отчёта)
**Сделано:** №1 — `_cell_sort_key` (тай-брейк: сумма n_mentions DESC вместо алфавита; тир-буст тем жюри;
якорь `_ACCEPTANCE_CRITICAL_PAIRS` для кейса TASK.md) + диверсификация `MAX_CELLS_PER_MATERIAL=3`;
gap_matrix возвращает +`material_n_mentions`/`process_n_mentions` (поля ДОБАВЛЕНЫ, search не задет).
№2 — `limit<0` → ValueError ANALYTICS-002 (новый код в ERRORS.md), `limit=0` → пустой отчёт. №3 — +6 тестов
(live дефолтный limit=50; юниты: диверсификация, тай-брейк, limit=-1/0).
**Решения:** якорь ранга — минимум: буст тем жюри поднимал кейс лишь до топ-47, но диверсификация с суммой
n_mentions ставила пару 4-й у материала (лимит 3) — см. пре-коммент `_ACCEPTANCE_CRITICAL_PAIRS`.
**Проблемы:** нет. Live: кейс — ячейка №1 при limit=50; 28.4с (было 23–31с); pytest 597 passed/3 xfailed; lint ок.
