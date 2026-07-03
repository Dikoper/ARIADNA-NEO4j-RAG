"""Тесты extraction/ollama_client.py (A-08): clean_env_var, HTTP-слой Ollama
native /api/chat (call_extraction_llm), снятие ```json-обёртки и валидация
сырого ответа (parse_raw_extraction), стабильность prompt_hash.

HTTP-слой мокается подменой `ollama_client._NO_PROXY_OPENER.open` (тот же приём,
что в tests/search/test_embeddings.py) — тесты офлайновые, живой Ollama не нужен.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from ariadna.extraction import ollama_client
from ariadna.extraction.ollama_client import (
    ExtractionSchemaError,
    OllamaExtractionError,
    call_extraction_llm,
    clean_env_var,
    parse_raw_extraction,
    prompt_hash,
)


# ══════════════════════ clean_env_var ══════════════════════

# Назначение: инлайн-комментарий после значения (` # ...`) не должен попасть
#   в результат — баг, из-за которого EXTRACTION_MODEL уходил в Ollama целиком
#   с комментарием (HTTP 400), см. worklog A-08.
# Уровень: ✅ реализовано (module-tester A-08)
def test_clean_env_var_strips_inline_comment(monkeypatch):
    monkeypatch.setenv("EXTRACTION_MODEL", "qwen3.5:35b-a3b          # комментарий про модель")
    assert clean_env_var("EXTRACTION_MODEL", "default") == "qwen3.5:35b-a3b"


# Назначение: значение без инлайн-комментария и хвостовых пробелов проходит
#   без изменений.
# Уровень: ✅ реализовано (module-tester A-08)
def test_clean_env_var_no_comment_passthrough(monkeypatch):
    monkeypatch.setenv("EXTRACTION_MODEL", "qwen3.5:9b")
    assert clean_env_var("EXTRACTION_MODEL", "default") == "qwen3.5:9b"


# Назначение: переменная не выставлена в окружении -> возвращается default как есть.
# Уровень: ✅ реализовано (module-tester A-08)
def test_clean_env_var_missing_returns_default(monkeypatch):
    monkeypatch.delenv("SOME_UNSET_EXTRACTION_VAR", raising=False)
    assert clean_env_var("SOME_UNSET_EXTRACTION_VAR", "fallback-value") == "fallback-value"


# Назначение: хвостовые пробелы без символа `#` тоже срезаются (rstrip).
# Уровень: ✅ реализовано (module-tester A-08)
def test_clean_env_var_trailing_whitespace_stripped(monkeypatch):
    monkeypatch.setenv("EXTRACTION_MODEL", "qwen3.5:35b-a3b   \n")
    assert clean_env_var("EXTRACTION_MODEL", "default") == "qwen3.5:35b-a3b"


# ══════════════════════ parse_raw_extraction / _strip_fences ══════════════════════

# Назначение: пустые списки entities/relations — валидный минимальный ответ.
# Уровень: ✅ реализовано (module-tester A-08)
def test_parse_raw_extraction_empty_lists():
    raw = parse_raw_extraction('{"entities": [], "relations": []}')
    assert raw.entities == []
    assert raw.relations == []


# Назначение: ответ обёрнут в ```json … ``` — обёртка снимается перед парсингом.
# Уровень: ✅ реализовано (module-tester A-08)
def test_parse_raw_extraction_strips_json_fence():
    content = '```json\n{"entities": [], "relations": []}\n```'
    raw = parse_raw_extraction(content)
    assert raw.entities == []
    assert raw.relations == []


# Назначение: обёртка без пометки языка (голый ```) тоже снимается.
# Уровень: ✅ реализовано (module-tester A-08)
def test_parse_raw_extraction_strips_plain_fence():
    content = '```\n{"entities": [], "relations": []}\n```'
    raw = parse_raw_extraction(content)
    assert raw.entities == []


# Назначение: сущности/связи со смешанными RU/EN именами и типами онтологии
#   парсятся штатно — схема не зависит от языка имени.
# Уровень: ✅ реализовано (module-tester A-08)
def test_parse_raw_extraction_mixed_ru_en_ok():
    content = json.dumps({
        "entities": [
            {"name": "электроэкстракция", "type": "Process", "synonyms": ["electrowinning"], "attrs": {}},
            {"name": "Copper", "type": "Material", "synonyms": [], "attrs": {}},
        ],
        "relations": [
            {"source": "электроэкстракция", "target": "Copper", "type": "uses_material", "confidence": 0.7},
        ],
    })
    raw = parse_raw_extraction(content)
    assert len(raw.entities) == 2
    assert raw.relations[0].source == "электроэкстракция"
    assert raw.relations[0].target == "Copper"


# Назначение: пустая строка (после снятия обёртки) -> невалидный JSON -> ExtractionSchemaError,
#   а не голое исключение json.JSONDecodeError наружу.
# Уровень: ✅ реализовано (module-tester A-08)
def test_parse_raw_extraction_empty_content_raises_schema_error():
    with pytest.raises(ExtractionSchemaError):
        parse_raw_extraction("")


# Назначение: синтаксически битый JSON -> ExtractionSchemaError.
# Уровень: ✅ реализовано (module-tester A-08)
def test_parse_raw_extraction_invalid_json_raises_schema_error():
    with pytest.raises(ExtractionSchemaError):
        parse_raw_extraction('{"entities": [это не json')


# Назначение: JSON валиден, но не проходит схему Entity/Relation (неизвестный
#   EntityType) -> ExtractionSchemaError, а не pydantic.ValidationError наружу.
# Уровень: ✅ реализовано (module-tester A-08)
def test_parse_raw_extraction_unknown_entity_type_raises_schema_error():
    content = json.dumps({
        "entities": [{"name": "X", "type": "НеизвестныйТип", "synonyms": [], "attrs": {}}],
        "relations": [],
    })
    with pytest.raises(ExtractionSchemaError):
        parse_raw_extraction(content)


# Назначение: confidence вне диапазона 0..1 нарушает контракт Relation ->
#   ExtractionSchemaError (проверка, что contracts-ограничения применяются и здесь).
# Уровень: ✅ реализовано (module-tester A-08)
def test_parse_raw_extraction_confidence_out_of_range_raises_schema_error():
    content = json.dumps({
        "entities": [{"name": "X", "type": "Material", "synonyms": [], "attrs": {}}],
        "relations": [{"source": "X", "target": "X", "type": "uses_material", "confidence": 1.7}],
    })
    with pytest.raises(ExtractionSchemaError):
        parse_raw_extraction(content)


# Назначение: JSON — не объект (например, список) -> схема Pydantic не проходит,
#   а не падает низкоуровневым TypeError.
# Уровень: ✅ реализовано (module-tester A-08)
def test_parse_raw_extraction_top_level_list_raises_schema_error():
    with pytest.raises(ExtractionSchemaError):
        parse_raw_extraction('[1, 2, 3]')


# ══════════════════════ call_extraction_llm (HTTP-слой, мок opener) ══════════════════════

class _FakeResp:
    """Мок ответа urllib — context manager с .read(), как в tests/search/test_embeddings.py."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


