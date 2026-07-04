"""Streamlit-демо «Ариадны» (A-13): вкладка «Чат» (вопрос -> Answer с цитатами,
подграф ответа, contradicts красным — У-3) + вкладка «Карта пробелов ⭐».

Вход: вопрос пользователя (текст/пресет) + сайдбар-фильтры (гео/год; A-23 —
типы объектов, порог уверенности связей, размер подграфа). Экспорт ответа и
карты пробелов в Markdown — кнопки скачивания (A-23, ui.export_md). Выход:
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

from datetime import datetime

import streamlit as st
from streamlit_agraph import agraph

from ariadna.contracts import Answer, Contradiction, GapReport
from ui import backend
from ui.citations_view import GEOGRAPHY_FILTER_UNAVAILABLE_NOTE, filter_citations_by_year, format_citation
from ui.export_md import answer_to_markdown, gap_report_to_markdown
from ui.gap_view import GAP_MATRIX_GEOGRAPHY_NOTE, build_gap_rows, select_geography_topics
from ui.subgraph_view import ENTITY_TYPE_LABELS_RU, build_agraph_elements, format_flat_node_list, legend_items

st.set_page_config(page_title="Ариадна — карта знаний R&D", layout="wide")

# Диапазон честный, как в README («Ограничения»): 24–480 с в зависимости от
# загрузки локальной модели — расхождение UI/README отмечал reviewer A-19.
SYNTHESIS_WAIT_NOTICE = (
    "Синтез ответа локальной моделью может занять от полуминуты до 8 минут — "
    "это честная оценка живого прогона (не зависание). Пожалуйста, дождитесь "
    "завершения."
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


# Назначение: сайдбар с фильтрами; возвращает словарь выбранных значений для
#   вкладок ниже. Год — реально фильтрует цитаты чата (Citation.year есть в
#   контракте); гео влияет только на карту пробелов (only_ru/only_foreign) —
#   для цитат данных о географии источника нет (Citation без этого поля),
#   честно поясняем это под контролом, а не прячем ограничение. Фильтры
#   подграфа (A-23) применяются к уже загруженным данным — переключение не
#   ходит на стенд заново (кэш _cached_subgraph по node_ids).
# Уровень: ✅ реализовано (A-13, фильтры подграфа — A-23)
def _render_sidebar() -> dict:
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
    years_valid = year_from <= year_to
    if not years_valid:
        st.sidebar.warning("Начальный год больше конечного — проверьте фильтр.")

    st.sidebar.markdown("**Подграф ответа**")
    type_labels = list(ENTITY_TYPE_LABELS_RU.values())
    selected_labels = st.sidebar.multiselect(
        "Типы объектов", type_labels, default=type_labels,
        help="Пустой выбор означает «показывать все типы».",
    )
    label_to_type = {label: t for t, label in ENTITY_TYPE_LABELS_RU.items()}
    selected_types = {label_to_type[label] for label in selected_labels}
    entity_types = None if not selected_types or len(selected_types) == len(type_labels) else selected_types

    min_confidence = st.sidebar.slider(
        "Минимальная уверенность связи", min_value=0.0, max_value=1.0, value=0.0, step=0.05,
    )
    st.sidebar.caption("Связи-противоречия (красные) показываются всегда, независимо от порога.")
    max_nodes = st.sidebar.slider(
        "Число объектов на схеме", min_value=15, max_value=backend.MAX_SUBGRAPH_NODES,
        value=backend.MAX_SUBGRAPH_NODES, step=5,
        help="Меньше объектов — свободнее раскладка и крупнее подписи.",
    )

    return {
        "geography": geography_filter,
        "year_from": None if not years_valid or year_from == 1950 else int(year_from),
        "year_to": None if not years_valid or year_to == 2100 else int(year_to),
        "entity_types": entity_types,
        "min_confidence": float(min_confidence),
        "max_nodes": int(max_nodes),
    }


# Назначение: рендер подграфа ответа (agraph) с фильтрами сайдбара (A-23:
#   типы объектов, порог уверенности связей, размер схемы) и цветовой
#   легендой типов; честный фолбэк (плоский список ID узлов), если
#   fetch_subgraph недоступен/стенд не отвечает.
# Уровень: ✅ реализовано (A-13, фильтры и легенда — A-23)
def _render_subgraph(subgraph_node_ids: list[str], filters: dict) -> None:
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
    view_kwargs = {
        "max_nodes": filters["max_nodes"],
        "allowed_types": filters["entity_types"],
        "min_edge_confidence": filters["min_confidence"],
    }
    nodes, edges, config = build_agraph_elements(subgraph, **view_kwargs)
    if not nodes:
        st.caption("Под выбранные фильтры не попал ни один объект — ослабьте фильтры в боковой панели.")
        return
    legend_html = " · ".join(
        f"<span style='color:{color}'>●</span> {label}"
        for label, color in legend_items(subgraph, max_nodes=filters["max_nodes"], allowed_types=filters["entity_types"])
    )
    st.markdown(legend_html + " · ◆ техническое решение", unsafe_allow_html=True)
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
#   подграф с фильтрами A-23, кнопка экспорта отчёта в Markdown (A-23).
# Уровень: ✅ реализовано (A-13, экспорт и фильтры — A-23)
def _render_answer(answer: Answer, *, from_cache: bool, filters: dict) -> None:
    if from_cache:
        st.info("Ответ показан из кэша демо.")
        if st.button("Пересчитать заново (живой синтез, до 8 минут)"):
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

    visible_citations = filter_citations_by_year(
        answer.citations, year_from=filters["year_from"], year_to=filters["year_to"]
    )
    if answer.citations:
        st.subheader("Источники")
        if len(visible_citations) < len(answer.citations):
            st.caption(f"Показано {len(visible_citations)} из {len(answer.citations)} — остальные скрыты фильтром года.")
        for i, cit in enumerate(answer.citations, start=1):
            if cit in visible_citations:
                st.markdown(f"[{i}] {format_citation(cit)}")

    st.download_button(
        "Скачать отчёт (.md)",
        data=answer_to_markdown(answer, generated_at=datetime.now().strftime("%d.%m.%Y %H:%M")),
        file_name="ariadna_answer.md",
        mime="text/markdown",
    )

    _render_contradictions(answer.contradictions)
    _render_subgraph(answer.subgraph_node_ids, filters)


# Назначение: вкладка «Чат» — пресеты 4 эталонных вопросов жюри + свободный
#   ввод, вызов backend.get_answer со спиннером с честным текстом ожидания.
# Уровень: ✅ реализовано (A-13)
def _render_chat_tab(filters: dict) -> None:
    st.subheader("Задайте вопрос по корпусу R&D")

    st.caption("Готовые запросы жюри:")
    preset_cols = st.columns(len(backend.PRESET_QUESTIONS))
    for col, (label, question_text) in zip(preset_cols, backend.PRESET_QUESTIONS):
        if col.button(label, width="stretch"):
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
            filters=filters,
        )


# Назначение: вкладка «Карта пробелов ⭐» — таблица ячеек (пробелы n_sources=0
#   выделены; чекбокс A-23 «только пробелы») + блоки only_ru/only_foreign по
#   гео-фильтру сайдбара + кнопка экспорта отчёта в Markdown (A-23). Фолбэк
#   «раздел готовится», если analytics.gap_map ещё не приземлился/стенд недоступен.
# Уровень: ✅ реализовано (A-13, экспорт и чекбокс — A-23)
def _render_gap_tab(geography_filter: str) -> None:
    st.subheader("Карта пробелов ⭐")
    st.caption("Комбинации «материал–процесс–условие», для которых в корпусе не нашлось источников.")

    report = _get_gap_report()
    if report is None:
        st.info("Раздел готовится — обратитесь к администратору демо.")
        return

    gaps_only = st.checkbox("Показывать только пробелы (0 источников)", value=False)
    rows = build_gap_rows(report)
    if gaps_only:
        rows = [row for row in rows if row["is_gap"]]
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
            width="stretch",
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

    st.download_button(
        "Скачать карту пробелов (.md)",
        data=gap_report_to_markdown(report, generated_at=datetime.now().strftime("%d.%m.%Y %H:%M")),
        file_name="ariadna_gap_report.md",
        mime="text/markdown",
    )


def main() -> None:
    st.title("Ариадна — карта знаний R&D горно-металлургической отрасли")
    filters = _render_sidebar()

    tab_chat, tab_gaps = st.tabs(["Чат", "Карта пробелов ⭐"])
    with tab_chat:
        _render_chat_tab(filters)
    with tab_gaps:
        _render_gap_tab(filters["geography"])


# Guard: Streamlit исполняет этот файл как __main__ (обычный запуск) или
#   __page__ (некоторые версии multipage-навигации) — рендер должен запускаться
#   только при реальном исполнении Streamlit, а не при `import ui.app` в тестах.
if __name__ in ("__main__", "__page__"):
    main()
