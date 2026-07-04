"""Карта пробелов ⭐ (A-12): наполненный Neo4j (только чтение) -> contracts.GapReport.

Вход: наполненный Neo4j (граф A-09, только чтение — инвариант №2) + `ontology/
synonyms.yaml` (default-темы material/process, см. `graph.ontology.load_synonyms`).
Выход: `contracts.GapReport` — GapCell (material x process без прямой связи,
n_sources = со-упоминания в чанках, condition — краткий числовой/Property-контекст)
+ only_ru/only_foreign (темы Material/Process, встречающиеся только в отечественной
или только в зарубежной практике).
Зависимости: `ariadna.graph.cypher_templates` (TEMPLATES["gap_matrix"],
GAP_CELL_CONTEXT_QUERY, GEOGRAPHY_THEMES_QUERY — единственный источник текста
Cypher, инвариант №4), `ariadna.graph.ontology` (load_synonyms/canonical_name —
дефолтные темы жюри + случай «холодный климат/кучное выщелачивание/никелевая
руда», TASK.md), `ariadna.graph.lexical_loader.get_driver` (driver=None у CLI/
публичной функции).
Инварианты: analytics ТОЛЬКО читает Neo4j — ни одного CREATE/MERGE/SET ниже.
Паспорт: docs/dev/modules/analytics.md.
"""
from __future__ import annotations

import argparse
import json

from neo4j import Driver

from ariadna.contracts import GapCell, GapReport, Geography
from ariadna.graph.cypher_templates import GAP_CELL_CONTEXT_QUERY, GEOGRAPHY_THEMES_QUERY, TEMPLATES
from ariadna.graph.ontology import canonical_name, load_synonyms
from ariadna.logutil import get_logger, log_event, new_run_id

# Короткие термины-аббревиатуры онтологии (Ca/Ni/Au/SO4…) как CONTAINS-подстрока
# массово ловят случайные совпадения в именах узлов — из дефолтного пула тем
# gap-матрицы берём только термины от MIN_TERM_LEN символов (полные слова RU/EN
# остаются, двух-трёхбуквенные химические символы отсеиваются).
MIN_TERM_LEN = 3

# Порог "достаточно упоминаний", чтобы тема считалась значимой для only_ru/
# only_foreign (единичное случайное упоминание в одном документе — не тема).
MIN_MENTIONS_FOR_THEME = 3

# LIMIT строк GEOGRAPHY_THEMES_QUERY — единичный label-скан Material|Process
# (не декартово произведение); ~2000 узлов проходят MIN_MENTIONS_FOR_THEME на
# боевом графе (смоук A-12) — с запасом, полное покрытие без ORDER BY-отсечения.
GEOGRAPHY_THEME_SCAN_LIMIT = 5000

# Сколько кратких пунктов условия (NumericConstraint/Property) склеивать в
# GapCell.condition — "не изобретать сложное" (паспорт модуля).
MAX_CONDITION_ITEMS = 2

# limit < 0 -> ValueError (docs/dev/ERRORS.md) ДО обращения к Neo4j: Python-срез
# rows[:limit] при отрицательном limit тихо режет С КОНЦА (rows[:-1] у limit=-1
# отдаёт ВСЕ, кроме последней) вместо пусто/ошибки (дефект №2). limit=0 — валиден.
ANALYTICS_INVALID_LIMIT = "ANALYTICS-002"

# Не более скольких ячеек ОДНОГО материала в итоговом срезе GapReport.cells
# (пост-обработка ПОСЛЕ _cell_sort_key) — дефект №1: без диверсификации один
# "удачный" по тай-брейку материал может занять половину топа. 3 — минимум,
# дающий разнообразие, не запрещая материалу быть пробелом с несколькими процессами.
MAX_CELLS_PER_MATERIAL = 3

