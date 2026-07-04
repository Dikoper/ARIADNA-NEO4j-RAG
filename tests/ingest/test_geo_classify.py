"""Тесты ingest/geo_classify.py (A-22): гео-разметка документов правилами
(без LLM) — словари RU/foreign маркеров, пороги MIN_HITS, классификация
ru/foreign/global/unknown, snippet для unknown-хвоста, CLI.

Офлайновые тесты — синтетические meta.jsonl/chunks.jsonl во `tmp_path`
(fixtures/ этого модуля НЕ трогаем — генерируются отдельной задачей, известные
грабли, см. постановку A-22). Живых Neo4j-тестов нет — geo_classify только
читает JSONL и пишет JSONL, Neo4j не участвует (загрузчик — отдельный модуль,
tests/graph/test_doc_geography_loader.py).
"""
from __future__ import annotations

import json
import sys

import pytest

from ariadna.contracts import Geography
from ariadna.ingest import geo_classify


# ══════════════════════ classify_text — пороги/классы ══════════════════════

# Назначение: >= MIN_HITS РАЗНЫХ RU-маркеров, < MIN_HITS foreign -> Geography.RU.
# Уровень: ✅ реализовано (module-tester A-22)
def test_classify_text_ru_when_only_ru_markers_pass_threshold():
    text = "норильск талнах мончегорск зарубежн"  # 3 ru, 1 foreign
    geography, ru_hits, foreign_hits, evidence = geo_classify.classify_text(text)
    assert geography == Geography.RU
    assert ru_hits == 3
    assert foreign_hits == 1
    assert set(evidence) == {"норильск", "талнах", "мончегорск", "зарубежн"}


# Назначение: >= MIN_HITS РАЗНЫХ foreign-маркеров, < MIN_HITS ru -> Geography.FOREIGN.
# Уровень: ✅ реализовано (module-tester A-22)
def test_classify_text_foreign_when_only_foreign_markers_pass_threshold():
    text = "канада австралия чили норильск"  # 3 foreign, 1 ru
    geography, ru_hits, foreign_hits, evidence = geo_classify.classify_text(text)
    assert geography == Geography.FOREIGN
    assert ru_hits == 1
    assert foreign_hits == 3


# Назначение: обе стороны >= MIN_HITS -> Geography.GLOBAL (У-2, «обе практики
#   в одном источнике»).
# Уровень: ✅ реализовано (module-tester A-22)
def test_classify_text_global_when_both_sides_pass_threshold():
    text = "норильск талнах мончегорск канада австралия чили"  # 3 ru, 3 foreign
    geography, ru_hits, foreign_hits, evidence = geo_classify.classify_text(text)
    assert geography == Geography.GLOBAL
    assert ru_hits == 3
    assert foreign_hits == 3


# Назначение: обе стороны < MIN_HITS -> Geography.UNKNOWN (кандидат Haiku-волны).
# Уровень: ✅ реализовано (module-tester A-22)
def test_classify_text_unknown_when_neither_side_passes_threshold():
    text = "норильск канада безобидный текст без явных маркеров"  # 1 ru, 1 foreign
    geography, ru_hits, foreign_hits, evidence = geo_classify.classify_text(text)
    assert geography == Geography.UNKNOWN
    assert ru_hits == 1
    assert foreign_hits == 1


# Назначение: текст без единого маркера -> unknown, 0/0, пустой evidence.
# Уровень: ✅ реализовано (module-tester A-22)
def test_classify_text_no_markers_at_all():
    geography, ru_hits, foreign_hits, evidence = geo_classify.classify_text("совершенно нейтральный технический текст")
    assert geography == Geography.UNKNOWN
    assert ru_hits == 0 and foreign_hits == 0
    assert evidence == []


# ══════════════════════ Границы слова (защита от ложных срабатываний) ══════════════════════

# Назначение: маркер "рф" (акроним, 2 символа) не должен матчиться ВНУТРИ
#   другого слова ("интерфейс") — регрессия найденного на живом корпусе бага:
#   substring-поиск без границы слова даёт 95 "хитов" вместо 34 реальных
#   (docs/dev/worklogs/ingest.md#A-22).
# Уровень: ✅ реализовано (module-tester A-22)
def test_marker_rf_does_not_match_inside_unrelated_word():
    text_with_false_positive = "особенности пользовательского интерфейса для управления"
    _, ru_hits, _, evidence = geo_classify.classify_text(text_with_false_positive)
    assert "рф" not in evidence
    assert ru_hits == 0


# Назначение: тот же маркер "рф" ловится, когда встречается как отдельное
#   слово (акроним "РФ") — граница слова не ломает штатное срабатывание.
# Уровень: ✅ реализовано (module-tester A-22)
def test_marker_rf_matches_as_standalone_word():
    _, ru_hits, _, evidence = geo_classify.classify_text("предприятия рф ведут добычу")
    assert "рф" in evidence
    assert ru_hits >= 1


