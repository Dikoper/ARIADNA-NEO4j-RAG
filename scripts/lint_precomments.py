#!/usr/bin/env python3
"""Линтер пре-комментариев (CONVENTIONS.md §2) и базовой гигиены src/ariadna/.

Проверяет: (1) у каждого .py-файла есть docstring-шапка; (2) перед каждой def —
блок комментариев с ключами «Назначение:» и «Уровень:» с валидным уровнем 📋/✅/🔒;
для публичных функций дополнительно «Входные связи:» и «Выходные данные:»;
(3) размер файла ≤ 350 строк. Только стандартная библиотека.

Запуск: python scripts/lint_precomments.py  → exit 0 (ок) / 1 (нарушения списком).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "ariadna"
MAX_LINES = 350  # модуль целиком должен помещаться в контекст агента (CONVENTIONS §3)
LEVELS = ("📋", "✅", "🔒")
FULL_KEYS = ("Назначение:", "Входные связи:", "Выходные данные:", "Уровень:")
SHORT_KEYS = ("Назначение:", "Уровень:")


def _preceding_comment_block(lines: list[str], first_lineno: int) -> str:
    """Собирает подряд идущие строки-комментарии непосредственно над строкой №first_lineno."""
    block: list[str] = []
    i = first_lineno - 2  # индекс строки над сигнатурой/декоратором
    while i >= 0 and lines[i].strip().startswith("#"):
        block.append(lines[i].strip())
        i -= 1
    return "\n".join(reversed(block))


def _check_function(node: ast.AST, lines: list[str], path: Path, errors: list[str]) -> None:
    """Проверяет одну def: наличие блока, обязательные ключи, валидный уровень."""
    first_line = min([node.lineno] + [d.lineno for d in node.decorator_list])
    block = _preceding_comment_block(lines, first_line)
    public = not node.name.startswith("_")
    keys = FULL_KEYS if public else SHORT_KEYS
    where = f"{path.relative_to(SRC.parent.parent)}:{node.lineno} def {node.name}"
    if not block:
        errors.append(f"{where} — нет пре-комментария")
        return
    for key in keys:
        if key not in block:
            errors.append(f"{where} — в пре-комментарии нет ключа «{key}»")
    if "Уровень:" in block and not any(lv in block for lv in LEVELS):
        errors.append(f"{where} — «Уровень:» без валидного значения (📋/✅/🔒)")


def main() -> int:
    """Обходит src/ariadna/**/*.py, печатает нарушения, возвращает код выхода."""
    errors: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        rel = path.relative_to(SRC.parent.parent)
        if len(lines) > MAX_LINES:
            errors.append(f"{rel} — {len(lines)} строк (лимит {MAX_LINES}, CONVENTIONS §3)")
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            errors.append(f"{rel} — синтаксическая ошибка: {exc}")
            continue
        if lines and path.name != "__init__.py" and not ast.get_docstring(tree):
            errors.append(f"{rel} — нет docstring-шапки модуля (CONVENTIONS §1)")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _check_function(node, lines, path, errors)
    if errors:
        print(f"lint_precomments: {len(errors)} нарушений")
        for err in errors:
            print(f"  ✗ {err}")
        return 1
    print("lint_precomments: ок")
    return 0


if __name__ == "__main__":
    sys.exit(main())
