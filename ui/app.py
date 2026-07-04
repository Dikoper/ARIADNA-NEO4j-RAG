"""Streamlit-демо «Ариадны» (A-13): вкладка «Чат» (вопрос -> Answer с цитатами,
подграф ответа, contradicts красным — У-3) + вкладка «Карта пробелов ⭐».

Вход: вопрос пользователя (текст/пресет) + сайдбар-фильтры гео/год. Выход:
экраны Streamlit; никакой бизнес-логики здесь не пишется — только вызовы
`ui.backend` (обёртка над `search.answer_question`/`graph.templates.
fetch_subgraph`/`analytics.gap_map.build_gap_report`, все с ленивым импортом
и честной деградацией) и чистых хелперов `ui.answer_cache`/`ui.subgraph_view`/
`ui.citations_view`/`ui.gap_view`.

Запуск: `.venv/bin/python -m streamlit run ui/app.py` из корня репозитория
(нужен корень в sys.path для `from ui import ...` — обеспечивает `-m`).
Пользователь — исследователь без подготовки в графовых БД: слова «чанк»/
«Cypher» в интерфейсе не используются (паспорт docs/dev/modules/ui.md).
"""
from __future__ import annotations

import streamlit as st
from streamlit_agraph import agraph

from ariadna.contracts import Answer, Contradiction, GapReport
from ui import backend
from ui.citations_view import GEOGRAPHY_FILTER_UNAVAILABLE_NOTE, filter_citations_by_year, format_citation
from ui.gap_view import GAP_MATRIX_GEOGRAPHY_NOTE, build_gap_rows, select_geography_topics
from ui.subgraph_view import build_agraph_elements, format_flat_node_list

st.set_page_config(page_title="Ариадна — карта знаний R&D", layout="wide")

SYNTHESIS_WAIT_NOTICE = (
    "Синтез ответа локальной моделью может занять от 2 до 7 минут — это честная "
    "оценка живого прогона (не зависание). Пожалуйста, дождитесь завершения."
)


# Назначение: кэш-обёртки над ui.backend для тяжёлых вызовов (карта пробелов —
#   15-30 с на построение, подграф — секунды на большой узел), чтобы Streamlit
#   не пересчитывал их на каждый rerun скрипта (клик по любому виджету).
#   Живут в app.py (не в backend.py), т.к. backend.py обязан импортироваться
#   без Streamlit-контекста (tests/ui импортируют его напрямую) — декоратор
#   @st.cache_data вне ScriptRunContext просто исполняет функцию с ворнингом,
#   что не мешает существующим тестам (они эти обёртки не вызывают).
#   GapReport — pydantic-модель: кэшируем сериализованный dict (гарантированно
#   переваривается st.cache_data) и восстанавливаем модель после чтения кэша.
# Уровень: ✅ реализовано (fixer, блокер 2)
@st.cache_data(ttl=3600, show_spinner=False)
def _cached_gap_report_dump(limit: int = 50) -> dict | None:
    report = backend.get_gap_report(limit=limit)
    return report.model_dump() if report is not None else None


def _get_gap_report(*, limit: int = 50) -> GapReport | None:
    dump = _cached_gap_report_dump(limit=limit)
    return GapReport.model_validate(dump) if dump is not None else None


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_subgraph(node_ids: tuple[str, ...]) -> dict | None:
    return backend.get_subgraph(list(node_ids))


def _get_subgraph(node_ids: list[str]) -> dict | None:
    return _cached_subgraph(tuple(node_ids))


