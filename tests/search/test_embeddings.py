"""Тесты search/embeddings.py (A-04): контракт выходного чанка, перезапускаемость
run_embedding_batch, изоляция сбоев Ollama (SEARCH-003), embed_texts (пустой вход,
живой смоук, обход системного HTTP_PROXY, ошибки сети/JSON/размерности).

Офлайновые тесты (контракт/перезапуск/изоляция) не требуют живой Ollama —
embed_texts подменяется детерминированной заглушкой или указывает на мёртвый порт.
Живые тесты (смоук, обход прокси) скипаются, если Ollama на localhost:11434 недоступна
(см. conftest.OLLAMA_LIVE).
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from ariadna.contracts import Chunk
from ariadna.search import embeddings

from conftest import FIXTURES_DIR, OLLAMA_LIVE

MINI_CHUNKS = FIXTURES_DIR / "chunks_mini.jsonl"
REAL_EMBEDDED_PATH = Path("data/processed/chunks_embedded.jsonl")


# Назначение: детерминированная заглушка embed_texts — разным текстам разные
#   векторы фиксированной размерности 4, без сети (для офлайновых тестов).
# Уровень: ✅ реализовано (module-tester A-04)
def _fake_embed_texts(texts, *, model=None, base_url=None):
    return [[float(len(t) % 13), float(sum(map(ord, t)) % 97), 1.0, 0.0] for t in texts]


# Назначение: читает JSONL построчно как сырые dict (без валидации через Chunk) —
#   для побайтового сравнения полей с выходом run_embedding_batch.
# Уровень: ✅ реализовано (module-tester A-04)
def _read_raw_lines(path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ══════════════════════ Контракт выходного файла ══════════════════════

# Назначение: все строки chunks_embedded парсятся как Chunk, embedding непустой
#   и единой размерности; прочие поля исходного чанка сохранены байт-в-байт.
# Уровень: ✅ реализовано (module-tester A-04)
def test_run_embedding_batch_output_matches_chunk_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(embeddings, "embed_texts", _fake_embed_texts)
    output_path = tmp_path / "chunks_embedded.jsonl"
    skipped_path = tmp_path / "embed_skipped.jsonl"

    stats = embeddings.run_embedding_batch(
        chunks_path=MINI_CHUNKS, output_path=output_path, skipped_path=skipped_path,
        run_id="test_a04_contract",
    )

    assert stats["n_total"] == 3
    assert stats["n_embedded_now"] == 3
    assert stats["n_skipped"] == 0
    assert not skipped_path.exists() or skipped_path.read_text() == ""

    originals = {c["chunk_id"]: c for c in _read_raw_lines(MINI_CHUNKS)}
    out_lines = _read_raw_lines(output_path)
    assert len(out_lines) == 3

    dims = set()
    for raw in out_lines:
        chunk = Chunk.model_validate(raw)  # валидна как Chunk
        assert chunk.embedding is not None and len(chunk.embedding) > 0
        dims.add(len(chunk.embedding))

        original = originals[chunk.chunk_id]
        for field in ("chunk_id", "doc_id", "text", "start", "end", "lang"):
            assert raw[field] == original[field], f"поле {field} изменилось для {chunk.chunk_id}"

    assert len(dims) == 1, f"размерность не едина: {dims}"


# ══════════════════════ Перезапускаемость ══════════════════════

# Назначение: частично заполненный выходной файл -> дозаписываются только
#   недостающие chunk_id; embed_texts не вызывается для уже готовых.
# Уровень: ✅ реализовано (module-tester A-04)
def test_run_embedding_batch_resumes_only_missing(tmp_path, monkeypatch):
    calls: list[str] = []

    def spy_embed_texts(texts, *, model=None, base_url=None):
        calls.extend(texts)
        return _fake_embed_texts(texts)

    monkeypatch.setattr(embeddings, "embed_texts", spy_embed_texts)

    output_path = tmp_path / "chunks_embedded.jsonl"
    skipped_path = tmp_path / "embed_skipped.jsonl"

    all_chunks = list(embeddings._iter_chunks(MINI_CHUNKS))
    already_done = all_chunks[0]
    already_done.embedding = [9.0, 9.0, 9.0, 9.0]  # маркер: не должен быть пересчитан
    output_path.write_text(already_done.model_dump_json() + "\n", encoding="utf-8")

    stats = embeddings.run_embedding_batch(
        chunks_path=MINI_CHUNKS, output_path=output_path, skipped_path=skipped_path,
        run_id="test_a04_resume",
    )

    assert stats["n_already_done"] == 1
    assert stats["n_embedded_now"] == 2
    assert already_done.text not in calls, "уже готовый чанк не должен переотправляться в embed_texts"

    out_lines = _read_raw_lines(output_path)
    assert len(out_lines) == 3
    resumed = next(c for c in out_lines if c["chunk_id"] == already_done.chunk_id)
    assert resumed["embedding"] == [9.0, 9.0, 9.0, 9.0], "готовая строка не должна перезаписываться"


# Назначение: выходной файл уже содержит все чанки -> no-op (embed_texts не
#   вызывается вовсе, файл не меняется).
# Уровень: ✅ реализовано (module-tester A-04)
def test_run_embedding_batch_full_output_is_noop(tmp_path, monkeypatch):
    def boom_embed_texts(texts, *, model=None, base_url=None):
        raise AssertionError("embed_texts не должен вызываться, когда всё уже готово")

    monkeypatch.setattr(embeddings, "embed_texts", boom_embed_texts)

    output_path = tmp_path / "chunks_embedded.jsonl"
    skipped_path = tmp_path / "embed_skipped.jsonl"

    all_chunks = list(embeddings._iter_chunks(MINI_CHUNKS))
    with open(output_path, "w", encoding="utf-8") as f:
        for c in all_chunks:
            c.embedding = [1.0, 2.0, 3.0, 4.0]
            f.write(c.model_dump_json() + "\n")
    before = output_path.read_text(encoding="utf-8")

    stats = embeddings.run_embedding_batch(
        chunks_path=MINI_CHUNKS, output_path=output_path, skipped_path=skipped_path,
        run_id="test_a04_noop",
    )

    assert stats["n_embedded_now"] == 0
    assert stats["n_already_done"] == 3
    assert output_path.read_text(encoding="utf-8") == before


# Назначение: повторный прогон от пустого файла до полного не дублирует строки
#   (второй запуск — no-op поверх результата первого).
# Уровень: ✅ реализовано (module-tester A-04)
def test_run_embedding_batch_rerun_no_duplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(embeddings, "embed_texts", _fake_embed_texts)
    output_path = tmp_path / "chunks_embedded.jsonl"
    skipped_path = tmp_path / "embed_skipped.jsonl"

    embeddings.run_embedding_batch(
        chunks_path=MINI_CHUNKS, output_path=output_path, skipped_path=skipped_path,
        run_id="test_a04_rerun1",
    )
    stats2 = embeddings.run_embedding_batch(
        chunks_path=MINI_CHUNKS, output_path=output_path, skipped_path=skipped_path,
        run_id="test_a04_rerun2",
    )

    assert stats2["n_embedded_now"] == 0
    out_lines = _read_raw_lines(output_path)
    assert len(out_lines) == 3
    chunk_ids = [c["chunk_id"] for c in out_lines]
    assert len(chunk_ids) == len(set(chunk_ids)), "повторный прогон продублировал строки"


# ══════════════════════ Изоляция сбоев (мёртвая Ollama) ══════════════════════

# Назначение: недоступный хост Ollama (мёртвый порт, соединение отклонено) ->
#   после ретраев все чанки изолируются в embed_skipped.jsonl с SEARCH-003;
#   выходной файл не создаётся/не портится, процесс не падает.
# Уровень: ✅ реализовано (module-tester A-04)
def test_run_embedding_batch_dead_host_isolates_to_skipped(tmp_path, monkeypatch):
    # порт без слушателя на loopback -> ConnectionRefused сразу, без таймаута 120с
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:1")

    output_path = tmp_path / "chunks_embedded.jsonl"
    skipped_path = tmp_path / "embed_skipped.jsonl"

    stats = embeddings.run_embedding_batch(
        chunks_path=MINI_CHUNKS, output_path=output_path, skipped_path=skipped_path,
        run_id="test_a04_deadhost",
    )

    assert stats["n_embedded_now"] == 0
    assert stats["n_skipped"] == 3
    assert not output_path.exists() or _read_raw_lines(output_path) == []

    skipped = _read_raw_lines(skipped_path)
    assert len(skipped) == 3
    original_ids = {c["chunk_id"] for c in _read_raw_lines(MINI_CHUNKS)}
    for entry in skipped:
        assert entry["chunk_id"] in original_ids
        assert entry["code"] == "SEARCH-003"
        assert entry["reason"]  # не пусто — контекст для воспроизведения

    # SEARCH-003 действительно попадает в лог прогона (не только в skip-файл)
    from ariadna.logutil import LOG_DIR
    log_file = LOG_DIR / "test_a04_deadhost.jsonl"
    assert log_file.exists()
    log_text = log_file.read_text(encoding="utf-8")
    assert "SEARCH-003" in log_text


# ══════════════════════ embed_texts ══════════════════════

# Назначение: пустой список текстов -> пустой список векторов, без HTTP-запроса.
# Уровень: ✅ реализовано (module-tester A-04)
def test_embed_texts_empty_list_returns_empty_list():
    assert embeddings.embed_texts([]) == []


# Назначение: мёртвый хост -> EmbeddingAPIError (не голое исключение urllib).
# Уровень: ✅ реализовано (module-tester A-04)
def test_embed_texts_dead_host_raises_embedding_api_error():
    with pytest.raises(embeddings.EmbeddingAPIError):
        embeddings.embed_texts(["текст"], base_url="http://127.0.0.1:1")


# Назначение: битый JSON в ответе -> EmbeddingAPIError, а не JSONDecodeError наружу.
# Уровень: ✅ реализовано (module-tester A-04)
def test_embed_texts_invalid_json_raises_embedding_api_error(monkeypatch):
    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return "не json".encode("utf-8")

    monkeypatch.setattr(embeddings._NO_PROXY_OPENER, "open", lambda *a, **k: _FakeResp())
    with pytest.raises(embeddings.EmbeddingAPIError):
        embeddings.embed_texts(["текст"])


# Назначение: число векторов в ответе не совпадает с числом текстов ->
#   EmbeddingAPIError (защита от молчаливого рассинхрона порядка).
# Уровень: ✅ реализовано (module-tester A-04)
def test_embed_texts_dimension_count_mismatch_raises(monkeypatch):
    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"embeddings": [[0.1, 0.2]]}).encode("utf-8")  # 1 вектор на 2 текста

    monkeypatch.setattr(embeddings._NO_PROXY_OPENER, "open", lambda *a, **k: _FakeResp())
    with pytest.raises(embeddings.EmbeddingAPIError):
        embeddings.embed_texts(["текст1", "текст2"])


# Назначение: живой смоук — разные тексты дают разные векторы размерности 1024
#   (модель qwen3-embedding:0.6b). Скип, если Ollama недоступна.
# Уровень: ✅ реализовано (module-tester A-04)
@pytest.mark.skipif(not OLLAMA_LIVE, reason="Ollama недоступна на localhost:11434")
def test_embed_texts_live_smoke_distinct_vectors():
    texts = [
        "Экстракция меди сульфатным раствором.",
        "Электролиз цинка при низком pH.",
        "Флотация никелевой руды.",
    ]
    vectors = embeddings.embed_texts(texts)
    assert len(vectors) == 3
    for vec in vectors:
        assert len(vec) == 1024
    assert vectors[0] != vectors[1] != vectors[2]
    assert vectors[0] != vectors[2]


# Назначение: клиент не должен использовать HTTP_PROXY/HTTPS_PROXY из окружения —
#   даже если проставлен заведомо мёртвый прокси-адрес, живой запрос к Ollama
#   на localhost проходит напрямую. Скип, если Ollama недоступна.
# Уровень: ✅ реализовано (module-tester A-04)
@pytest.mark.skipif(not OLLAMA_LIVE, reason="Ollama недоступна на localhost:11434")
def test_embed_texts_ignores_http_proxy_env(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:1")

    vectors = embeddings.embed_texts(["проверка обхода прокси"])
    assert len(vectors) == 1
    assert len(vectors[0]) == 1024


# ══════════════════════ Смоук по реальному chunks_embedded.jsonl ══════════════════════

# Назначение: выборочная (не полная — файл 139 МБ/9580 строк) проверка боевого
#   data/processed/chunks_embedded.jsonl: первая/последняя/~100 случайных строк
#   валидны как Chunk с непустым embedding единой размерности. Только чтение.
#   Скип, если файла нет (data/ вне git).
# Уровень: ✅ реализовано (module-tester A-04)
@pytest.mark.skipif(not REAL_EMBEDDED_PATH.exists(), reason="data/processed/chunks_embedded.jsonl отсутствует (data/ вне git)")
def test_real_chunks_embedded_sample_matches_contract():
    with open(REAL_EMBEDDED_PATH, encoding="utf-8") as f:
        n_total = sum(1 for _ in f)
    assert n_total > 0

    random.seed(42)  # воспроизводимая выборка
    sample_size = min(100, n_total)
    targets = {0, n_total - 1} | set(random.sample(range(n_total), sample_size))

    sampled_lines: dict[int, str] = {}
    with open(REAL_EMBEDDED_PATH, encoding="utf-8") as f:
        for idx, line in enumerate(f):
            if idx in targets:
                sampled_lines[idx] = line
            if len(sampled_lines) == len(targets):
                break

    assert len(sampled_lines) == len(targets)

    dims = set()
    seen_chunk_ids = set()
    for line in sampled_lines.values():
        raw = json.loads(line)
        chunk = Chunk.model_validate(raw)
        assert chunk.embedding is not None and len(chunk.embedding) > 0
        assert chunk.chunk_id.startswith(chunk.doc_id + "#")
        assert chunk.chunk_id not in seen_chunk_ids, "дубликат chunk_id в выборке"
        seen_chunk_ids.add(chunk.chunk_id)
        dims.add(len(chunk.embedding))

    assert dims == {1024}, f"размерность выборки не 1024: {dims}"
