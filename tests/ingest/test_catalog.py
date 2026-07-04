"""Тесты каталожного слоя ingest (A-20): src/ariadna/ingest/catalog.py.

Независимая проверка module-dev'а: контракт CatalogEntry, извлечение годов,
схлопывание периодических компонентов пути, классификация kind, граничные
случаи корпуса (пустая папка, спецсимволы, глубокая вложенность), идемпотентность,
плюс смоук на боевом data/processed/catalog.jsonl. Фикстуры — программные
деревья каталогов во tmp_path (никаких файлов из data/ не читается/не пишется).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ariadna.contracts import CatalogEntry
from ariadna.ingest import catalog

CATALOG_KINDS = {"journal", "conference", "market_analytics", "other"}


# Назначение: создаёт файл вместе с недостающими родительскими директориями
#   (тестовый хелпер сборки деревьев каталога во tmp_path).
# Уровень: тест-хелпер
def _touch(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ═══════════════════════ _is_period_component ═══════════════════════

# Назначение: покрывает словарь периодических токенов (год/диапазон, месяцы
#   RU/EN, кварталы, периодичность издания) и известный открытый вопрос из
#   worklog — «CRU-2010» НЕ распознаётся как период (не начинается с года).
# Уровень: юнит
@pytest.mark.parametrize("name,expected", [
    ("2020", True),
    ("2020-2021", True),
    ("2010 г.", True),
    ("январь", True),
    ("Январь", True),
    ("декабрь", True),
    ("квартальные издания", True),
    ("Квартальные издания", True),
    ("q1", True),
    ("Q3", True),
    ("jan", True),
    ("Jan.", True),
    ("December", True),
    ("Dec 2020", True),
    ("CRU", False),
    ("CRU-2010", False),  # открытый вопрос worklogs/ingest.md#2026-07-04
    ("China", False),
    ("Copper", False),
    ("Copper 2010", False),  # не начинается с года -> не период
])
def test_is_period_component(name, expected):
    assert catalog._is_period_component(name) is expected


# ═══════════════════════ _collapse_to_card_dir ═══════════════════════

# Назначение: хвостовой год схлопывается в карточку родителя.
# Уровень: юнит
def test_collapse_pops_trailing_year(tmp_path):
    content_dir = tmp_path / "Материалы конференций" / "ConfName" / "2020"
    assert catalog._collapse_to_card_dir(content_dir, tmp_path) == tmp_path / "Материалы конференций" / "ConfName"


# Назначение: несколько хвостовых периодических компонентов схлопываются разом.
# Уровень: юнит
def test_collapse_pops_multiple_trailing_period_components(tmp_path):
    content_dir = tmp_path / "Материалы конференций" / "ConfName" / "2020" / "январь"
    assert catalog._collapse_to_card_dir(content_dir, tmp_path) == tmp_path / "Материалы конференций" / "ConfName"


# Назначение: непериодический хвост останавливает схлопывание немедленно —
#   даже если выше по пути есть год, он остаётся не тронут (баг/особенность:
#   схлопывание проверяет только хвост, не весь путь).
# Уровень: юнит
def test_collapse_stops_at_non_period_tail(tmp_path):
    content_dir = tmp_path / "Материалы конференций" / "ConfName" / "Extra"
    assert catalog._collapse_to_card_dir(content_dir, tmp_path) == content_dir


# Назначение: год, вложенный НЕ в хвосте (перед непериодическим компонентом),
#   не схлопывается вовсе — документирует фактическое поведение алгоритма.
# Уровень: юнит
def test_collapse_does_not_touch_embedded_non_trailing_year(tmp_path):
    content_dir = tmp_path / "Материалы конференций" / "Conf" / "2020" / "SubEvent"
    assert catalog._collapse_to_card_dir(content_dir, tmp_path) == content_dir


# Назначение: граница CATALOG_ROOTS — схлопывание не поднимается выше
#   единственного оставшегося компонента (сам корень).
# Уровень: юнит
def test_collapse_does_not_go_above_root(tmp_path):
    content_dir = tmp_path / "Журналы" / "2020"
    assert catalog._collapse_to_card_dir(content_dir, tmp_path) == tmp_path / "Журналы"


# Назначение: если content_dir — уже сам корень (1 компонент), схлопывание
#   не трогает его, даже гипотетически совпадающий с периодическим паттерном.
# Уровень: юнит
def test_collapse_noop_for_single_component_path(tmp_path):
    content_dir = tmp_path / "Журналы"
    assert catalog._collapse_to_card_dir(content_dir, tmp_path) == content_dir


# ═══════════════════════ discover_card_dirs ═══════════════════════

# Назначение: базовый скан двух корней CATALOG_ROOTS с обычными вложенными
#   годами — карточки схлопнуты до содержательного имени.
# Уровень: интеграционный (файловая система tmp_path)
def test_discover_card_dirs_basic(tmp_path):
    _touch(tmp_path / "Журналы" / "Горная промышленность" / "2020" / "a.pdf")
    _touch(tmp_path / "Материалы конференций" / "Copper 2010" / "b.pdf")
    rels = {str(d.relative_to(tmp_path)) for d in catalog.discover_card_dirs(tmp_path)}
    assert rels == {"Журналы/Горная промышленность", "Материалы конференций/Copper 2010"}


# Назначение: папки вне CATALOG_ROOTS (например, ядро Обзоры/Статьи/Доклады)
#   не попадают в каталог совсем.
# Уровень: граничный случай
def test_discover_card_dirs_ignores_other_roots(tmp_path):
    _touch(tmp_path / "Обзоры" / "some_doc.pdf")
    assert catalog.discover_card_dirs(tmp_path) == []


# Назначение: пустая (без файлов) папка-корень не порождает карточек и не падает.
# Уровень: граничный случай
def test_discover_card_dirs_empty_root_gives_nothing(tmp_path):
    (tmp_path / "Журналы").mkdir()
    assert catalog.discover_card_dirs(tmp_path) == []


# Назначение: отсутствующий на диске корень (data_dir без Журналы вовсе)
#   не падает, просто не даёт карточек.
# Уровень: граничный случай
def test_discover_card_dirs_missing_root_dir(tmp_path):
    assert catalog.discover_card_dirs(tmp_path) == []


# Назначение: скрытые файлы/директории игнорируются полностью (не сканируются).
# Уровень: граничный случай
def test_discover_card_dirs_ignores_hidden_files_and_dirs(tmp_path):
    _touch(tmp_path / "Журналы" / "J1" / ".DS_Store")
    assert catalog.discover_card_dirs(tmp_path) == []
    _touch(tmp_path / "Журналы" / "J1" / ".hidden_dir" / "file.pdf")
    assert catalog.discover_card_dirs(tmp_path) == []


# ═══════════════════════ _years_in_text ═══════════════════════

# Назначение: диапазон правдоподобия годов (1980..2026) отсекает мусор
#   (ISSN/ГОСТ-подобные числа) и годы вне диапазона.
# Уровень: юнит
@pytest.mark.parametrize("text,expected", [
    ("Отчёт 2015 год", [2015]),
    ("1979 не входит", []),
    ("2027 не входит", []),
    ("2010-2012", [2010, 2012]),
    ("ISSN 1234-5678 без года", []),
    ("два года 2011 и 2019", [2011, 2019]),
    ("", []),
])
def test_years_in_text(text, expected):
    assert catalog._years_in_text(text) == expected


# ═══════════════════════ _classify_kind ═══════════════════════

# Назначение: правило классификации journal/market_analytics/conference
#   и приоритет «Журналы» над ключевым словом «МПГ».
# Уровень: юнит
@pytest.mark.parametrize("rel_path,expected", [
    ("Журналы/Горная промышленность", "journal"),
    ("Материалы конференций/Copper 2010", "conference"),
    ("Материалы конференций/МПГ/GFMS/2011", "market_analytics"),
    ("Материалы конференций/Источники данных о меди/CRU-2010", "market_analytics"),
    ("Материалы конференций/МПГ", "market_analytics"),
    ("Журналы/МПГ дайджест", "journal"),  # приоритет top-level "Журналы"
])
def test_classify_kind(rel_path, expected):
    assert catalog._classify_kind(rel_path) == expected


# ═══════════════════════ _build_title ═══════════════════════

def test_build_title_journal():
    assert catalog._build_title("Журналы/Горная промышленность", "journal") == "Журнал «Горная промышленность»"


def test_build_title_market_analytics_with_topic_from_source_folder():
    rel = "Материалы конференций/Источники данных о меди/CRU-2010"
    assert catalog._build_title(rel, "market_analytics") == "Рыночная аналитика меди: CRU-2010"


def test_build_title_market_analytics_mpg_without_topic_prefix():
    rel = "Материалы конференций/МПГ/GFMS"
    assert catalog._build_title(rel, "market_analytics") == "Рыночная аналитика МПГ: GFMS"


def test_build_title_market_analytics_no_topic_signal_falls_back():
    rel = "Материалы конференций/Прочее/Barclays-Cu"
    title = catalog._build_title(rel, "market_analytics")
    assert title == "Рыночная аналитика: Barclays-Cu"


def test_build_title_conference():
    assert catalog._build_title("Материалы конференций/Copper 2010", "conference") == "Конференция Copper 2010"


def test_build_title_conference_single_part_root():
    assert catalog._build_title("Материалы конференций", "conference") == "Материалы конференций: отдельные документы"


def test_build_title_conference_strips_trailing_comma_and_collapses_spaces():
    rel = "Материалы конференций/Copper  2013,   1-4 декабря,  Чили,"
    assert catalog._build_title(rel, "conference") == "Конференция Copper 2013, 1-4 декабря, Чили"


# ═══════════════════════ _plural_files ═══════════════════════

# Назначение: русское склонение файл/файла/файлов по числу, включая
#   исключения 11-14 и составные числа (21, 22, 25, 101, 111...).
# Уровень: юнит
@pytest.mark.parametrize("n,expected", [
    (1, "файл"), (21, "файл"), (101, "файл"),
    (2, "файла"), (3, "файла"), (4, "файла"), (22, "файла"), (24, "файла"),
    (0, "файлов"), (5, "файлов"), (11, "файлов"), (12, "файлов"), (14, "файлов"),
    (111, "файлов"), (112, "файлов"), (100, "файлов"), (25, "файлов"),
])
def test_plural_files(n, expected):
    assert catalog._plural_files(n) == expected


# ═══════════════════════ _build_description ═══════════════════════

def test_build_description_year_range():
    d = catalog._build_description("Журнал «X»", 2018, 2020, 5)
    assert d == "Журнал «X», выпуски 2018–2020, 5 файлов. Содержимое не индексировано, метаданные по названию."


def test_build_description_single_year():
    d = catalog._build_description("Журнал «X»", 2018, 2018, 1)
    assert "2018 год" in d
    assert "1 файл." in d


def test_build_description_no_year():
    d = catalog._build_description("Журнал «X»", None, None, 3)
    assert "год не определён" in d


# ═══════════════════════ _make_catalog_id ═══════════════════════

def test_make_catalog_id_stable_and_unique():
    a = catalog._make_catalog_id("Журналы/Горная промышленность")
    b = catalog._make_catalog_id("Журналы/Горная промышленность")
    c = catalog._make_catalog_id("Журналы/Другой")
    assert a == b
    assert a != c


# ═══════════════════════ build_catalog_entries (интеграция) ═══════════════════════

# Назначение: соответствие контракту CatalogEntry + корректные годы/kind/n_files
#   на простом дереве с двумя годовыми подпапками одного журнала.
# Уровень: интеграционный
def test_build_catalog_entries_contract_and_fields(tmp_path):
    _touch(tmp_path / "Журналы" / "Горная промышленность" / "2020" / "issue1.pdf")
    _touch(tmp_path / "Журналы" / "Горная промышленность" / "2021" / "issue2.pdf")
    entries = catalog.build_catalog_entries(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    assert isinstance(e, CatalogEntry)
    assert e.path == "Журналы/Горная промышленность"
    assert e.kind == "journal"
    assert e.year_from == 2020
    assert e.year_to == 2021
    assert e.n_files == 2
    assert e.embedding is None
    # round-trip через JSON — контракт валидируется собственным парсером pydantic
    assert CatalogEntry.model_validate_json(e.model_dump_json()) == e


# Назначение: карточка содержит год, который встречается ТОЛЬКО в имени
#   собственного пути карточки (не в файлах/поддиректориях) — регрессия на
#   решение из worklog («год из пути ДОБАВЛЕН объединением»).
# Уровень: интеграционный
def test_build_catalog_entries_year_from_card_path_itself(tmp_path):
    _touch(tmp_path / "Материалы конференций" / "МПГ" / "GFMS" / "2011" / "PGM" / "report.pdf")
    entries = catalog.build_catalog_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0].year_from == 2011
    assert entries[0].year_to == 2011


# Назначение: файлы вложенной, отдельно раскрытой карточки НЕ считаются
#   дважды и не приписываются карточке-предку (защита от двойного счёта).
# Уровень: граничный случай
def test_build_catalog_entries_nested_card_boundary_no_double_count(tmp_path):
    _touch(tmp_path / "Материалы конференций" / "Conf" / "2020" / "a.pdf")       # схлопнется в "Conf"
    _touch(tmp_path / "Материалы конференций" / "Conf" / "SubEvent" / "b.pdf")   # своя карточка "Conf/SubEvent"
    entries = catalog.build_catalog_entries(tmp_path)
    by_path = {e.path: e for e in entries}
    assert set(by_path) == {"Материалы конференций/Conf", "Материалы конференций/Conf/SubEvent"}
    assert by_path["Материалы конференций/Conf"].n_files == 1
    assert by_path["Материалы конференций/Conf/SubEvent"].n_files == 1
    assert sum(e.n_files for e in entries) == 2  # ровно 2 файла на диске, без двойного счёта


# Назначение: спецсимволы (кириллица+латиница+амперсанд+скобки) в именах
#   директорий не роняют скан и корректно попадают в путь/n_files.
# Уровень: граничный случай
def test_build_catalog_entries_special_characters_in_names(tmp_path):
    _touch(tmp_path / "Материалы конференций" / "Al-Р&D (2019), спецвыпуск №2" / "file.pdf")
    entries = catalog.build_catalog_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0].n_files == 1
    assert "Al-Р&D (2019), спецвыпуск №2" in entries[0].path


# Назначение: известный открытый вопрос из worklog — разные пути с одинаковым
#   последним компонентом дают одинаковый title, НО остаются разными карточками
#   (разные catalog_id/path) — не настоящий дубликат данных.
# Уровень: регрессия документирует поведение
def test_known_issue_same_title_for_different_paths(tmp_path):
    _touch(tmp_path / "Материалы конференций" / "Co-2013" / "China" / "a.pdf")
    _touch(tmp_path / "Материалы конференций" / "Co-2012" / "China" / "b.pdf")
    entries = sorted(catalog.build_catalog_entries(tmp_path), key=lambda e: e.path)
    assert len(entries) == 2
    assert entries[0].title == entries[1].title == "Конференция China"
    assert entries[0].catalog_id != entries[1].catalog_id
    assert entries[0].path != entries[1].path


# Назначение: пустой список карточек (совсем нет данных) не падает.
# Уровень: граничный случай
def test_build_catalog_entries_empty_data_dir(tmp_path):
    assert catalog.build_catalog_entries(tmp_path) == []


# Назначение: идемпотентность — два прогона по одному и тому же дереву дают
#   идентичный список карточек (включая стабильный catalog_id).
# Уровень: инвариант
def test_build_catalog_entries_idempotent(tmp_path):
    _touch(tmp_path / "Материалы конференций" / "Copper 2010" / "a.pdf")
    _touch(tmp_path / "Материалы конференций" / "Copper 2010" / "b.pdf")
    e1 = catalog.build_catalog_entries(tmp_path)
    e2 = catalog.build_catalog_entries(tmp_path)
    assert [e.model_dump(exclude={"embedding"}) for e in e1] == [e.model_dump(exclude={"embedding"}) for e in e2]
    assert e1[0].catalog_id == e2[0].catalog_id


# Назначение: INGEST-004 (0 файлов в собственной области карточки после
#   разбиения по границам вложенных карточек) — «теоретически не должна
#   происходить» (ERRORS.md), но код обязан её отловить и залогировать,
#   а не выдать битую карточку с n_files=0. Изолируем через monkeypatch
#   discover_card_dirs/_scan_own, т.к. на реальной ФС сконструировать этот
#   случай через текущий алгоритм схлопывания не удалось (см. финальный отчёт).
# Уровень: защитный путь (граничный случай)
def test_build_catalog_entries_skips_zero_file_card_and_logs(monkeypatch, tmp_path):
    fake_card_dir = tmp_path / "Материалы конференций" / "GhostCard"
    monkeypatch.setattr(catalog, "discover_card_dirs", lambda dd: [fake_card_dir])
    monkeypatch.setattr(catalog, "_scan_own", lambda card_dir, all_card_dirs: (0, []))

    events = []
    monkeypatch.setattr(
        catalog, "log_event",
        lambda logger, **kwargs: events.append(kwargs),
    )

    entries = catalog.build_catalog_entries(tmp_path, logger=object())
    assert entries == []
    assert any(ev.get("event") == catalog.CATALOG_EMPTY_CARD for ev in events)


# Назначение: тот же защитный путь без logger (None) не должен падать.
# Уровень: граничный случай
def test_build_catalog_entries_skips_zero_file_card_no_logger(monkeypatch, tmp_path):
    fake_card_dir = tmp_path / "Материалы конференций" / "GhostCard"
    monkeypatch.setattr(catalog, "discover_card_dirs", lambda dd: [fake_card_dir])
    monkeypatch.setattr(catalog, "_scan_own", lambda card_dir, all_card_dirs: (0, []))
    assert catalog.build_catalog_entries(tmp_path, logger=None) == []


# ═══════════════════════ embed_catalog_entries ═══════════════════════

def _make_entry(catalog_id: str, description: str = "d") -> CatalogEntry:
    return CatalogEntry(
        catalog_id=catalog_id, path=f"p/{catalog_id}", title=f"t{catalog_id}",
        kind="journal", n_files=1, description=description,
    )


def test_embed_catalog_entries_success(monkeypatch):
    entries = [_make_entry("1"), _make_entry("2")]
    monkeypatch.setattr(catalog, "embed_texts", lambda texts: [[0.1, 0.2] for _ in texts])
    result = catalog.embed_catalog_entries(entries)
    assert all(e.embedding == [0.1, 0.2] for e in result)


def test_embed_catalog_entries_empty_list_is_noop(monkeypatch):
    called = []
    monkeypatch.setattr(catalog, "embed_texts", lambda texts: called.append(texts) or [])
    assert catalog.embed_catalog_entries([]) == []
    assert called == []


# Назначение: сбой Ollama после 2 попыток батчем целиком не роняет прогон —
#   карточки остаются с embedding=None (панель рекомендаций деградирует).
# Уровень: граничный случай / отказоустойчивость
def test_embed_catalog_entries_failure_after_retries_leaves_embedding_none(monkeypatch):
    entries = [_make_entry("1")]
    calls = {"n": 0}

    def always_fail(texts):
        calls["n"] += 1
        raise catalog.EmbeddingAPIError("boom")

    monkeypatch.setattr(catalog, "embed_texts", always_fail)
    result = catalog.embed_catalog_entries(entries)
    assert result[0].embedding is None
    assert calls["n"] == 2  # ровно 2 попытки согласно докстрингу embed_catalog_entries


# Назначение: первая попытка падает, вторая — успешна: карточки получают
#   эмбеддинг (без потери батча из-за временного сбоя).
# Уровень: граничный случай
def test_embed_catalog_entries_recovers_on_second_attempt(monkeypatch):
    entries = [_make_entry("1")]
    calls = {"n": 0}

    def fail_then_succeed(texts):
        calls["n"] += 1
        if calls["n"] == 1:
            raise catalog.EmbeddingAPIError("boom")
        return [[0.5] for _ in texts]

    monkeypatch.setattr(catalog, "embed_texts", fail_then_succeed)
    result = catalog.embed_catalog_entries(entries)
    assert result[0].embedding == [0.5]
    assert calls["n"] == 2


# ═══════════════════════ write_catalog_jsonl ═══════════════════════

def test_write_catalog_jsonl_roundtrip(tmp_path):
    entries = [CatalogEntry(catalog_id="1", path="p", title="t", kind="journal",
                             n_files=3, description="d", embedding=[0.1, 0.2])]
    out = tmp_path / "catalog.jsonl"
    catalog.write_catalog_jsonl(entries, out)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert CatalogEntry.model_validate_json(lines[0]) == entries[0]


# Назначение: повторная запись перезаписывает файл целиком, а не дозаписывает
#   (docstring write_catalog_jsonl: «перегенерация дешевле инкрементальной»).
# Уровень: идемпотентность
def test_write_catalog_jsonl_overwrites_not_appends(tmp_path):
    out = tmp_path / "catalog.jsonl"
    catalog.write_catalog_jsonl([_make_entry("1")], out)
    catalog.write_catalog_jsonl([_make_entry("2")], out)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["catalog_id"] == "2"


# Назначение: пустой список карточек создаёт пустой (но существующий) файл.
# Уровень: граничный случай
def test_write_catalog_jsonl_empty_list_creates_empty_file(tmp_path):
    out = tmp_path / "sub" / "catalog.jsonl"
    catalog.write_catalog_jsonl([], out)
    assert out.exists()
    assert out.read_text(encoding="utf-8") == ""


# ═══════════════════════ Смоук на боевом data/processed/catalog.jsonl ═══════════════════════

# Назначение: боевой прогон module-dev'а (86 карточек) — контракт, сумма
#   n_files=1273, все kind из словаря, все с эмбеддингом dim=1024, ID уникальны.
# Уровень: смоук (реальные данные, только чтение)
def test_smoke_real_catalog_jsonl():
    path = Path("data/processed/catalog.jsonl")
    if not path.exists():
        pytest.skip("data/processed/catalog.jsonl отсутствует в этом окружении")
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 86
    entries = [CatalogEntry.model_validate_json(l) for l in lines]
    assert sum(e.n_files for e in entries) == 1273
    assert all(e.kind in CATALOG_KINDS for e in entries)
    assert all(e.embedding is not None and len(e.embedding) == 1024 for e in entries)
    assert len({e.catalog_id for e in entries}) == 86
    assert len({e.path for e in entries}) == 86
