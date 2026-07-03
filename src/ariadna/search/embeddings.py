"""Эмбеддинги чанков — офлайн-индексация и онлайн-переиспользование (A-04).

Вход: `contracts.Chunk` (текст, `embedding=None`) — офлайн из `data/processed/
chunks.jsonl`, онлайн — произвольный текст запроса от роутера (A-10/A-11).
Выход: те же `Chunk` с заполненным `embedding` (офлайн — `chunks_embedded.jsonl`)
либо «сырые» векторы `list[list[float]]` (онлайн, через `embed_texts`).
Зависимости: только stdlib (`urllib`, `json`) + `pydantic` (contracts.Chunk);
модель и хост Ollama — из окружения/`.env` (`EMBEDDING_MODEL`, `OLLAMA_BASE_URL`).
Инварианты: не пишет в Neo4j; `embed_texts` не имеет зависимости от файлов —
переиспользуется online-путём поиска.
Паспорт: docs/dev/modules/search.md.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from ariadna.contracts import Chunk
from ariadna.logutil import get_logger, log_event, new_run_id

# ─── Конфигурация Ollama (переопределяется .env/окружением) ───────────────
OLLAMA_BASE_URL_DEFAULT = "http://localhost:11434"
EMBEDDING_MODEL_DEFAULT = "qwen3-embedding:0.6b"
REQUEST_TIMEOUT_SEC = 120  # холодный первый запрос грузит модель в GPU — наблюдалось до ~20с

# ─── Офлайн-батч (A-04) ────────────────────────────────────────────────────
CHUNKS_PATH = Path("data/processed/chunks.jsonl")
EMBEDDED_PATH = Path("data/processed/chunks_embedded.jsonl")
SKIPPED_PATH = Path("data/processed/embed_skipped.jsonl")

# Размер батча подобран прогоном на живой Ollama (DGX Spark): 32 чанка по
# ~1500 симв. (CHUNK_SIZE_CHARS ingest/config.py) — один HTTP-запрос ~1с на
# прогретой модели (см. docs/dev/worklogs/search.md#2026-07-03).
EMBED_BATCH_SIZE = 32
RETRY_ATTEMPTS = 2       # попыток на батч целиком, прежде чем изолировать по чанку
PROGRESS_LOG_EVERY = 500  # чанков между записями прогресса в лог
ERR_TRUNCATE = 500        # усечение ответа Ollama в лог (CONVENTIONS.md §4)


class EmbeddingAPIError(Exception):
    """Ollama недоступна, вернула не-JSON или пустой/неполный набор векторов."""


# Назначение: подхватывает переменные из .env в os.environ, не перезаписывая уже
#   выставленные (запуск в контейнере/CI приоритетнее файла) — без внешней
#   зависимости вроде python-dotenv.
# Уровень: ✅ реализовано (A-04, worklogs/search.md#2026-07-03)
def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()  # один раз при импорте модуля

# Ollama — локальная инфраструктура проекта (deploy/docker-compose.yml), а не
# внешний сервис: системные HTTP_PROXY/HTTPS_PROXY (если выставлены в окружении
# агента) не должны применяться к запросам на localhost — иначе 502 от прокси.
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ─── embed_texts ────────────────────────────────────────────────────────────
# Назначение: считает эмбеддинги списка текстов через Ollama `/api/embed`
#   (батч в одном HTTP-запросе); переиспользуется офлайн-индексацией (A-04)
#   и онлайн роутером запросов (A-10/A-11) — не завязана на файлы.
# Входные связи: EMBEDDING_MODEL/OLLAMA_BASE_URL из окружения (.env)
# Выходные данные: list[list[float]] — по вектору на входной текст, тот же порядок
# Уровень: ✅ реализовано (A-04, worklogs/search.md#2026-07-03)
def embed_texts(
    texts: list[str],
    *,
    model: str | None = None,
    base_url: str | None = None,
) -> list[list[float]]:
    if not texts:
        return []
    model = model or os.environ.get("EMBEDDING_MODEL", EMBEDDING_MODEL_DEFAULT)
    base_url = (base_url or os.environ.get("OLLAMA_BASE_URL", OLLAMA_BASE_URL_DEFAULT)).rstrip("/")

    payload = json.dumps({"model": model, "input": texts}).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/api/embed",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with _NO_PROXY_OPENER.open(request, timeout=REQUEST_TIMEOUT_SEC) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise EmbeddingAPIError(f"Ollama недоступна ({base_url}): {exc}") from exc
    except json.JSONDecodeError as exc:
        raise EmbeddingAPIError(f"битый JSON от Ollama: {exc}") from exc

    embeddings = body.get("embeddings")
    if not embeddings or len(embeddings) != len(texts):
        got = len(embeddings) if embeddings else 0
        raise EmbeddingAPIError(
            f"пустой/неполный ответ Ollama: ожидали {len(texts)} векторов, получили {got}; "
            f"тело={str(body)[:ERR_TRUNCATE]}"
        )
    return embeddings


# Назначение: читает Chunk построчно из JSONL, пропуская пустые строки.
# Уровень: ✅ реализовано (A-04, worklogs/search.md#2026-07-03)
def _iter_chunks(path: Path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield Chunk.model_validate_json(line)


# Назначение: множество уже посчитанных chunk_id из выходного файла (для
#   перезапуска) + размерность вектора, если в файле уже есть хотя бы один.
# Уровень: ✅ реализовано (A-04, worklogs/search.md#2026-07-03)
def _load_done_chunk_ids(output_path: Path) -> tuple[set[str], int | None]:
    done: set[str] = set()
    dimension: int | None = None
    if not output_path.exists():
        return done, dimension
    for chunk in _iter_chunks(output_path):
        done.add(chunk.chunk_id)
        if dimension is None and chunk.embedding:
            dimension = len(chunk.embedding)
    return done, dimension


# Назначение: режет список на батчи фиксированного размера (последний может быть короче).
# Уровень: ✅ реализовано (A-04, worklogs/search.md#2026-07-03)
def _batched(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


# Назначение: считает эмбеддинги батча; при сбое — до RETRY_ATTEMPTS попыток
#   батчем целиком, затем изоляция по одному чанку (не роняет весь прогон
#   из-за одного плохого текста). Возвращает (успешные (chunk, vec), сбойные (chunk, reason)).
# Уровень: ✅ реализовано (A-04, worklogs/search.md#2026-07-03)
def _embed_batch_with_retry(batch: list[Chunk], logger, run_id: str):
    texts = [c.text for c in batch]
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            vectors = embed_texts(texts)
            return list(zip(batch, vectors)), []
        except EmbeddingAPIError as exc:
            log_event(
                logger, stage="embeddings", event="SEARCH-003", level="WARNING",
                detail=f"батч={len(batch)} попытка={attempt}/{RETRY_ATTEMPTS}: {str(exc)[:ERR_TRUNCATE]}",
            )
    ok: list[tuple[Chunk, list[float]]] = []
    failed: list[tuple[Chunk, str]] = []
    for chunk in batch:
        try:
            vec = embed_texts([chunk.text])[0]
            ok.append((chunk, vec))
        except EmbeddingAPIError as exc:
            reason = str(exc)[:ERR_TRUNCATE]
            log_event(
                logger, stage="embeddings", event="SEARCH-003", level="ERROR",
                doc_id=chunk.doc_id, detail=f"chunk_id={chunk.chunk_id} ответ={reason}",
            )
            failed.append((chunk, reason))
    return ok, failed


# Назначение: пишет строку в embed_skipped.jsonl + лог-событие (SEARCH-00x).
# Уровень: ✅ реализовано (A-04, worklogs/search.md#2026-07-03)
def _write_skip(skip_f, logger, chunk: Chunk, code: str, reason: str) -> None:
    skip_f.write(json.dumps(
        {"chunk_id": chunk.chunk_id, "doc_id": chunk.doc_id, "code": code, "reason": reason},
        ensure_ascii=False,
    ) + "\n")
    log_event(
        logger, stage="embeddings", event=code, level="WARNING",
        doc_id=chunk.doc_id, detail=f"chunk_id={chunk.chunk_id} reason={reason}",
    )


# ─── run_embedding_batch ────────────────────────────────────────────────────
# Назначение: офлайн-индексация A-04 — считает эмбеддинги всех чанков
#   chunks.jsonl батчами через embed_texts, пишет chunks_embedded.jsonl;
#   перезапускаемо (не пересчитывает уже готовые chunk_id по выходному файлу),
#   сбойные чанки (после ретраев) — в embed_skipped.jsonl, не теряются молча.
# Входные связи: data/processed/chunks.jsonl (contracts.Chunk, embedding=None)
# Выходные данные: dict сводки прогона (n_total, n_embedded_now, n_skipped, dimension, elapsed_sec)
# Уровень: ✅ реализовано (A-04, worklogs/search.md#2026-07-03)
def run_embedding_batch(
    chunks_path: Path = CHUNKS_PATH,
    output_path: Path = EMBEDDED_PATH,
    skipped_path: Path = SKIPPED_PATH,
    run_id: str | None = None,
) -> dict:
    run_id = run_id or new_run_id("embed_")
    logger = get_logger("search", run_id)

    all_chunks = list(_iter_chunks(chunks_path))
    done_ids, dimension = _load_done_chunk_ids(output_path)
    remaining = [c for c in all_chunks if c.chunk_id not in done_ids]

    log_event(
        logger, stage="embeddings", event="run_start",
        detail=f"total={len(all_chunks)} already_done={len(done_ids)} remaining={len(remaining)}",
    )

    n_embedded_now = 0
    n_skipped = 0
    since_last_log = 0
    start = time.monotonic()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        open(output_path, "a", encoding="utf-8") as out_f,
        open(skipped_path, "a", encoding="utf-8") as skip_f,
    ):
        for batch in _batched(remaining, EMBED_BATCH_SIZE):
            ok, failed = _embed_batch_with_retry(batch, logger, run_id)
            for chunk, vec in ok:
                if dimension is None:
                    dimension = len(vec)
                    model = os.environ.get("EMBEDDING_MODEL", EMBEDDING_MODEL_DEFAULT)
                    log_event(
                        logger, stage="embeddings", event="dimension_detected",
                        detail=f"dim={dimension} model={model}",
                    )
                if len(vec) != dimension:
                    _write_skip(skip_f, logger, chunk, "SEARCH-003",
                                f"размерность {len(vec)} != ожидаемой {dimension}")
                    n_skipped += 1
                    continue
                chunk.embedding = vec
                out_f.write(chunk.model_dump_json() + "\n")
                n_embedded_now += 1

            for chunk, reason in failed:
                _write_skip(skip_f, logger, chunk, "SEARCH-003", reason)
                n_skipped += 1

            since_last_log += len(ok) + len(failed)
            if since_last_log >= PROGRESS_LOG_EVERY:
                elapsed = time.monotonic() - start
                speed = n_embedded_now / elapsed if elapsed > 0 else 0.0
                log_event(
                    logger, stage="embeddings", event="progress",
                    detail=f"done={len(done_ids) + n_embedded_now + n_skipped}/{len(all_chunks)} "
                           f"speed={speed:.1f} чанк/с",
                )
                since_last_log = 0

    elapsed = time.monotonic() - start
    stats = {
        "run_id": run_id,
        "n_total": len(all_chunks),
        "n_already_done": len(done_ids),
        "n_embedded_now": n_embedded_now,
        "n_skipped": n_skipped,
        "dimension": dimension,
        "elapsed_sec": round(elapsed, 1),
    }
    log_event(
        logger, stage="embeddings", event="run_complete",
        detail=json.dumps({k: v for k, v in stats.items() if k != "run_id"}, ensure_ascii=False),
    )
    return stats


# ─── main ───────────────────────────────────────────────────────────────────
# Назначение: CLI-точка входа полного прогона офлайн-эмбеддинга (A-04); печатает
#   сводку в stdout.
# Входные связи: аргументов командной строки нет — конфигурация через окружение/.env
# Выходные данные: нет (побочный эффект — chunks_embedded.jsonl/embed_skipped.jsonl + печать)
# Уровень: ✅ реализовано (A-04, worklogs/search.md#2026-07-03)
def main() -> None:
    stats = run_embedding_batch()
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
