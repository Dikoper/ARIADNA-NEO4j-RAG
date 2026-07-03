"""Пакет search: путь «вопрос → ответ» (карта знаний → синтез ответа Claude).

Вход: вопрос пользователя + наполненный Neo4j. Выход: contracts.Answer.
Подмодули: `embeddings` (Qwen3-Embedding-0.6B через Ollama — офлайн-индексация
A-04 и онлайн-запросы), `router`/`retrieval`/`answer` — последующие задачи.
Паспорт: docs/dev/modules/search.md.
"""
from __future__ import annotations
