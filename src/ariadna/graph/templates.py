"""Исполнение Cypher-шаблонов графового поиска (A-10): QueryIntent -> подграф.

Вход: `contracts.QueryIntent` (template_id, slots, filters — заполняет
`search/router.py`) + наполненный Neo4j (только чтение). Выход: `execute_intent()`
возвращает НЕ pydantic-контракт (это намеренно — промежуточный формат между graph
и search, задокументирован ниже), а plain `dict`, из которого A-11 (`search/
retrieval.py`/`answer.py`) собирает `contracts.Answer`. Сами тексты Cypher-запросов
и `TEMPLATES` — в `graph/cypher_templates.py` (вынесены отдельно ради лимита
~350 строк на модуль, CONVENTIONS.md §3; исполняемой логики там нет).

Зависимости: `neo4j` (bolt-драйвер, ТОЛЬКО чтение — инвариант №2), `ariadna.
graph.cypher_templates` (TEMPLATES, дефолты слотов), `ariadna.graph.ontology`
(канонизация/синонимы слотов), `ariadna.graph.lexical_loader.get_driver`
(только у CLI), `ariadna.logutil`.
Инварианты: только параметризованные запросы — `$param`, конкатенация строк в
Cypher ЗАПРЕЩЕНА (инвариант №4); модуль не пишет в Neo4j.
Паспорт: docs/dev/modules/graph.md (A-10).

─── Формат результата execute_intent() (промежуточный, НЕ в contracts.py) ───
{
    "rows": list[dict],              # строки результата шаблона (поля см. cypher_templates.TEMPLATES)
    "node_ids": list[str],           # id узлов подграфа для UI — объединение row["node_ids"]
    "chunk_ids": list[str],          # чанки-свидетельства для цитат — объединение row["chunk_ids"]
    "contradiction_pairs": list[dict],  # {node_a_id, node_b_id, name_a, name_b, doc_id, chunk_id}
}
Каждый Cypher-шаблон в TEMPLATES обязан возвращать в каждой строке поля
`node_ids: list[str]` и `chunk_ids: list[str]` — execute_intent их просто
объединяет по всем строкам (единый контракт между шаблонами, не завязан на
конкретные имена столбцов конкретного шаблона).
"""
from __future__ import annotations

import json
import sys

from neo4j import Driver

from ariadna.contracts import QueryIntent
from ariadna.graph.cypher_templates import TEMPLATE_DEFAULT_CANONICALS, TEMPLATES
from ariadna.graph.ontology import canonical_name, load_synonyms
from ariadna.logutil import get_logger, log_event, new_run_id

# Неизвестный template_id передан в execute_intent — docs/dev/ERRORS.md.
# ПРИМЕЧАНИЕ: постановка задачи предлагала код SEARCH-002, но он уже занят
# другим симптомом («слот шаблона не заполнен», см. ERRORS.md); SEARCH-006/007
# заняты параллельным A-11 (retrieval/answer) на момент записи — следующий
# свободный код SEARCH-008 (см. worklog/search.md записи A-10).
SEARCH_UNKNOWN_TEMPLATE = "SEARCH-008"

# ─── Лимиты выдачи по шаблону (constants — CONVENTIONS.md §3) ────────────
DEFAULT_ROW_LIMIT = 30       # основные шаблоны a-d
COMPARE_ROW_LIMIT = 50       # compare_ru_foreign — шире, т.к. агрегируется по гео
GAP_ROW_LIMIT = 30           # gap_matrix — заготовка для A-12


# Назначение: разворачивает канонический термин (или свободный текст слота)
#   в набор нижнерегистровых search-подстрок для CONTAINS-матчинга: сам
#   термин + его каноническая форма (ontology.canonical_name) + все синонимы
#   канона (ontology.load_synonyms) — надёжнее одиночной подстроки при
#   RU/EN-синонимии темы (обессоливание/desalination/…).
# Уровень: ✅ реализовано (A-10, worklogs/graph.md)
def _expand_terms(term: str) -> list[str]:
    term = term.strip()
    if not term:
        return []
    canon = canonical_name(term) or term
    synonyms_db = load_synonyms()
    record = synonyms_db.get(canon, {})
    pool = {term.lower(), canon.lower(), *(s.lower() for s in record.get("synonyms", []))}
    return sorted(pool)


