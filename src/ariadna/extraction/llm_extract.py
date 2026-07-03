"""LLM-извлечение сущностей/связей по онтологии — extraction/llm_extract.py (A-08).

Вход: `data/processed/chunks.jsonl` (contracts.Chunk), опционально подмножество
по `data/processed/targets.jsonl` (doc_id из 4 эталонных тем жюри, см. ingest/
select.py). Выход: `data/processed/extracted.jsonl` — contracts.ExtractionResult
на чанк (append, `model_dump_json()`); чанки, не прошедшие извлечение после
ретрая — `data/processed/extract_skiplist.jsonl`.
Зависимости: `extraction.ollama_client` (HTTP к Ollama native `/api/chat`,
парсинг/схема ответа), `extraction.prompt` (системный промпт по онтологии),
`extraction.postprocess` (канонизация имён + фильтр связей), `extraction.
rules.extract_constraints` (числа/единицы — только правилами, инвариант №3),
`logutil` (JSON-события, run_id).
Инварианты: LLM не извлекает числа/единицы (ExtractionResult.constraints
заполняет rules.extract_constraints); doc_id/chunk_id/model/prompt_hash
проставляет код, не LLM; падение одного чанка не останавливает батч (skip-лист).
Паспорт: docs/dev/modules/extraction.md.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ariadna.contracts import Chunk, ExtractionResult
from ariadna.extraction.ollama_client import (
    PROMPT_HASH,
    ERR_TRUNCATE,
    EXTRACTION_MODEL_DEFAULT,
    ExtractionSchemaError,
    OllamaExtractionError,
    call_extraction_llm,
    clean_env_var,
    parse_raw_extraction,
)
from ariadna.extraction.postprocess import canonicalize_entities, filter_relations
from ariadna.extraction.prompt import SYSTEM_PROMPT, build_user_prompt
from ariadna.extraction.rules import extract_constraints
from ariadna.logutil import get_logger, log_event, new_run_id
# Импорт подхватывает .env в os.environ (embeddings._load_dotenv при импорте) —
# переиспользуем побочный эффект вместо своего загрузчика .env (как rag_demo.py).
from ariadna.search import embeddings as _embeddings_mod  # noqa: F401

# ─── Пути офлайн-батча (A-08) ───────────────────────────────────────────────
# TARGETS_PATH не задаётся константой по умолчанию: --targets — опциональный
# фильтр (без флага батч идёт по всему chunks.jsonl), путь передаётся явно
# в CLI (см. main(), пример — data/processed/targets.jsonl).
CHUNKS_PATH = Path("data/processed/chunks.jsonl")
EXTRACTED_PATH = Path("data/processed/extracted.jsonl")
SKIPLIST_PATH = Path("data/processed/extract_skiplist.jsonl")

RETRY_ATTEMPTS = 2         # попыток на чанк (1-й + 1 ретрай с уточнением при ошибке схемы)
PROGRESS_LOG_EVERY = 10    # чанков между записями прогресса (медленнее эмбеддингов — ~30с/чанк)


class _NullLogger:
    """Заглушка логгера для вызовов вне батч-раннера (тесты/smoke) — .log() не пишет никуда."""

    # ─── log ─────────────────────────────────────────────────────────
    # Назначение: заглушка интерфейса logging.LoggerAdapter.log — не пишет
    #   никуда (нужна вызовам extract_chunk/postprocess вне батч-раннера).
    # Входные связи: сигнатура совместима с logging.LoggerAdapter.log(level, msg, extra=...)
    # Выходные данные: нет
    # Уровень: ✅ реализовано (A-08)
    def log(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003 — сигнатура logging.LoggerAdapter
        pass


_NULL_LOGGER = _NullLogger()


# Назначение: строит messages для ретрая — исходные + бракованный ответ
#   ассистента + уточнение об ошибке (модель видит, что было не так).
# Уровень: ✅ реализовано (A-08)
def _build_retry_messages(base_messages: list[dict], bad_content: str, error: str) -> list[dict]:
    return base_messages + [
        {"role": "assistant", "content": bad_content},
        {"role": "user", "content": (
            f"Твой предыдущий ответ не прошёл валидацию: {error[:300]}. "
            "Верни ТОЛЬКО валидный JSON строго по описанной схеме, без markdown-обёртки "
            "и без пояснений вне JSON."
        )},
    ]


# ─── extract_chunk ───────────────────────────────────────────────────────
# Назначение: извлечение по одному чанку — LLM (до RETRY_ATTEMPTS попыток,
#   ретрай при ошибке схемы с уточнением) -> канонизация имён сущностей ->
#   отбраковка связей с source/target вне сущностей чанка -> constraints
#   правилами (rules.extract_constraints, НЕ LLM) -> ExtractionResult.
# Входные связи: contracts.Chunk; EXTRACTION_MODEL/OLLAMA_BASE_URL (.env)
# Выходные данные: (ExtractionResult | None, код_ошибки | None, причина | None) —
#   None-результат и код/причина заполнены при провале обеих попыток
# Уровень: ✅ реализовано (A-08)
def extract_chunk(
    chunk: Chunk, *, model: str | None = None, base_url: str | None = None, logger=None,
) -> tuple[ExtractionResult | None, str | None, str | None]:
    base_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(chunk.text)},
    ]
    model_name = model or clean_env_var("EXTRACTION_MODEL", EXTRACTION_MODEL_DEFAULT)
    log = logger if logger is not None else _NULL_LOGGER

    messages = base_messages
    last_kind, last_reason = "EXTRACT-001", "нет попыток"
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            content = call_extraction_llm(messages, model=model_name, base_url=base_url)
        except OllamaExtractionError as exc:
            last_kind, last_reason = "EXTRACT-004", str(exc)[:ERR_TRUNCATE]
            log_event(log, stage="extraction", event="EXTRACT-004", level="WARNING",
                       doc_id=chunk.doc_id,
                       detail=f"chunk_id={chunk.chunk_id} попытка={attempt}/{RETRY_ATTEMPTS}: {last_reason}")
            messages = base_messages  # сетевой сбой — повтор тем же промптом, не ретрай-подсказкой
            continue
        try:
            raw = parse_raw_extraction(content)
        except ExtractionSchemaError as exc:
            last_kind, last_reason = "EXTRACT-001", str(exc)[:ERR_TRUNCATE]
            log_event(log, stage="extraction", event="EXTRACT-001", level="WARNING",
                       doc_id=chunk.doc_id,
                       detail=f"chunk_id={chunk.chunk_id} попытка={attempt}/{RETRY_ATTEMPTS}: {last_reason} "
                              f"ответ={content[:ERR_TRUNCATE]}")
            messages = _build_retry_messages(base_messages, content, str(exc))
            continue

        entities = canonicalize_entities(raw.entities)
        entity_names = {e.name for e in entities}
        relations = filter_relations(
            raw.relations, entity_names, logger=log,
            chunk_id=chunk.chunk_id, doc_id=chunk.doc_id,
        )
        result = ExtractionResult(
            doc_id=chunk.doc_id,
            chunk_id=chunk.chunk_id,
            entities=entities,
            relations=relations,
            constraints=extract_constraints(chunk.text),
            model=model_name,
            prompt_hash=PROMPT_HASH,
        )
        return result, None, None

    return None, last_kind, last_reason


# ─── Батч-раннер (A-08) ──────────────────────────────────────────────────

# Назначение: читает Chunk построчно из JSONL, пропуская пустые строки.
# Уровень: ✅ реализовано (A-08)
def _iter_chunks(path: Path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield Chunk.model_validate_json(line)


# Назначение: множество chunk_id, уже присутствующих в extracted.jsonl
#   (перезапуск не пересчитывает готовые чанки).
# Уровень: ✅ реализовано (A-08)
def _load_done_chunk_ids(output_path: Path) -> set[str]:
    done: set[str] = set()
    if not output_path.exists():
        return done
    with open(output_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                done.add(json.loads(line).get("chunk_id", ""))
    return done


# Назначение: множество doc_id из targets.jsonl (фильтр --targets — только
#   документы 4 эталонных тем жюри, см. ingest/select.py).
# Уровень: ✅ реализовано (A-08)
def _load_target_doc_ids(path: Path) -> set[str]:
    doc_ids: set[str] = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                doc_ids.add(json.loads(line)["doc_id"])
    return doc_ids


# Назначение: пишет строку в extract_skiplist.jsonl (потокобезопасно через lock).
# Уровень: ✅ реализовано (A-08)
def _write_skip(skip_f, lock: threading.Lock, chunk: Chunk, code: str, reason: str) -> None:
    with lock:
        skip_f.write(json.dumps(
            {"chunk_id": chunk.chunk_id, "doc_id": chunk.doc_id, "code": code, "reason": reason},
            ensure_ascii=False,
        ) + "\n")
        skip_f.flush()


# ─── run_extraction_batch ────────────────────────────────────────────────
# Назначение: офлайн-батч A-08 — извлекает ExtractionResult по чанкам
#   chunks.jsonl (опционально только doc_id из targets.jsonl), пишет
#   extracted.jsonl; перезапускаемо (по chunk_id из выходного файла), сбойные
#   чанки (после ретрая) — в extract_skiplist.jsonl, не останавливают прогон.
#   --workers>1 — параллельные HTTP-запросы к Ollama через ThreadPoolExecutor.
# Входные связи: data/processed/chunks.jsonl, опц. targets.jsonl, --limit
# Выходные данные: dict сводки прогона (n_total_selected, n_already_done,
#   n_done_now, n_skipped, elapsed_sec)
# Уровень: ✅ реализовано (A-08)
def run_extraction_batch(
    chunks_path: Path = CHUNKS_PATH,
    output_path: Path = EXTRACTED_PATH,
    skiplist_path: Path = SKIPLIST_PATH,
    targets_path: Path | None = None,
    limit: int | None = None,
    workers: int = 1,
    run_id: str | None = None,
) -> dict:
    run_id = run_id or new_run_id("extract_")
    logger = get_logger("extraction", run_id)

    all_chunks = list(_iter_chunks(chunks_path))
    if targets_path is not None:
        target_doc_ids = _load_target_doc_ids(targets_path)
        all_chunks = [c for c in all_chunks if c.doc_id in target_doc_ids]

    done_ids = _load_done_chunk_ids(output_path)
    remaining = [c for c in all_chunks if c.chunk_id not in done_ids]
    if limit is not None:
        remaining = remaining[:limit]

    log_event(
        logger, stage="extraction", event="run_start",
        detail=f"total_selected={len(all_chunks)} already_done={len(done_ids)} "
               f"this_run={len(remaining)} workers={workers}",
    )

    n_done_now = 0
    n_skipped = 0
    since_last_log = 0
    start = time.monotonic()
    write_lock = threading.Lock()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        open(output_path, "a", encoding="utf-8") as out_f,
        open(skiplist_path, "a", encoding="utf-8") as skip_f,
        ThreadPoolExecutor(max_workers=max(1, workers)) as pool,
    ):
        futures = {pool.submit(extract_chunk, chunk, logger=logger): chunk for chunk in remaining}
        for future in as_completed(futures):
            chunk = futures[future]
            result, code, reason = future.result()
            if result is not None:
                with write_lock:
                    out_f.write(result.model_dump_json() + "\n")
                    out_f.flush()
                n_done_now += 1
            else:
                _write_skip(skip_f, write_lock, chunk, code or "EXTRACT-001", reason or "")
                log_event(logger, stage="extraction", event=code or "EXTRACT-001", level="ERROR",
                           doc_id=chunk.doc_id, detail=f"chunk_id={chunk.chunk_id} причина={reason}")
                n_skipped += 1

            since_last_log += 1
            if since_last_log >= PROGRESS_LOG_EVERY:
                elapsed = time.monotonic() - start
                speed = (n_done_now + n_skipped) / elapsed if elapsed > 0 else 0.0
                log_event(
                    logger, stage="extraction", event="progress",
                    detail=f"done={len(done_ids) + n_done_now + n_skipped}/{len(all_chunks)} "
                           f"speed={speed:.2f} чанк/с",
                )
                since_last_log = 0

    elapsed = time.monotonic() - start
    stats = {
        "run_id": run_id,
        "n_total_selected": len(all_chunks),
        "n_already_done": len(done_ids),
        "n_done_now": n_done_now,
        "n_skipped": n_skipped,
        "elapsed_sec": round(elapsed, 1),
    }
    log_event(
        logger, stage="extraction", event="run_complete",
        detail=json.dumps({k: v for k, v in stats.items() if k != "run_id"}, ensure_ascii=False),
    )
    return stats


# ─── main ────────────────────────────────────────────────────────────────
# Назначение: CLI-точка входа `python -m ariadna.extraction.llm_extract`.
# Входные связи: --limit N, --targets PATH, --workers N (аргументы командной строки)
# Выходные данные: нет (побочный эффект — extracted.jsonl/extract_skiplist.jsonl + печать сводки)
# Уровень: ✅ реализовано (A-08)
def main() -> None:
    parser = argparse.ArgumentParser(description="LLM-извлечение сущностей/связей по онтологии (A-08)")
    parser.add_argument("--limit", type=int, default=None, help="максимум чанков за этот запуск")
    parser.add_argument("--targets", type=Path, default=None,
                         help="путь к targets.jsonl — обрабатывать только чанки этих doc_id")
    parser.add_argument("--workers", type=int, default=1, help="параллельные запросы к Ollama")
    args = parser.parse_args()

    stats = run_extraction_batch(targets_path=args.targets, limit=args.limit, workers=args.workers)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