# Назначение: маркер "чили" (стем/страна) не матчится внутри случайных слов
#   ("получились", "увеличились", "заключили" — все содержат подстроку "чили")
#   — тот же принцип границы слова, другой найденный на живых данных случай.
# Уровень: ✅ реализовано (module-tester A-22)
def test_marker_chili_does_not_match_inside_unrelated_words():
    text = "показатели увеличились, параметры получились стабильными, договор заключили"
    _, _, foreign_hits, evidence = geo_classify.classify_text(text)
    assert "чили" not in evidence
    assert foreign_hits == 0


# Назначение: стем-маркеры ("канад", "финлянд", "австрал") ловят словоформы
#   (канадский/канадской/Канада) — левая граница слова не требует границы
#   СПРАВА, иначе стем не матчил бы ни одну инфлексию (см. докстринг _compile_markers).
# Уровень: ✅ реализовано (module-tester A-22)
def test_stem_markers_match_inflected_word_forms():
    _, _, foreign_hits, evidence = geo_classify.classify_text(
        "рудник в канаде, канадский опыт эксплуатации, канадской компании принадлежит"
    )
    assert "канад" in evidence
    assert foreign_hits >= 1


# ══════════════════════ classify_documents / write_doc_geography (интеграция) ══════════════════════

# Назначение: пишет синтетические meta.jsonl (3 документа: ru/foreign/unknown
#   по маркерам) + chunks.jsonl (по 1 чанку на документ) во tmp_path.
# Уровень: ✅ реализовано (module-tester A-22)
def _write_fixture(tmp_path):
    meta_path = tmp_path / "meta.jsonl"
    chunks_path = tmp_path / "chunks.jsonl"

    meta_rows = [
        {"doc_id": "doc-ru", "path": "Обзоры/ru.pdf", "title": "Опыт Норильска"},
        {"doc_id": "doc-foreign", "path": "Обзоры/foreign.pdf", "title": "Обзор зарубежных рудников"},
        {"doc_id": "doc-unknown", "path": "Обзоры/unknown.pdf", "title": "Нейтральный технический документ"},
        {"doc_id": "doc-no-chunks", "path": "Обзоры/no_chunks.pdf", "title": "Документ без чанков"},
    ]
    with meta_path.open("w", encoding="utf-8") as f:
        for row in meta_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    chunk_rows = [
        {"chunk_id": "doc-ru#0", "doc_id": "doc-ru",
         "text": "Норильск Талнах Мончегорск — отечественная практика переработки руды."},
        {"chunk_id": "doc-foreign#0", "doc_id": "doc-foreign",
         "text": "Канада Австралия Чили — зарубежный опыт эксплуатации рудников."},
        {"chunk_id": "doc-unknown#0", "doc_id": "doc-unknown",
         "text": "Общий технический текст без явных гео-маркеров той или иной стороны."},
    ]
    with chunks_path.open("w", encoding="utf-8") as f:
        for row in chunk_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    return meta_path, chunks_path


# Назначение: 4 документа -> 4 строки вывода (в т.ч. документ без чанков —
#   не падает); geography/method согласованы; evidence непуст у решённых.
# Уровень: ✅ реализовано (module-tester A-22)
def test_classify_documents_end_to_end_on_synthetic_fixture(tmp_path):
    meta_path, chunks_path = _write_fixture(tmp_path)
    rows = geo_classify.classify_documents(meta_path, chunks_path)

    assert len(rows) == 4
    by_id = {row["doc_id"]: row for row in rows}

    assert by_id["doc-ru"]["geography"] == Geography.RU.value
    assert by_id["doc-ru"]["method"] == "rules"
    assert by_id["doc-ru"]["evidence"]

    assert by_id["doc-foreign"]["geography"] == Geography.FOREIGN.value
    assert by_id["doc-foreign"]["method"] == "rules"

    assert by_id["doc-unknown"]["geography"] == Geography.UNKNOWN.value
    assert by_id["doc-unknown"]["method"] == "unknown"

    assert by_id["doc-no-chunks"]["geography"] == Geography.UNKNOWN.value
    assert by_id["doc-no-chunks"]["ru_hits"] == 0
    assert by_id["doc-no-chunks"]["foreign_hits"] == 0


