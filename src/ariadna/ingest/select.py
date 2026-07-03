"""Отбор целевых документов под 4 эталонных запроса жюри + флаг is_core.

Вход: `data/processed/meta.jsonl` (DocumentMeta), `data/processed/texts.jsonl`
(DocumentText) — оба произведены A-02 (`ingest.pipeline`). Выход:
1) `meta.jsonl` перезаписан на месте (атомарно, tmp+rename) с `is_core=True`
   для всех документов из папок ядра (config.CORE_FOLDERS) — формат DocumentMeta
   не меняется; 2) новый файл `data/processed/targets.jsonl` — внутренний
   артефакт модуля ingest (НЕ контракт из contracts.py), см. формат ниже.
Зависимости: только stdlib + contracts/config/logutil этого же модуля.
Инвариант: поиск по ключевым словам — простые "стемы" (совпадение начала
слова), без морфологических библиотек (см. CLAUDE.md задача A-03).
Паспорт: docs/dev/modules/ingest.md.

Формат targets.jsonl (одна строка — пара документ×тема; пишется, только
если найдено ≥1 совпадение — нулевые пары не материализуются, ноль по теме
виден по сводке в логе):
    {"doc_id": str, "topic": str, "matched_keywords": [str, ...], "n_hits": int}

Точка входа: `python -m ariadna.ingest.select`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ariadna.contracts import DocumentMeta, DocumentText
from ariadna.ingest.config import CORE_FOLDERS, PROCESSED_DIR
from ariadna.logutil import get_logger, log_event, new_run_id

# ═══════════════════════ Ключевые слова 4 эталонных тем (TASK.md) ═══════════════════════
# Паттерн — "начало слова" (см. _stem_pattern): покрывает русские словоформы
# (обессоливание/обессоленный/обессоливания) без библиотек лемматизации.

TOPIC_DESALINATION = "desalination"
TOPIC_CATHOLYTE = "catholyte"
TOPIC_MATTE_SLAG_PGM = "matte_slag_pgm"
TOPIC_MINE_WATER = "mine_water"

TOPIC_KEYWORDS: dict[str, list[str]] = {
    # Запрос 1 (TASK.md): обессоливание/опреснение воды для обогатительной фабрики;
    # сульфаты/хлориды — показатели качества воды из формулировки запроса.
    # "сухой остаток"/"сух" исключены: на корпусе фраза как термин качества воды не
    # встретилась ни разу, а короткий стем "сух" ловит идиому «в сухом остатке»
    # (= «в итоге») — проверено выборкой, ложное срабатывание. "осмос" — короче и
    # надёжнее полной фразы "обратный осмос" (та не матчит все падежные формы).
    TOPIC_DESALINATION: [
        "обессол", "опресн", "деминерализ", "сульфат", "хлорид",
        "осмос", "desalination", "reverse osmosis",
    ],
    # Запрос 2 (TASK.md): циркуляция католита при электроэкстракции никеля.
    # "никель" намеренно не включён отдельным словом — слишком общий термин для
    # корпуса горно-металлургической тематики (даст ложный шум почти по всем
    # документам); вместо этого — составные термины с указанием на контекст
    # электролиза (анолит — типовая парная сущность рядом с католитом).
    TOPIC_CATHOLYTE: [
        "католит", "электроэкстракц", "electrowinning", "catholyte",
        "циркуляция электролита", "никелевый электролит", "катодный никель",
        "анолит",
    ],
    # Запрос 3 (TASK.md): распределение Au, Ag и МПГ между штейном и шлаком.
    # "Au"/"Ag" как голые химические символы не включены — 2-буквенные токены дают
    # слишком много ложных совпадений внутри других слов/аббревиатур; вместо них —
    # полные русские/английские названия металлов.
    # "золот"/"серебр" (не "золото"/"серебро") — короткий стем нужен, чтобы ловить
    # косвенные падежи ("золота", "серебром"), которые полное словоформа-слово не матчит.
    TOPIC_MATTE_SLAG_PGM: [
        "штейн", "шлак", "мпг", "платинов", "платиноид", "золот", "серебр",
        "matte", "slag", "pgm", "распределение металлов",
    ],
    # Запрос 4 (TASK.md): закачка шахтных вод в глубокие горизонты.
    # Голое "injection" исключено — дало ложные срабатывания на корпусе ("pneumatic
    # injection" в пирометаллургии, "metal injection molding" в порошковой металлургии).
    # "шахтн"/"подземн"/"горизонт"/"скважин"/"дренаж" тоже исключены отдельными словами —
    # проверка на корпусе показала, что это общие горно-технические термины (шахтная
    # печь, шахтный ствол, шахтная крепь, горный горизонт как уровень отработки и т.п.),
    # не специфичные для темы шахтных вод, и сильно размывают тему. "закачка"/
    # "глубокие горизонты"/"подземные воды"/"обратная закачка" как точные словоформы
    # ни разу не встретились (0 совпадений) — заменены на глагольный стем "закачив"
    # (закачивание/закачивать), который реально есть в корпусе.
    TOPIC_MINE_WATER: [
        "шахтные воды", "рудничн", "водоотлив", "закачив", "нагнетан",
        "mine water", "water injection", "underground injection",
    ],
}

_MIN_HITS_FOR_TARGET = 1  # ≥1 совпадение стема — пара документ×тема попадает в targets.jsonl

# Символ, который regex не должен считать частью соседнего слова (RU+EN буквы, цифры) —
# используется как левая граница для псевдо-стемминга (см. _stem_pattern).
_WORD_CHARS = r"а-яёА-ЯЁa-zA-Z0-9"


# Назначение: компилирует regex "начало слова" для одного ключевого слова/фразы —
#   заменяет морфологический анализ простым совпадением по префиксу.
# Уровень: ✅ реализовано (A-03, worklogs/ingest.md#2026-07-03)
def _stem_pattern(keyword: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![{_WORD_CHARS}]){re.escape(keyword)}", re.IGNORECASE)


# Скомпилированные паттерны на модуль — поиск идёт по всем 177 документам ядра,
# компиляция один раз на процесс дешевле, чем на каждый документ.
_TOPIC_PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    topic: [(kw, _stem_pattern(kw)) for kw in keywords]
    for topic, keywords in TOPIC_KEYWORDS.items()
}


# ─── set_core_flag ────────────────────────────────────────────────────
# Назначение: проставляет is_core=True в meta.jsonl для документов из папок
#   ядра (config.CORE_FOLDERS); перезапись файла атомарная (tmp + rename),
#   формат DocumentMeta не меняется. Идемпотентно — повторный запуск безопасен.
# Входные связи: data/processed/meta.jsonl (DocumentMeta), config.CORE_FOLDERS
# Выходные данные: int — сколько записей было обновлено (0 = уже было True)
# Уровень: ✅ реализовано (A-03, worklogs/ingest.md#2026-07-03)
def set_core_flag(processed_dir: Path = PROCESSED_DIR) -> int:
    meta_path = processed_dir / "meta.jsonl"
    rows: list[DocumentMeta] = []
    updated = 0
    with open(meta_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            meta = DocumentMeta.model_validate_json(line)
            if meta.source_folder in CORE_FOLDERS and not meta.is_core:
                meta.is_core = True
                updated += 1
            rows.append(meta)

    tmp_path = meta_path.with_name(meta_path.name + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        for meta in rows:
            f.write(meta.model_dump_json() + "\n")
    tmp_path.replace(meta_path)  # атомарная замена — без окна с частично записанным файлом
    return updated


# Назначение: считает совпадения ключевых слов темы в тексте одного документа.
# Уровень: ✅ реализовано (A-03, worklogs/ingest.md#2026-07-03)
def _count_topic_hits(text: str, patterns: list[tuple[str, re.Pattern[str]]]) -> tuple[list[str], int]:
    matched: list[str] = []
    total = 0
    for keyword, pattern in patterns:
        n = len(pattern.findall(text))
        if n:
            matched.append(keyword)
            total += n
    return matched, total


# ─── find_target_documents ──────────────────────────────────────────────
# Назначение: находит документы, релевантные каждой из 4 эталонных тем жюри,
#   по вхождению ключевых слов/стемов в нормализованный текст (texts.jsonl).
# Входные связи: data/processed/texts.jsonl (DocumentText), TOPIC_KEYWORDS
# Выходные данные: (targets, topic_counts) — targets: list[dict] формата
#   targets.jsonl (только пары с n_hits>0); topic_counts: dict[topic, int] —
#   сколько документов нашлось по теме (для сводки в лог/отчёт)
# Уровень: ✅ реализовано (A-03, worklogs/ingest.md#2026-07-03)
def find_target_documents(processed_dir: Path = PROCESSED_DIR) -> tuple[list[dict], dict[str, int]]:
    texts_path = processed_dir / "texts.jsonl"
    targets: list[dict] = []
    topic_counts: dict[str, int] = {topic: 0 for topic in TOPIC_KEYWORDS}

    with open(texts_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            doc = DocumentText.model_validate_json(line)
            for topic, patterns in _TOPIC_PATTERNS.items():
                matched, n_hits = _count_topic_hits(doc.text, patterns)
                if n_hits >= _MIN_HITS_FOR_TARGET:
                    targets.append({
                        "doc_id": doc.doc_id,
                        "topic": topic,
                        "matched_keywords": matched,
                        "n_hits": n_hits,
                    })
                    topic_counts[topic] += 1

    return targets, topic_counts


# ─── run_selection ───────────────────────────────────────────────────────
# Назначение: полный прогон A-03 — проставляет is_core, ищет целевые документы
#   по 4 темам, пишет targets.jsonl, логирует сводку (WARN, если тема дала 0).
# Входные связи: set_core_flag, find_target_documents; ariadna.logutil
# Выходные данные: dict со сводными цифрами прогона (для отчёта агента)
# Уровень: ✅ реализовано (A-03, worklogs/ingest.md#2026-07-03)
def run_selection(processed_dir: Path = PROCESSED_DIR, run_id: str | None = None) -> dict:
    run_id = run_id or new_run_id("select_")
    logger = get_logger("ingest", run_id)

    updated = set_core_flag(processed_dir)
    log_event(
        logger, stage="select", event="core_flag_set",
        detail=f"updated={updated} (0 = уже было is_core=True для всех документов ядра)",
    )

    targets, topic_counts = find_target_documents(processed_dir)
    targets_path = processed_dir / "targets.jsonl"
    with open(targets_path, "w", encoding="utf-8") as f:
        for row in targets:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    for topic, count in topic_counts.items():
        if count == 0:
            log_event(
                logger, stage="select", event="INGEST-003", level="WARNING",
                detail=f"topic={topic}: 0 документов — дыра в корпусе перед демо",
            )
        else:
            log_event(logger, stage="select", event="topic_summary", detail=f"topic={topic} n_docs={count}")

    stats = {"run_id": run_id, "core_updated": updated, "n_targets": len(targets), "topic_counts": topic_counts}
    log_event(logger, stage="select", event="run_complete", detail=json.dumps(topic_counts, ensure_ascii=False))
    return stats


# ─── main ─────────────────────────────────────────────────────────────────
# Назначение: CLI-точка входа A-03; печатает сводку в stdout для верификации.
# Входные связи: аргументов командной строки нет — конфигурация через config.py
# Выходные данные: нет (побочный эффект — meta.jsonl/targets.jsonl + печать сводки)
# Уровень: ✅ реализовано (A-03, worklogs/ingest.md#2026-07-03)
def main() -> None:
    stats = run_selection()
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
