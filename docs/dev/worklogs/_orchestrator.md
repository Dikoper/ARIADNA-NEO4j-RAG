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
