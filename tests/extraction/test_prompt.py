"""Тесты extraction/prompt.py (A-08): системный промпт покрывает всю онтологию
(8 EntityType + 6 RelationType), явно запрещает извлекать числа/единицы (инвариант
№3) и не просит doc_id/chunk_id/model — их проставляет код (llm_extract.py).
"""
from __future__ import annotations

from ariadna.contracts import EntityType, RelationType
from ariadna.extraction.prompt import SYSTEM_PROMPT, build_user_prompt


# Назначение: промпт содержит ровно все 8 значений EntityType из contracts.py —
#   защита от рассинхрона онтологии и текста промпта при будущих правках.
# Уровень: ✅ реализовано (module-tester A-08)
def test_system_prompt_contains_all_entity_types():
    for entity_type in EntityType:
        assert f'"{entity_type.value}"' in SYSTEM_PROMPT, f"нет {entity_type.value} в промпте"


# Назначение: промпт содержит ровно все 6 значений RelationType из contracts.py.
# Уровень: ✅ реализовано (module-tester A-08)
def test_system_prompt_contains_all_relation_types():
    for relation_type in RelationType:
        assert f'"{relation_type.value}"' in SYSTEM_PROMPT, f"нет {relation_type.value} в промпте"


# Назначение: промпт явно запрещает извлекать числа/единицы измерения — это
#   зона extraction/rules.py, не LLM (инвариант №3, паспорт extraction.md).
# Уровень: ✅ реализовано (module-tester A-08)
def test_system_prompt_forbids_numbers_and_units():
    lowered = SYSTEM_PROMPT.lower()
    assert "не извлекай числа" in lowered


# Назначение: промпт не упоминает doc_id/chunk_id — их проставляет код,
#   не LLM (контракт ExtractionResult).
# Уровень: ✅ реализовано (module-tester A-08)
def test_system_prompt_does_not_ask_for_doc_or_chunk_id():
    assert "doc_id" not in SYSTEM_PROMPT
    assert "chunk_id" not in SYSTEM_PROMPT


# Назначение: промпт требует строго JSON без markdown-обёртки — совместимо
#   с ollama_client._strip_fences (защита от расхождения инструкции и парсера).
# Уровень: ✅ реализовано (module-tester A-08)
def test_system_prompt_requires_json_without_markdown_wrapper():
    assert "JSON" in SYSTEM_PROMPT
    assert "markdown" in SYSTEM_PROMPT.lower()


# Назначение: build_user_prompt оборачивает текст чанка целиком, без усечения
#   и без искажения содержимого (RU/EN смешанный текст проходит как есть).
# Уровень: ✅ реализовано (module-tester A-08)
def test_build_user_prompt_contains_chunk_text_verbatim():
    text = "Электролиз меди при 60°C, electrowinning process, without changes."
    prompt = build_user_prompt(text)
    assert text in prompt


# Назначение: build_user_prompt на пустом тексте не бросает исключение —
#   пустой чанк тоже валидный (краевой случай пустого чанка).
# Уровень: ✅ реализовано (module-tester A-08)
def test_build_user_prompt_empty_text_no_error():
    prompt = build_user_prompt("")
    assert isinstance(prompt, str)
    assert len(prompt) > 0
