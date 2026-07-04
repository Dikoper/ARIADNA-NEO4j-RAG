# STATUS.md — доска задач «Ариадны»

Словарь: `📋 беклог · 🚧 в работе · 🧪 на тестировании · ✅ готово · ⛔ заблокировано ·
🔥 регрессия · 💤 отложено`

Правила (полные — CLAUDE.md): берёшь задачу → 📋→🚧; закончил код → 🚧→🧪; правишь только
свою строку; код + STATUS + worklog — один пакет изменений. Порядок ID = критический
путь. Вехи (M-xx) закрывает оркестратор/PM. Мета-модули `scripts` и `docs` паспортов
не имеют: весь контекст таких задач — в строке задачи и постановке от оркестратора;
worklog для них — worklogs/_orchestrator.md.

## Беклог

| ID | Модуль | Задача | Состояние | Агент | Worklog |
|---|---|---|---|---|---|
| A-00 | docs | Bootstrap документации и контрактов | ✅ | оркестратор | worklogs/_orchestrator.md#2026-07-03 |
| A-01 | deploy | docker-compose: Neo4j + Ollama (qwen3.5:35b-a3b, осн. рантайм; vLLM запаркован) + healthcheck, инструкция запуска | ✅ | module-dev+fixer (Sonnet), волна 1 | worklogs/deploy.md |
| A-02 | ingest | Конвертация ядра корпуса (Обзоры+Статьи+Доклады) → DocumentText/Meta + чанкинг | ✅ | module-dev (Sonnet), волна 1 | worklogs/ingest.md |
| A-03 | ingest | Отбор целевых документов под 4 эталонных запроса (ключевые слова: обессоливание, католит, штейн/шлак МПГ, шахтные воды) + флаг is_core | ✅ | module-dev (Sonnet), волна 2 | worklogs/ingest.md |
| A-04 | search | Эмбеддинги Qwen3-Embedding-0.6B для чанков (модуль embeddings, офлайн-батч) | ✅ | module-dev (Sonnet), волна 2 | worklogs/search.md |
| A-05 | graph | Загрузка лексического графа Document→Chunk + vector index в Neo4j | ✅ | module-dev (Sonnet), волна 2 | worklogs/graph.md |
| M-01 | — | **ВЕХА: страховочное демо — векторный RAG-ответ с цитатами** (~5 ч) | ✅ | оркестратор → module-dev+tester (Sonnet) | worklogs/search.md |
| A-06 | graph | OWL-онтология + словарь синонимов RU/EN в ontology/ | ✅ | module-dev+tester (Sonnet), волна 3 | worklogs/graph.md |
| A-07 | extraction | Правила чисел/единиц: regex-нормализатор → NumericConstraint + тесты на примерах эталонных запросов | ✅ | module-dev+tester (Sonnet), волна 3 | worklogs/extraction.md |
| A-08 | extraction | LLM-извлечение по онтологии (Ollama/qwen3.5:35b-a3b — осн. рантайм, откат qwen3.5:9b; structured output по схеме ExtractionResult; учесть reasoning-режим — бюджет токенов) | ✅ | module-dev+tester (Sonnet) | worklogs/extraction.md |
| A-09 | graph | Загрузка сущностного графа: дедуп по синонимам, хабы Experiment/TechSolution, provenance + поля У-4 (source/updated_at/confidence/edited_by) | ✅ | module-dev+tester+fixer (Sonnet), reviewer APPROVE | worklogs/graph.md |
| M-02 | — | **ВЕХА: граф виден в Neo4j Browser** (~10 ч) | ✅ | виза PM 04.07 | — |
| M-03 | — | **ЧЕК-ПОЙНТ ДЕГРАДАЦИИ (10–12 ч): сквозной путь работает? нет → RAG-first** | 📋 | решение PM | — |
| A-10 | search | Роутер запросов: QueryIntent, шаблонные Cypher под 4 эталонных запроса + сравнительный шаблон «RU vs зарубеж» (У-2) | ✅ | module-dev+tester+fixer (Sonnet), reviewer APPROVE, волна 4 | worklogs/search.md |
| A-11 | search | Гибридный ответ: retrieval граф+вектор → синтез локальным Qwen через Ollama (ANSWER_BACKEND, бюджет на thinking) → Answer с цитатами + пометка contradicts (У-3) | ✅ | module-dev+tester+fixer (Sonnet), reviewer APPROVE, волна 4 | worklogs/search.md |
| M-04 | — | **ВЕХА: 4 эталонных запроса отвечают** (~15 ч) | ✅ | виза PM 04.07 | — |
| A-12 | analytics | Карта пробелов ⭐: gap-матрица (Cypher NOT EXISTS) → GapReport + темы only_ru/only_foreign | ✅ | module-dev+tester+fixer (Sonnet), reviewer APPROVE, волна 5 | worklogs/analytics.md |
| A-13 | ui | Streamlit: чат + подграф ответа (contradicts красным, У-3) + карта пробелов + фильтры гео/год | ✅ | module-dev+tester+fixer×2 (Sonnet), reviewer APPROVE, волна 5 | worklogs/ui.md |
| A-14 | analytics | Блок «Рекомендации» (У-1): похожие кейсы (векторная близость), эксперты (обход графа), смежные темы → Recommendation | ✅ | module-dev (Sonnet), волна 6, reviewer APPROVE | worklogs/analytics.md |
| A-15 | ui | Блок «Рекомендации» в UI (панель рядом с ответом) | ✅ | module-dev (Sonnet), волна 6, reviewer APPROVE | worklogs/ui.md |
| M-05 | — | **ВЕХА: демо кликабельно** (~18 ч) | ✅ | виза PM 04.07 | — |
| A-16 | analytics | Литобзор: ReviewSection (консенсус/разногласия) + таблица сравнения технологий (У-2) + экспорт MD/JSON-LD | 📋 | — | — |
| A-17 | scripts | diagnose.py: по коду ошибки/doc_id собирает пакет для ремонтного агента (лог-трасса + паспорт + реестр) | 📋 | — | — |
| A-18 | graph | Семантический фасад: экспорт JSON-LD, валидация данных по онтологии | 📋 | — | — |
| A-19 | docs | Подача: README-финал, видео-демо (вкл. ручную правку в Neo4j Browser, У-4), презентация, деплой | ✅ | оркестратор + 2×module-dev (Sonnet), reviewer APPROVE; видео/ссылки — PM по SUBMISSION.md | worklogs/_orchestrator.md |
| A-20 | ingest | Каталожный слой: карточки CatalogEntry «директория+годы» по необработанным папкам (Журналы, Конференции) + эмбеддинги + загрузка в Neo4j (label CatalogEntry) | ✅ | module-dev+tester (Sonnet) | worklogs/ingest.md |
| A-21 | scripts | Обогащение тем жюри Haiku-субагентами: партии 1+2 = 3500 чанков → extracted_haiku.jsonl (65k сущн., 30k св.); хвост ~5.5k низкорелевантных чанков — решение PM | ✅ | оркестратор + Haiku-волны | worklogs/_orchestrator.md |
| A-22 | ingest | Гео-разметка документов (решение PM 04.07): правила по маркерам RU/зарубеж + Haiku-доразметка неоднозначных → Document.geography → only_ru/only_foreign карты пробелов; + косметика UI width | ✅ | оркестратор + module-dev (Sonnet), reviewer APPROVE | worklogs/ingest.md |
| A-23 | ui | Полировка UI (решение PM 04.07): фильтры confidence/тип сущности/размер подграфа, MD-экспорт ответа и gap-отчёта, читаемость графа (физика agraph, подписи, легенда) | ✅ | оркестратор (Fable) лично, reviewer APPROVE | worklogs/ui.md |
| M-06 | — | **ВЕХА: пакет сдачи готов** (дедлайн 04.07 23:59) | 📋 | PM+оркестратор | — |

## Отложено (💤 Could — только при запасе времени)

| ID | Модуль | Задача | Состояние |
|---|---|---|---|
| C-01 | api | Тонкий FastAPI поверх search/analytics | 💤 |
| C-02 | ui | RBAC-заглушка (2–3 роли) + дашборд руководителя | 💤 |
| C-03 | analytics | PDF-экспорт (из Markdown) | 💤 |
| C-04 | ingest | Распаковка ZIP/RAR (рыночная аналитика) | 💤 |
