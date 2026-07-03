# Ариадна — карта знаний R&D для горно-металлургической отрасли

Решение задачи «Научный клубок» хакатона Норникеля: GraphRAG-система, которая извлекает
сущности и связи из корпуса научно-технических документов, строит граф знаний в Neo4j
и отвечает на естественно-языковые запросы с числовыми ограничениями, географией и
провенансом до исходного документа.

## Архитектура

```
Документы (PDF/DOCX/PPTX)
   ↓  ingest: конвертация → текст + метаданные
   ↓  extraction: LLM-извлечение по онтологии + правила для чисел/единиц
Neo4j: двойной граф
   • лексический:  Document → Chunk (embeddings, vector index)
   • сущностный:   Experiment / TechSolution / Material / Process / Equipment /
                   Property / Publication / Expert / Facility (+ provenance до чанка)
   ↓  search: роутер запросов → шаблоны Cypher + гибридный retrieval
   ↓  analytics: литобзор, консенсус/разногласия, карта пробелов
UI: чат-поиск · интерактивный граф · карта пробелов
```

## Структура репозитория

```
src/ariadna/
  ingest/       конвертация документов в текст
  extraction/   LLM-пайплайн извлечения, правила чисел/единиц, синонимия RU/EN
  graph/        онтология, загрузка в Neo4j, шаблоны Cypher
  search/       ретриверы, роутер запросов, синтез ответов
  analytics/    литобзор, gap-анализ, экспорт MD/JSON-LD
  api/          FastAPI-бэкенд
ui/             Streamlit-интерфейс
ontology/       OWL-онтология, словарь синонимов RU/EN
config/         конфигурация пайплайна
scripts/        скрипты запуска этапов пайплайна
deploy/         docker-compose (Neo4j + Ollama + приложение)
tests/          тесты
docs/           презентация и материалы сдачи
data/           корпус документов (не в git; https://disk.yandex.ru/d/npigiuw4Rbe9Pg)
```

## Быстрый старт

```bash
cp .env.example .env          # заполнить ключи/пароли
docker compose -f deploy/docker-compose.yml up -d   # Neo4j + Ollama
# далее — скрипты пайплайна из scripts/ (см. документацию по мере разработки)
```

## Стек

Neo4j (граф + vector index) · neo4j-graphrag · Qwen3.5-35B-A3B через vLLM+NVFP4
(извлечение — локально, откат: Ollama/qwen3.5:9b) · Qwen3-Embedding-0.6B (эмбеддинги
RU/EN, Ollama) · Claude API (синтез ответов) · FastAPI · Streamlit
