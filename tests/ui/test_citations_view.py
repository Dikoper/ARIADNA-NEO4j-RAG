"""Тесты форматирования цитат и фильтра по году (A-13)."""
from __future__ import annotations

from ariadna.contracts import Citation
from ui.citations_view import format_citation, filter_citations_by_year


def test_format_citation_full_fields():
    c = Citation(doc_id="d1", chunk_id="d1#3", title="Обзор технологий", year=2020, quote="цитата из документа")
    assert format_citation(c) == 'Обзор технологий (2020) — «цитата из документа»'


def test_format_citation_missing_title_falls_back_to_doc_id():
    c = Citation(doc_id="doc-42", chunk_id="doc-42#1", title="", year=2019, quote="текст")
    assert format_citation(c).startswith("doc-42 (2019)")


def test_format_citation_missing_year_shows_bg():
    c = Citation(doc_id="d1", chunk_id="d1#1", title="Статья", year=None, quote="текст")
    assert "б/г" in format_citation(c)


def test_format_citation_no_quote_has_no_dash():
    c = Citation(doc_id="d1", chunk_id="d1#1", title="Статья", year=2021, quote="")
    assert format_citation(c) == "Статья (2021)"
    assert "—" not in format_citation(c)


def test_format_citation_never_mentions_forbidden_words():
    c = Citation(doc_id="d1", chunk_id="d1#1", title="Статья", year=2021, quote="текст")
    rendered = format_citation(c).lower()
    assert "чанк" not in rendered
    assert "cypher" not in rendered


def _citation(year):
    return Citation(doc_id=f"d{year}", chunk_id=f"d{year}#1", title=f"Doc {year}", year=year, quote="q")


def test_filter_citations_by_year_no_bounds_returns_all():
    citations = [_citation(2000), _citation(2020), _citation(None)]
    assert filter_citations_by_year(citations) == citations


def test_filter_citations_by_year_range():
    citations = [_citation(2000), _citation(2015), _citation(2020)]
    result = filter_citations_by_year(citations, year_from=2010, year_to=2020)
    assert [c.year for c in result] == [2015, 2020]


def test_filter_citations_by_year_keeps_unknown_year():
    citations = [_citation(2000), _citation(None)]
    result = filter_citations_by_year(citations, year_from=2010, year_to=2020)
    assert None in [c.year for c in result]
    assert 2000 not in [c.year for c in result]


def test_filter_citations_by_year_does_not_mutate_input():
    citations = [_citation(2000), _citation(2020)]
    original = list(citations)
    filter_citations_by_year(citations, year_from=2010)
    assert citations == original
