"""Каталожный слой необработанной части корпуса — карточки-указатели для папок
data/Журналы и data/Материалы конференций, которые ingest не конвертирует (вне
ядра, задача A-20). Метаданные — только из имён файлов/директорий, содержимое
файлов не читается.

Вход: файловая система data/Журналы, data/Материалы конференций (только чтение).
Выход: contracts.CatalogEntry — data/processed/catalog.jsonl + узлы Neo4j
(метка CatalogEntry, векторный индекс catalog_embedding_idx). Карточка НЕ
попадает в ответ с цитатами (инвариант №6) — только в панель рекомендаций UI
(«неиндексированный источник по теме → путь к папке»).

Зависимости: ariadna.search.embeddings.embed_texts (Ollama-клиент уже решает
грабли системного HTTP_PROXY), ariadna.ingest.catalog_loader (запись в Neo4j —
отдельный файл, декомпозиция по лимиту ~350 строк CONVENTIONS §3), contracts,
logutil. Инвариант: карточка может рекурсивно охватывать вложенные директории
до границы соседней карточки — читает только пути, не содержимое файлов.
Точка входа: `python -m ariadna.ingest.catalog [--dry-run] [--no-load]`.
Паспорт: docs/dev/modules/ingest.md (каталожный слой — постановка оркестратора A-20).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

from ariadna.contracts import CatalogEntry
from ariadna.ingest import catalog_loader
from ariadna.ingest.config import DATA_DIR, PROCESSED_DIR
from ariadna.logutil import get_logger, log_event, new_run_id
from ariadna.search.embeddings import EmbeddingAPIError, embed_texts

# ─── Корни скана — вне ядра ingest (паспорт ingest.md: CORE_FOLDERS их не включает) ──
CATALOG_ROOTS = ("Журналы", "Материалы конференций")
CATALOG_OUTPUT_PATH = PROCESSED_DIR / "catalog.jsonl"

# ─── Годы: диапазон правдоподобия — отбрасываем мусор (номера ГОСТ, ISSN и т.п.) ──
YEAR_RE = re.compile(r"(19\d{2}|20\d{2})")
MIN_YEAR, MAX_YEAR = 1980, 2026

# ─── Периодические компоненты пути — сворачиваются в карточку родителя ────────
_YEAR_TOKEN_RE = re.compile(r"^(19\d{2}|20\d{2})([-\s].*)?$")
_MONTH_NAMES_RU = {
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
}
_PERIODICITY_NAMES_RU = {
    "годовые издания", "дневные издания", "ежемесячные издания",
    "квартальные издания", "недельные издания",
}
_QUARTER_WORDS = {"quaterly", "quarterly", "q1", "q2", "q3", "q4"}
_EN_MONTH_RE = re.compile(
    r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{0,4}$", re.IGNORECASE
)

# ─── Заголовок «Источники данных о <тема>» — источник темы для title рынка ────
_TOPIC_RE = re.compile(r"^Источники данных о (.+)$", re.IGNORECASE)

CATALOG_EMPTY_CARD = "INGEST-004"  # карточка с 0 файлов после разбиения по границам вложенных карточек
CATALOG_EMBED_FAILED = "INGEST-005"  # эмбеддинг каталога не получен (Ollama недоступна/ошибка)


# Назначение: True — компонент пути чисто временная метка (год/месяц/периодичность),
#   схлопывается в карточку родителя, а не порождает свою собственную карточку.
# Уровень: ✅ реализовано (A-20, worklogs/ingest.md#2026-07-04)
def _is_period_component(name: str) -> bool:
    n = name.strip()
    nl = n.lower()
    if _YEAR_TOKEN_RE.match(n):
        return True
    if nl in _MONTH_NAMES_RU or nl in _PERIODICITY_NAMES_RU or nl in _QUARTER_WORDS:
        return True
    return bool(_EN_MONTH_RE.match(n))


# Назначение: директории CATALOG_ROOTS, непосредственно содержащие ≥1 файл
#   (рекурсивно по всему поддереву, скрытые файлы/папки пропускаются).
# Уровень: ✅ реализовано (A-20, worklogs/ingest.md#2026-07-04)
def _iter_content_dirs(data_dir: Path) -> list[Path]:
    content_dirs: list[Path] = []
    for root_name in CATALOG_ROOTS:
        root_path = data_dir / root_name
        if not root_path.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root_path):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            if any(not f.startswith(".") for f in filenames):
                content_dirs.append(Path(dirpath))
    return content_dirs


# Назначение: сворачивает директорию вверх, отбрасывая хвостовые «периодические»
#   компоненты пути, пока не встретится содержательное имя; не выше CATALOG_ROOTS.
# Уровень: ✅ реализовано (A-20, worklogs/ingest.md#2026-07-04)
def _collapse_to_card_dir(content_dir: Path, data_dir: Path) -> Path:
    parts = list(content_dir.relative_to(data_dir).parts)
    while len(parts) > 1 and _is_period_component(parts[-1]):
        parts.pop()
    return data_dir.joinpath(*parts)


# ─── discover_card_dirs ────────────────────────────────────────────────────
# Назначение: список директорий-карточек каталога — «листовых» директорий с
#   файлами, схлопнутых по периодическим компонентам пути до содержательного
#   имени (журнал/конференция/источник рыночных данных).
# Входные связи: файловая система data/Журналы, data/Материалы конференций
# Выходные данные: отсортированный список уникальных Path (директории карточек)
# Уровень: ✅ реализовано (A-20, worklogs/ingest.md#2026-07-04)
def discover_card_dirs(data_dir: Path = DATA_DIR) -> list[Path]:
    content_dirs = _iter_content_dirs(data_dir)
    card_dirs = {_collapse_to_card_dir(d, data_dir) for d in content_dirs}
    return sorted(card_dirs)


# Назначение: годы (1980..2026) из строки. Уровень: ✅ (A-20, worklogs/ingest.md#2026-07-04)
def _years_in_text(text: str) -> list[int]:
    return [int(y) for y in YEAR_RE.findall(text) if MIN_YEAR <= int(y) <= MAX_YEAR]


# Назначение: файлы и годы «в собственном владении» карточки — рекурсивно по
#   card_dir, но без захода в поддеревья других карточек (иначе файлы вложенной
#   карточки посчитались бы дважды).
# Уровень: ✅ реализовано (A-20, worklogs/ingest.md#2026-07-04)
def _scan_own(card_dir: Path, all_card_dirs: set[Path]) -> tuple[int, list[int]]:
    n_files = 0
    years: list[int] = []
    for dirpath, dirnames, filenames in os.walk(card_dir):
        current = Path(dirpath)
        if current != card_dir and current in all_card_dirs:
            dirnames[:] = []  # граница вложенной карточки — не спускаемся и не считаем
            continue
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        years.extend(_years_in_text(current.name))
        for f in filenames:
            if f.startswith("."):
                continue
            n_files += 1
            years.extend(_years_in_text(f))
    return n_files, years


# Назначение: стабильный ID карточки — хеш относительного пути (по образцу
#   discover._make_doc_id).
# Уровень: ✅ реализовано (A-20, worklogs/ingest.md#2026-07-04)
def _make_catalog_id(rel_path: str) -> str:
    return hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:16]


# Назначение: journal (путь начинается с «Журналы») | market_analytics (путь
#   содержит «МПГ»/«Источники данных») | conference (остальное) — правило A-20.
# Уровень: ✅ реализовано (A-20, worklogs/ingest.md#2026-07-04)
def _classify_kind(rel_path: str) -> str:
    top = rel_path.split("/", 1)[0]
    if top == "Журналы":
        return "journal"
    if "МПГ" in rel_path or "Источники данных" in rel_path:
        return "market_analytics"
    return "conference"


# Назначение: человекочитаемый title по виду карточки — журнал по имени папки,
#   рыночная аналитика с темой из «Источники данных о <тема>», конференция по
#   имени папки (запятые/пробелы подчищены).
# Уровень: ✅ реализовано (A-20, worklogs/ingest.md#2026-07-04)
def _build_title(rel_path: str, kind: str) -> str:
    parts = rel_path.split("/")
    if kind == "journal":
        return f"Журнал «{parts[-1]}»"
    if kind == "market_analytics":
        topic = next((m.group(1) for p in parts if (m := _TOPIC_RE.match(p))), None)
        if topic is None and "МПГ" in parts:
            topic = "МПГ"
        source = parts[-1]
        return f"Рыночная аналитика {topic}: {source}" if topic else f"Рыночная аналитика: {source}"
    if len(parts) == 1:
        return "Материалы конференций: отдельные документы"
    name = re.sub(r"\s+", " ", parts[-1].strip().rstrip(","))
    return f"Конференция {name}"


# Назначение: русское склонение «файл/файла/файлов» по числу.
# Уровень: ✅ реализовано (A-20, worklogs/ingest.md#2026-07-04)
def _plural_files(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "файл"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "файла"
    return "файлов"


# Назначение: RU-строка для эмбеддинга — title + диапазон лет + число файлов +
#   стандартная пометка «не индексировано» (панель рекомендаций должна честно
#   сообщать, что это указатель на папку, а не проиндексированный источник).
# Уровень: ✅ реализовано (A-20, worklogs/ingest.md#2026-07-04)
def _build_description(title: str, year_from: int | None, year_to: int | None, n_files: int) -> str:
    if year_from and year_to and year_from != year_to:
        years_part = f"выпуски {year_from}–{year_to}"
    elif year_from:
        years_part = f"{year_from} год"
    else:
        years_part = "год не определён"
    return (
        f"{title}, {years_part}, {n_files} {_plural_files(n_files)}. "
        "Содержимое не индексировано, метаданные по названию."
    )


# ─── build_catalog_entries ─────────────────────────────────────────────────
# Назначение: полный скан data/Журналы + data/Материалы конференций →
#   contracts.CatalogEntry (без embedding) — карточки-указатели для панели
#   рекомендаций UI.
# Входные связи: файловая система data/ (только чтение)
# Выходные данные: list[contracts.CatalogEntry], embedding=None
# Уровень: ✅ реализовано (A-20, worklogs/ingest.md#2026-07-04)
def build_catalog_entries(data_dir: Path = DATA_DIR, logger=None) -> list[CatalogEntry]:
    card_dirs = discover_card_dirs(data_dir)
    card_dir_set = set(card_dirs)
    entries: list[CatalogEntry] = []
    for card_dir in card_dirs:
        rel_path = str(card_dir.relative_to(data_dir))
        n_files, years = _scan_own(card_dir, card_dir_set)
        years += _years_in_text(rel_path)  # год мог остаться в пути карточки, не в файлах/поддиректориях
        if n_files == 0:
            if logger is not None:
                log_event(logger, stage="catalog", event=CATALOG_EMPTY_CARD, level="WARNING",
                           detail=f"path={rel_path} — 0 файлов в собственной области, карточка пропущена")
            continue
        year_from = min(years) if years else None
        year_to = max(years) if years else None
        kind = _classify_kind(rel_path)
        title = _build_title(rel_path, kind)
        description = _build_description(title, year_from, year_to, n_files)
        entries.append(CatalogEntry(
            catalog_id=_make_catalog_id(rel_path),
            path=rel_path,
            title=title,
            kind=kind,
            year_from=year_from,
            year_to=year_to,
            n_files=n_files,
            description=description,
        ))
    return entries


# ─── embed_catalog_entries ─────────────────────────────────────────────────
# Назначение: считает эмбеддинги description всех карточек через
#   search.embeddings.embed_texts (один батч — карточек мало, ~30-90);
#   сбой Ollama не роняет прогон — карточки остаются с embedding=None + ERROR
#   в лог (панель рекомендаций деградирует до текстового списка).
# Входные связи: list[contracts.CatalogEntry] (build_catalog_entries)
# Выходные данные: тот же список — embedding проставлен на месте (мутация)
# Уровень: ✅ реализовано (A-20, worklogs/ingest.md#2026-07-04)
def embed_catalog_entries(entries: list[CatalogEntry], logger=None) -> list[CatalogEntry]:
    if not entries:
        return entries
    texts = [e.description for e in entries]
    last_error: Exception | None = None
    for attempt in range(1, 3):  # 2 попытки батчем целиком — карточек мало, изоляция по одной не нужна
        try:
            vectors = embed_texts(texts)
            for entry, vec in zip(entries, vectors):
                entry.embedding = vec
            return entries
        except EmbeddingAPIError as exc:
            last_error = exc
            if logger is not None:
                log_event(logger, stage="catalog", event=CATALOG_EMBED_FAILED, level="WARNING",
                           detail=f"попытка={attempt}/2: {str(exc)[:500]}")
    if logger is not None:
        log_event(logger, stage="catalog", event=CATALOG_EMBED_FAILED, level="ERROR",
                   detail=f"эмбеддинг каталога не получен после 2 попыток: {str(last_error)[:500]}")
    return entries


# ─── write_catalog_jsonl ───────────────────────────────────────────────────
# Назначение: перезаписывает data/processed/catalog.jsonl целиком (объём мал,
#   перегенерация дешевле инкрементального дозаписывания).
# Входные связи: list[contracts.CatalogEntry]
# Выходные данные: нет (побочный эффект — файл на диске)
# Уровень: ✅ реализовано (A-20, worklogs/ingest.md#2026-07-04)
def write_catalog_jsonl(entries: list[CatalogEntry], output_path: Path = CATALOG_OUTPUT_PATH) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(entry.model_dump_json() + "\n")


# ─── main ───────────────────────────────────────────────────────────────────
# Назначение: CLI полного прогона A-20 — скан → (эмбеддинги → jsonl → Neo4j),
#   с флагами --dry-run (только скан) и --no-load (без записи в Neo4j).
# Входные связи: аргументы командной строки --dry-run/--no-load
# Выходные данные: нет (побочный эффект — файлы/Neo4j + печать JSON-сводки)
# Уровень: ✅ реализовано (A-20, worklogs/ingest.md#2026-07-04)
def main() -> None:
    parser = argparse.ArgumentParser(description="Каталожный слой Журналы/Материалы конференций (A-20)")
    parser.add_argument("--dry-run", action="store_true", help="только скан + карточки, без эмбеддингов и Neo4j")
    parser.add_argument("--no-load", action="store_true", help="карточки + эмбеддинги в jsonl, без Neo4j")
    args = parser.parse_args()

    run_id = new_run_id("catalog_")
    logger = get_logger("ingest", run_id)

    entries = build_catalog_entries(logger=logger)
    by_kind = {k: sum(1 for e in entries if e.kind == k) for k in ("journal", "conference", "market_analytics")}
    n_with_years = sum(1 for e in entries if e.year_from is not None)
    log_event(logger, stage="catalog", event="scan_complete",
              detail=f"n_entries={len(entries)} n_with_years={n_with_years} by_kind={by_kind}")

    if args.dry_run:
        summary = {
            "n_entries": len(entries), "n_with_years": n_with_years, "by_kind": by_kind,
            "examples": [e.model_dump(exclude={"embedding"}) for e in entries[:5]],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    entries = embed_catalog_entries(entries, logger=logger)
    write_catalog_jsonl(entries)
    n_embedded = sum(1 for e in entries if e.embedding is not None)
    log_event(logger, stage="catalog", event="embed_complete", detail=f"n_embedded={n_embedded}/{len(entries)}")

    if args.no_load:
        print(json.dumps({"n_entries": len(entries), "n_embedded": n_embedded}, ensure_ascii=False, indent=2))
        return

    driver = catalog_loader.get_driver()
    try:
        catalog_loader.ensure_catalog_constraint(driver)
        n_loaded = catalog_loader.load_catalog_entries(driver, entries)
        dims = {len(e.embedding) for e in entries if e.embedding}
        dimension = next(iter(dims)) if dims else None
        if dimension is not None:
            catalog_loader.ensure_catalog_vector_index(driver, dimension)
            log_event(logger, stage="catalog", event="vector_index_ready", detail=f"dim={dimension}")
        report = catalog_loader.self_check(driver)
        log_event(logger, stage="catalog", event="load_complete", detail=json.dumps(report, ensure_ascii=False))
        summary = {
            "n_entries": len(entries), "n_embedded": n_embedded, "n_loaded": n_loaded,
            "dimension": dimension, **report,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
