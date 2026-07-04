"""Тесты ui.app (A-13, fixer-пакет): модуль импортируется без побочного
рендера (guard `if __name__ in ("__main__", "__page__")` вокруг main() —
дефект №2), пользовательские тексты фолбэков не содержат технического жаргона
(регресс дефекта №1: Neo4j/Cypher/chunk/чанк/contradicts/имена функций-контракта/
слово «граф» не должны утекать в st.caption/st.info/st.warning/... ни напрямую
в app.py, ни через строковые константы view-модулей ui/*.py), и набор
пресетов жюри равен 4.

Усиление после REJECT ревью (волна 5, блокер №1): старая проверка смотрела
только на строковые литералы внутри app.py и пропускала жаргон, спрятанный в
константах citations_view.GEOGRAPHY_FILTER_UNAVAILABLE_NOTE и
gap_view.GAP_MATRIX_GEOGRAPHY_NOTE (импортируются в app.py и склеиваются с
текстом через `+`, поэтому не видны как Constant в AST app.py). Теперь
дополнительно сканируются все строковые константы модульного уровня во всех
ui/*.py, и список запрещённых паттернов расширен: "contracts.", имя функции
build_gap_report, "()" (след кода в пользовательском тексте) и слово «граф» с
границей слова (\bграф — не путать с «география»/«географии», где «граф» не
начало слова)."""
from __future__ import annotations

import ast
import inspect
import pathlib
import re

import ui
from ui import app as app_module
from ui import backend

FORBIDDEN_TERMS = ("neo4j", "cypher", "chunk", "чанк", "contradicts")
FORBIDDEN_SUBSTRINGS = ("contracts.", "build_gap_report", "()")
FORBIDDEN_GRAF_RE = re.compile(r"\bграф", re.IGNORECASE)
USER_FACING_METHODS = {"caption", "info", "warning", "write", "markdown", "text", "error", "success"}

UI_DIR = pathlib.Path(ui.__file__).resolve().parent


def _violations_in_text(text: str) -> list[str]:
    lowered = text.lower()
    found = [term for term in FORBIDDEN_TERMS if term in lowered]
    found += [s for s in FORBIDDEN_SUBSTRINGS if s in text]
    if FORBIDDEN_GRAF_RE.search(text):
        found.append(r"\bграф")
    return found


def _iter_str_constants(node: ast.AST):
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            yield n.value


def _iter_user_facing_strings(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in USER_FACING_METHODS:
                for arg in list(node.args) + [kw.value for kw in node.keywords]:
                    yield from _iter_str_constants(arg)


def _iter_module_level_str_constants(tree: ast.Module):
    for node in tree.body:
        value = None
        if isinstance(node, ast.Assign):
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            yield value.value


def test_module_imports_without_exception() -> None:
    # Импорт уже произошёл выше (from ui import app) — если бы main() исполнялся
    # на уровне модуля без guard, эта строка отработала бы уже с побочными
    # эффектами/исключениями Streamlit script-run-context. Явная проверка ниже:
    # main существует как функция и НЕ была невольно превращена во что-то ещё.
    assert callable(app_module.main)


def test_user_facing_text_has_no_forbidden_terms() -> None:
    source = inspect.getsource(app_module)
    tree = ast.parse(source)
    violations = [
        (violations_found, text)
        for text in _iter_user_facing_strings(tree)
        for violations_found in [_violations_in_text(text)]
        if violations_found
    ]
    assert not violations, violations


def test_module_level_string_constants_have_no_forbidden_terms() -> None:
    # Ловит жаргон, спрятанный в константах view-модулей (GEOGRAPHY_FILTER_
    # UNAVAILABLE_NOTE, GAP_MATRIX_GEOGRAPHY_NOTE и т.п.), которые в app.py
    # склеиваются с текстом через `+`/f-строку и потому невидимы как обычный
    # строковый литерал вызова st.* в самом app.py.
    violations = []
    for py_file in sorted(UI_DIR.glob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for text in _iter_module_level_str_constants(tree):
            found = _violations_in_text(text)
            if found:
                violations.append((py_file.name, found, text))
    assert not violations, violations


def test_main_guarded_at_module_level() -> None:
    source = inspect.getsource(app_module)
    assert 'if __name__ in ("__main__", "__page__"):' in source
    # main() не должен вызываться без охраны отступом (голый вызов на уровне модуля).
    assert "\nmain()\n" not in source


def test_preset_questions_count() -> None:
    assert len(backend.PRESET_QUESTIONS) == 4
