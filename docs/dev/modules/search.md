# Паспорт модуля: search

**Назначение:** путь «вопрос → ответ»: эмбеддинги, роутер запросов (выбор Cypher-шаблона
и заполнение слотов), гибридный retrieval (граф + вектор), синтез ответа локальной LLM
(Qwen через Ollama, решение PM 03.07.2026) с цитатами до чанка.

**Вход:** вопрос пользователя (строка) + наполненный Neo4j.
**Выход:** Answer (текст, citations, contradictions У-3, recommendations У-1 —
рекомендации считает analytics, search их только вкладывает в Answer).

**Контракты:** QueryIntent, QueryFilters, Citation, Answer, Contradiction
(`contracts.py`).

**Подмодули:** `embeddings` (Qwen3-Embedding-0.6B через Ollama — решение PM
03.07.2026; используется и офлайн для индексации A-04, и онлайн для запросов),
`router` (вопрос → QueryIntent), `retrieval` (выполнение шаблона + векторный поиск),
`answer` (промпт к ANSWER_MODEL через Ollama `/v1/chat/completions` + сборка Answer).

**Зависимости:** Neo4j (только чтение!), Ollama (EMBEDDING_MODEL=qwen3-embedding:0.6b;
ANSWER_MODEL/ANSWER_BACKEND из `.env` — синтез ответов, по умолчанию ollama;
ANSWER_BACKEND=anthropic — опциональный возврат на Claude API), Cypher-шаблоны из graph.

**Не входит в зону ответственности:** запись в Neo4j (graph), карта пробелов и
литобзор (analytics), отрисовка (ui).

**Известные ограничения:**
- Свободный text2cypher ЗАПРЕЩЁН (инвариант №4): LLM только классифицирует вопрос
  в template_id и заполняет слоты; нет шаблона → template_id='rag_fallback'
  (чисто векторный ответ) + лог SEARCH-001.
- Ответ без цитат запрещён (инвариант №6): нет источников → found=False,
  текст «в корпусе не найдено» + передача темы в карту пробелов.
- Числовые условия из вопроса парсит нормализатор extraction/rules (переиспользовать,
  не дублировать).
- 4 эталонных запроса из TASK.md — обязательные приёмочные тесты роутера.
- ANSWER_MODEL qwen3.5:35b-a3b — reasoning-модель: тратит max_tokens на thinking
  (при малом бюджете content пуст) — закладывать большой бюджет токенов или
  управлять reasoning-режимом (критично для A-11).
