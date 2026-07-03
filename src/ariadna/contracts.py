"""Контракты данных «Ариадны» — единственный источник истины о границах модулей.

Все структуры конвейера: документ → чанк → извлечение → граф → запрос → ответ →
аналитика. Схема двойного назначения: `model_json_schema()` моделей вставляется
в промпты локальной LLM (структурированный вывод) и валидирует её ответы —
один контракт для кода и для LLM.

Зависимости: только pydantic. Инвариант: модули обмениваются данными только через
эти модели. Уровень файла: 🔒 — правки только через solver/оркестратора (эскалация,
см. CLAUDE.md). Паспорта модулей: docs/dev/modules/.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

# ══════════════════════════ Словари (Enum) ══════════════════════════

# ─── EntityType · Уровень: 🔒 ─────────────────────────────────────────
# Назначение: 8 типов сущностей — онтология фиксирована заданием, не расширять.
class EntityType(str, Enum):
    MATERIAL = "Material"
    PROCESS = "Process"
    EQUIPMENT = "Equipment"
    PROPERTY = "Property"
    EXPERIMENT = "Experiment"
    PUBLICATION = "Publication"
    EXPERT = "Expert"
    FACILITY = "Facility"


# ─── RelationType · Уровень: 🔒 ───────────────────────────────────────
# Назначение: 6 типов связей — онтология фиксирована заданием, не расширять.
class RelationType(str, Enum):
    USES_MATERIAL = "uses_material"
    OPERATES_AT_CONDITION = "operates_at_condition"
    PRODUCES_OUTPUT = "produces_output"
    DESCRIBED_IN = "described_in"
    VALIDATED_BY = "validated_by"
    CONTRADICTS = "contradicts"


# ─── Lang / Geography / CompareOp · Уровень: 🔒 ───────────────────────
# Назначение: служебные словари: язык текста, гео-признак практики,
#   оператор числового ограничения.
class Lang(str, Enum):
    RU = "ru"
    EN = "en"
    MIXED = "mixed"


class Geography(str, Enum):
    RU = "ru"                # отечественная практика
    FOREIGN = "foreign"      # зарубежная практика
    UNKNOWN = "unknown"


class CompareOp(str, Enum):
    LE = "<="
    GE = ">="
    LT = "<"
    GT = ">"
    EQ = "="
    RANGE = "range"          # диапазон вида «200–300 мг/л»: value..value_max


# ══════════════════════════ Ingest ══════════════════════════

# ─── DocumentMeta · Уровень: 🔒 ───────────────────────────────────────
# Назначение: метаданные документа корпуса; источник поля Expert — authors.
class DocumentMeta(BaseModel):
    doc_id: str = Field(description="Стабильный ID документа (хеш относительного пути)")
    path: str = Field(description="Путь к исходному файлу относительно data/")
    title: str = Field(default="", description="Название документа (из мета или имени файла)")
    authors: list[str] = Field(default_factory=list, description="Авторы — источник сущностей Expert")
    year: int | None = Field(default=None, description="Год публикации, если удалось определить")
    lang: Lang = Field(default=Lang.RU, description="Основной язык документа")
    geography: Geography = Field(default=Geography.UNKNOWN, description="Отечественная/зарубежная практика")
    source_folder: str = Field(default="", description="Папка корпуса: Обзоры/Статьи/Доклады/Журналы/Конференции")
    is_core: bool = Field(default=False, description="Входит в ядро для графового извлечения (~180 док.)")


# ─── DocumentText · Уровень: 🔒 ───────────────────────────────────────
# Назначение: нормализованный полный текст документа (после чистки колонтитулов).
class DocumentText(BaseModel):
    doc_id: str = Field(description="ID документа (= DocumentMeta.doc_id)")
    text: str = Field(description="Нормализованный текст без колонтитулов и мусора")
    n_chars: int = Field(default=0, description="Длина текста в символах (контроль качества конвертации)")


# ─── Chunk · Уровень: 🔒 ──────────────────────────────────────────────
# Назначение: чанк текста — единица индексации, извлечения и цитирования.
class Chunk(BaseModel):
    chunk_id: str = Field(description="ID чанка: <doc_id>#<порядковый номер>")
    doc_id: str = Field(description="ID документа-родителя")
    text: str = Field(description="Текст чанка (границы предложений сохранены)")
    start: int = Field(default=0, description="Смещение начала чанка в тексте документа")
    end: int = Field(default=0, description="Смещение конца чанка в тексте документа")
    lang: Lang = Field(default=Lang.RU, description="Язык чанка")
    embedding: list[float] | None = Field(default=None, description="Вектор bge-m3 (заполняет search/embeddings)")


# ══════════════════════════ Extraction ══════════════════════════

# ─── NumericConstraint · Уровень: 🔒 ──────────────────────────────────
# Назначение: числовое ограничение/условие; извлекается ТОЛЬКО правилами
#   (regex-нормализатор), не LLM — ошибки в числах недопустимы по заданию.
class NumericConstraint(BaseModel):
    param: str = Field(description="Параметр: «сульфаты», «температура», «скорость потока»")
    op: CompareOp = Field(description="Оператор сравнения или диапазон")
    value: float = Field(description="Числовое значение (нижняя граница для range)")
    value_max: float | None = Field(default=None, description="Верхняя граница для range")
    unit: str = Field(description="Единица измерения как в тексте: «мг/дм³»")
    norm_value: float = Field(description="Значение в канонической единице")
    norm_unit: str = Field(description="Каноническая единица: «мг/л», «°C», «м³/ч»")
    source_text: str = Field(default="", description="Исходный фрагмент текста (для верификации)")


# ─── Entity · Уровень: 🔒 ─────────────────────────────────────────────
# Назначение: извлечённая сущность онтологии до дедупликации/загрузки в граф.
class Entity(BaseModel):
    name: str = Field(description="Каноническое имя сущности (RU, если есть)")
    type: EntityType = Field(description="Тип по онтологии задания")
    synonyms: list[str] = Field(default_factory=list, description="Синонимы RU/EN: «электроэкстракция»/«electrowinning»")
    attrs: dict[str, str] = Field(default_factory=dict, description="Атрибуты: гео, климат, роль в процессе и т.п.")


# ─── Relation · Уровень: 🔒 ───────────────────────────────────────────
# Назначение: извлечённая связь между сущностями с провенансом до чанка.
class Relation(BaseModel):
    source: str = Field(description="Имя сущности-источника (= Entity.name)")
    target: str = Field(description="Имя сущности-цели (= Entity.name)")
    type: RelationType = Field(description="Тип связи по онтологии задания")
    constraints: list[NumericConstraint] = Field(default_factory=list, description="Числовые условия связи (для operates_at_condition)")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Уверенность извлечения 0..1")


# ─── ExtractionResult · Уровень: 🔒 ───────────────────────────────────
# Назначение: результат извлечения по одному чанку; JSON-схема этой модели
#   вставляется в промпт локальной LLM и валидирует её ответ.
class ExtractionResult(BaseModel):
    doc_id: str = Field(description="ID документа")
    chunk_id: str = Field(description="ID чанка — провенанс всех фактов ниже")
    entities: list[Entity] = Field(default_factory=list, description="Извлечённые сущности")
    relations: list[Relation] = Field(default_factory=list, description="Извлечённые связи")
    constraints: list[NumericConstraint] = Field(default_factory=list, description="Числа/единицы, найденные правилами (не LLM)")
    model: str = Field(default="", description="Модель извлечения: qwen3:8b и т.п.")
    prompt_hash: str = Field(default="", description="Хеш промпта — для воспроизведения ошибок")


# ══════════════════════════ Graph ══════════════════════════

# ─── Provenance · Уровень: 🔒 ─────────────────────────────────────────
# Назначение: верификация и версионирование факта (У-4): источник,
#   достоверность, дата актуализации, автор ручной правки.
class Provenance(BaseModel):
    doc_id: str = Field(description="Документ-источник")
    chunk_id: str = Field(default="", description="Чанк-источник (цитируемость до чанка)")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Уровень достоверности 0..1")
    updated_at: str = Field(default="", description="Дата актуализации факта, ISO-8601")
    edited_by: str = Field(default="", description="Автор ручной правки (пусто = автоизвлечение)")


# ─── GraphNode / GraphEdge · Уровень: 🔒 ──────────────────────────────
# Назначение: узел/ребро в форме, готовой к записи в Neo4j; пишет только graph/loader.
class GraphNode(BaseModel):
    id: str = Field(description="Уникальный ID узла (slug канонического имени)")
    label: EntityType = Field(description="Метка узла = тип сущности")
    name: str = Field(description="Каноническое имя (RU)")
    name_en: str = Field(default="", description="Английское имя/синоним для двуязычного поиска")
    geography: Geography = Field(default=Geography.UNKNOWN, description="Гео-признак, если применим")
    year: int | None = Field(default=None, description="Год (для Experiment/Publication)")
    properties: dict[str, str] = Field(default_factory=dict, description="Прочие атрибуты узла")
    provenance: Provenance = Field(description="Источник и версионирование")


class GraphEdge(BaseModel):
    source_id: str = Field(description="ID узла-источника")
    target_id: str = Field(description="ID узла-цели")
    type: RelationType = Field(description="Тип связи")
    constraints: list[NumericConstraint] = Field(default_factory=list, description="Числовые условия на связи")
    provenance: Provenance = Field(description="Источник и версионирование")


# ══════════════════════════ Search ══════════════════════════

# ─── QueryFilters / QueryIntent · Уровень: 🔒 ─────────────────────────
# Назначение: распознанный смысл вопроса: какой Cypher-шаблон применить
#   и чем заполнить слоты. Свободный text2cypher запрещён инвариантом №4.
class QueryFilters(BaseModel):
    geography: Geography | None = Field(default=None, description="Фильтр по гео-признаку")
    year_from: int | None = Field(default=None, description="Нижняя граница года публикации")
    year_to: int | None = Field(default=None, description="Верхняя граница года публикации")
    min_confidence: float | None = Field(default=None, description="Порог достоверности фактов")
    numeric: list[NumericConstraint] = Field(default_factory=list, description="Числовые условия из вопроса")


class QueryIntent(BaseModel):
    question: str = Field(description="Исходный вопрос пользователя")
    template_id: str = Field(description="ID Cypher-шаблона из graph/ (или 'rag_fallback')")
    slots: dict[str, str] = Field(default_factory=dict, description="Заполненные слоты шаблона: материал, процесс…")
    filters: QueryFilters = Field(default_factory=QueryFilters, description="Фильтры гео/год/достоверность/числа")
    compare_geography: bool = Field(default=False, description="У-2: сравнительный режим «RU vs зарубеж»")


# ─── Citation / Contradiction / Recommendation · Уровень: 🔒 ──────────
# Назначение: элементы ответа: цитата до чанка; противоречие для подсветки (У-3);
#   рекомендация «похожий кейс / эксперт / смежная тема» (У-1).
class Citation(BaseModel):
    doc_id: str = Field(description="Документ-источник")
    chunk_id: str = Field(description="Чанк-источник")
    title: str = Field(default="", description="Название документа")
    year: int | None = Field(default=None, description="Год документа")
    quote: str = Field(default="", description="Цитируемый фрагмент (≤ 300 символов)")


class Contradiction(BaseModel):
    claim_a: str = Field(description="Утверждение А")
    claim_b: str = Field(description="Противоречащее утверждение Б")
    citations: list[Citation] = Field(default_factory=list, description="Источники обоих утверждений")


class RecommendationKind(str, Enum):
    SIMILAR_CASE = "similar_case"    # похожий кейс (векторная близость)
    EXPERT = "expert"                # эксперт/команда по теме (обход графа)
    ADJACENT_TOPIC = "adjacent_topic"  # смежная тема для изучения


class Recommendation(BaseModel):
    kind: RecommendationKind = Field(description="Вид рекомендации")
    title: str = Field(description="Что рекомендуем: кейс/эксперт/тема")
    reason: str = Field(default="", description="Почему релевантно (одной фразой)")
    citations: list[Citation] = Field(default_factory=list, description="Подтверждающие источники")


# ─── Answer · Уровень: 🔒 ─────────────────────────────────────────────
# Назначение: финальный ответ системы; инвариант №6 — без цитат ответа нет,
#   честное «не найдено» + вход для карты пробелов.
class Answer(BaseModel):
    question: str = Field(description="Исходный вопрос")
    text: str = Field(description="Текст ответа (синтез Claude) или «в корпусе не найдено»")
    citations: list[Citation] = Field(default_factory=list, description="Цитаты-источники ответа")
    contradictions: list[Contradiction] = Field(default_factory=list, description="У-3: найденные противоречия")
    recommendations: list[Recommendation] = Field(default_factory=list, description="У-1: похожие кейсы, эксперты, смежные темы")
    subgraph_node_ids: list[str] = Field(default_factory=list, description="ID узлов подграфа для визуализации в UI")
    found: bool = Field(default=True, description="False = честное «не найдено» (вход карты пробелов)")


# ══════════════════════════ Analytics ══════════════════════════

# ─── GapCell / GapReport · Уровень: 🔒 ────────────────────────────────
# Назначение: карта пробелов ⭐ — неизученные комбинации «материал–режим–условие»
#   (Cypher NOT EXISTS по графу).
class GapCell(BaseModel):
    material: str = Field(description="Материал/сырьё")
    process: str = Field(description="Процесс/технология")
    condition: str = Field(default="", description="Условие: климат, концентрация…")
    n_sources: int = Field(default=0, description="Сколько источников нашлось (0 = пробел)")


class GapReport(BaseModel):
    cells: list[GapCell] = Field(default_factory=list, description="Ячейки матрицы пробелов")
    only_ru: list[str] = Field(default_factory=list, description="Темы только в отечественной литературе")
    only_foreign: list[str] = Field(default_factory=list, description="Темы только в зарубежной литературе")


# ─── ComparisonRow / ReviewSection / LitReview · Уровень: 🔒 ──────────
# Назначение: литобзор с консенсусом/разногласиями и таблица сравнения
#   технологий по параметрам (У-2).
class ComparisonRow(BaseModel):
    technology: str = Field(description="Технология/метод")
    parameters: dict[str, str] = Field(default_factory=dict, description="Параметр → значение (эффективность, затраты, климат…)")
    geography: Geography = Field(default=Geography.UNKNOWN, description="Где применялась")
    citations: list[Citation] = Field(default_factory=list, description="Источники строки")


class ReviewSection(BaseModel):
    topic: str = Field(description="Тема секции: метод/группа источников")
    consensus: list[str] = Field(default_factory=list, description="Консенсусные выводы")
    disagreements: list[str] = Field(default_factory=list, description="Зоны разногласий")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Степень уверенности секции")
    citations: list[Citation] = Field(default_factory=list, description="Источники секции")


class LitReview(BaseModel):
    question: str = Field(description="Запрос, по которому строился обзор")
    sections: list[ReviewSection] = Field(default_factory=list, description="Секции обзора")
    comparison: list[ComparisonRow] = Field(default_factory=list, description="У-2: таблица сравнения технологий")
    gaps: GapReport | None = Field(default=None, description="Пробелы по теме обзора")
