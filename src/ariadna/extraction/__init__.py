"""Пакет extraction: числа/единицы правилами (rules.py) + LLM-извлечение по онтологии (A-08).

Публичные входы: `extraction.rules.extract_constraints` (числа/единицы, не LLM);
`extraction.llm_extract.extract_chunk`/`run_extraction_batch` (сущности/связи
локальной LLM, CLI: `python -m ariadna.extraction.llm_extract`).
Паспорт: docs/dev/modules/extraction.md.
"""