# Назначение: snippet — ТОЛЬКО у unknown-строк (вход Haiku-волны), у решённых
#   правилами документов поля snippet нет (не нужно — оркестратор их не трогает).
# Уровень: ✅ реализовано (module-tester A-22)
def test_snippet_field_only_present_for_unknown_rows(tmp_path):
    meta_path, chunks_path = _write_fixture(tmp_path)
    rows = geo_classify.classify_documents(meta_path, chunks_path)
    by_id = {row["doc_id"]: row for row in rows}

    assert "snippet" in by_id["doc-unknown"]
    assert by_id["doc-unknown"]["snippet"].startswith("Нейтральный технический документ")
    assert "snippet" not in by_id["doc-ru"]
    assert "snippet" not in by_id["doc-foreign"]

    # Документ без чанков — snippet собирается хотя бы из title, не падает.
    assert by_id["doc-no-chunks"]["snippet"].startswith("Документ без чанков")


# Назначение: snippet обрезается до SNIPPET_CHARS символов (не раздувает вход
#   Haiku-волны неограниченным текстом документа).
# Уровень: ✅ реализовано (module-tester A-22)
def test_snippet_is_truncated_to_snippet_chars_limit(tmp_path):
    meta_path = tmp_path / "meta.jsonl"
    chunks_path = tmp_path / "chunks.jsonl"
    meta_path.write_text(json.dumps({"doc_id": "doc-long", "path": "x.pdf", "title": "T"}, ensure_ascii=False) + "\n", encoding="utf-8")
    long_text = "а" * 5000
    chunks_path.write_text(
        json.dumps({"chunk_id": "doc-long#0", "doc_id": "doc-long", "text": long_text}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    rows = geo_classify.classify_documents(meta_path, chunks_path)
    assert len(rows[0]["snippet"]) == geo_classify.SNIPPET_CHARS


# Назначение: write_doc_geography пишет ровно len(rows) строк JSONL,
#   round-trip сохраняет все поля.
# Уровень: ✅ реализовано (module-tester A-22)
def test_write_doc_geography_round_trips_rows(tmp_path):
    rows = [
        {"doc_id": "a", "path": "p/a.pdf", "geography": "ru", "method": "rules",
         "ru_hits": 3, "foreign_hits": 0, "evidence": ["норильск"]},
        {"doc_id": "b", "path": "p/b.pdf", "geography": "unknown", "method": "unknown",
         "ru_hits": 0, "foreign_hits": 0, "evidence": [], "snippet": "T\ntext"},
    ]
    output_path = tmp_path / "doc_geography.jsonl"
    geo_classify.write_doc_geography(rows, output_path)

    lines = output_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    loaded = [json.loads(line) for line in lines]
    assert loaded == rows


# ══════════════════════ CLI main() ══════════════════════

# Назначение: CLI пишет doc_geography.jsonl и печатает распределение +
#   n_unknown человекочитаемо (без --json).
# Уровень: ✅ реализовано (module-tester A-22)
def test_main_writes_output_file_and_prints_summary(tmp_path, monkeypatch, capsys):
    meta_path, chunks_path = _write_fixture(tmp_path)
    output_path = tmp_path / "doc_geography.jsonl"
    monkeypatch.setattr(sys, "argv", [
        "geo_classify",
        "--meta-path", str(meta_path),
        "--chunks-path", str(chunks_path),
        "--output", str(output_path),
    ])

    geo_classify.main()

    assert output_path.exists()
    lines = output_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4

    out = capsys.readouterr().out
    assert "Гео-разметка: 4 документов" in out
    assert "unknown" in out


# Назначение: --json печатает список строк (не человекочитаемую сводку).
# Уровень: ✅ реализовано (module-tester A-22)
def test_main_json_flag_prints_full_row_list(tmp_path, monkeypatch, capsys):
    meta_path, chunks_path = _write_fixture(tmp_path)
    output_path = tmp_path / "doc_geography.jsonl"
    monkeypatch.setattr(sys, "argv", [
        "geo_classify",
        "--meta-path", str(meta_path),
        "--chunks-path", str(chunks_path),
        "--output", str(output_path),
        "--json",
    ])

    geo_classify.main()

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert isinstance(payload, list)
    assert len(payload) == 4


# ══════════════════════ Смоук на боевых данных ══════════════════════

# Назначение: боевой прогон classify_documents по data/processed/*.jsonl —
#   ровно 177 строк (177 документов корпуса), unknown-хвост ограничен (цель
#   постановки A-22 — «≤ ~40 документов»), все значения geography валидны.
# Уровень: ✅ реализовано (module-tester A-22)
def test_smoke_classify_documents_on_live_corpus_data():
    if not geo_classify.DEFAULT_META_PATH.exists() or not geo_classify.DEFAULT_CHUNKS_PATH.exists():
        pytest.skip("боевые data/processed/*.jsonl недоступны в этом окружении")

    rows = geo_classify.classify_documents()
    assert len(rows) == 177

    valid_values = {g.value for g in Geography}
    assert all(row["geography"] in valid_values for row in rows)

    n_unknown = sum(1 for row in rows if row["geography"] == Geography.UNKNOWN.value)
    assert n_unknown <= 40
