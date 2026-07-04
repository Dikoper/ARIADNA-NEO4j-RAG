"""Обвязка вызовов бэкенда для Streamlit-демо (A-13): чат, подграф, карта
пробелов, рекомендации (У-1, A-15).

Вход: вопрос пользователя / список ID узлов / готовый Answer / ничего (карта
пробелов). Выход: `contracts.Answer` (через кэш или живой синтез), словарь
подграфа (`graph.templates.fetch_subgraph`), `contracts.GapReport`
(`analytics.gap_map.build_gap_report`) или `list[contracts.Recommendation]`
(`analytics.recommendations.build_recommendations`).

Зависимости: `ariadna.search.answer.answer_question`, `ariadna.graph.templates.
fetch_subgraph`, `ariadna.analytics.gap_map.build_gap_report`,
`ariadna.analytics.recommendations.build_recommendations` — ВСЕ ленивым
импортом (пишутся параллельно/ещё не существуют на момент A-13/A-15, см.
постановку задачи): ImportError/любая ошибка стенда (Neo4j/Ollama недоступны) —
честная деградация, UI не падает целиком (app.py показывает заглушку).
Инвариант: никакой бизнес-логики (Cypher/аналитика) здесь не пишется — только
вызов готовых функций search/analytics/graph и разбор их результата.
Рекомендации считаются на лету поверх готового Answer и НЕ пишутся в кэш
ответов `answer_cache` (решение оркестратора, A-15) — только кэшируются в
памяти Streamlit на время сессии (`@st.cache_data`) по нормализованному
вопросу, чтобы не пересчитывать при каждом rerun скрипта.
Паспорт: docs/dev/modules/ui.md.
"""
from __future__ import annotations

import streamlit as st

from ariadna.contracts import Answer, GapReport, Recommendation
from ariadna.logutil import get_logger, log_event, new_run_id
from ui import answer_cache

# 4 эталонных запроса жюри (docs/dev/TASK.md) — пресеты вкладки «Чат».
PRESET_QUESTIONS: list[tuple[str, str]] = [
    (
        "№1 Обессоливание воды",
        "Какие методы обессоливания воды подходят для обогатительной фабрики, если "
        "исходная вода содержит сульфаты, хлориды, Ca, Mg, Na по 200–300 мг/л, а "
        "требуемый сухой остаток — ≤1000 мг/дм³?",
    ),
    (
        "№2 Циркуляция католита",
        "Какие технические решения организации циркуляции католита при "
        "электроэкстракции никеля описаны в мировой практике, и какая скорость "
        "потока считается оптимальной?",
    ),
    (
        "№3 Au/Ag/МПГ штейн-шлак",
        "Покажите все эксперименты и публикации по распределению Au, Ag и МПГ "
        "между медным/никелевым штейном и шлаком за последние 5 лет.",
    ),
    (
        "№4 Закачка шахтных вод",
        "Какие способы закачки шахтных вод в глубокие горизонты применялись в "
        "России и за рубежом, и каковы их технико-экономические показатели?",
    ),
]

MAX_SUBGRAPH_NODES = 60


# ─── get_answer ─────────────────────────────────────────────────────────
# Назначение: вопрос -> Answer, кэш-first (кроме force_recompute) — синтез
#   локальной LLM занимает 2–7 мин (docs/dev/worklogs/search.md), демо не
#   может ждать это на каждый клик жюри; свежий живой ответ дописывается
#   в кэш тем же ключом.
# Входные связи: текст вопроса, use_cache/force_recompute — управление сайдбара/
#   кнопки «пересчитать», путь к файлу кэша
# Выходные данные: (Answer, from_cache: bool)
# Уровень: ✅ реализовано (A-13)
def get_answer(
    question: str,
    *,
    use_cache: bool = True,
    force_recompute: bool = False,
    cache_path=answer_cache.DEFAULT_CACHE_PATH,
) -> tuple[Answer, bool]:
    if use_cache and not force_recompute:
        cached = answer_cache.get_cached_answer(answer_cache.load_cache(cache_path), question)
        if cached is not None:
            return Answer.model_validate(cached["answer"]), True

    from ariadna.search.answer import answer_question  # noqa: PLC0415 — ленивый импорт (2-7 мин синтез)

    answer = answer_question(question)
    answer_cache.put_answer(question, answer.model_dump(), path=cache_path)
    return answer, False


# ─── get_subgraph ───────────────────────────────────────────────────────
# Назначение: node_ids ответа -> словарь подграфа для визуализации; открывает
#   и закрывает свой Neo4j-драйвер (только чтение). Недоступность fetch_subgraph
#   (ещё не реализован analytics/graph-агентом) или стенда — None, а не
#   исключение наружу (app.py рисует плоский список имён как честный фолбэк).
# Входные связи: subgraph_node_ids (Answer.subgraph_node_ids)
# Выходные данные: {"nodes": [...], "edges": [...]} | None
# Уровень: ✅ реализовано (A-13)
def get_subgraph(node_ids: list[str], *, max_nodes: int = MAX_SUBGRAPH_NODES) -> dict | None:
    if not node_ids:
        return None
    logger = get_logger("ui", new_run_id("ui_"))
    try:
        from ariadna.graph.lexical_loader import get_driver
        from ariadna.graph.templates import fetch_subgraph
    except ImportError as exc:
        log_event(logger, stage="subgraph", event="UI-001", level="WARNING",
                   detail=f"fetch_subgraph недоступен ({str(exc)[:200]}) — плоский список узлов")
        return None
    try:
        driver = get_driver()
    except Exception as exc:  # noqa: BLE001 — стенд недоступен, честный фолбэк
        log_event(logger, stage="subgraph", event="UI-001", level="WARNING",
                   detail=f"Neo4j недоступен ({str(exc)[:200]}) — плоский список узлов")
        return None
    try:
        return fetch_subgraph(driver, node_ids, max_nodes=max_nodes)
    except Exception as exc:  # noqa: BLE001 — любая ошибка Cypher/стенда не должна ронять UI
        log_event(logger, stage="subgraph", event="UI-001", level="ERROR",
                   detail=f"fetch_subgraph упал: {str(exc)[:300]} — плоский список узлов")
        return None
    finally:
        driver.close()


