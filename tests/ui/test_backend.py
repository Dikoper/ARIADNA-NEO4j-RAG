"""Тесты обвязки ui.backend (A-13): кэш-first ответ, честная деградация
get_subgraph/get_gap_report при недоступности fetch_subgraph/build_gap_report
(ленивый импорт, ещё не приземлились/стенд недоступен)."""
from __future__ import annotations

import sys
import types

import pytest

from ariadna.contracts import Answer, GapReport
from ui import backend


def _fake_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def test_get_answer_cache_hit_does_not_call_answer_question(monkeypatch, tmp_path):
    cache_path = tmp_path / "answer_cache.json"
    seed_answer = Answer(question="Вопрос из кэша", text="Ответ из кэша", found=True)
    backend.answer_cache.put_answer(seed_answer.question, seed_answer.model_dump(), path=cache_path)

    # Модуль search.answer НЕ подложен — если бы код попытался его импортировать
    # при попадании в кэш, тест бы упал с ImportError (модуль реально существует,
    # но был бы вызван answer_question, которого тут нет смысла гонять).
    answer, from_cache = backend.get_answer("Вопрос из кэша", cache_path=cache_path)
    assert from_cache is True
    assert answer.text == "Ответ из кэша"


def test_get_answer_cache_miss_calls_answer_question_and_writes_cache(monkeypatch, tmp_path):
    cache_path = tmp_path / "answer_cache.json"
    calls = []

    def fake_answer_question(question):
        calls.append(question)
        return Answer(question=question, text="свежий ответ", found=True)

    fake_mod = _fake_module("ariadna.search.answer", answer_question=fake_answer_question)
    monkeypatch.setitem(sys.modules, "ariadna.search.answer", fake_mod)

    answer, from_cache = backend.get_answer("Новый вопрос", cache_path=cache_path)
    assert from_cache is False
    assert answer.text == "свежий ответ"
    assert calls == ["Новый вопрос"]

    # Ответ дописан в кэш — повторный вызов больше не должен звать answer_question.
    calls.clear()
    answer2, from_cache2 = backend.get_answer("Новый вопрос", cache_path=cache_path)
    assert from_cache2 is True
    assert calls == []


def test_get_answer_force_recompute_bypasses_cache(monkeypatch, tmp_path):
    cache_path = tmp_path / "answer_cache.json"
    backend.answer_cache.put_answer(
        "Вопрос", Answer(question="Вопрос", text="старый", found=True).model_dump(), path=cache_path
    )

    def fake_answer_question(question):
        return Answer(question=question, text="пересчитано", found=True)

    monkeypatch.setitem(sys.modules, "ariadna.search.answer", _fake_module("ariadna.search.answer", answer_question=fake_answer_question))

    answer, from_cache = backend.get_answer("Вопрос", force_recompute=True, cache_path=cache_path)
    assert from_cache is False
    assert answer.text == "пересчитано"


def test_get_subgraph_empty_node_ids_returns_none_without_import():
    assert backend.get_subgraph([]) is None


def test_get_subgraph_import_error_degrades_to_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "ariadna.graph.templates", None)
    assert backend.get_subgraph(["n1", "n2"]) is None


def test_get_subgraph_success_returns_dict_and_closes_driver(monkeypatch):
    closed = []

    class FakeDriver:
        def close(self):
            closed.append(True)

    fake_result = {"nodes": [{"id": "n1", "name": "X", "type": "Material", "is_tech_solution": False}], "edges": []}

    def fake_fetch_subgraph(driver, node_ids, *, max_nodes=60):
        assert isinstance(driver, FakeDriver)
        assert node_ids == ["n1"]
        return fake_result

    monkeypatch.setitem(
        sys.modules, "ariadna.graph.templates",
        _fake_module("ariadna.graph.templates", fetch_subgraph=fake_fetch_subgraph),
    )
    monkeypatch.setitem(
        sys.modules, "ariadna.graph.lexical_loader",
        _fake_module("ariadna.graph.lexical_loader", get_driver=lambda: FakeDriver()),
    )

    result = backend.get_subgraph(["n1"])
    assert result == fake_result
    assert closed == [True]


def test_get_subgraph_driver_connection_error_returns_none(monkeypatch):
    def broken_get_driver():
        raise RuntimeError("Neo4j недоступен")

    monkeypatch.setitem(
        sys.modules, "ariadna.graph.templates",
        _fake_module("ariadna.graph.templates", fetch_subgraph=lambda *a, **k: {}),
    )
    monkeypatch.setitem(
        sys.modules, "ariadna.graph.lexical_loader",
        _fake_module("ariadna.graph.lexical_loader", get_driver=broken_get_driver),
    )
    assert backend.get_subgraph(["n1"]) is None


def test_get_gap_report_import_error_degrades_to_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "ariadna.analytics.gap_map", None)
    assert backend.get_gap_report() is None


def test_get_gap_report_success_returns_report(monkeypatch):
    expected = GapReport(cells=[], only_ru=["тема"], only_foreign=[])
    monkeypatch.setitem(
        sys.modules, "ariadna.analytics.gap_map",
        _fake_module("ariadna.analytics.gap_map", build_gap_report=lambda **kwargs: expected),
    )
    assert backend.get_gap_report() == expected


def test_preset_questions_match_jury_count():
    assert len(backend.PRESET_QUESTIONS) == 4
    for label, question in backend.PRESET_QUESTIONS:
        assert label
        assert len(question) > 20
