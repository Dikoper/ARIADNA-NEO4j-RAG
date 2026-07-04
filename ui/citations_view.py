"""Форматирование цитат и фильтр по году (A-13) — чистые функции без Streamlit.

Вход: `contracts.Citation` (список из `Answer.citations`). Выход: строки для
отображения («документ, фрагмент» — по паспорту ui слова «чанк»/Cypher в
интерфейсе запрещены) и отфильтрованный по году список цитат.

Зависимости: только `contracts.Citation` (pydantic). Инвариант: фильтр
географии для цитат НЕ реализован здесь — у `Citation` нет поля geography
(контракт 🔒, см. contracts.py) и добавлять своё поле нельзя (паспорт ui:
«своих структур данных не заводит»); сайдбар обязан показывать этот фильтр
как disabled с честным пояснением, а не выдумывать данные.
"""
from __future__ import annotations

from ariadna.contracts import Citation

GEOGRAPHY_FILTER_UNAVAILABLE_NOTE = (
    "Фильтр по географии для цитат пока не поддерживается — источники не "
    "размечены по странам."
)


# ─── format_citation ────────────────────────────────────────────────────
# Назначение: человекочитаемая строка одной цитаты — «Название (год) —
#   "цитата"»; без слов «чанк»/Cypher (паспорт ui — пользователь без графовой
#   подготовки). Пустой title — подстраховка doc_id, год — «б/г» (без года).
# Входные связи: contracts.Citation
# Выходные данные: str
# Уровень: ✅ реализовано (A-13)
def format_citation(citation: Citation) -> str:
    title = citation.title.strip() or citation.doc_id
    year = str(citation.year) if citation.year else "б/г"
    quote = citation.quote.strip()
    base = f"{title} ({year})"
    return f"{base} — «{quote}»" if quote else base


# ─── filter_citations_by_year ───────────────────────────────────────────
# Назначение: оставляет цитаты в диапазоне [year_from, year_to] (границы
#   опциональны — None = без ограничения с этой стороны); цитаты БЕЗ года
#   (year=None) сохраняются всегда — честно (нет данных для решения, не
#   выбрасываем источник по умолчанию).
# Входные связи: список Citation, опциональные границы года
# Выходные данные: отфильтрованный список Citation (новый список, не мутирует вход)
# Уровень: ✅ реализовано (A-13)
def filter_citations_by_year(
    citations: list[Citation],
    *,
    year_from: int | None = None,
    year_to: int | None = None,
) -> list[Citation]:
    if year_from is None and year_to is None:
        return list(citations)
    result = []
    for c in citations:
        if c.year is None:
            result.append(c)
            continue
        if year_from is not None and c.year < year_from:
            continue
        if year_to is not None and c.year > year_to:
            continue
        result.append(c)
    return result