# Назначение: сайдбар с фильтрами гео/год; возвращает выбранные значения для
#   вкладок ниже. Год — реально фильтрует цитаты чата (Citation.year есть в
#   контракте); гео влияет только на карту пробелов (only_ru/only_foreign) —
#   для цитат данных о географии источника нет (Citation без этого поля),
#   честно поясняем это под контролом, а не прячем ограничение.
# Уровень: ✅ реализовано (A-13)
def _render_sidebar() -> tuple[str, int | None, int | None]:
    st.sidebar.header("Фильтры")

    geography_label_to_code = {"Все": "all", "Отечественная практика": "ru", "Зарубежная практика": "foreign"}
    geography_label = st.sidebar.selectbox("География", list(geography_label_to_code), index=0)
    geography_filter = geography_label_to_code[geography_label]
    st.sidebar.caption(
        "Применяется к карте пробелов (списки «только в отечественной/зарубежной "
        "литературе»). " + GEOGRAPHY_FILTER_UNAVAILABLE_NOTE
    )

    st.sidebar.markdown("**Год публикации** (фильтр цитат в чате)")
    col_from, col_to = st.sidebar.columns(2)
    year_from = col_from.number_input("С", min_value=1950, max_value=2100, value=1950, step=1)
    year_to = col_to.number_input("По", min_value=1950, max_value=2100, value=2100, step=1)
    st.sidebar.caption(
        "Границы по умолчанию (1950/2100) означают «без ограничения». Цитаты без "
        "указанного года всегда показываются — честно, а не скрываются по умолчанию."
    )
    if year_from > year_to:
        st.sidebar.warning("Начальный год больше конечного — проверьте фильтр.")
        return geography_filter, None, None
    year_from_val = None if year_from == 1950 else int(year_from)
    year_to_val = None if year_to == 2100 else int(year_to)
    return geography_filter, year_from_val, year_to_val


# Назначение: рендер подграфа ответа (agraph) или честного фолбэка (плоский
#   список ID узлов), если fetch_subgraph недоступен/стенд не отвечает.
# Уровень: ✅ реализовано (A-13)
def _render_subgraph(subgraph_node_ids: list[str]) -> None:
    st.subheader("Подграф ответа")
    if not subgraph_node_ids:
        st.caption("Для этого ответа подграф пуст.")
        return
    subgraph = _get_subgraph(subgraph_node_ids)
    if subgraph is None:
        st.caption(
            "Визуализация связей временно недоступна — показан список найденных объектов."
        )
        st.text(format_flat_node_list(subgraph_node_ids))
        return
    nodes, edges, config = build_agraph_elements(subgraph)
    st.caption("Красным выделены противоречащие друг другу данные (⚠). Наведите курсор на узел/связь для подробностей.")
    agraph(nodes=nodes, edges=edges, config=config)


# Назначение: рендер списка противоречий (У-3) — блок «⚠ Противоречия».
# Уровень: ✅ реализовано (A-13)
def _render_contradictions(contradictions: list[Contradiction]) -> None:
    if not contradictions:
        return
    st.warning("⚠ Противоречия в источниках")
    for i, c in enumerate(contradictions, start=1):
        st.markdown(f"**{i}.** «{c.claim_a}» **противоречит** «{c.claim_b}»")
        for cit in c.citations:
            st.caption(format_citation(cit))


# Назначение: рендер уже полученного Answer (из кэша или свежего синтеза) —
#   текст, честное «не найдено», цитаты (с фильтром по году), противоречия,
#   подграф.
# Уровень: ✅ реализовано (A-13)
def _render_answer(answer: Answer, *, from_cache: bool, year_from: int | None, year_to: int | None) -> None:
    if from_cache:
        st.info("Ответ показан из кэша демо.")
        if st.button("Пересчитать заново (живой синтез, 2–7 мин)"):
            with st.spinner(SYNTHESIS_WAIT_NOTICE):
                fresh_answer, _ = backend.get_answer(answer.question, force_recompute=True)
            st.session_state["current_answer"] = fresh_answer.model_dump()
            st.session_state["current_from_cache"] = False
            st.rerun()

    if not answer.found:
        st.warning(
            "В корпусе не найдено прямого ответа на этот вопрос. Это может быть "
            "пробел в изученных данных — см. вкладку «Карта пробелов ⭐»."
        )

    st.markdown(answer.text)

    visible_citations = filter_citations_by_year(answer.citations, year_from=year_from, year_to=year_to)
    if answer.citations:
        st.subheader("Источники")
        if len(visible_citations) < len(answer.citations):
            st.caption(f"Показано {len(visible_citations)} из {len(answer.citations)} — остальные скрыты фильтром года.")
        for i, cit in enumerate(answer.citations, start=1):
            if cit in visible_citations:
                st.markdown(f"[{i}] {format_citation(cit)}")

    _render_contradictions(answer.contradictions)
    _render_subgraph(answer.subgraph_node_ids)


