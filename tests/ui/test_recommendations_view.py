"""Тесты панели «Рекомендации» (У-1, A-15): группировка по видам в фиксированном
порядке (similar_case -> expert -> adjacent_topic), пустые группы опускаются,
рендер на мок-данных не падает (Streamlit вне ScriptRunContext — no-op с
ворнингом, см. worklogs/ui.md#A-13), формат карточек переиспользует
citations_view.format_citation."""
from __future__ import annotations

from ariadna.contracts import Citation, Recommendation, RecommendationKind
from ui.recommendations_view import (
    KIND_ICONS,
    KIND_ORDER,
    KIND_TITLES_RU,
    NO_RECOMMENDATIONS_NOTE,
    group_recommendations,
    render_recommendations,
)


def _rec(kind: RecommendationKind, title: str, **overrides) -> Recommendation:
    fields = {"kind": kind, "title": title, "reason": "релевантно теме вопроса"}
    fields.update(overrides)
    return Recommendation(**fields)


def test_kind_order_matches_task_specification():
    assert KIND_ORDER == [
        RecommendationKind.SIMILAR_CASE,
        RecommendationKind.EXPERT,
        RecommendationKind.ADJACENT_TOPIC,
    ]


def test_kind_titles_and_icons_cover_all_kinds():
    for kind in RecommendationKind:
        assert kind in KIND_TITLES_RU
        assert kind in KIND_ICONS


def test_group_recommendations_preserves_order_and_drops_empty_kinds():
    recs = [
        _rec(RecommendationKind.ADJACENT_TOPIC, "Смежная тема А"),
        _rec(RecommendationKind.SIMILAR_CASE, "Кейс А"),
        _rec(RecommendationKind.SIMILAR_CASE, "Кейс Б"),
    ]
    groups = group_recommendations(recs)
    assert [kind for kind, _ in groups] == [RecommendationKind.SIMILAR_CASE, RecommendationKind.ADJACENT_TOPIC]
    kinds_to_items = dict(groups)
    assert [r.title for r in kinds_to_items[RecommendationKind.SIMILAR_CASE]] == ["Кейс А", "Кейс Б"]
    # Эксперты отсутствуют в списке -> группа не должна появиться вовсе.
    assert RecommendationKind.EXPERT not in kinds_to_items


def test_group_recommendations_empty_input_returns_empty_list():
    assert group_recommendations([]) == []


def test_group_recommendations_all_three_kinds():
    recs = [
        _rec(RecommendationKind.SIMILAR_CASE, "Кейс"),
        _rec(RecommendationKind.EXPERT, "Эксперт"),
        _rec(RecommendationKind.ADJACENT_TOPIC, "Тема"),
    ]
    groups = group_recommendations(recs)
    assert len(groups) == 3
    assert [kind for kind, _ in groups] == KIND_ORDER


def test_render_recommendations_empty_list_does_not_raise():
    # Streamlit вне ScriptRunContext исполняет вызовы как no-op (см. worklogs/
    # ui.md#A-13) — здесь важно только отсутствие исключения на пустом входе.
    render_recommendations([])


def test_render_recommendations_three_kinds_with_citations_does_not_raise():
    recs = [
        _rec(
            RecommendationKind.SIMILAR_CASE,
            "Похожий эксперимент по обессоливанию",
            citations=[Citation(doc_id="d1", chunk_id="d1#1", title="Статья", year=2021, quote="осмос эффективен")],
        ),
        _rec(RecommendationKind.EXPERT, "Лаборатория гидрометаллургии"),
        _rec(RecommendationKind.ADJACENT_TOPIC, "Электродиализ шахтных вод"),
    ]
    render_recommendations(recs)


def test_no_recommendations_note_has_no_forbidden_jargon():
    lowered = NO_RECOMMENDATIONS_NOTE.lower()
    for term in ("chunk", "чанк", "cypher", "contradicts"):
        assert term not in lowered