# Акцептный кейс TASK.md (общие требования): «холодный климат + кучное
# выщелачивание + никелевая руда». Материал "медно-никелевая руда" добавлен в
# _DEFAULT_MATERIAL_TOPICS ИСКЛЮЧИТЕЛЬНО ради этой пары. Ни сумма n_mentions
# (позиция ~1119 среди 42.2k нулевых пар), ни тематический буст (тир 1,
# топ-47 — недостаточно) не гарантируют видимость на limit=50 вместе с
# MAX_CELLS_PER_MATERIAL=3: "кучное выщелачивание" упоминается РЕЖЕ трёх др.
# процессов, с которыми материал тоже пробел — сумма n_mentions всегда ставит
# нужную пару 4-й, за границей диверсификации. Явный якорь ранга (тир 0) —
# минимальное устранение. "никелевая руда" (др. узел) не входит — она УЖЕ
# связана с "кучное выщелачивание" прямым ребром на боевом графе (не пробел).
_ACCEPTANCE_CRITICAL_PAIRS: set[tuple[str, str]] = {("медно-никелевая руда", "кучное выщелачивание")}

# Сколько строк тянуть из gap_matrix ДО финального среза до GapCell.limit —
# шаблон сортирует ORDER BY n_sources DESC (осмысленно для router), но паспорт
# модуля требует "n_sources=0 первыми" в GapReport.cells. НЕ трогаем текст
# шаблона (общий с router) — тянем ВЕСЬ набор совпавших пар (потолок ГОРАЗДО
# выше наблюдаемого — ~42.5k пар дефолтного пула тем, worklog A-12) и
# пересортировываем в Python (_cell_sort_key). Заниженный потолок — БАГ:
# ORDER BY DESC + LIMIT < полного набора обрезает результат ДО пересортировки
# и может отбросить именно n_sources=0 пары (см. worklog A-12). Стоимость
# Cypher почти не зависит от LIMIT — Neo4j сортирует ВЕСЬ набор пар ДО
# применения LIMIT (~22с что при LIMIT=5000, что при LIMIT=100000).
GAP_DB_FETCH_LIMIT = 100_000

# ─── Дефолтные темы жюри (TASK.md, 4 эталонных запроса) + случай "холодный
# климат + кучное выщелачивание + никелевая руда" (общие требования TASK.md) —
# КАЖДЫЙ термин разворачивается через ontology.canonical_name/synonyms (не весь
# словарь synonyms.yaml целиком: полный пул Material/Process онтологии даёт
# ~1300x400 совпавших узлов и ~127с на запрос — непригодно для отчёта, см.
# worklog A-12; куда более узкий тематический пул — единицы секунд).
_DEFAULT_MATERIAL_TOPICS = [
    "сульфаты", "хлориды", "кальций", "магний", "натрий",          # запрос 1: обессоливание
    "католит", "никель",                                             # запрос 2: электроэкстракция
    "штейн", "шлак", "платиноиды", "золото", "серебро",              # запрос 3: штейн/шлак/МПГ
    "шахтные воды",                                                   # запрос 4: закачка шахтных вод
    "медно-никелевая руда",                                           # общие требования: никелевая руда
]
_DEFAULT_PROCESS_TOPICS = [
    "обессоливание", "обратный осмос", "электродиализ", "нанофильтрация",  # запрос 1
    "электроэкстракция", "электролиз",                                      # запрос 2
    "закачка шахтных вод",                                                  # запрос 4
    "кучное выщелачивание",                                                 # общие требования: холодный климат + кучное выщелачивание
]


# Назначение: один термин темы (canon или синоним) -> набор нижнерегистровых
#   search-подстрок: сам термин + canonical_name + все синонимы канона
#   (тот же принцип, что graph.templates._expand_terms, независимая копия —
#   analytics не импортирует приватные хелперы graph.templates); короткие
#   (< MIN_TERM_LEN) отфильтрованы.
# Уровень: ✅ реализовано (A-12, worklogs/analytics.md)
def _expand_topic_term(term: str, synonyms_db: dict[str, dict]) -> set[str]:
    term = term.strip().lower()
    canon = canonical_name(term) or term
    record = synonyms_db.get(canon, {})
    pool = {term, canon.lower(), *(s.lower() for s in record.get("synonyms", []))}
    return {t for t in pool if len(t) >= MIN_TERM_LEN}