# Назначение: вкладка «Чат» — пресеты 4 эталонных вопросов жюри + свободный
#   ввод, вызов backend.get_answer со спиннером с честным текстом ожидания.
# Уровень: ✅ реализовано (A-13)
def _render_chat_tab(year_from: int | None, year_to: int | None) -> None:
    st.subheader("Задайте вопрос по корпусу R&D")

    st.caption("Готовые запросы жюри:")
    preset_cols = st.columns(len(backend.PRESET_QUESTIONS))
    for col, (label, question_text) in zip(preset_cols, backend.PRESET_QUESTIONS):
        if col.button(label, use_container_width=True):
            st.session_state["question_input"] = question_text

    question = st.text_area(
        "Вопрос", key="question_input", height=100,
        placeholder="Например: какие способы закачки шахтных вод применялись в России и за рубежом?",
    )

    if st.button("Спросить", type="primary") and question.strip():
        with st.spinner(SYNTHESIS_WAIT_NOTICE):
            answer, from_cache = backend.get_answer(question.strip())
        st.session_state["current_answer"] = answer.model_dump()
        st.session_state["current_from_cache"] = from_cache
        st.session_state["current_question"] = question.strip()

    if "current_answer" in st.session_state:
        st.divider()
        answer = Answer.model_validate(st.session_state["current_answer"])
        _render_answer(
            answer,
            from_cache=st.session_state.get("current_from_cache", False),
            year_from=year_from,
            year_to=year_to,
        )


# Назначение: вкладка «Карта пробелов ⭐» — таблица ячеек (пробелы n_sources=0
#   выделены) + блоки only_ru/only_foreign по гео-фильтру сайдбара. Фолбэк
#   «раздел готовится», если analytics.gap_map ещё не приземлился/стенд недоступен.
# Уровень: ✅ реализовано (A-13)
def _render_gap_tab(geography_filter: str) -> None:
    st.subheader("Карта пробелов ⭐")
    st.caption("Комбинации «материал–процесс–условие», для которых в корпусе не нашлось источников.")

    report = _get_gap_report()
    if report is None:
        st.info("Раздел готовится — обратитесь к администратору демо.")
        return

    rows = build_gap_rows(report)
    if not rows:
        st.caption("По выбранным темам пробелы не найдены — попробуйте увеличить лимит.")
    else:
        st.dataframe(
            rows,
            column_config={
                "material": "Материал",
                "process": "Процесс",
                "condition": "Условие",
                "n_sources": "Источников найдено",
                "is_gap": st.column_config.CheckboxColumn("Пробел (0 источников)"),
            },
            use_container_width=True,
            hide_index=True,
        )
    st.caption(GAP_MATRIX_GEOGRAPHY_NOTE)

    for title, topics in select_geography_topics(report, geography_filter).items():
        st.markdown(f"**{title}**")
        if topics:
            for topic in topics:
                st.markdown(f"- {topic}")
        else:
            st.caption("Пусто.")


def main() -> None:
    st.title("Ариадна — карта знаний R&D горно-металлургической отрасли")
    geography_filter, year_from, year_to = _render_sidebar()

    tab_chat, tab_gaps = st.tabs(["Чат", "Карта пробелов ⭐"])
    with tab_chat:
        _render_chat_tab(year_from, year_to)
    with tab_gaps:
        _render_gap_tab(geography_filter)


# Guard: Streamlit исполняет этот файл как __main__ (обычный запуск) или
#   __page__ (некоторые версии multipage-навигации) — рендер должен запускаться
#   только при реальном исполнении Streamlit, а не при `import ui.app` в тестах.
if __name__ in ("__main__", "__page__"):
    main()