# Назначение: успешный ответ Ollama chat -> message.content возвращается как есть
#   (без парсинга JSON внутри content — это отдельный шаг parse_raw_extraction).
# Уровень: ✅ реализовано (module-tester A-08)
def test_call_extraction_llm_success_returns_content(monkeypatch):
    body = json.dumps({"message": {"content": '{"entities": [], "relations": []}'}}).encode("utf-8")
    monkeypatch.setattr(ollama_client._NO_PROXY_OPENER, "open", lambda *a, **k: _FakeResp(body))
    content = call_extraction_llm([{"role": "user", "content": "текст"}])
    assert content == '{"entities": [], "relations": []}'


# Назначение: запрос собирается с "think": false и "stream": false (обязательно
#   для reasoning-модели, см. паспорт extraction.md) — проверяем реальное тело запроса.
# Уровень: ✅ реализовано (module-tester A-08)
def test_call_extraction_llm_sends_think_false_and_model(monkeypatch):
    captured = {}

    def _fake_open(request, timeout=None):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["url"] = request.full_url
        body = json.dumps({"message": {"content": "ok"}}).encode("utf-8")
        return _FakeResp(body)

    monkeypatch.setattr(ollama_client._NO_PROXY_OPENER, "open", _fake_open)
    call_extraction_llm(
        [{"role": "system", "content": "sys"}],
        model="qwen3.5:9b",
        base_url="http://localhost:11434",
    )
    assert captured["payload"]["think"] is False
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["model"] == "qwen3.5:9b"
    assert captured["url"] == "http://localhost:11434/api/chat"


