"""Карточки источников (A-24) — свёрнутые карточки цитат ответа вместо
плоского списка: «[n] 🇷🇺/🌍 название (год)», внутри — авторы, цитата и кнопка
«Открыть документ» (скачивание исходного файла корпуса).

Вход: `contracts.Citation` (список из `Answer.citations`) + метаданные
документов из `data/processed/meta.jsonl` (география/авторы/путь к файлу —
разметка A-22, контракт `contracts.DocumentMeta`). Выход: экспандеры Streamlit.

Зависимости: `streamlit`, `ariadna.contracts.Citation`, `ui.citations_view.
format_citation` (тот же формат текста цитаты, что и раньше). Инварианты:
своих структур данных не заводим — meta.jsonl читается как есть (dict);
файл меты/документа недоступен — карточка честно деградирует (без иконки/
авторов/кнопки), цитата видна всегда; нумерация [n] — по ИСХОДНОМУ порядку
Answer.citations (совпадает с маркерами в тексте ответа и MD-экспортом),
скрытые фильтром года карточки пропускаются без перенумерации.
Паспорт: docs/dev/modules/ui.md.
"""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from ariadna.contracts import Citation
from ui.citations_view import format_citation

_REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = _REPO_ROOT / "data"
DEFAULT_META_PATH = DATA_ROOT / "processed" / "meta.jsonl"

# География документа (разметка A-22) -> иконка карточки; UNKNOWN/нет меты —
# без иконки (не выдумываем страну источника).
_GEO_ICONS: dict[str, str] = {"ru": "🇷🇺", "foreign": "🌍", "global": "🌐"}

_MIME_BY_SUFFIX: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".docm": "application/vnd.ms-word.document.macroEnabled.12",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
}


# ─── load_doc_meta ───────────────────────────────────────────────────────
# Назначение: meta.jsonl -> {doc_id: запись меты} для карточек (география,
#   авторы, путь к исходному файлу). Файл вне git (data/) — на чужой машине
#   его может не быть: отсутствие/битая строка -> пустой словарь/строка
#   пропускается, карточки деградируют до «название + цитата» без падения.
# Входные связи: путь к meta.jsonl (по умолчанию data/processed/meta.jsonl)
# Выходные данные: dict[doc_id -> dict меты]
# Уровень: ✅ реализовано (A-24)
@st.cache_data(ttl=3600, show_spinner=False)
def load_doc_meta(meta_path: str = str(DEFAULT_META_PATH)) -> dict[str, dict]:
    path = Path(meta_path)
    if not path.exists():
        return {}
    meta_by_doc: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        doc_id = record.get("doc_id")
        if doc_id:
            meta_by_doc[doc_id] = record
    return meta_by_doc


# ─── card_label ──────────────────────────────────────────────────────────
# Назначение: заголовок свёрнутой карточки — «[n] 🇷🇺 Название (год)»;
#   иконка географии только при известной разметке, год «б/г» — как в
#   format_citation (без выдумывания данных).
# Входные связи: номер цитаты (исходная нумерация Answer.citations),
#   Citation, запись меты документа (может быть пустой)
# Выходные данные: str
# Уровень: ✅ реализовано (A-24)
def card_label(index: int, citation: Citation, meta: dict) -> str:
    icon = _GEO_ICONS.get(str(meta.get("geography", "")), "")
    title = citation.title.strip() or str(meta.get("title", "")).strip() or citation.doc_id
    year = citation.year or meta.get("year")
    year_text = str(year) if year else "б/г"
    icon_part = f"{icon} " if icon else ""
    return f"[{index}] {icon_part}{title} ({year_text})"


# ─── _read_doc_bytes ─────────────────────────────────────────────────────
# Назначение: байты исходного документа для кнопки скачивания. Кэш —
#   st.cache_resource, НЕ cache_data (находка ревью A-24): cache_data на
#   каждый rerun отдаёт pickle-копию всех байтов каждого документа (десятки МБ
#   на клик по любому виджету), cache_resource возвращает один и тот же
#   объект — bytes неизменяемы, разделять безопасно. Файл не найден/не
#   читается -> None.
# Входные связи: путь записи меты (относительно data/)
# Выходные данные: bytes | None
# Уровень: ✅ реализовано (A-24, cache_resource и relative_to — ревью)
@st.cache_resource(ttl=3600, show_spinner=False)
def _read_doc_bytes(relative_path: str) -> bytes | None:
    path = (DATA_ROOT / relative_path).resolve()
    # Путь из меты не должен выводить за пределы data/ — проверка через
    # relative_to, а не startswith (префикс строки пропускал бы соседний
    # каталог вида data-old/ — находка ревью A-24).
    try:
        path.relative_to(DATA_ROOT.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


# ─── _render_card_body ───────────────────────────────────────────────────
# Назначение: содержимое раскрытой карточки — авторы (из меты), цитата
#   (формат общий с остальным UI) и кнопка «Открыть документ»; документа нет
#   на диске — честная подпись вместо кнопки.
# Уровень: ✅ реализовано (A-24)
def _render_card_body(index: int, citation: Citation, meta: dict) -> None:
    authors = [a for a in meta.get("authors", []) if str(a).strip()]
    if authors:
        st.caption("Авторы: " + ", ".join(str(a) for a in authors))
    st.markdown(format_citation(citation))

    relative_path = str(meta.get("path", "")).strip()
    payload = _read_doc_bytes(relative_path) if relative_path else None
    if payload is None:
        st.caption("Исходный файл недоступен в этой установке демо.")
        return
    file_name = Path(relative_path).name
    st.download_button(
        "Открыть документ",
        data=payload,
        file_name=file_name,
        mime=_MIME_BY_SUFFIX.get(Path(relative_path).suffix.lower(), "application/octet-stream"),
        key=f"open_doc_{index}_{citation.doc_id}_{citation.chunk_id}",
    )


# ─── render_source_cards ─────────────────────────────────────────────────
# Назначение: рендер вкладки «Источники» — карточка-экспандер на каждую
#   видимую цитату; скрытые фильтром года пропускаются (номера остальных не
#   сдвигаются — совпадают с маркерами [n] в тексте ответа и MD-отчётом).
# Входные связи: Answer.citations (полный список — для нумерации), visible —
#   отфильтрованный по году список (ui.citations_view.filter_citations_by_year)
# Выходные данные: нет (побочный эффект — виджеты Streamlit)
# Уровень: ✅ реализовано (A-24)
def render_source_cards(citations: list[Citation], visible: list[Citation]) -> None:
    meta_by_doc = load_doc_meta()
    for index, citation in enumerate(citations, start=1):
        if citation not in visible:
            continue
        meta = meta_by_doc.get(citation.doc_id, {})
        with st.expander(card_label(index, citation, meta)):
            _render_card_body(index, citation, meta)
