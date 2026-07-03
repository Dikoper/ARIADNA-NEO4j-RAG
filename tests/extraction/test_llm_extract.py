"""Тесты extraction/llm_extract.py (A-08): extract_chunk (контракт результата,
ретрай на схему, skip после 2 провалов, EXTRACT-001/EXTRACT-004, фильтр связей
через postprocess, числа только правилами) и run_extraction_batch (skip-лист,
перезапускаемость по chunk_id, изоляция сбоя одного чанка, --targets/--limit).

LLM-слой мокается подменой `llm_extract.call_extraction_llm` (имя, импортированное
в модуль llm_extract) — офлайновые тесты, живой Ollama не требуется.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ariadna.contracts import Chunk, ExtractionResult
from ariadna.extraction import llm_extract
from ariadna.extraction.ollama_client import (
    OllamaExtractionError,
    PROMPT_HASH,
)
from ariadna.extraction.rules import extract_constraints

FIXTURES_DIR = Path(__file__).parent / "fixtures"
MINI_CHUNKS = FIXTURES_DIR / "chunks_mini.jsonl"

VALID_JSON = '{"entities": [], "relations": []}'


# Назначение: строит Chunk-заглушку без чтения фикстуры (для точечных тестов extract_chunk).
# Уровень: ✅ реализовано (module-tester A-08)
def _make_chunk(text="Электролиз меди при 60 °C.", chunk_id="doc1#0", doc_id="doc1") -> Chunk:
    return Chunk(chunk_id=chunk_id, doc_id=doc_id, text=text, start=0, end=len(text), lang="ru")


# ══════════════════════ extract_chunk: контракт результата ══════════════════════

# Назначение: doc_id/chunk_id/model/prompt_hash проставляет код из объекта Chunk
#   и параметров вызова — не LLM (LLM отвечает только {entities, relations}).
# Уровень: ✅ реализовано (module-tester A-08)
def test_extract_chunk_sets_provenance_fields_from_code_not_llm(monkeypatch):
    monkeypatch.setattr(llm_extract, "call_extraction_llm", lambda messages, **kw: VALID_JSON)
    chunk = _make_chunk(chunk_id="docX#7", doc_id="docX")
    result, code, reason = llm_extract.extract_chunk(chunk, model="qwen3.5:9b")
    assert code is None and reason is None
    assert isinstance(result, ExtractionResult)
    assert result.doc_id == "docX"
    assert result.chunk_id == "docX#7"
    assert result.model == "qwen3.5:9b"
    assert result.prompt_hash == PROMPT_HASH


# Назначение: constraints в ExtractionResult заполняются rules.extract_constraints(chunk.text),
#   а не LLM — даже если LLM (в моке) вернула бы числа, они не попадут в constraints
#   (LLM в этом тесте вообще не видит числового текста — только rules его извлекает).
# Уровень: ✅ реализовано (module-tester A-08)
def test_extract_chunk_constraints_come_from_rules_not_llm(monkeypatch):
    monkeypatch.setattr(llm_extract, "call_extraction_llm", lambda messages, **kw: VALID_JSON)
    text = "Температура не более 60 °C, содержание сульфатов ≤300 мг/л."
    chunk = _make_chunk(text=text)
    result, _, _ = llm_extract.extract_chunk(chunk)
    expected = extract_constraints(text)
    assert len(result.constraints) == len(expected) == 2
    assert [c.model_dump() for c in result.constraints] == [c.model_dump() for c in expected]


# Назначение: LLM-ответ, содержащий "лишние" поля (doc_id/chunk_id/model), не
#   переопределяет значения, проставленные кодом — _RawExtraction схема их игнорирует.
# Уровень: ✅ реализовано (module-tester A-08)
def test_extract_chunk_llm_supplied_ids_are_ignored(monkeypatch):
    content = json.dumps({
        "entities": [], "relations": [],
        "doc_id": "WRONG_DOC", "chunk_id": "WRONG_CHUNK", "model": "WRONG_MODEL",
    })
    monkeypatch.setattr(llm_extract, "call_extraction_llm", lambda messages, **kw: content)
    chunk = _make_chunk(chunk_id="real#0", doc_id="real_doc")
    result, _, _ = llm_extract.extract_chunk(chunk, model="qwen3.5:35b-a3b")
    assert result.doc_id == "real_doc"
    assert result.chunk_id == "real#0"
    assert result.model == "qwen3.5:35b-a3b"


# Назначение: пустые entities/relations — валидный результат, не ошибка.
# Уровень: ✅ реализовано (module-tester A-08)
def test_extract_chunk_empty_entities_relations_is_valid(monkeypatch):
    monkeypatch.setattr(llm_extract, "call_extraction_llm", lambda messages, **kw: VALID_JSON)
    chunk = _make_chunk(text="Текст без сущностей онтологии.")
    result, code, reason = llm_extract.extract_chunk(chunk)
    assert code is None
    assert result.entities == []
    assert result.relations == []


# Назначение: ответ в ```json … ``` обёртке распознаётся так же, как голый JSON.
# Уровень: ✅ реализовано (module-tester A-08)
def test_extract_chunk_handles_json_fenced_response(monkeypatch):
    fenced = f"```json\n{VALID_JSON}\n```"
    monkeypatch.setattr(llm_extract, "call_extraction_llm", lambda messages, **kw: fenced)
    chunk = _make_chunk()
    result, code, _ = llm_extract.extract_chunk(chunk)
    assert code is None
    assert result is not None


# Назначение: смешанный RU/EN ответ (сущности на разных языках) обрабатывается
#   штатно, включая канонизацию EN-синонима к RU-каноническому имени.
# Уровень: ✅ реализовано (module-tester A-08)
def test_extract_chunk_mixed_ru_en_entities_canonicalized(monkeypatch):
    content = json.dumps({
        "entities": [
            {"name": "electrowinning", "type": "Process", "synonyms": [], "attrs": {}},
            {"name": "Copper", "type": "Material", "synonyms": [], "attrs": {}},
        ],
        "relations": [
            {"source": "electrowinning", "target": "Copper", "type": "uses_material", "confidence": 0.8},
        ],
    })
    monkeypatch.setattr(llm_extract, "call_extraction_llm", lambda messages, **kw: content)
    chunk = _make_chunk()
    result, code, _ = llm_extract.extract_chunk(chunk)
    assert code is None
    names = {e.name for e in result.entities}
    assert "электроэкстракция" in names  # electrowinning -> канонизировано (ontology/synonyms.yaml)
    assert "медь" in names                # Copper -> тоже канонизировано (тот же словарь)
    assert result.relations[0].source == "электроэкстракция"
    assert result.relations[0].target == "медь"


# Назначение: связь на сущность вне списка entities чанка отбрасывается
#   (postprocess.filter_relations), не попадает в ExtractionResult.relations.
# Уровень: ✅ реализовано (module-tester A-08)
def test_extract_chunk_drops_relation_to_unknown_entity(monkeypatch):
    content = json.dumps({
        "entities": [{"name": "Электролиз", "type": "Process", "synonyms": [], "attrs": {}}],
        "relations": [
            {"source": "Электролиз", "target": "Призрачная сущность", "type": "uses_material", "confidence": 0.5},
        ],
    })
    monkeypatch.setattr(llm_extract, "call_extraction_llm", lambda messages, **kw: content)
    chunk = _make_chunk()
    result, code, _ = llm_extract.extract_chunk(chunk)
    assert code is None
    assert result.relations == []
    assert len(result.entities) == 1


# ══════════════════════ extract_chunk: ретрай / skip ══════════════════════

# Назначение: первая попытка — битый JSON, вторая (ретрай с уточнением) —
#   валидный ответ: extract_chunk возвращает результат без ошибки за 2 попытки.
# Уровень: ✅ реализовано (module-tester A-08)
def test_extract_chunk_retries_once_on_schema_error_then_succeeds(monkeypatch):
    calls = []

    def fake_call(messages, **kw):
        calls.append(messages)
        if len(calls) == 1:
            return "это не json вообще"
        return VALID_JSON

    monkeypatch.setattr(llm_extract, "call_extraction_llm", fake_call)
    chunk = _make_chunk()
    result, code, reason = llm_extract.extract_chunk(chunk)
    assert code is None
    assert result is not None
    assert len(calls) == 2
    # второй вызов — ретрай-сообщения (длиннее исходных: + assistant + уточнение user)
    assert len(calls[1]) == len(calls[0]) + 2
    assert calls[1][-2]["role"] == "assistant"
    assert calls[1][-1]["role"] == "user"


# Назначение: обе попытки дают невалидный JSON -> extract_chunk возвращает
#   (None, "EXTRACT-001", причина) — чанк уходит в skip-лист вызывающим кодом.
# Уровень: ✅ реализовано (module-tester A-08)
def test_extract_chunk_both_attempts_invalid_returns_extract_001(monkeypatch):
    monkeypatch.setattr(llm_extract, "call_extraction_llm", lambda messages, **kw: "мусор не json")
    chunk = _make_chunk()
    result, code, reason = llm_extract.extract_chunk(chunk)
    assert result is None
    assert code == "EXTRACT-001"
    assert reason  # непустая причина для лога/воспроизведения


# Назначение: сетевая ошибка (OllamaExtractionError) на обеих попытках ->
#   (None, "EXTRACT-004", причина); сетевой сбой повторяется тем же промптом
#   (не ретрай-подсказкой, т.к. проблема не в содержимом ответа).
# Уровень: ✅ реализовано (module-tester A-08)
def test_extract_chunk_network_error_both_attempts_returns_extract_004(monkeypatch):
    def fake_call(messages, **kw):
        raise OllamaExtractionError("Ollama chat недоступна (мок)")

    monkeypatch.setattr(llm_extract, "call_extraction_llm", fake_call)
    chunk = _make_chunk()
    result, code, reason = llm_extract.extract_chunk(chunk)
    assert result is None
    assert code == "EXTRACT-004"
    assert "недоступна" in reason


# Назначение: после сетевого сбоя повтор идёт с исходными messages (не с
#   ретрай-уточнением) — сообщения на второй попытке идентичны первой.
# Уровень: ✅ реализовано (module-tester A-08)
def test_extract_chunk_network_error_retries_with_same_messages(monkeypatch):
    calls = []

    def fake_call(messages, **kw):
        calls.append(messages)
        raise OllamaExtractionError("сеть недоступна")

    monkeypatch.setattr(llm_extract, "call_extraction_llm", fake_call)
    chunk = _make_chunk()
    llm_extract.extract_chunk(chunk)
    assert len(calls) == 2
    assert calls[0] == calls[1]


# Назначение: смешанный случай — первая попытка сетевой сбой, вторая успешна ->
#   extract_chunk восстанавливается за 2 попытки (RETRY_ATTEMPTS = 2 покрывает оба типа сбоя).
# Уровень: ✅ реализовано (module-tester A-08)
def test_extract_chunk_network_error_then_success_recovers(monkeypatch):
    calls = []

    def fake_call(messages, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise OllamaExtractionError("временный сбой сети")
        return VALID_JSON

    monkeypatch.setattr(llm_extract, "call_extraction_llm", fake_call)
    chunk = _make_chunk()
    result, code, reason = llm_extract.extract_chunk(chunk)
    assert code is None
    assert result is not None


# Назначение: пустой ответ LLM (content == "") приводит к OllamaExtractionError
#   уже на уровне call_extraction_llm (проверено в test_ollama_client.py) —
#   здесь проверяем, что extract_chunk корректно доводит такой сбой до EXTRACT-004.
# Уровень: ✅ реализовано (module-tester A-08)
def test_extract_chunk_empty_llm_response_surfaces_as_extract_004(monkeypatch):
    def fake_call(messages, **kw):
        raise OllamaExtractionError("пустой content в ответе Ollama chat")

    monkeypatch.setattr(llm_extract, "call_extraction_llm", fake_call)
    chunk = _make_chunk()
    result, code, reason = llm_extract.extract_chunk(chunk)
    assert result is None
    assert code == "EXTRACT-004"


# ══════════════════════ run_extraction_batch ══════════════════════

# Назначение: успешный прогон по фикстуре — extracted.jsonl содержит по строке
#   на чанк, все результаты — валидные ExtractionResult, skiplist пуст.
# Уровень: ✅ реализовано (module-tester A-08)
def test_run_extraction_batch_writes_valid_results(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_extract, "call_extraction_llm", lambda messages, **kw: VALID_JSON)
    output_path = tmp_path / "extracted.jsonl"
    skiplist_path = tmp_path / "skip.jsonl"

    stats = llm_extract.run_extraction_batch(
        chunks_path=MINI_CHUNKS, output_path=output_path, skiplist_path=skiplist_path,
        run_id="test_a08_batch_ok",
    )

    assert stats["n_total_selected"] == 3
    assert stats["n_done_now"] == 3
    assert stats["n_skipped"] == 0
    assert not skiplist_path.exists() or skiplist_path.read_text() == ""

    lines = [json.loads(l) for l in output_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 3
    for line in lines:
        ExtractionResult.model_validate(line)


# Назначение: перезапуск не пересчитывает уже готовые chunk_id — уже
#   присутствующий в output_path chunk_id пропускается (n_already_done учитывает его,
#   call_extraction_llm для него не вызывается).
# Уровень: ✅ реализовано (module-tester A-08)
def test_run_extraction_batch_resumes_only_missing(tmp_path, monkeypatch):
    calls = []

    def fake_call(messages, **kw):
        calls.append(1)
        return VALID_JSON

    monkeypatch.setattr(llm_extract, "call_extraction_llm", fake_call)

    output_path = tmp_path / "extracted.jsonl"
    skiplist_path = tmp_path / "skip.jsonl"

    all_chunks = list(llm_extract._iter_chunks(MINI_CHUNKS))
    already_done = ExtractionResult(doc_id=all_chunks[0].doc_id, chunk_id=all_chunks[0].chunk_id)
    output_path.write_text(already_done.model_dump_json() + "\n", encoding="utf-8")

    stats = llm_extract.run_extraction_batch(
        chunks_path=MINI_CHUNKS, output_path=output_path, skiplist_path=skiplist_path,
        run_id="test_a08_resume",
    )

    assert stats["n_already_done"] == 1
    assert stats["n_done_now"] == 2
    assert len(calls) == 2  # LLM вызван только для двух оставшихся чанков


# Назначение: повторный запуск на полностью готовом выходе — no-op, LLM не вызывается,
#   файл не растёт дублями.
# Уровень: ✅ реализовано (module-tester A-08)
def test_run_extraction_batch_full_output_is_noop(tmp_path, monkeypatch):
    def boom(messages, **kw):
        raise AssertionError("call_extraction_llm не должен вызываться, когда всё уже готово")

    monkeypatch.setattr(llm_extract, "call_extraction_llm", boom)

    output_path = tmp_path / "extracted.jsonl"
    skiplist_path = tmp_path / "skip.jsonl"

    all_chunks = list(llm_extract._iter_chunks(MINI_CHUNKS))
    with open(output_path, "w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(ExtractionResult(doc_id=c.doc_id, chunk_id=c.chunk_id).model_dump_json() + "\n")

    stats = llm_extract.run_extraction_batch(
        chunks_path=MINI_CHUNKS, output_path=output_path, skiplist_path=skiplist_path,
        run_id="test_a08_full_noop",
    )
    assert stats["n_done_now"] == 0
    assert stats["n_already_done"] == 3


# Назначение: повторный прогон не плодит дубли строк в extracted.jsonl.
# Уровень: ✅ реализовано (module-tester A-08)
def test_run_extraction_batch_rerun_no_duplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_extract, "call_extraction_llm", lambda messages, **kw: VALID_JSON)
    output_path = tmp_path / "extracted.jsonl"
    skiplist_path = tmp_path / "skip.jsonl"

    llm_extract.run_extraction_batch(
        chunks_path=MINI_CHUNKS, output_path=output_path, skiplist_path=skiplist_path,
        run_id="test_a08_rerun1",
    )
    stats2 = llm_extract.run_extraction_batch(
        chunks_path=MINI_CHUNKS, output_path=output_path, skiplist_path=skiplist_path,
        run_id="test_a08_rerun2",
    )
    assert stats2["n_done_now"] == 0
    lines = [l for l in output_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 3


# Назначение: падение одного чанка (постоянный сетевой сбой) не останавливает
#   прогон — остальные чанки обрабатываются, сбойный уходит в skiplist с EXTRACT-004.
# Уровень: ✅ реализовано (module-tester A-08)
def test_run_extraction_batch_isolates_single_chunk_failure(tmp_path, monkeypatch):
    def fake_call(messages, **kw):
        # определяем "плохой" чанк по содержимому user-сообщения (тест из фикстуры)
        user_text = messages[1]["content"]
        if "electrowinning" in user_text:
            raise OllamaExtractionError("постоянный сбой сети (мок)")
        return VALID_JSON

    monkeypatch.setattr(llm_extract, "call_extraction_llm", fake_call)

    output_path = tmp_path / "extracted.jsonl"
    skiplist_path = tmp_path / "skip.jsonl"

    stats = llm_extract.run_extraction_batch(
        chunks_path=MINI_CHUNKS, output_path=output_path, skiplist_path=skiplist_path,
        run_id="test_a08_isolate",
    )

    assert stats["n_done_now"] == 2
    assert stats["n_skipped"] == 1

    skipped = [json.loads(l) for l in skiplist_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(skipped) == 1
    assert skipped[0]["code"] == "EXTRACT-004"
    assert skipped[0]["chunk_id"] == "test_a08_doc2#0"

    done = [json.loads(l) for l in output_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(done) == 2
    assert all(d["chunk_id"] != "test_a08_doc2#0" for d in done)


# Назначение: --targets фильтрует чанки по doc_id из targets.jsonl — чанки
#   документов вне списка не обрабатываются и не попадают в extracted.jsonl.
# Уровень: ✅ реализовано (module-tester A-08)
def test_run_extraction_batch_targets_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_extract, "call_extraction_llm", lambda messages, **kw: VALID_JSON)
    targets_path = tmp_path / "targets.jsonl"
    targets_path.write_text(json.dumps({"doc_id": "test_a08_doc1"}) + "\n", encoding="utf-8")

    output_path = tmp_path / "extracted.jsonl"
    skiplist_path = tmp_path / "skip.jsonl"

    stats = llm_extract.run_extraction_batch(
        chunks_path=MINI_CHUNKS, output_path=output_path, skiplist_path=skiplist_path,
        targets_path=targets_path, run_id="test_a08_targets",
    )

    assert stats["n_total_selected"] == 2  # только 2 чанка doc1
    done = [json.loads(l) for l in output_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert all(d["doc_id"] == "test_a08_doc1" for d in done)


# Назначение: --limit ограничивает число чанков, обрабатываемых за один запуск
#   (для контроля объёма/времени прогона).
# Уровень: ✅ реализовано (module-tester A-08)
def test_run_extraction_batch_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_extract, "call_extraction_llm", lambda messages, **kw: VALID_JSON)
    output_path = tmp_path / "extracted.jsonl"
    skiplist_path = tmp_path / "skip.jsonl"

    stats = llm_extract.run_extraction_batch(
        chunks_path=MINI_CHUNKS, output_path=output_path, skiplist_path=skiplist_path,
        limit=1, run_id="test_a08_limit",
    )
    assert stats["n_done_now"] == 1


# ══════════════════════ Живой смоук (skip, если Ollama недоступна) ══════════════════════

def _check_ollama_alive() -> bool:
    import urllib.error
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://localhost:11434/api/tags", timeout=3):
            return True
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return False


# Назначение: живой смоук на реальной Ollama — один короткий чанк даёт валидный
#   ExtractionResult (по образцу tests/search — скип, если стенд недоступен).
# Уровень: ✅ реализовано (module-tester A-08)
@pytest.mark.skipif(not _check_ollama_alive(), reason="Ollama недоступна на localhost:11434")
def test_extract_chunk_live_smoke():
    chunk = _make_chunk(text="Электроэкстракция цинка применяется на промышленных заводах.")
    result, code, reason = llm_extract.extract_chunk(chunk)
    assert code is None, f"живой смоук не прошёл: {code} {reason}"
    assert isinstance(result, ExtractionResult)
    assert result.doc_id == chunk.doc_id
    assert result.chunk_id == chunk.chunk_id
