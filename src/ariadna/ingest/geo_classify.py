"""Гео-разметка документов правилами (A-22): маркеры RU/foreign по вхождению,
без LLM — готовит вход для последующей Haiku-волны оркестратора по unknown-хвосту.

Вход: `data/processed/meta.jsonl` (contracts.DocumentMeta, geography="unknown" у
всех 177 строк), `data/processed/chunks.jsonl` (contracts.Chunk, текст без
эмбеддингов — гео-классификации векторы не нужны). Выход:
`data/processed/doc_geography.jsonl` — по строке на документ: `doc_id, path,
geography ("ru"|"foreign"|"global"|"unknown"), method ("rules"|"unknown"),
ru_hits, foreign_hits, evidence` (+ `snippet` у unknown-строк — title + начало
текста первого содержательного чанка, вход Haiku-волны).
Зависимости: только `ariadna.contracts` (Geography) + stdlib (re/json).
Инвариант: НЕ пишет meta.jsonl — единый источник геопризнака после этой задачи
`data/processed/doc_geography.jsonl`; слияние geography в meta.jsonl (после
LLM-доразметки unknown-хвоста) — задача оркестратора, не этого модуля.
Паспорт: docs/dev/modules/ingest.md (A-22).
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from ariadna.contracts import DocumentMeta, Geography
from ariadna.ingest.config import PROCESSED_DIR
from ariadna.logutil import get_logger, log_event, new_run_id

DEFAULT_META_PATH = PROCESSED_DIR / "meta.jsonl"
DEFAULT_CHUNKS_PATH = PROCESSED_DIR / "chunks.jsonl"
DEFAULT_OUTPUT_PATH = PROCESSED_DIR / "doc_geography.jsonl"

# Гео-разметка документа не удалась правилами (обе стороны < MIN_HITS) —
# doc_id уходит в unknown-хвост Haiku-волны оркестратора, не блокирует прогон.
INGEST_GEO_UNKNOWN = "INGEST-006"

# Сколько РАЗНЫХ маркеров одной стороны нужно набрать, чтобы считать сторону
# подтверждённой (не число вхождений — единичный маркер, повторённый много раз
# в длинном документе, не даёт более сильного сигнала, чем 3 РАЗНЫХ маркера;
# см. worklogs/ingest.md — эмпирический подбор: 3 даёт unknown-хвост 17/177 и
# ни одной явной ошибки на выборочной проверке 20 документов).
MIN_HITS = 3

# Сколько символов начала первого содержательного чанка класть в snippet
# unknown-документов — вход Haiku-волны оркестратора (промпт не должен быть
# избыточно длинным, но должен содержать достаточно контекста для гео-решения).
SNIPPET_CHARS = 1200

# Сколько маркеров-свидетельств класть в evidence (топ по порядку словаря,
# не по частоте — частоту (число вхождений) мы сознательно не считаем, см.
# докстринг _count_hits: разные маркеры считаются РАВНОЗНАЧНО, по факту
# присутствия, не по числу повторов).
MAX_EVIDENCE_MARKERS = 5

# ─── RU-маркеры ────────────────────────────────────────────────────────────
# Отечественная практика: топонимы никелевого/медного Кольского и Норильского
# промрайонов + профильные НИИ + общие маркеры российской принадлежности
# ("росси" — стем, покрывает Россия/России/российский; "рф" — акроним).
# Калибровка на живом корпусе (data/processed/chunks.jsonl, worklogs/ingest.md):
# "рф"/"чили" без границы слова массово ловят подстроку внутри случайных слов
# ("интеРФейс", "полуЧИЛИсь") — см. _MARKER_PATTERN (левая граница слова).
RU_MARKERS: tuple[str, ...] = (
    "норильск", "талнах", "заполярн", "кольская гмк", "надеждинск", "мончегорск",
    "норильский никель", "гмк", "гипроникель", "иргиредмет", "вниицветмет", "внии",
    "отечественн", "росси", "рф", "урал", "сибир", "красноярск", "мурманск",
    "печенга", "печенганикель", "забайкал", "уралэлектромедь", "среднеуральск",
    "кыштым", "кировоград", "ревда", "дальневосточ", "иркутск", "чита",
    "медногорск", "верхнепышминск",
)

# ─── Foreign-маркеры ─────────────────────────────────────────────────────────
# Зарубежная практика: страны/регионы + известные зарубежные горно-
# металлургические компании и технологии (Outotec/Outokumpu, Ausmelt/Isasmelt —
# зарубежные лицензируемые технологии плавки; Sudbury/Voisey Bay — рудники
# Канады). Латинские названия компаний — как встречаются в русскоязычном
# тексте (в кавычках/на латинице), см. живую выборку worklogs/ingest.md.
FOREIGN_MARKERS: tuple[str, ...] = (
    "зарубежн", "мировой опыт", "мировая практика", "финлянд", "канад", "китай", "кнр",
    "австрал", "чили", "юар", "индонез", "ботсван", "зимбабв", "норвег", "швеци",
    "бразили", "перу", "замби", "конго", "казахст", "монголи", "намиби", "марокко",
    "glencore", "vale", "bhp", "outotec", "outokumpu", "metso", "boliden", "sudbury",
    "voisey", "jinchuan", "sherritt", "ausmelt", "isasmelt", "xstrata", "rio tinto",
    "freeport", "codelco", "kghm", "anglo american", "teck resources", "ivanhoe",
    "first quantum", "antofagasta", "aurubis", "umicore", "saimm",
)


# Назначение: маркер -> скомпилированный regex с ЛЕВОЙ границей слова (\b перед
#   маркером, без \b после) — сохраняет стем-семантику ("канад" ловит канадский/
#   канадской/Канада) и одновременно отсекает вхождение маркера ВНУТРИ другого
#   слова (см. докстринг модуля: "рф" внутри "интерфейс", "чили" внутри
#   "получились/увеличились/заключили" — без границы слева таких ложных
#   срабатываний на живом корпусе десятки на документ).
# Уровень: ✅ реализовано (A-22, worklogs/ingest.md)
def _compile_markers(markers: tuple[str, ...]) -> list[tuple[str, re.Pattern]]:
    return [(m, re.compile(r"\b" + re.escape(m))) for m in markers]


_RU_PATTERNS = _compile_markers(RU_MARKERS)
_FOREIGN_PATTERNS = _compile_markers(FOREIGN_MARKERS)


# ─── _count_hits ─────────────────────────────────────────────────────────────
# Назначение: число РАЗНЫХ маркеров словаря, встреченных в тексте (не число
#   вхождений — см. константу MIN_HITS), + список найденных маркеров в порядке
#   словаря (для evidence).
# Входные связи: lower-case текст документа (title + конкатенация чанков)
# Выходные данные: (hits: int, found_markers: list[str])
# Уровень: ✅ реализовано (A-22, worklogs/ingest.md)
def _count_hits(text: str, patterns: list[tuple[str, re.Pattern]]) -> tuple[int, list[str]]:
    found = [marker for marker, pattern in patterns if pattern.search(text)]
    return len(found), found


# ─── classify_text ───────────────────────────────────────────────────────────
# Назначение: гео-признак ОДНОГО текста (title+chunks уже склеены и приведены
#   к нижнему регистру вызывающей стороной) по числу совпавших маркеров —
#   обе стороны >= MIN_HITS -> global (У-2, «обе практики в одном источнике»),
#   одна >= MIN_HITS -> её сторона, обе < MIN_HITS -> unknown.
# Входные связи: lower-case текст документа
# Выходные данные: (geography, ru_hits, foreign_hits, evidence) — evidence до
#   MAX_EVIDENCE_MARKERS маркеров ОБЕИХ сторон, найденных в тексте
# Уровень: ✅ реализовано (A-22, worklogs/ingest.md)
def classify_text(text_lower: str) -> tuple[Geography, int, int, list[str]]:
    ru_hits, ru_found = _count_hits(text_lower, _RU_PATTERNS)
    foreign_hits, foreign_found = _count_hits(text_lower, _FOREIGN_PATTERNS)

    if ru_hits >= MIN_HITS and foreign_hits >= MIN_HITS:
        geography = Geography.GLOBAL
    elif ru_hits >= MIN_HITS:
        geography = Geography.RU
    elif foreign_hits >= MIN_HITS:
        geography = Geography.FOREIGN
    else:
        geography = Geography.UNKNOWN

    evidence = (ru_found[:MAX_EVIDENCE_MARKERS] + foreign_found[:MAX_EVIDENCE_MARKERS])
    return geography, ru_hits, foreign_hits, evidence


# ─── _load_chunk_texts_by_doc ─────────────────────────────────────────────────
# Назначение: doc_id -> список текстов чанков В ПОРЯДКЕ ПОЯВЛЕНИЯ в chunks.jsonl
#   (файл уже отсортирован по документу/порядковому номеру чанка, см. ingest/
#   chunk.py) — нужен и для конкатенации (гео-классификация), и для snippet
#   (первый чанк документа, unknown-хвост).
# Входные связи: путь к chunks.jsonl (contracts.Chunk)
# Выходные данные: dict[doc_id, list[str]]
# Уровень: ✅ реализовано (A-22, worklogs/ingest.md)
def _load_chunk_texts_by_doc(chunks_path: Path) -> dict[str, list[str]]:
    texts_by_doc: dict[str, list[str]] = {}
    with chunks_path.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            texts_by_doc.setdefault(row["doc_id"], []).append(row["text"])
    return texts_by_doc


# ─── classify_documents ───────────────────────────────────────────────────────
# Назначение: гео-разметка всего корпуса — по документу: title + конкатенация
#   текстов его чанков (нижний регистр) -> classify_text; unknown-документы
#   получают snippet (title + первые SNIPPET_CHARS символов первого чанка) —
#   вход Haiku-волны оркестратора. Документы БЕЗ чанков (в текущем корпусе не
#   встречается, meta.jsonl/chunks.jsonl согласованы, см. worklog) — hits=0,
#   geography=unknown, snippet = только title (не падает).
# Входные связи: meta_path (contracts.DocumentMeta), chunks_path (contracts.Chunk)
# Выходные данные: list[dict] — по одной записи на документ, ключи см. докстринг
#   модуля (doc_id/path/geography/method/ru_hits/foreign_hits/evidence[/snippet])
# Уровень: ✅ реализовано (A-22, worklogs/ingest.md)
def classify_documents(meta_path: Path = DEFAULT_META_PATH, chunks_path: Path = DEFAULT_CHUNKS_PATH) -> list[dict]:
    chunk_texts = _load_chunk_texts_by_doc(chunks_path)
    rows: list[dict] = []
    with meta_path.open(encoding="utf-8") as f:
        for line in f:
            meta = DocumentMeta.model_validate_json(line)
            doc_chunks = chunk_texts.get(meta.doc_id, [])
            full_text = (meta.title + "\n" + "\n".join(doc_chunks)).lower()
            geography, ru_hits, foreign_hits, evidence = classify_text(full_text)

            row = {
                "doc_id": meta.doc_id,
                "path": meta.path,
                "geography": geography.value,
                "method": "rules" if geography != Geography.UNKNOWN else "unknown",
                "ru_hits": ru_hits,
                "foreign_hits": foreign_hits,
                "evidence": evidence,
            }
            if geography == Geography.UNKNOWN:
                first_chunk = doc_chunks[0] if doc_chunks else ""
                row["snippet"] = (meta.title + "\n" + first_chunk)[:SNIPPET_CHARS]
            rows.append(row)
    return rows


# ─── write_doc_geography ──────────────────────────────────────────────────────
# Назначение: пишет rows в JSONL (одна строка на документ) — вход graph/
#   doc_geography_loader.py и Haiku-волны оркестратора.
# Входные связи: list[dict] из classify_documents
# Выходные данные: нет (побочный эффект — файл на диске)
# Уровень: ✅ реализовано (A-22, worklogs/ingest.md)
def write_doc_geography(rows: list[dict], output_path: Path = DEFAULT_OUTPUT_PATH) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ─── main ─────────────────────────────────────────────────────────────────────
# Назначение: CLI `python -m ariadna.ingest.geo_classify [--json]` — правиловая
#   гео-разметка корпуса, печатает распределение geography (ru/foreign/global/
#   unknown) + число unknown (вход Haiku-волны).
# Входные связи: sys.argv — --meta-path/--chunks-path/--output (пути), --json
# Выходные данные: нет (побочный эффект — doc_geography.jsonl + печать сводки)
# Уровень: ✅ реализовано (A-22, worklogs/ingest.md)
def main() -> None:
    parser = argparse.ArgumentParser(description="Гео-разметка документов правилами (A-22)")
    parser.add_argument("--meta-path", type=Path, default=DEFAULT_META_PATH)
    parser.add_argument("--chunks-path", type=Path, default=DEFAULT_CHUNKS_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--json", action="store_true", help="печать полного списка строк в JSON")
    args = parser.parse_args()

    run_id = new_run_id("geo_classify_")
    logger = get_logger("ingest", run_id)
    log_event(logger, stage="geo_classify", event="started", detail=f"min_hits={MIN_HITS}")

    rows = classify_documents(args.meta_path, args.chunks_path)
    write_doc_geography(rows, args.output)

    dist = Counter(row["geography"] for row in rows)
    n_unknown = dist.get(Geography.UNKNOWN.value, 0)
    log_event(
        logger, stage="geo_classify", event="finished",
        detail=f"n_docs={len(rows)} dist={dict(dist)} n_unknown={n_unknown}",
    )
    if n_unknown:
        log_event(
            logger, stage="geo_classify", event=INGEST_GEO_UNKNOWN, level="WARNING",
            detail=f"n_unknown={n_unknown} — вход Haiku-волны оркестратора",
        )

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    print(f"Гео-разметка: {len(rows)} документов (MIN_HITS={MIN_HITS})")
    print(f"Распределение: {dict(dist)}")
    print(f"unknown (вход Haiku-волны): {n_unknown}")


if __name__ == "__main__":
    main()
