"""Именованные константы модуля graph: пути по умолчанию, имя vector index,
размер батча загрузки, переменные окружения подключения к Neo4j.

Вход: нет (статическая конфигурация). Выход: константы для lexical_loader
и последующих задач graph — CHUNK_VECTOR_INDEX_NAME понадобится search/retrieval
для векторного поиска по чанкам (имя зафиксировано здесь, не менять без
согласования с search). Зависимости: только stdlib. Паспорт: docs/dev/modules/graph.md.
"""
from __future__ import annotations

from pathlib import Path

# ─── Входные пути (по умолчанию) ────────────────────────────────────────
DEFAULT_META_PATH = Path("data/processed/meta.jsonl")
DEFAULT_CHUNKS_PATH = Path("data/processed/chunks_embedded.jsonl")
DEFAULT_EXTRACTED_PATH = Path("data/processed/extracted_haiku.jsonl")  # A-09 entity_loader

# ─── Vector index по Chunk.embedding — имя фиксировано для search/retrieval ──
CHUNK_VECTOR_INDEX_NAME = "chunk_embedding_idx"
VECTOR_SIMILARITY_FUNCTION = "cosine"

# ─── Батч UNWIND — компромисс память/round-trips (9580 чанков корпуса) ──
LOAD_BATCH_SIZE = 500

# ─── Провенанс узлов сущностного графа (A-09) — автоизвлечение Haiku ────
ENTITY_NODE_DEFAULT_CONFIDENCE = 0.9

# ─── Подключение к Neo4j — .env в корне проекта (CLAUDE.md) ────────────
ENV_FILE = Path(".env")
NEO4J_URI_VAR = "NEO4J_URI"
NEO4J_USER_VAR = "NEO4J_USER"
NEO4J_PASSWORD_VAR = "NEO4J_PASSWORD"
