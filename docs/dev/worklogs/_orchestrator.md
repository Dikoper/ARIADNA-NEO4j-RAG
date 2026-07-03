# Worklog: _orchestrator (Fable)

Журнал решений оркестратора: bootstrap, приёмка вех, эскалации, деградация.

---

## 2026-07-03 · оркестратор (Fable) · A-00
**Сделано:** bootstrap: CLAUDE.md, TASK.md, ARCHITECTURE.md, contracts.py (🔒),
CONVENTIONS.md, ERRORS.md, lint_precomments.py, pyproject.toml, STATUS.md (беклог
A-01…A-19 + вехи M-01…M-06 + Could C-01…C-04), 8 паспортов модулей, worklog-и.
**Решения:** улучшения У-1…У-4 (ревизия 3 анализа) вшиты в контракты (Recommendation,
Contradiction, ComparisonRow, Provenance), паспорта и беклог; api — вне критического
пути (UI импортирует search напрямую); chunking отнесён к ingest, embeddings — к search.
**Проблемы:** pydantic отсутствовал в системе — создан .venv, пакет ставится `pip install -e .`.
**Открыто:** выбор qwen3:8b vs 14b — по факту скорости на GB10 (решится в A-08).

## 2026-07-03 (позже) · оркестратор (Fable) · решение PM по моделям
**Сделано:** веб-исследование свежих LLM; решением PM пайплайн переведён на семейство
Qwen: извлечение — Qwen3.5-35B-A3B через vLLM+NVFP4 (откат Ollama/qwen3.5:9b),
эмбеддинги — Qwen3-Embedding-0.6B вместо bge-m3. Обновлены: .env.example, README,
ARCHITECTURE, STATUS (A-01/A-04/A-08), паспорта deploy/extraction/search, contracts.py
(только тексты description), ANALYSIS (артефакт).
**Решения:** vLLM+NVFP4 — основной рантайм извлечения (~600 против ~40 ток/с в батче —
условие попадания в дедлайн); MLX-сборки неприменимы (Apple Silicon); Ornith-1.0 отвергнут.
**Открыто:** точный HF-id NVFP4-сборки Qwen3.5-35B-A3B — уточнит A-01 (deploy). Закрыт
пункт «Открыто» записи A-00 (выбор 8b/14b неактуален).

## 2026-07-03 19:40 · оркестратор · волна 1 (A-01+A-02)
**Сделано:** волна 1 закрыта: A-02 APPROVE→коммит 0ec018e; A-01 REJECT→fixer→APPROVE.
Инфраструктура жива: neo4j+ollama healthy, qwen3.5:35b-a3b (23 ГБ) и qwen3-embedding:0.6b скачаны.
**Решения:** ревизия PM 03.07 вечером — основной рантайм извлечения Ollama/qwen3.5:35b-a3b
(llama.cpp/Ollama получили NVFP4+Blackwell-ядра, генерация = vLLM); vLLM запаркован
(профиль vllm), веса ig1 (~21,8 ГБ) сохранены в volume. Паспорт deploy актуализирован.
**Открыто:** для A-08 — qwen3.5:35b-a3b reasoning-модель: закладывать бюджет max_tokens
на thinking; при реанимации vLLM развести EXTRACTION_MODEL/served-model-name.

## 2026-07-03 · оркестратор · актуализация STATUS.md (A-08)
**Сделано:** по команде PM строка A-08 приведена к ревизии рантайма от 03.07:
основной — Ollama/qwen3.5:35b-a3b, откат qwen3.5:9b (было: vLLM+NVFP4); добавлено
напоминание про reasoning-режим (бюджет токенов).
**Решения:** остальные пункты плана (M-01/M-03/A-17/A-18) не трогаем — решение PM.
**Открыто:** — .

## 2026-07-03 ~21:00 · оркестратор · волна 2 (A-03+A-04+A-05) закрыта
**Сделано:** все три задачи APPROVE с первого ревью: A-03 (c43f542, отбор 99/33/121/28
по темам жюри), A-05 (df71027), A-04 (эмбеддинги 9580/9580, dim=1024, ~20 мин, 0 ошибок).
Полная загрузка графа выполнена: 177 Document, 9580 Chunk с векторами, HAS_CHUNK 9580,
vector index жив, self-match ок. Тесты волны: 81+10+12 зелёные.
**Решения:** фикстуры tests/ingest перегенерируются conftest-ом при прогонах (байт-шум) —
откатывать, не коммитить. HTTP_PROXY в окружении ломает localhost-запросы — в embeddings
обход прокси, учитывать в будущих модулях.
**Открыто:** tech-debt (не блокирует): logutil LOG_DIR относительно cwd + тестовый мусор
в logs/pipeline/; двойное SEARCH-003 на один сбойный чанк; self_check без ORDER BY.
Готовность к M-01 — по команде PM.
