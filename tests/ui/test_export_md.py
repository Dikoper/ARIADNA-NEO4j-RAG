"""Тесты экспорта в Markdown (A-23): ответ чата и карта пробелов —
структура отчёта, честная пометка «не найдено», пустые секции опускаются,
формат цитат совпадает с экранным (format_citation); секция «Рекомендации»
(У-1, A-15) — те же группы/порядок/иконки, что на экране, опускается при
пустом списке."""
from __future__ import annotations

from ariadna.contracts import Answer, Citation, Contradiction, GapCell, GapReport, Recommendation, RecommendationKind
from ui.citations_view import format_citation
from ui.export_md import answer_to_markdown, gap_report_to_markdown


def _sample_answer(**overrides) -> Answer:
    fields = {
        "question": "Какие методы обессоливания подходят?",
        "text": "Подходят обратный осмос и электродиализ.",
        "citations": [
            Citation(doc_id="d1", chunk_id="c1", title="Обзор мембранных методов", year=2023, quote="осмос эффективен"),
            Citation(doc_id="d2", chunk_id="c2", title="", year=None, quote=""),
        ],
        "contradictions": [
            Contradiction(
                claim_a="скорость 1 м/с оптимальна",
                claim_b="скорость 2 м/с оптимальна",
                citations=[Citation(doc_id="d3", chunk_id="c3", title="Статья", year=2020, quote="")],
            )
        ],
        "found": True,
    }
    fields.update(overrides)
    return Answer(**fields)


def test_answer_markdown_contains_question_text_and_sections():
    md = answer_to_markdown(_sample_answer(), generated_at="04.07.2026 20:00")
    assert md.startswith("# Какие методы обессоливания подходят?")
    assert "Подходят обратный осмос и электродиализ." in md
    assert "## Источники" in md
    assert "## Противоречия в источниках" in md
    assert "04.07.2026 20:00" in md


def test_answer_markdown_citations_match_screen_format():
    answer = _sample_answer()
    md = answer_to_markdown(answer)
    for cit in answer.citations:
        assert format_citation(cit) in md


def test_answer_markdown_not_found_note():
    md = answer_to_markdown(_sample_answer(found=False))
    assert "не найдено прямого ответа" in md


def test_answer_markdown_empty_sections_omitted():
    md = answer_to_markdown(_sample_answer(citations=[], contradictions=[]))
    assert "## Источники" not in md
    assert "## Противоречия" not in md


def test_answer_markdown_recommendations_section_omitted_when_empty():
    md = answer_to_markdown(_sample_answer(recommendations=[]))
    assert "## Рекомендации" not in md


def test_answer_markdown_recommendations_section_grouped_and_ordered():
    recommendations = [
        Recommendation(
            kind=RecommendationKind.ADJACENT_TOPIC, title="Электродиализ", reason="смежная тема вопроса",
        ),
        Recommendation(
            kind=RecommendationKind.SIMILAR_CASE, title="Похожий эксперимент", reason="близкая постановка",
            citations=[Citation(doc_id="d4", chunk_id="d4#1", title="Отчёт", year=2022, quote="аналогичный режим")],
        ),
    ]
    md = answer_to_markdown(_sample_answer(recommendations=recommendations))
    assert "## Рекомендации" in md
    assert "### Похожие кейсы" in md
    assert "### Смежные темы" in md
    # Порядок групп в тексте: «Похожие кейсы» перед «Смежные темы», даже если
    # вход пришёл в обратном порядке (similar_case -> ... -> adjacent_topic).
    assert md.index("### Похожие кейсы") < md.index("### Смежные темы")
    assert "📄 **Похожий эксперимент** — близкая постановка" in md
    assert "🧭 **Электродиализ** — смежная тема вопроса" in md
    assert format_citation(recommendations[1].citations[0]) in md


def test_answer_markdown_no_date_when_not_passed():
    md = answer_to_markdown(_sample_answer())
    assert md.rstrip().endswith("«Ариадна»_")


def test_gap_report_markdown_table_and_topics():
    report = GapReport(
        cells=[
            GapCell(material="медно-никелевая руда", process="кучное выщелачивание", condition="холодный климат", n_sources=0),
            GapCell(material="никель", process="электроэкстракция", condition="", n_sources=7),
        ],
        only_ru=["тема-ru"],
        only_foreign=["тема-en"],
    )
    md = gap_report_to_markdown(report, generated_at="04.07.2026 20:00")
    assert "| медно-никелевая руда | кучное выщелачивание | холодный климат | 0 | **да** |" in md
    assert "| никель | электроэкстракция | — | 7 | нет |" in md
    assert "## Темы только в отечественной литературе" in md
    assert "- тема-ru" in md
    assert "- тема-en" in md


def test_gap_report_markdown_empty_report_still_valid():
    md = gap_report_to_markdown(GapReport())
    assert md.startswith("# Карта пробелов")
    assert "| Материал |" not in md  # пустая таблица не рисуется
