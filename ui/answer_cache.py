"""Кэш готовых ответов для демо (A-13): вопрос -> `contracts.Answer` (как dict).

Вход: нормализованный текст вопроса + `Answer.model_dump()`. Выход: JSON-файл
`data/processed/answer_cache.json` (вне git, см. .gitignore) — ключ: нормализованный
вопрос (нижний регистр, схлопнутые пробелы, без завершающего «?»/«.»), значение:
сериализованный `Answer` + служебное поле `cached_at` (ISO-время записи, только
для отображения в UI, не часть контракта).

Зависимости: только стандартная библиотека (json, pathlib). Ничего не знает
о Streamlit/Neo4j/Ollama — чистый модуль, тестируется без стенда.
Инвариант: запись атомарна (пишем во временный файл рядом и переименовываем) —
падение середины записи не портит уже накопленный кэш демонстрации.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

DEFAULT_CACHE_PATH = Path("data/processed/answer_cache.json")

_WHITESPACE_RE = re.compile(r"\s+")
_TRAILING_PUNCT_RE = re.compile(r"[?!.…\s]+$")


# Назначение: нормализует вопрос для использования как ключ кэша — нижний
#   регистр, схлопнутые пробелы, без хвостовой пунктуации (пользователь может
#   ввести тот же вопрос с другим регистром/пробелами/знаком в конце).
# Входные связи: строка вопроса пользователя
# Выходные данные: нормализованная строка-ключ
# Уровень: ✅ реализовано (A-13)
def normalize_question(question: str) -> str:
    text = question.strip().lower()
    text = _WHITESPACE_RE.sub(" ", text)
    text = _TRAILING_PUNCT_RE.sub("", text)
    return text


# Назначение: читает кэш с диска; отсутствующий или битый (не-JSON/не dict)
#   файл — не ошибка демо, тихо возвращает пустой кэш (честная деградация,
#   не роняет UI).
# Входные связи: путь к файлу кэша
# Выходные данные: dict[нормализованный вопрос -> {"answer": {...}, "cached_at": str}]
# Уровень: ✅ реализовано (A-13)
def load_cache(path: Path = DEFAULT_CACHE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


# Назначение: атомарно сохраняет кэш на диск (запись во временный файл +
#   os.replace) — обрыв процесса посреди записи не оставляет битый JSON.
# Входные связи: dict кэша (см. load_cache), путь назначения
# Выходные данные: нет (побочный эффект — файл на диске); директория создаётся
#   при необходимости
# Уровень: ✅ реализовано (A-13)
def save_cache(cache: dict[str, Any], path: Path = DEFAULT_CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


# ─── get_cached_answer ────────────────────────────────────────────────
# Назначение: ищет ответ по вопросу (после normalize_question) в уже
#   загруженном словаре кэша.
# Входные связи: cache (load_cache()), исходный текст вопроса
# Выходные данные: {"answer": dict, "cached_at": str} | None (кэш-промах)
# Уровень: ✅ реализовано (A-13)
def get_cached_answer(cache: dict[str, Any], question: str) -> dict[str, Any] | None:
    return cache.get(normalize_question(question))


# ─── put_answer ────────────────────────────────────────────────────────
# Назначение: дописывает свежий ответ в кэш (в памяти) под нормализованным
#   ключом вопроса и сразу сохраняет на диск — вызывающий код (app.py) не
#   обязан помнить порядок «положить -> сохранить».
# Входные связи: путь к файлу кэша, исходный вопрос, answer_dict (Answer.model_dump())
# Выходные данные: обновлённый dict кэша (для немедленного использования в UI
#   без повторного чтения с диска)
# Уровень: ✅ реализовано (A-13)
def put_answer(question: str, answer_dict: dict[str, Any], *, path: Path = DEFAULT_CACHE_PATH) -> dict[str, Any]:
    cache = load_cache(path)
    cache[normalize_question(question)] = {
        "answer": answer_dict,
        "cached_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    save_cache(cache, path)
    return cache