# Назначение: пустой content в теле ответа -> OllamaExtractionError (EXTRACT-004),
#   не молчаливый пустой результат.
# Уровень: ✅ реализовано (module-tester A-08)
def test_call_extraction_llm_empty_content_raises_ollama_error(monkeypatch):
    body = json.dumps({"message": {"content": ""}}).encode("utf-8")
    monkeypatch.setattr(ollama_client._NO_PROXY_OPENER, "open", lambda *a, **k: _FakeResp(body))
    with pytest.raises(OllamaExtractionError):
        call_extraction_llm([{"role": "user", "content": "текст"}])


# Назначение: message-ключ отсутствует целиком в ответе -> тоже OllamaExtractionError
#   (пустой content по .get()-цепочке), а не KeyError/AttributeError наружу.
# Уровень: ✅ реализовано (module-tester A-08)
def test_call_extraction_llm_missing_message_key_raises_ollama_error(monkeypatch):
    body = json.dumps({"done": True}).encode("utf-8")
    monkeypatch.setattr(ollama_client._NO_PROXY_OPENER, "open", lambda *a, **k: _FakeResp(body))
    with pytest.raises(OllamaExtractionError):
        call_extraction_llm([{"role": "user", "content": "текст"}])


# Назначение: битый транспортный JSON (не JSON вообще) -> OllamaExtractionError,
#   не голый json.JSONDecodeError.
# Уровень: ✅ реализовано (module-tester A-08)
def test_call_extraction_llm_invalid_transport_json_raises_ollama_error(monkeypatch):
    monkeypatch.setattr(ollama_client._NO_PROXY_OPENER, "open", lambda *a, **k: _FakeResp(b"not json"))
    with pytest.raises(OllamaExtractionError):
        call_extraction_llm([{"role": "user", "content": "текст"}])


# Назначение: сетевая ошибка (мёртвый хост, порт без слушателя) -> OllamaExtractionError
#   (EXTRACT-004), сообщение включает base_url/model для воспроизведения.
# Уровень: ✅ реализовано (module-tester A-08)
def test_call_extraction_llm_dead_host_raises_ollama_error():
    with pytest.raises(OllamaExtractionError) as exc_info:
        call_extraction_llm(
            [{"role": "user", "content": "текст"}],
            base_url="http://127.0.0.1:1",
            model="qwen3.5:35b-a3b",
        )
    assert "127.0.0.1:1" in str(exc_info.value)
    assert "qwen3.5:35b-a3b" in str(exc_info.value)


# Назначение: без явного model/base_url используются EXTRACTION_MODEL/OLLAMA_BASE_URL
#   из окружения (через clean_env_var) — проверяем, что env действительно читается.
# Уровень: ✅ реализовано (module-tester A-08)
def test_call_extraction_llm_uses_env_model_when_not_passed(monkeypatch):
    monkeypatch.setenv("EXTRACTION_MODEL", "custom-model-from-env  # коммент")
    captured = {}

    def _fake_open(request, timeout=None):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeResp(json.dumps({"message": {"content": "ok"}}).encode("utf-8"))

    monkeypatch.setattr(ollama_client._NO_PROXY_OPENER, "open", _fake_open)
    call_extraction_llm([{"role": "user", "content": "текст"}], base_url="http://localhost:11434")
    assert captured["payload"]["model"] == "custom-model-from-env"


# ══════════════════════ prompt_hash ══════════════════════

# Назначение: prompt_hash — стабильный 12-символьный hex, не зависящий от чанка
#   (детерминированная функция от SYSTEM_PROMPT).
# Уровень: ✅ реализовано (module-tester A-08)
def test_prompt_hash_stable_12_hex_chars():
    h1 = prompt_hash()
    h2 = prompt_hash()
    assert h1 == h2
    assert len(h1) == 12
    int(h1, 16)  # не бросает — валидный hex
