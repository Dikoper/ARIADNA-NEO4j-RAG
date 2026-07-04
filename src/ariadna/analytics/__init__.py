"""Пакет analytics: аналитика поверх готового графа (только чтение).

Вход: наполненный Neo4j (см. graph/entity_graph_writer). Выход: contracts.GapReport
(A-12, карта пробелов ⭐ — главная демо-фича), позже LitReview/Recommendation
(A-14/A-16). Публичный вход — `ariadna.analytics.gap_map.build_gap_report()`
(НЕ реэкспортирован здесь — эager-импорт submodule в `__init__.py` ломает
`python -m ariadna.analytics.gap_map` (RuntimeWarning двойного импорта в runpy);
тот же приём, что `graph/__init__.py`/`search/__init__.py` — пакетные докстринги
без реэкспорта подмодулей).
Паспорт: docs/dev/modules/analytics.md.
"""