# Назначение: дефолтный пул терминов для $material_terms/$process_terms шаблона
#   gap_matrix — темы жюри + случай "холодный климат/кучное выщелачивание/
#   никелевая руда" (см. _DEFAULT_MATERIAL_TOPICS/_DEFAULT_PROCESS_TOPICS),
#   каждый развёрнут через онтологию (canonical_name + synonyms.yaml).
# Уровень: ✅ реализовано (A-12, worklogs/analytics.md)
def _default_terms(topics: list[str]) -> list[str]:
    synonyms_db = load_synonyms()
    pool: set[str] = set()
    for topic in topics:
        pool.update(_expand_topic_term(topic, synonyms_db))
    return sorted(pool)


# Назначение: batched-условие для набора ячеек — один запрос GAP_CELL_CONTEXT_QUERY
#   по всем chunk_id всех строк gap_matrix сразу (не по ячейке), см. cypher_templates.py.
# Уровень: ✅ реализовано (A-12, worklogs/analytics.md)
def _fetch_conditions(driver: Driver, chunk_ids: list[str]) -> dict[str, str]:
    if not chunk_ids:
        return {}
    with driver.session() as session:
        rows = [dict(r) for r in session.run(GAP_CELL_CONTEXT_QUERY, chunk_ids=chunk_ids)]
    result: dict[str, str] = {}
    for row in rows:
        # nc.param бывает пуст в извлечении (наблюдение A-09/A-10, worklogs/graph.md) —
        # strip() убирает висячий пробел перед op ("param op value unit" -> " op value unit").
        items = [t.strip() for t in (*row.get("constraint_texts", []), *row.get("property_names", []))]
        items = [t for t in items if t]
        if items:
            result[row["chunk_id"]] = items[0]
    return result


# Назначение: краткая строка GapCell.condition по chunk_ids строки gap_matrix —
#   до MAX_CONDITION_ITEMS различных условий из condition_by_chunk, "" если по
#   паре нет ни одного NumericConstraint/Property-контекста (n_sources=0 — самый
#   частый случай, chunk_ids пуст).
# Уровень: ✅ реализовано (A-12, worklogs/analytics.md)
def _condition_for_row(row: dict, condition_by_chunk: dict[str, str]) -> str:
    seen: list[str] = []
    for cid in row.get("chunk_ids", []):
        text = condition_by_chunk.get(cid)
        if text and text not in seen:
            seen.append(text)
        if len(seen) >= MAX_CONDITION_ITEMS:
            break
    return "; ".join(seen)


# Назначение: канонические имена тем пула (без разворота синонимов) — строгий
#   "буст"-тир в _cell_sort_key: тема засчитывается, только если узел графа
#   ТОЧНО (канонически) совпадает с ней, а не случайно через широкий CONTAINS-
#   синоним (EN-синонимы вроде "nickel" в name_en дают ~300 совпадений,
#   которые лишь упоминают никель, а не сама тема).
# Уровень: ✅ реализовано (fixer A-12, worklogs/analytics.md)
def _topic_canonicals(topics: list[str]) -> set[str]:
    return {(canonical_name(t) or t).strip().lower() for t in topics}


# Назначение: ключ сортировки строк gap_matrix (дефект №1 tester-отчёта) —
#   n_sources ASC (пробелы первыми), приоритет-тир ASC (0 — акцептный кейс
#   TASK.md, _ACCEPTANCE_CRITICAL_PAIRS, иначе гарантированно вычёркиваемый
#   диверсификацией; 1 — "буст" тем жюри, ОБА узла точно совпадают с темой
#   пула; 2 — прочие пары), сумма material_n_mentions+process_n_mentions DESC
#   (тай-брейк по значимости — ЗАМЕНЯЕТ алфавитный, топивший кейс материалом
#   "(NH4)2SO4"), имена — финальный стабильный тай-брейк.
# Уровень: ✅ реализовано (fixer A-12, worklogs/analytics.md)
def _cell_sort_key(row: dict, material_topics: set[str], process_topics: set[str]):
    material_canon = (canonical_name(row["material_name"]) or row["material_name"]).strip().lower()
    process_canon = (canonical_name(row["process_name"]) or row["process_name"]).strip().lower()
    if (material_canon, process_canon) in _ACCEPTANCE_CRITICAL_PAIRS:
        tier = 0
    elif material_canon in material_topics and process_canon in process_topics:
        tier = 1
    else:
        tier = 2
    mentions_sum = row.get("material_n_mentions", 0) + row.get("process_n_mentions", 0)
    return (row["n_sources"], tier, -mentions_sum, row["material_name"], row["process_name"])


