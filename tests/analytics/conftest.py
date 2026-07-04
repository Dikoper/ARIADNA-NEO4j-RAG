"""Фикстуры тестов analytics/gap_map (A-12): живой Neo4j-driver (только чтение —
analytics не пишет, автоочистка тестовых узлов не нужна, в отличие от
tests/graph/conftest.py). Живые интеграционные тесты скипаются, если Neo4j
недоступен (NEO4J_LIVE), не падают — офлайновые тесты (мок driver) от живой
инфраструктуры не зависят.

Вход: нет. Выход: NEO4J_LIVE (bool), driver (session-scoped, get_driver()).
"""
from __future__ import annotations

import pytest


# ─── _check_neo4j_alive ─────────────────────────────────────────────────────
# Назначение: проверяет доступность Neo4j (bolt, NEO4J_URI/USER/PASSWORD из .env,
#   тот же способ подключения, что analytics.gap_map.build_gap_report(driver=None)
#   через graph.lexical_loader.get_driver) — тот же приём, что tests/search/conftest.py.
# Уровень: ✅ реализовано (module-tester A-12)
def _check_neo4j_alive() -> bool:
    try:
        from ariadna.graph.lexical_loader import get_driver

        driver = get_driver()
        try:
            driver.verify_connectivity()
            return True
        finally:
            driver.close()
    except Exception:  # noqa: BLE001 — любой сбой подключения = живого стенда нет
        return False


NEO4J_LIVE = _check_neo4j_alive()


# ─── driver ──────────────────────────────────────────────────────────────
# Назначение: один neo4j.Driver на весь тестовый модуль — переиспользуется всеми
#   живыми тестами (build_gap_report — не дешёвая операция, см. worklog A-12:
#   ~20-25с на боевом графе независимо от лимита — тесты группируют вызовы
#   через фикстуры module-scope, не пересчитывают отчёт на каждый assert).
# Уровень: ✅ реализовано (module-tester A-12)
@pytest.fixture(scope="session")
def driver():
    from ariadna.graph.lexical_loader import get_driver

    d = get_driver()
    yield d
    d.close()
