"""Тесты A-03: `ariadna.ingest.select` — set_core_flag, find_target_documents, run_selection.

Контракт: DocumentMeta/DocumentText (`contracts.py`). `targets.jsonl` — внутренний
формат модуля (не pydantic-модель из contracts.py), формат описан в докстринге
select.py: {doc_id, topic, matched_keywords, n_hits}.

Фикстуры meta.jsonl/texts.jsonl строятся программно во временных директориях
(tmp_path) — данные чисто текстовые (JSONL), физические бинарные фикстуры
(как для PDF/DOCX в A-02) здесь не нужны. Смоук на боевом `data/processed/
targets.jsonl` — только чтение, файлы data/ не меняются.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ariadna import logutil
from ariadna.contracts import DocumentMeta, DocumentText, Geography, Lang
from ariadna.ingest.select import (
    TOPIC_CATHOLYTE,
    TOPIC_DESALINATION,
    TOPIC_KEYWORDS,
    TOPIC_MATTE_SLAG_PGM,
    TOPIC_MINE_WATER,
    find_target_documents,
    run_selection,
    set_core_flag,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _meta_row(doc_id: str, source_folder: str, is_core: bool = False, **overrides) -> dict:
    meta = DocumentMeta(
        doc_id=doc_id,
        path=f"{source_folder}/{doc_id}.pdf",
        source_folder=source_folder,
        is_core=is_core,
        **overrides,
    )
    return meta.model_dump(mode="json")


def _text_row(doc_id: str, text: str) -> dict:
    return DocumentText(doc_id=doc_id, text=text, n_chars=len(text)).model_dump(mode="json")


# ═══════════════════════════ set_core_flag ═══════════════════════════

class TestSetCoreFlag:
    # Назначение: is_core=True проставляется только документам из папок ядра
    #   (CORE_FOLDERS), остальные (Журналы, Материалы конференций) не трогаются;
    #   возвращаемое значение = число реально изменённых строк.
    # Уровень: ✅ реализовано (A-03 tests)
    def test_sets_true_only_for_core_folders(self, tmp_path):
        rows = [
            _meta_row("d1", "Обзоры"),
            _meta_row("d2", "Статьи"),
            _meta_row("d3", "Доклады"),
            _meta_row("d4", "Журналы"),
            _meta_row("d5", "Материалы конференций"),
        ]
        _write_jsonl(tmp_path / "meta.jsonl", rows)

        updated = set_core_flag(tmp_path)

        assert updated == 3
        out = {
            m.doc_id: m
            for m in (
                DocumentMeta.model_validate_json(ln)
                for ln in (tmp_path / "meta.jsonl").read_text(encoding="utf-8").strip().splitlines()
            )
        }
        assert out["d1"].is_core is True
        assert out["d2"].is_core is True
        assert out["d3"].is_core is True
        assert out["d4"].is_core is False
        assert out["d5"].is_core is False

    # Назначение: повторный запуск на уже проставленных флагах — 0 изменений
    #   (идемпотентность), первый запуск считает корректно.
    # Уровень: ✅ реализовано (A-03 tests)
    def test_idempotent_second_run_updates_zero(self, tmp_path):
        rows = [_meta_row("d1", "Обзоры"), _meta_row("d2", "Журналы")]
        _write_jsonl(tmp_path / "meta.jsonl", rows)

        first = set_core_flag(tmp_path)
        second = set_core_flag(tmp_path)

        assert first == 1
        assert second == 0

    # Назначение: поля, не относящиеся к is_core (title/authors/year/lang/
    #   geography), не изменяются перезаписью.
    # Уровень: ✅ реализовано (A-03 tests)
    def test_preserves_other_fields(self, tmp_path):
        rows = [
            _meta_row(
                "d1", "Обзоры",
                title="Т1", authors=["Иванов"], year=2020,
                lang=Lang.EN, geography=Geography.FOREIGN,
            )
        ]
        _write_jsonl(tmp_path / "meta.jsonl", rows)

        set_core_flag(tmp_path)

        lines = (tmp_path / "meta.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        m = DocumentMeta.model_validate_json(lines[0])
        assert m.title == "Т1"
        assert m.authors == ["Иванов"]
        assert m.year == 2020
        assert m.lang == Lang.EN
        assert m.geography == Geography.FOREIGN
        assert m.is_core is True

    # Назначение: каждая строка вывода валидна как DocumentMeta (контракт).
    # Уровень: ✅ реализовано (A-03 tests)
    def test_each_output_row_validates_as_documentmeta(self, tmp_path):
        rows = [_meta_row(f"d{i}", "Обзоры" if i % 2 == 0 else "Журналы") for i in range(6)]
        _write_jsonl(tmp_path / "meta.jsonl", rows)

        set_core_flag(tmp_path)

        for ln in (tmp_path / "meta.jsonl").read_text(encoding="utf-8").strip().splitlines():
            DocumentMeta.model_validate_json(ln)  # не должно бросать ValidationError

    # Назначение: атомарная перезапись — временный файл .tmp не остаётся после
    #   успешного завершения (tmp_path.replace выполнился).
    # Уровень: ✅ реализовано (A-03 tests)
    def test_atomic_no_tmp_file_left_behind(self, tmp_path):
        _write_jsonl(tmp_path / "meta.jsonl", [_meta_row("d1", "Обзоры")])

        set_core_flag(tmp_path)

        assert not (tmp_path / "meta.jsonl.tmp").exists()

    # Назначение: пустые строки во входном meta.jsonl пропускаются, не портят
    #   количество строк на выходе.
    # Уровень: ✅ реализовано (A-03 tests)
    def test_empty_lines_in_input_are_skipped(self, tmp_path):
        content = (
            json.dumps(_meta_row("d1", "Обзоры"), ensure_ascii=False) + "\n"
            "\n"
            + json.dumps(_meta_row("d2", "Журналы"), ensure_ascii=False) + "\n"
        )
        (tmp_path / "meta.jsonl").write_text(content, encoding="utf-8")

        updated = set_core_flag(tmp_path)

        assert updated == 1
        lines = (tmp_path / "meta.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2


# ═══════════════════════════ find_target_documents ═══════════════════════════

class TestFindTargetDocuments:
    # Назначение: разные словоформы одного стема ("закачивания"/"закачивать" →
    #   стем "закачив") оба попадают в тему mine_water.
    # Уровень: ✅ реализовано (A-03 tests)
    def test_word_form_variants_match_same_stem(self, tmp_path):
        rows = [
            _text_row("d1", "Технология закачивания шахтных вод в глубокие горизонты."),
            _text_row("d2", "Метод предполагает закачивать воду постоянно."),
        ]
        _write_jsonl(tmp_path / "texts.jsonl", rows)

        targets, counts = find_target_documents(tmp_path)

        pairs = {(t["doc_id"], t["topic"]) for t in targets}
        assert ("d1", TOPIC_MINE_WATER) in pairs
        assert ("d2", TOPIC_MINE_WATER) in pairs
        assert counts[TOPIC_MINE_WATER] == 2

    # Назначение: косвенные падежи "золота"/"золотом" ловятся коротким стемом
    #   "золот" (а не только словарной формой "золото").
    # Уровень: ✅ реализовано (A-03 tests)
    def test_indirect_case_forms_match_short_stem(self, tmp_path):
        rows = [
            _text_row("d1", "Распределение золота между штейном и шлаком."),
            _text_row("d2", "Извлечение золотом и серебром при плавке."),
        ]
        _write_jsonl(tmp_path / "texts.jsonl", rows)

        targets, _ = find_target_documents(tmp_path)

        topics_d1 = {t["topic"] for t in targets if t["doc_id"] == "d1"}
        topics_d2 = {t["topic"] for t in targets if t["doc_id"] == "d2"}
        assert TOPIC_MATTE_SLAG_PGM in topics_d1
        assert TOPIC_MATTE_SLAG_PGM in topics_d2

    # Назначение: поиск регистронезависим (re.IGNORECASE в _stem_pattern).
    # Уровень: ✅ реализовано (A-03 tests)
    def test_case_insensitive_match(self, tmp_path):
        _write_jsonl(tmp_path / "texts.jsonl", [_text_row("d1", "ШТЕЙН и ШЛАК образуются при плавке.")])

        _, counts = find_target_documents(tmp_path)

        assert counts[TOPIC_MATTE_SLAG_PGM] == 1

    # Назначение: стем НЕ матчится, если непосредственно перед ним стоит
    #   буква/цифра (совпадение — только с начала слова, см. _stem_pattern) —
    #   "надшлаковый" не должен засчитаться в тему matte_slag_pgm по стему "шлак".
    # Уровень: ✅ реализовано (A-03 tests)
    def test_stem_not_matched_when_embedded_mid_word(self, tmp_path):
        _write_jsonl(tmp_path / "texts.jsonl", [_text_row("d1", "надшлаковый слой на поверхности расплава")])

        _, counts = find_target_documents(tmp_path)

        assert counts[TOPIC_MATTE_SLAG_PGM] == 0

    # Назначение: стем матчится, когда слово НАЧИНАЕТСЯ с ключевого слова
    #   ("шлакообразование" начинается с "шлак" — валидное совпадение начала слова).
    # Уровень: ✅ реализовано (A-03 tests)
    def test_stem_matches_at_start_of_compound_word(self, tmp_path):
        _write_jsonl(tmp_path / "texts.jsonl", [_text_row("d1", "процесс шлакообразования при плавке")])

        _, counts = find_target_documents(tmp_path)

        assert counts[TOPIC_MATTE_SLAG_PGM] == 1

    # Назначение: документ, релевантный нескольким темам одновременно, даёт
    #   несколько строк targets (по одной на тему).
    # Уровень: ✅ реализовано (A-03 tests)
    def test_document_with_multiple_topics(self, tmp_path):
        rows = [_text_row("d1", "Обессоливание воды и распределение золота и штейна между шлаком.")]
        _write_jsonl(tmp_path / "texts.jsonl", rows)

        targets, _ = find_target_documents(tmp_path)

        topics = {t["topic"] for t in targets if t["doc_id"] == "d1"}
        assert topics == {TOPIC_DESALINATION, TOPIC_MATTE_SLAG_PGM}

    # Назначение: тема без единого совпадения в корпусе — счётчик 0, targets пуст
    #   (это ветка, приводящая к WARN INGEST-003 в run_selection).
    # Уровень: ✅ реализовано (A-03 tests)
    def test_topic_with_zero_documents(self, tmp_path):
        rows = [_text_row("d1", "Совершенно не относящийся к темам текст про кулинарию.")]
        _write_jsonl(tmp_path / "texts.jsonl", rows)

        targets, counts = find_target_documents(tmp_path)

        assert targets == []
        assert all(c == 0 for c in counts.values())
        assert set(counts.keys()) == set(TOPIC_KEYWORDS.keys())

    # Назначение: документ без совпадений вообще НЕ материализуется в targets
    #   (контракт: только пары с n_hits>=1 попадают в список/файл).
    # Уровень: ✅ реализовано (A-03 tests)
    def test_zero_hit_doc_not_materialized_in_targets(self, tmp_path):
        rows = [
            _text_row("d1", "Обессоливание воды на фабрике."),
            _text_row("d2", "Текст, не относящийся ни к одной из тем жюри."),
        ]
        _write_jsonl(tmp_path / "texts.jsonl", rows)

        targets, _ = find_target_documents(tmp_path)

        doc_ids = {t["doc_id"] for t in targets}
        assert "d1" in doc_ids
        assert "d2" not in doc_ids

    # Назначение: формат строки targets — ровно 4 ключа; n_hits >= число
    #   matched_keywords >= 1 (n_hits суммирует все вхождения, а не только
    #   число уникальных ключевых слов).
    # Уровень: ✅ реализовано (A-03 tests)
    def test_target_row_format_keys_and_counts(self, tmp_path):
        rows = [_text_row("d1", "обессоливание опреснение сульфат хлорид сульфат")]
        _write_jsonl(tmp_path / "texts.jsonl", rows)

        targets, _ = find_target_documents(tmp_path)

        assert len(targets) == 1
        row = targets[0]
        assert set(row.keys()) == {"doc_id", "topic", "matched_keywords", "n_hits"}
        assert len(row["matched_keywords"]) >= 1
        assert row["n_hits"] >= len(row["matched_keywords"])
        assert row["n_hits"] >= 1

    # Назначение: документирует ФАКТИЧЕСКОЕ ограничение _stem_pattern (не баг —
    #   осознанный компромисс без морфологии, см. докстринг select.py): стем
    #   "штейн" матчит начало ЛЮБОГО слова, включая несвязанные имена
    #   собственные ("Штейнберг") — ложное попадание в matte_slag_pgm.
    #   Тест фиксирует поведение, чтобы регресс/фикс были осознанными.
    # Уровень: ✅ реализовано (A-03 tests)
    def test_stem_false_positive_on_unrelated_proper_noun(self, tmp_path):
        rows = [_text_row("d1", "Инженер Штейнберг руководил проектом обогащения.")]
        _write_jsonl(tmp_path / "texts.jsonl", rows)

        _, counts = find_target_documents(tmp_path)

        # Фактическое поведение сегодня: ложное срабатывание (n=1).
        assert counts[TOPIC_MATTE_SLAG_PGM] == 1

    # Назначение: пустые строки во входном texts.jsonl пропускаются, не роняют парсинг.
    # Уровень: ✅ реализовано (A-03 tests)
    def test_empty_lines_in_input_are_skipped(self, tmp_path):
        content = (
            json.dumps(_text_row("d1", "обессоливание воды"), ensure_ascii=False) + "\n"
            "\n"
            + json.dumps(_text_row("d2", "не по теме"), ensure_ascii=False) + "\n"
        )
        (tmp_path / "texts.jsonl").write_text(content, encoding="utf-8")

        targets, _ = find_target_documents(tmp_path)

        assert {t["doc_id"] for t in targets} == {"d1"}


# ═══════════════════════════ run_selection (интеграция) ═══════════════════════════

class TestRunSelection:
    # Назначение: полный прогон — meta.jsonl обновлён (is_core), targets.jsonl
    #   записан, каждый doc_id из targets существует в meta, stats согласован
    #   с числом строк targets.jsonl.
    # Уровень: ✅ реализовано (A-03 tests)
    def test_full_run_writes_targets_consistent_with_meta(self, tmp_path, monkeypatch):
        monkeypatch.setattr(logutil, "LOG_DIR", tmp_path / "logs")
        monkeypatch.setattr(logutil, "_HANDLERS", {})

        meta_rows = [_meta_row("d1", "Обзоры"), _meta_row("d2", "Журналы")]
        text_rows = [
            _text_row("d1", "Обессоливание воды на обогатительной фабрике."),
            _text_row("d2", "Текст без ключевых слов ни одной темы."),
        ]
        _write_jsonl(tmp_path / "meta.jsonl", meta_rows)
        _write_jsonl(tmp_path / "texts.jsonl", text_rows)

        stats = run_selection(tmp_path, run_id="test_select_run_a")

        assert (tmp_path / "targets.jsonl").exists()
        meta_ids = {
            DocumentMeta.model_validate_json(ln).doc_id
            for ln in (tmp_path / "meta.jsonl").read_text(encoding="utf-8").strip().splitlines()
        }
        target_lines = (tmp_path / "targets.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(target_lines) >= 1
        for ln in target_lines:
            row = json.loads(ln)
            assert row["doc_id"] in meta_ids

        d1 = next(
            DocumentMeta.model_validate_json(ln)
            for ln in (tmp_path / "meta.jsonl").read_text(encoding="utf-8").strip().splitlines()
            if json.loads(ln)["doc_id"] == "d1"
        )
        assert d1.is_core is True
        assert stats["n_targets"] == len(target_lines)
        assert stats["run_id"] == "test_select_run_a"

    # Назначение: тема с 0 документами логирует WARN с кодом INGEST-003
    #   (реестр ERRORS.md); темы с находками логируют INFO topic_summary.
    # Уровень: ✅ реализовано (A-03 tests)
    def test_topic_with_zero_docs_logs_ingest_003_warning(self, tmp_path, monkeypatch):
        monkeypatch.setattr(logutil, "LOG_DIR", tmp_path / "logs")
        monkeypatch.setattr(logutil, "_HANDLERS", {})

        meta_rows = [_meta_row("d1", "Обзоры")]
        text_rows = [_text_row("d1", "Обессоливание воды и опреснение — единственная тема здесь.")]
        _write_jsonl(tmp_path / "meta.jsonl", meta_rows)
        _write_jsonl(tmp_path / "texts.jsonl", text_rows)

        run_id = "test_select_run_b"
        run_selection(tmp_path, run_id=run_id)

        log_path = tmp_path / "logs" / f"{run_id}.jsonl"
        assert log_path.exists()
        events = [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").strip().splitlines()]

        warn_events = [e for e in events if e["event"] == "INGEST-003"]
        # только desalination найден -> минимум 3 из 4 тем должны дать WARN
        assert len(warn_events) >= 3
        assert all(e["level"] == "WARNING" for e in warn_events)

        info_events = [e for e in events if e["event"] == "topic_summary"]
        assert any(TOPIC_DESALINATION in e["detail"] for e in info_events)

    # Назначение: run_id генерируется автоматически, если не передан явно
    #   (new_run_id со стандартным форматом).
    # Уровень: ✅ реализовано (A-03 tests)
    def test_run_id_autogenerated_when_not_passed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(logutil, "LOG_DIR", tmp_path / "logs")
        monkeypatch.setattr(logutil, "_HANDLERS", {})

        _write_jsonl(tmp_path / "meta.jsonl", [_meta_row("d1", "Обзоры")])
        _write_jsonl(tmp_path / "texts.jsonl", [_text_row("d1", "не по теме")])

        stats = run_selection(tmp_path)

        assert stats["run_id"]  # непустая строка
        assert (tmp_path / "logs" / f"{stats['run_id']}.jsonl").exists()


# ═══════════════════════════ Смоук на боевых data/processed ═══════════════════════════

PROCESSED_DIR = Path("data/processed")
TARGETS_PATH = PROCESSED_DIR / "targets.jsonl"
META_PATH = PROCESSED_DIR / "meta.jsonl"

pytestmark_targets = pytest.mark.skipif(
    not TARGETS_PATH.exists(),
    reason="data/processed/targets.jsonl отсутствует — нет результата реального прогона select",
)


class TestRealTargetsSmoke:
    pytestmark = pytestmark_targets

    # Назначение: на реальном корпусе присутствуют все 4 эталонные темы жюри,
    #   у каждой темы найден хотя бы один документ (счётчик > 0).
    # Уровень: ✅ реализовано (A-03 tests)
    def test_all_four_topics_present_with_nonzero_counts(self):
        lines = TARGETS_PATH.read_text(encoding="utf-8").strip().splitlines()
        rows = [json.loads(ln) for ln in lines]
        topics_present = {r["topic"] for r in rows}
        assert topics_present == {
            TOPIC_DESALINATION, TOPIC_CATHOLYTE, TOPIC_MATTE_SLAG_PGM, TOPIC_MINE_WATER,
        }
        counts = {t: sum(1 for r in rows if r["topic"] == t) for t in topics_present}
        assert all(n > 0 for n in counts.values()), counts

    # Назначение: каждая строка targets.jsonl ссылается на doc_id, реально
    #   существующий в meta.jsonl (провенанс не битый).
    # Уровень: ✅ реализовано (A-03 tests)
    def test_all_target_doc_ids_exist_in_meta(self):
        meta_ids = {
            json.loads(ln)["doc_id"]
            for ln in META_PATH.read_text(encoding="utf-8").strip().splitlines()
        }
        rows = [json.loads(ln) for ln in TARGETS_PATH.read_text(encoding="utf-8").strip().splitlines()]
        missing = [r["doc_id"] for r in rows if r["doc_id"] not in meta_ids]
        assert missing == [], f"doc_id из targets.jsonl отсутствуют в meta.jsonl: {missing[:5]}"

    # Назначение: n_hits >= 1 для каждой строки (контракт «нулевые пары не материализуются»).
    # Уровень: ✅ реализовано (A-03 tests)
    def test_all_rows_have_n_hits_at_least_one(self):
        rows = [json.loads(ln) for ln in TARGETS_PATH.read_text(encoding="utf-8").strip().splitlines()]
        offenders = [r for r in rows if r["n_hits"] < 1]
        assert offenders == []