# ─── get_gap_report ─────────────────────────────────────────────────────
# Назначение: вызывает build_gap_report (analytics, A-12) для вкладки «Карта
#   пробелов ⭐». Недоступность модуля/стенда — None, app.py показывает
#   заглушку «раздел готовится» с инструкцией запуска.
# Входные связи: нет (сам открывает драйвер внутри analytics-функции)
# Выходные данные: GapReport | None
# Уровень: ✅ реализовано (A-13)
def get_gap_report(*, limit: int = 50) -> GapReport | None:
    logger = get_logger("ui", new_run_id("ui_"))
    try:
        from ariadna.analytics.gap_map import build_gap_report
    except ImportError as exc:
        log_event(logger, stage="gap_report", event="UI-002", level="WARNING",
                   detail=f"build_gap_report недоступен ({str(exc)[:200]}) — заглушка «раздел готовится»")
        return None
    try:
        return build_gap_report(limit=limit)
    except Exception as exc:  # noqa: BLE001 — стенд/данные недоступны, честный фолбэк
        log_event(logger, stage="gap_report", event="UI-002", level="ERROR",
                   detail=f"build_gap_report упал: {str(exc)[:300]} — заглушка «раздел готовится»")
        return None


# ─── _cached_recommendations ────────────────────────────────────────────
# Назначение: кэш-слой над `build_recommendations` (A-14, параллельный
#   агент) — ключ кэша: только `normalized_question` + `top_k` (нормализация —
#   `answer_cache.normalize_question`, тот же приём, что у кэша ответов).
#   `_driver`/`_answer` начинаются с подчёркивания — Streamlit НЕ хэширует
#   такие параметры (обязательно: `contracts.Answer` без frozen=True
#   нехэшируем — pydantic сам определяет `__eq__` без `__hash__`; сырой
#   neo4j.Driver тоже не гарантированно хэшируем). Если `_driver` не передан —
#   открывает и закрывает свой (как `get_subgraph`), чтобы вызывающая сторона
#   (app.py) не обязана была сама управлять подключением; переданный явно
#   driver (тесты/переиспользование) НЕ закрывается — им владеет вызывающий.
#   Недоступность модуля/стенда — пустой список, а не исключение наружу.
# Входные связи: normalized_question, _answer (Answer), _driver, top_k
# Выходные данные: list[contracts.Recommendation]
# Уровень: ✅ реализовано (A-15)
@st.cache_data(show_spinner=False)
def _cached_recommendations(
    normalized_question: str,
    _answer: Answer,
    *,
    _driver=None,
    top_k: int = 3,
) -> list[Recommendation]:
    logger = get_logger("ui", new_run_id("ui_"))
    try:
        from ariadna.analytics.recommendations import build_recommendations
    except ImportError as exc:
        log_event(logger, stage="recommendations", event="UI-004", level="WARNING",
                   detail=f"build_recommendations недоступен ({str(exc)[:200]}) — пустой список")
        return []

    driver = _driver
    owns_driver = driver is None
    if owns_driver:
        try:
            from ariadna.graph.lexical_loader import get_driver
            driver = get_driver()
        except Exception as exc:  # noqa: BLE001 — стенд недоступен, честный фолбэк
            log_event(logger, stage="recommendations", event="UI-004", level="WARNING",
                       detail=f"Neo4j недоступен ({str(exc)[:200]}) — пустой список")
            return []

    try:
        return build_recommendations(driver, normalized_question, _answer, top_k=top_k)
    except Exception as exc:  # noqa: BLE001 — любая ошибка стенда/аналитики не должна ронять UI
        log_event(logger, stage="recommendations", event="UI-004", level="ERROR",
                   detail=f"build_recommendations упал: {str(exc)[:300]} — пустой список")
        return []
    finally:
        if owns_driver:
            driver.close()


# ─── get_recommendations ─────────────────────────────────────────────────
# Назначение: рекомендации (У-1) поверх готового ответа — публичная обёртка
#   над `_cached_recommendations`: нормализует вопрос для ключа кэша, driver
#   передаёт под подчёркиванием (не участвует в хэше). НЕ пишет в кэш ответов
#   `answer_cache.json` (решение оркестратора) — только in-memory кэш Streamlit
#   на время сессии демо.
# Входные связи: question (для ключа кэша), answer — уже полученный Answer,
#   driver — свой Neo4j-driver вызывающей стороны (опционально; None — функция
#   откроет и закроет свой)
# Выходные данные: list[contracts.Recommendation] — до top_k каждого вида
# Уровень: ✅ реализовано (A-15)
def get_recommendations(
    question: str,
    answer: Answer,
    *,
    driver=None,
    top_k: int = 3,
) -> list[Recommendation]:
    return _cached_recommendations(
        answer_cache.normalize_question(question), answer, _driver=driver, top_k=top_k
    )
