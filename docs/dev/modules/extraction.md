# Паспорт модуля: extraction

**Назначение:** извлечение знаний из чанков: сущности и связи — локальной LLM по
онтологии; числа и единицы — ТОЛЬКО правилами (regex-нормализатор). Инвариант №3.

**Вход:** `data/processed/chunks.jsonl` (Chunk), подмножество is_core.
**Выход:** `data/processed/extracted.jsonl` (ExtractionResult на чанк).

**Контракты:** ExtractionResult, Entity, Relation, NumericConstraint, EntityType,
RelationType (`contracts.py`). JSON-схема ExtractionResult вставляется в промпт LLM
(structured output) и валидирует ответ.

**Зависимости:** LLM через Ollama native API (`/api/chat`, `OLLAMA_BASE_URL`; модель
`EXTRACTION_MODEL` = qwen3.5:35b-a3b — основной рантайм, ревизия PM 03.07.2026; откат
`EXTRACTION_MODEL_FALLBACK` = qwen3.5:9b; vLLM запаркован). qwen3.5:35b-a3b —
reasoning-модель: thinking отключать (`"think": false` в /api/chat), иначе бюджет
токенов уходит в размышления (замер 03.07: no-think ~30 с/чанк, think ~67 с/чанк
с обрезанным ответом). Системный HTTP_PROXY ломает HTTP к localhost — клиент строить
без прокси (см. search/embeddings). Переключение рантайма/модели — сменой значений
в `.env`, не кода. Словарь синонимов — `ontology/synonyms.yaml` +
`graph.ontology.canonical_name()` (A-06); числа/единицы — `extraction.rules` (A-07).

**Не входит в зону ответственности:** дедупликация сущностей между документами и
загрузка в Neo4j (graph), чанкинг (ingest), ответы на вопросы (search).

**Известные ограничения:**
- Ошибки в числах недопустимы по заданию → LLM НЕ извлекает значения/единицы;
  правила: `мг/л, мг/дм³ (= мг/л), г/дм³, °C, м³/ч, т/сут, %, а также диапазоны
  «200–300 мг/л»` — таблица нормализации к каноническим единицам + тесты.
- Невалидный JSON от LLM: 1 ретрай с уточнением, затем чанк в skip-лист (EXTRACT-001).
- Прогон по всему ядру (~180 док.) — фоновый батч на GPU; запуск батча — отдельным
  скриптом, чтобы падение одного чанка не убивало прогон.