# Назначение: строит список терминов для слота `slot_key` шаблона `template_id`
#   — берёт значение из intent.slots, если заполнено, иначе перебирает
#   дефолтные канонические термины шаблона (TEMPLATE_DEFAULT_CANONICALS) и
#   разворачивает КАЖДЫЙ через _expand_terms (объединение синонимов всех
#   дефолтов — так «методы обессоливания» матчатся и на «обратный осмос»,
#   и на «электродиализ», а не только на канон «обессоливание»).
# Уровень: ✅ реализовано (A-10, worklogs/graph.md)
def _terms_for_slot(intent: QueryIntent, slot_key: str) -> list[str]:
    explicit = intent.slots.get(slot_key, "").strip()
    if explicit:
        return _expand_terms(explicit)
    defaults = TEMPLATE_DEFAULT_CANONICALS.get(intent.template_id, {}).get(slot_key, [])
    terms: set[str] = set()
    for canon in defaults:
        terms.update(_expand_terms(canon))
    return sorted(terms)


# Назначение: термины числового параметра из intent.filters.numeric (имена
#   param, найденные extraction.rules.extract_constraints в вопросе) — пустой
#   список означает «не сужать constraints по param», см. шаблоны в
#   cypher_templates.py.
# Уровень: ✅ реализовано (A-10, worklogs/graph.md)
def _param_terms(intent: QueryIntent) -> list[str]:
    return sorted({c.param.strip().lower() for c in intent.filters.numeric if c.param.strip()})


# Назначение: собирает параметры Cypher-запроса под конкретный template_id из
#   слотов/фильтров QueryIntent — единственное место, знающее соответствие
#   «шаблон -> его слоты» (кроме самих Cypher-строк в cypher_templates.py).
# Уровень: ✅ реализовано (A-10, worklogs/graph.md)
def _build_params(intent: QueryIntent) -> dict:
    tid = intent.template_id
    if tid == "desalination_methods":
        return {
            "process_terms": _terms_for_slot(intent, "process"),
            "param_terms": _param_terms(intent) or _expand_terms("минерализация"),
            "limit": DEFAULT_ROW_LIMIT,
        }
    if tid == "catholyte_circulation":
        return {
            "material_terms": _terms_for_slot(intent, "material"),
            "param_terms": _param_terms(intent) or _expand_terms("скорость потока"),
            "limit": DEFAULT_ROW_LIMIT,
        }
    if tid == "experiments_publications_by_topic":
        return {
            "material_terms": _terms_for_slot(intent, "material"),
            "year_from": intent.filters.year_from,
            "limit": DEFAULT_ROW_LIMIT,
        }
    if tid == "mine_water_injection":
        return {
            "process_terms": _terms_for_slot(intent, "process"),
            "material_terms": _terms_for_slot(intent, "material"),
            "param_terms": _param_terms(intent),
            "limit": DEFAULT_ROW_LIMIT,
        }
    if tid == "compare_ru_foreign":
        topic = intent.slots.get("topic", "") or intent.slots.get("process", "") or intent.slots.get("material", "")
        return {"terms": _expand_terms(topic) if topic else [], "limit": COMPARE_ROW_LIMIT}
    if tid == "gap_matrix":
        return {
            "material_terms": _terms_for_slot(intent, "material"),
            "process_terms": _terms_for_slot(intent, "process"),
            "limit": GAP_ROW_LIMIT,
        }
    raise ValueError(f"{SEARCH_UNKNOWN_TEMPLATE}: неизвестный template_id '{tid}' — нет в TEMPLATES")


# Назначение: выполняет Cypher-запрос и возвращает строки как plain dict
#   (neo4j.Record -> dict; вложенные map/list уже примитивы Python).
# Уровень: ✅ реализовано (A-10, worklogs/graph.md)
def _run(driver: Driver, query: str, params: dict) -> list[dict]:
    with driver.session() as session:
        return [dict(record) for record in session.run(query, **params)]


# ─── get_contradiction_pairs ───────────────────────────────────────────────
# Назначение: ищет связи CONTRADICTS МЕЖДУ узлами подграфа (node_ids) — У-3,
#   подсветка противоречий в UI/ответе; edge несёт provenance (doc_id/chunk_id)
#   как любая RelationType-связь (entity_graph_writer._relation_merge_query).
# Входные связи: neo4j.Driver, node_ids (уже найденные execute_intent)
# Выходные данные: list[dict] — node_a_id, node_b_id, name_a, name_b, doc_id, chunk_id
# Уровень: ✅ реализовано (A-10, worklogs/graph.md)
def get_contradiction_pairs(driver: Driver, node_ids: list[str]) -> list[dict]:
    if not node_ids:
        return []
    query = (
        "MATCH (a:Entity)-[r:CONTRADICTS]->(b:Entity) "
        "WHERE a.id IN $node_ids AND b.id IN $node_ids "
        "RETURN a.id AS node_a_id, b.id AS node_b_id, a.name AS name_a, b.name AS name_b, "
        "       coalesce(r.doc_id, '') AS doc_id, coalesce(r.chunk_id, '') AS chunk_id"
    )
    return _run(driver, query, {"node_ids": node_ids})


