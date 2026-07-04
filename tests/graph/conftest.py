"""Фикстуры тестов graph/lexical_loader и graph/entity_graph_writer: живой
Neo4j-драйвер + строгая изоляция тестовых данных по префиксам test_a05_/test_a09_
(см. docs/dev/modules/graph.md, инвариант «единственный модуль с правом записи
в Neo4j» — тесты используют тот же get_driver(), что и модуль, подключение
из .env, см. CLAUDE.md).

Вход: нет. Выход: driver (session-scoped), автоочистка узлов/связей с префиксами
test_a05_ (Document/Chunk лексического графа) и test_a09_ (сущностный граф A-09:
Chunk/Entity/NumericConstraint) до и после каждого теста — изоляция от
параллельной боевой загрузки и от чужих смоук-данных в той же базе.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ariadna.graph.lexical_loader import get_driver

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Префикс всех тестовых doc_id/chunk_id этого модуля — единственный признак,
# по которому очистка отличает тестовые узлы от боевых/смоук данных в той же БД.
TEST_PREFIX = "test_a05_"


# ─── driver ──────────────────────────────────────────────────────────────
# Назначение: один neo4j.Driver на весь тестовый модуль (переиспользуется всеми
#   тестами — тот же способ подключения, что использует lexical_loader.get_driver).
# Уровень: ✅ реализовано (module-tester A-05)
@pytest.fixture(scope="session")
def driver():
    d = get_driver()
    yield d
    d.close()


# ─── _delete_test_nodes ──────────────────────────────────────────────────
# Назначение: DETACH DELETE только узлов с doc_id/chunk_id, начинающимися
#   с TEST_PREFIX — не трогает боевые/смоук Document и Chunk в той же базе.
# Уровень: ✅ реализовано (module-tester A-05)
def _delete_test_nodes(driver) -> None:
    with driver.session() as session:
        session.run(
            "MATCH (c:Chunk) WHERE c.chunk_id STARTS WITH $prefix DETACH DELETE c",
            prefix=TEST_PREFIX,
        )
        session.run(
            "MATCH (d:Document) WHERE d.doc_id STARTS WITH $prefix DETACH DELETE d",
            prefix=TEST_PREFIX,
        )


# ─── clean_test_nodes ────────────────────────────────────────────────────
# Назначение: очищает тестовые узлы ДО и ПОСЛЕ каждого теста — гарантирует
#   чистый старт независимо от порядка тестов и состояния после упавшего теста.
# Уровень: ✅ реализовано (module-tester A-05)
@pytest.fixture(autouse=True)
def clean_test_nodes(driver):
    _delete_test_nodes(driver)
    yield
    _delete_test_nodes(driver)


# Префикс тестовых chunk_id/doc_id сущностного графа A-09 (entity_graph_writer/
# entity_loader) — отдельный от test_a05_, чтобы не путать лексический и
# сущностный тестовые наборы в одной автоочистке.
TEST_PREFIX_A09 = "test_a09_"


# ─── _delete_a09_nodes ────────────────────────────────────────────────────
# Назначение: удаляет тестовые узлы сущностного графа A-09 — Chunk с
#   chunk_id STARTS WITH test_a09_ (создаются тестами writer напрямую, без
#   lexical_loader), Entity с id, содержащим слаг test-a09- (make_node_id
#   транслитерирует "_" в "-" — см. entity_dedup.slugify), NumericConstraint
#   с param STARTS WITH test_a09_ (НЕ только подвешенный к Chunk через
#   HAS_CONSTRAINT — тест "missing chunk" намеренно создаёт constraint-узел
#   БЕЗ связи, см. test_load_numeric_constraints_missing_chunk_creates_node_
#   without_edge; фильтр только по HAS_CONSTRAINT такой узел не удаляет —
#   найдено на практике, см. worklog "Найденные баги"/"Проблемы"). Не трогает
#   боевые узлы — ни один боевой id/chunk_id/param не содержит эти маркеры.
# Уровень: ✅ реализовано (module-tester A-09)
def _delete_a09_nodes(driver) -> None:
    with driver.session() as session:
        session.run(
            "MATCH (n:NumericConstraint) WHERE n.param STARTS WITH $prefix DETACH DELETE n",
            prefix=TEST_PREFIX_A09,
        )
        session.run("MATCH (e:Entity) WHERE e.id CONTAINS 'test-a09' DETACH DELETE e")
        session.run(
            "MATCH (c:Chunk) WHERE c.chunk_id STARTS WITH $prefix DETACH DELETE c",
            prefix=TEST_PREFIX_A09,
        )


# ─── clean_a09_nodes ──────────────────────────────────────────────────────
# Назначение: очищает тестовые узлы сущностного графа A-09 ДО и ПОСЛЕ каждого
#   теста tests/graph/ (тот же паттерн, что clean_test_nodes для A-05, отдельный
#   префикс) — независимая изоляция от параллельного использования той же БД.
# Уровень: ✅ реализовано (module-tester A-09)
@pytest.fixture(autouse=True)
def clean_a09_nodes(driver):
    _delete_a09_nodes(driver)
    yield
    _delete_a09_nodes(driver)
