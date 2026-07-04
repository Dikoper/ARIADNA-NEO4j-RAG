"""Пакет search: путь «вопрос → ответ» (карта знаний → синтез ответа локальной LLM,
ANSWER_MODEL через Ollama; решение PM 03.07.2026).

Вход: вопрос пользователя + наполненный Neo4j. Выход: contracts.Answer.
Подмодули: `embeddings` (Qwen3-Embedding-0.6B через Ollama — офлайн-индексация
A-04 и онлайн-запросы), `rag_demo` (страховочный чисто векторный путь, M-01),
`retrieval` (слияние граф+вектор в пул чанков-свидетельств, A-11), `answer`
(синтез Answer с цитатами/contradicts, A-11), `router` — зона A-10.
Паспорт: docs/dev/modules/search.md.
"""
from __future__ import annotations
