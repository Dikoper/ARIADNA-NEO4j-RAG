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