# ─── execute_intent ────────────────────────────────────────────────────────
# Назначение: выполняет Cypher-шаблон intent.template_id с параметрами из
#   слотов/фильтров QueryIntent; возвращает промежуточный dict (см. docstring
#   модуля) — НЕ pydantic-контракт, A-11 строит из него contracts.Answer.
#   gap_matrix со ВСЕМИ пустыми material_terms И process_terms отклоняется —
#   иначе декартово произведение Material x Process (~26 млн пар) на боевой
#   базе (не защита от опечатки — защита от случайного полного скана).
# Входные связи: neo4j.Driver (только чтение), contracts.QueryIntent
# Выходные данные: dict{rows, node_ids, chunk_ids, contradiction_pairs}
# Уровень: ✅ реализовано (A-10, worklogs/graph.md)
def execute_intent(driver: Driver, intent: QueryIntent) -> dict:
    if intent.template_id not in TEMPLATES:
        raise ValueError(
            f"{SEARCH_UNKNOWN_TEMPLATE}: неизвестный template_id '{intent.template_id}' — "
            f"допустимые: {sorted(TEMPLATES)} или 'rag_fallback' (векторный путь, вне graph/templates)"
        )
    params = _build_params(intent)
    if intent.template_id == "gap_matrix" and not params["material_terms"] and not params["process_terms"]:
        # Не отдельный код ошибки (не путь SEARCH-00x — это защитная проверка
        # вызова, а не runtime-сбой инфраструктуры/данных): gap_matrix без
        # ХОТЯ БЫ одного слота — полный декартов скан Material x Process.
        raise ValueError(
            "gap_matrix требует хотя бы один непустой слот material/process — "
            "иначе полный декартов скан Material x Process (~26 млн пар на боевой базе)"
        )

    rows = _run(driver, TEMPLATES[intent.template_id], params)
    node_ids = sorted({nid for row in rows for nid in (row.get("node_ids") or [])})
    chunk_ids = sorted({cid for row in rows for cid in (row.get("chunk_ids") or [])})
    contradiction_pairs = get_contradiction_pairs(driver, node_ids)

    return {
        "rows": rows,
        "node_ids": node_ids,
        "chunk_ids": chunk_ids,
        "contradiction_pairs": contradiction_pairs,
    }


# ─── main ───────────────────────────────────────────────────────────────────
# Назначение: CLI-смоук — `python -m ariadna.graph.templates "вопрос"`:
#   route(question) (search/router, импорт локальный — graph не зависит от
#   search на уровне модуля, только у CLI) -> execute_intent -> счётчики.
# Входные связи: sys.argv[1] — вопрос
# Выходные данные: нет (печать JSON сводки в stdout)
# Уровень: ✅ реализовано (A-10, worklogs/graph.md)
def main() -> None:
    if len(sys.argv) < 2:
        print("Использование: python -m ariadna.graph.templates \"вопрос\"", file=sys.stderr)
        raise SystemExit(2)
    question = sys.argv[1]

    from ariadna.graph.lexical_loader import get_driver
    from ariadna.search.router import route

    run_id = new_run_id("templates_")
    logger = get_logger("graph", run_id)

    intent = route(question)
    log_event(logger, stage="templates_cli", event="intent_routed",
              detail=f"template_id={intent.template_id} slots={intent.slots}")

    driver = get_driver()
    try:
        if intent.template_id == "rag_fallback":
            result = {"rows": [], "node_ids": [], "chunk_ids": [], "contradiction_pairs": []}
        else:
            result = execute_intent(driver, intent)
    finally:
        driver.close()

    summary = {
        "question": question,
        "template_id": intent.template_id,
        "slots": intent.slots,
        "compare_geography": intent.compare_geography,
        "n_rows": len(result["rows"]),
        "n_node_ids": len(result["node_ids"]),
        "n_chunk_ids": len(result["chunk_ids"]),
        "n_contradiction_pairs": len(result["contradiction_pairs"]),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
