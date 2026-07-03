# Worklog: ingest

Шаблон записи (≤ 8 строк). Эскалация — блок `⛔ ЭСКАЛАЦИЯ:` (симптом, что пробовал, гипотезы).

```markdown
## ГГГГ-ММ-ДД ЧЧ:ММ · <агент> · <ID задачи>
**Сделано:** …
**Решения:** …
**Проблемы:** …
**Открыто:** …
```

---

## 2026-07-03 17:00 · module-dev (Sonnet) · A-02
**Сделано:** Конвейер ingest: discover→convert→normalize→metadata→chunk→pipeline
(`src/ariadna/ingest/`) + общий JSON-логгер `src/ariadna/logutil.py`. Полный
прогон ядра (180 файлов): 177 сконвертировано, 3 в skip-листе, 9580 чанков.
**Решения:** PDF — PyMuPDF (постранично, для чистки колонтитулов). DOCX/DOCM —
ручной разбор OOXML (zip+XML) вместо python-docx: он падает на .docm из-за
строгой проверки content-type. .doc — headless `soffice --convert-to docx`,
затем тот же OOXML-парсер. PPTX — python-pptx. Колонтитулы PDF чистятся
эвристикой частоты первой/последней строки страницы; чанкинг — regex-границы
предложений, константы CHUNK_SIZE_CHARS=1500/OVERLAP=200. is_core=True всем
обработанным (папки Обзоры/Статьи/Доклады = ядро по паспорту).
**Проблемы:** 2 файла .rar (архивы, C-04 вне скоупа) → INGEST-001; 1 скан-PDF
без текстового слоя → INGEST-002. Все ожидаемо, чинить не пытался (вне скоупа).
**Открыто:** Конвертация .doc требует системный `soffice`/libreoffice —
проверено в текущем окружении, но не проверял его наличие на DGX Spark
(деплой-окружение); если отсутствует — 6 файлов .doc уйдут в skip INGEST-001.

## 2026-07-03 18:20 · module-tester (Sonnet) · A-02
**Сделано:** 58 тестов в `tests/ingest/` (contracts/convert/normalize/metadata/
chunk/pipeline/logutil + смоук по реальным `data/processed/*.jsonl`), фикстуры
PDF/DOCX/DOCM/PPTX/.doc сгенерированы в `tests/ingest/fixtures/`. Все 58
прошли; `lint_precomments.py` — ок. Ключевая проверка на боевых данных:
`chunk.text == doc_text.text[start:end]` — сошлось на выборке.
**Найденные баги:** `convert_legacy_doc` (`convert.py:97-119`) не поднимает
`ConversionError`/INGEST-001 на "мусорном" .doc — soffice headless в autodetect
молча импортирует любые байты как plain-text вместо ошибки конвертации;
перехватится дальше только если результат < MIN_TEXT_CHARS (не гарантировано).
Тест `test_convert_legacy_doc_corrupted_is_silently_accepted_by_soffice`
документирует фактическое поведение.
**Открыто:** soffice 24.2.7.2 доступен в этой среде, .doc→docx roundtrip
подтверждён — вопрос про DGX Spark из предыдущей записи остаётся открытым
(другая машина). `run_pipeline()` не принимает `data_dir` — тестам пришлось
monkeypatch'ить `discover_core_documents` в pipeline.py для фикстур.
**Вердикт:** ПРОШЁЛ С ЗАМЕЧАНИЯМИ (см. «Найденные баги»). STATUS.md не трогал.
