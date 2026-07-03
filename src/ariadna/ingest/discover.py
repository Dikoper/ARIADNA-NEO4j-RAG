"""Обход корпуса: список файлов ядра + стабильный doc_id.

Вход: `config.DATA_DIR`, `config.CORE_FOLDERS`. Выход: список `DiscoveredFile`
(путь, папка-источник, расширение, doc_id) — вход для `convert.py`.
Паспорт: docs/dev/modules/ingest.md.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ariadna.ingest.config import CORE_FOLDERS, DATA_DIR, SUPPORTED_EXTS


@dataclass(frozen=True)
class DiscoveredFile:
    """Один файл корпуса до конвертации: путь, папка-источник, doc_id."""

    path: Path
    source_folder: str
    ext: str
    doc_id: str
    rel_path: str


# ─── make_doc_id ────────────────────────────────────────────────────────
# Назначение: стабильный ID документа — хеш пути относительно data/
#   (contracts.DocumentMeta.doc_id: «Стабильный ID... хеш относительного пути»).
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def _make_doc_id(rel_path: str) -> str:
    return hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:16]


# ─── discover_core_documents ────────────────────────────────────────────
# Назначение: перечисляет файлы ядра корпуса (Обзоры+Статьи+Доклады),
#   отфильтровывает по поддерживаемым расширениям, считает doc_id.
# Входные связи: config.DATA_DIR, config.CORE_FOLDERS, config.SUPPORTED_EXTS
# Выходные данные: (supported, unsupported) — два списка DiscoveredFile;
#   unsupported — форматы вне SUPPORTED_EXTS (например .rar) для skip-листа
# Уровень: ✅ реализовано (A-02, worklogs/ingest.md#2026-07-03)
def discover_core_documents(
    data_dir: Path = DATA_DIR,
) -> tuple[list[DiscoveredFile], list[DiscoveredFile]]:
    supported: list[DiscoveredFile] = []
    unsupported: list[DiscoveredFile] = []
    for folder in CORE_FOLDERS:
        folder_path = data_dir / folder
        if not folder_path.is_dir():
            continue
        for path in sorted(folder_path.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            ext = path.suffix.lower()
            rel_path = str(path.relative_to(data_dir))
            item = DiscoveredFile(
                path=path,
                source_folder=folder,
                ext=ext,
                doc_id=_make_doc_id(rel_path),
                rel_path=rel_path,
            )
            (supported if ext in SUPPORTED_EXTS else unsupported).append(item)
    return supported, unsupported
