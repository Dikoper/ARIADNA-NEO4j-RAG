"""Тесts кэша ответов UI (A-13): нормализация ключа, load/save, get/put,
атомарность записи, деградация при битом файле."""
from __future__ import annotations

import json

from ui.answer_cache import get_cached_answer, load_cache, normalize_question, put_answer, save_cache


def test_normalize_question_collapses_whitespace_and_case():
    a = normalize_question("  Какие Методы   обессоливания?  ")
    b = normalize_question("какие методы обессоливания")
    assert a == b == "какие методы обессоливания"


def test_normalize_question_strips_trailing_punctuation():
    assert normalize_question("Вопрос???") == "вопрос"
    assert normalize_question("Вопрос.") == "вопрос"


def test_load_cache_missing_file_returns_empty(tmp_path):
    path = tmp_path / "answer_cache.json"
    assert load_cache(path) == {}


def test_load_cache_corrupted_file_returns_empty(tmp_path):
    path = tmp_path / "answer_cache.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert load_cache(path) == {}


def test_load_cache_non_dict_json_returns_empty(tmp_path):
    path = tmp_path / "answer_cache.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_cache(path) == {}


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "sub" / "answer_cache.json"
    cache = {"вопрос": {"answer": {"question": "вопрос", "text": "ответ"}, "cached_at": "2026-07-04T00:00:00"}}
    save_cache(cache, path)
    assert path.exists()
    assert load_cache(path) == cache


def test_save_cache_no_leftover_tmp_file(tmp_path):
    path = tmp_path / "answer_cache.json"
    save_cache({"a": 1}, path)
    assert not path.with_suffix(".json.tmp").exists()
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


def test_put_answer_then_get_cached_answer(tmp_path):
    path = tmp_path / "answer_cache.json"
    answer_dict = {"question": "Как обессолить воду?", "text": "текст ответа", "citations": [], "found": True}
    cache = put_answer("Как обессолить воду?", answer_dict, path=path)

    hit = get_cached_answer(cache, "  как ОБЕССОЛИТЬ воду  ")
    assert hit is not None
    assert hit["answer"] == answer_dict
    assert "cached_at" in hit

    # Персистентность: повторная загрузка с диска видит ту же запись.
    reloaded = load_cache(path)
    assert get_cached_answer(reloaded, "Как обессолить воду?") is not None


def test_get_cached_answer_miss_returns_none():
    assert get_cached_answer({}, "неизвестный вопрос") is None


def test_put_answer_preserves_other_existing_entries(tmp_path):
    path = tmp_path / "answer_cache.json"
    put_answer("Вопрос А", {"question": "Вопрос А", "text": "1"}, path=path)
    cache = put_answer("Вопрос Б", {"question": "Вопрос Б", "text": "2"}, path=path)
    assert get_cached_answer(cache, "Вопрос А") is not None
    assert get_cached_answer(cache, "Вопрос Б") is not None