# Назначение: диверсификация — не более max_per_material ячеек одного материала
#   (дефект №1, п.2), ПОСЛЕ сортировки _cell_sort_key — материал сохраняет свои
#   первые по значимости пары, лишние отбрасываются.
# Уровень: ✅ реализовано (fixer A-12, worklogs/analytics.md)
def _apply_material_diversity_cap(rows: list[dict], max_per_material: int) -> list[dict]:
    counts: dict[str, int] = {}
    kept: list[dict] = []
    for row in rows:
        if counts.get(row["material_name"], 0) >= max_per_material:
            continue
        counts[row["material_name"]] = counts.get(row["material_name"], 0) + 1
        kept.append(row)
    return kept


# Назначение: ячейки карты пробелов — шаблон gap_matrix с дефолтными
#   тематическими пулами терминов (не через QueryIntent/execute_intent — тому
#   нужен ровно один термин на слот, здесь — темы жюри целиком), + condition
#   через _fetch_conditions/_condition_for_row. Тянем GAP_DB_FETCH_LIMIT строк,
#   пересортировываем по _cell_sort_key, режем повторы материала
#   (_apply_material_diversity_cap) и режем до публичного `limit` в Python, не
#   трогая текст общего шаблона (см. комментарий GAP_DB_FETCH_LIMIT).
# Уровень: ✅ реализовано (A-12, worklogs/analytics.md; fixer A-12 — дефект №1)
def _build_cells(driver: Driver, limit: int) -> list[GapCell]:
    material_terms = _default_terms(_DEFAULT_MATERIAL_TOPICS)
    process_terms = _default_terms(_DEFAULT_PROCESS_TOPICS)
    with driver.session() as session:
        rows = [
            dict(r)
            for r in session.run(
                TEMPLATES["gap_matrix"],
                material_terms=material_terms,
                process_terms=process_terms,
                limit=GAP_DB_FETCH_LIMIT,
            )
        ]
    material_topics = _topic_canonicals(_DEFAULT_MATERIAL_TOPICS)
    process_topics = _topic_canonicals(_DEFAULT_PROCESS_TOPICS)
    rows.sort(key=lambda r: _cell_sort_key(r, material_topics, process_topics))
    rows = _apply_material_diversity_cap(rows, MAX_CELLS_PER_MATERIAL)
    rows = rows[:limit]

    chunk_ids = sorted({cid for row in rows for cid in row.get("chunk_ids", [])})
    condition_by_chunk = _fetch_conditions(driver, chunk_ids)
    return [
        GapCell(
            material=row["material_name"],
            process=row["process_name"],
            condition=_condition_for_row(row, condition_by_chunk),
            n_sources=row["n_sources"],
        )
        for row in rows
    ]


# Назначение: темы only_ru/only_foreign — Material/Process с n_mentions >=
#   MIN_MENTIONS_FOR_THEME, чьё множество geography документов-упоминаний
#   состоит РОВНО из одного значения (см. GEOGRAPHY_THEMES_QUERY).
# Уровень: ✅ реализовано (A-12, worklogs/analytics.md)
def _build_geography_themes(driver: Driver) -> tuple[list[str], list[str]]:
    with driver.session() as session:
        rows = [
            dict(r)
            for r in session.run(
                GEOGRAPHY_THEMES_QUERY,
                min_mentions=MIN_MENTIONS_FOR_THEME,
                limit=GEOGRAPHY_THEME_SCAN_LIMIT,
            )
        ]
    only_ru: list[str] = []
    only_foreign: list[str] = []
    for row in rows:
        geos = set(row.get("doc_geographies") or [])
        if geos == {Geography.RU.value}:
            only_ru.append(row["name"])
        elif geos == {Geography.FOREIGN.value}:
            only_foreign.append(row["name"])
    return sorted(only_ru), sorted(only_foreign)


