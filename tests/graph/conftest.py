"""Фикстуры тестов graph/lexical_loader: живой Neo4j-драйвер + строгая изоляция
тестовых данных по префиксу test_a05_ (см. docs/dev/modules/graph.md, инвариант
«единственный модуль с правом записи в Neo4j» — тесты используют тот же
get_driver(), что и модуль, подключение из .env, см. CLAUDE.md).

Вход: нет. Выход: driver (session-scoped), автоочистка узлов/связей с префиксом
test_a05_ до и после каждого теста — изоляция от параллельной боевой загрузки
9580 чанков и от чужих смоук-данных в той же базе.
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