# ─── build_gap_report ────────────────────────────────────────────────────────
# Назначение: карта пробелов ⭐ — публичный вход модуля/пакета analytics и
#   интерфейсный контракт для UI (A-13, сигнатура ФИКСИРОВАНА). driver=None ->
#   открывает свой driver (как graph/templates.py CLI) и закрывает по завершении;
#   переданный driver — не закрывается. limit<0 -> ValueError (ANALYTICS_INVALID_
#   LIMIT) ДО обращения к Neo4j (дефект №2 tester-отчёта); limit=0 — валиден,
#   пустой отчёт.
# Входные связи: neo4j.Driver | None, limit (сколько ячеек в итоговом GapReport)
# Выходные данные: contracts.GapReport (cells — n_sources=0 первыми, only_ru,
#   only_foreign)
# Уровень: ✅ реализовано (A-12, worklogs/analytics.md; fixer A-12 — дефект №2)
def build_gap_report(driver: Driver | None = None, *, limit: int = 50) -> GapReport:
    if limit < 0:
        raise ValueError(f"{ANALYTICS_INVALID_LIMIT}: limit должен быть >= 0 (получено {limit})")
    owns_driver = driver is None
    if owns_driver:
        from ariadna.graph.lexical_loader import get_driver

        driver = get_driver()
    try:
        cells = _build_cells(driver, limit)
        only_ru, only_foreign = _build_geography_themes(driver)
    finally:
        if owns_driver:
            driver.close()
    return GapReport(cells=cells, only_ru=only_ru, only_foreign=only_foreign)


# ─── main ─────────────────────────────────────────────────────────────────────
# Назначение: CLI-демо `python -m ariadna.analytics.gap_map [--limit N] [--json]`
#   — печатает топ-пробелов (n_sources=0 первыми — build_gap_report уже
#   отсортировал) и списки only_ru/only_foreign; --json — полный GapReport.
# Входные связи: sys.argv — --limit (int, по умолчанию 50), --json (флаг)
# Выходные данные: нет (печать сводки/JSON GapReport в stdout)
# Уровень: ✅ реализовано (A-12, worklogs/analytics.md)
def main() -> None:
    parser = argparse.ArgumentParser(description="Карта пробелов «Ариадны» (A-12)")
    parser.add_argument("--limit", type=int, default=50, help="сколько строк gap_matrix запросить")
    parser.add_argument("--json", action="store_true", help="печать полного GapReport в JSON")
    args = parser.parse_args()

    run_id = new_run_id("gap_map_")
    logger = get_logger("analytics", run_id)
    log_event(logger, stage="gap_map_cli", event="build_started", detail=f"limit={args.limit}")

    report = build_gap_report(limit=args.limit)

    log_event(
        logger,
        stage="gap_map_cli",
        event="build_finished",
        detail=f"n_cells={len(report.cells)} n_only_ru={len(report.only_ru)} "
        f"n_only_foreign={len(report.only_foreign)}",
    )

    if args.json:
        print(report.model_dump_json(indent=2))
        return

    full_gaps = [c for c in report.cells if c.n_sources == 0]
    print(f"Карта пробелов: {len(report.cells)} ячеек, из них {len(full_gaps)} без источников (n_sources=0)")
    print("\nТоп пробелов (n_sources=0 первыми):")
    for cell in report.cells[:20]:
        cond = f" [{cell.condition}]" if cell.condition else ""
        print(f"  {cell.material} × {cell.process} — n_sources={cell.n_sources}{cond}")

    print(f"\nТолько отечественная практика ({len(report.only_ru)}): {json.dumps(report.only_ru, ensure_ascii=False)}")
    print(f"Только зарубежная практика ({len(report.only_foreign)}): "
          f"{json.dumps(report.only_foreign, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
