"""Streamlit-демо «Ариадны» (A-13, редизайн — A-24): вкладка «Чат» (вопрос ->
Answer) двумя колонками — слева текст ответа с экспортом в шапке и вкладками
«Источники» (карточки с «Открыть документ») / «Россия vs зарубеж», справа
вкладки «Подграф» / «⚠ Противоречия» (У-3) / «👥 Эксперты» / «Рекомендации»
(У-1); над колонками — чипы «Как система поняла вопрос» (детерминированный
роутер). Вторая вкладка приложения — «Карта пробелов ⭐» (экспорт в шапке,
вкладки «Таблица» / «Только в 🇷🇺/🌍»).

Вход: вопрос пользователя (текст/пресет) + сайдбар-фильтры (гео/год; A-23 —
типы объектов, порог уверенности связей, размер подграфа). Выход: экраны
Streamlit; никакой бизнес-логики здесь не пишется — только вызовы `ui.backend`
(обёртка над `search.answer_question`/`graph.templates.fetch_subgraph`/
`analytics.gap_map.build_gap_report`/`analytics.recommendations.
build_recommendations`, все с ленивым импортом и честной деградацией) и
хелперов `ui.answer_cache`/`ui.subgraph_view`/`ui.citations_view`/`ui.
gap_view`/`ui.recommendations_view`/`ui.question_view`/`ui.source_cards`.

Запуск: `.venv/bin/python -m streamlit run ui/app.py` из корня репозитория
(нужен корень в sys.path для `from ui import ...` — обеспечивает `-m`).
Пользователь — исследователь без подготовки в графовых БД: слова «чанк»/
«Cypher» в интерфейсе не используются (паспорт docs/dev/modules/ui.md).
"""
from __future__ import annotations

from datetime import datetime

import streamlit as st
from streamlit_agraph import agraph

from ariadna.contracts import Answer, Contradiction, GapReport, RecommendationKind
from ui import backend
from ui.citations_view import GEOGRAPHY_FILTER_UNAVAILABLE_NOTE, filter_citations_by_year, format_citation
from ui.export_md import answer_to_markdown, gap_report_to_markdown
from ui.gap_view import GAP_MATRIX_GEOGRAPHY_NOTE, build_gap_rows, select_geography_topics
from ui.question_view import build_question_chips
from ui.recommendations_view import (
    dedupe_expert_titles,
    expert_title_key,
    render_kind_cards,
    render_recommendations,
)
from ui.source_cards import render_source_cards
from ui.subgraph_view import (
    ENTITY_TYPE_LABELS_RU,
    build_agraph_elements,
    format_flat_node_list,
    legend_items,
    list_experts_and_facilities,
)

st.set_page_config(page_title="Ариадна — карта знаний R&D", layout="wide")

# Диапазон честный, как в README («Ограничения»): 24–480 с в зависимости от
# загрузки локальной модели — расхождение UI/README отмечал reviewer A-19.
SYNTHESIS_WAIT_NOTICE = (
    "Синтез ответа локальной моделью может занять от полуминуты до 8 минут — "
    "это честная оценка живого прогона (не зависание). Пожалуйста, дождитесь "
    "завершения."
)

# Подбор рекомендаций (У-1, A-15) — отдельный от синтеза ответа шаг, честная
# верхняя оценка ожидания (контракт A-14: build_recommendations < 5 с, запас
# на холодный старт подключения к стенду).
RECOMMENDATIONS_WAIT_NOTICE = "Подбор рекомендаций может занять до 10 секунд."


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


# Назначение: кэш чипов разбора вопроса (ревью A-24) — без кэша route()
#   выполнялся бы на каждый rerun (клик по любому виджету) и на вопросах вне
#   шаблонов плодил бы файл лога SEARCH-001 на каждый клик (см. пре-комментарий
#   ui.question_view.get_intent).
# Уровень: ✅ реализовано (A-24, ревью)
@st.cache_data(ttl=3600, show_spinner=False)
def _cached_question_chips(question: str) -> tuple[list[str], bool]:
    return build_question_chips(question)


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
#   fetch_subgraph недоступен/стенд не отвечает. С A-24 живёт во вкладке
#   «Подграф» правой колонки — свой подзаголовок не рисует (дублировал бы ярлык).
# Уровень: ✅ реализовано (A-13, фильтры и легенда — A-23, вкладка — A-24)
def _render_subgraph(subgraph_node_ids: list[str], filters: dict) -> None:
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


# Назначение: рендер списка противоречий (У-3) — с A-24 живёт во вкладке
#   «⚠ Противоречия (n)» правой колонки; счётчик в ярлыке вкладки не даёт
#   спрятать сигнал (инвариант У-3 при табличной раскладке). Пустой список —
#   честная подпись, а не пустая вкладка.
# Уровень: ✅ реализовано (A-13, вкладка — A-24)
def _render_contradictions(contradictions: list[Contradiction]) -> None:
    if not contradictions:
        st.caption("Противоречий между источниками этого ответа не выявлено.")
        return
    st.warning("⚠ Противоречия в источниках")
    for i, c in enumerate(contradictions, start=1):
        st.markdown(f"**{i}.** «{c.claim_a}» **противоречит** «{c.claim_b}»")
        for cit in c.citations:
            st.caption(format_citation(cit))


# Назначение: чипы «Как система поняла вопрос» (A-24) — материал/процесс,
#   числовые условия, география, период, бейдж «Россия vs зарубеж»; источник —
#   детерминированный роутер (мгновенно, без LLM). Ничего не распознано или
#   роутер недоступен — блок честно опускается (пустую рамку не рисуем).
# Уровень: ✅ реализовано (A-24)
def _render_question_chips(chips: list[str]) -> None:
    if not chips:
        return
    badges = " ".join(f":blue-badge[{chip}]" for chip in chips)
    st.markdown(f"🧩 **Как система поняла вопрос:** {badges}")


# Назначение: вкладки левой колонки (A-24) — «Источники (N)» (карточки с
#   фильтром года) и «Россия vs зарубеж» (при сравнительном вопросе; таблица —
#   следующий шаг, пока честная подпись, что сравнение дано в тексте). Вкладка
#   сравнения рисуется и БЕЗ цитат (ревью A-24: у честного «не найдено» цитат
#   нет, а чип сравнения обещает вкладку); текст ответа и кнопка экспорта —
#   выше, в _render_answer (текст не должен ждать подбора рекомендаций).
# Уровень: ✅ реализовано (A-24, вкладка сравнения без цитат — ревью)
def _render_answer_left_tabs(answer: Answer, *, compare_mode: bool, filters: dict) -> None:
    tab_labels = []
    if answer.citations:
        visible_citations = filter_citations_by_year(
            answer.citations, year_from=filters["year_from"], year_to=filters["year_to"]
        )
        if len(visible_citations) < len(answer.citations):
            tab_labels.append(f"Источники ({len(visible_citations)} из {len(answer.citations)})")
        else:
            tab_labels.append(f"Источники ({len(answer.citations)})")
    if compare_mode:
        tab_labels.append("Россия vs зарубеж")
    if not tab_labels:
        return

    tabs = st.tabs(tab_labels)
    if answer.citations:
        with tabs[0]:
            if len(visible_citations) < len(answer.citations):
                st.caption("Часть источников скрыта фильтром года — номера карточек сохранены.")
            render_source_cards(answer.citations, visible_citations)
    if compare_mode:
        with tabs[-1]:
            st.caption(
                "Вопрос распознан как сравнение отечественной и зарубежной практики — "
                "само сравнение дано в тексте ответа. Отдельная таблица готовится."
            )


# Назначение: правая колонка ответа (A-24) — вкладки «Подграф» (фильтры A-23),
#   «⚠ Противоречия (n)» (У-3: счётчик всегда в ярлыке), «👥 Эксперты (n)» —
#   Expert/Facility из полного подграфа ответа ПЛЮС рекомендации вида expert
#   (правка PM: подграф ответа может не содержать людей, а обход графа
#   рекомендаций их находит — показываем оба источника, без дублей в
#   «Рекомендациях»), «Рекомендации» (У-1, A-15) — остальные виды. Дедуп
#   экспертов сделан выше по потоку (_render_answer — экран и MD-экспорт видят
#   один список, ревью A-24); люди подграфа, совпадающие по expert_title_key с
#   рекомендованным экспертом, не дублируются буллетом (ревью A-24) — счётчик
#   ярлыка честный.
# Уровень: ✅ реализовано (A-24, эксперты из рекомендаций — правка PM 04.07
#   ~20:00, дедуп против подграфа — ревью)
def _render_answer_right(answer: Answer, recommendations, filters: dict) -> None:
    expert_recs = [r for r in recommendations if r.kind == RecommendationKind.EXPERT]
    other_recs = [r for r in recommendations if r.kind != RecommendationKind.EXPERT]
    subgraph = _get_subgraph(answer.subgraph_node_ids) if answer.subgraph_node_ids else None
    rec_keys = {expert_title_key(r.title) for r in expert_recs}
    people = [
        row for row in (list_experts_and_facilities(subgraph) if subgraph else [])
        # строка вида «👤 Имя» — ключ считается по имени без иконки
        if expert_title_key(row.split(" ", 1)[1] if " " in row else row) not in rec_keys
    ]

    tab_graph, tab_contra, tab_people, tab_recs = st.tabs([
        "Подграф",
        f"⚠ Противоречия ({len(answer.contradictions)})",
        f"👥 Эксперты ({len(people) + len(expert_recs)})",
        "Рекомендации",
    ])
    with tab_graph:
        _render_subgraph(answer.subgraph_node_ids, filters)
    with tab_contra:
        _render_contradictions(answer.contradictions)
    with tab_people:
        if people:
            st.caption("Эксперты и организации, связанные с темой ответа:")
            for row in people:
                st.markdown(f"- {row}")
        if expert_recs:
            if people:
                st.markdown("**Рекомендованы по смежным работам**")
            render_kind_cards(RecommendationKind.EXPERT, expert_recs)
        if not people and not expert_recs:
            st.caption("Эксперты и организации по теме этого ответа не найдены.")
    with tab_recs:
        render_recommendations(other_recs)


# Назначение: рендер уже полученного Answer (из кэша или свежего синтеза) —
#   с A-24 двумя колонками: слева текст с экспортом в шапке и вкладками
#   источников, справа вкладки подграф/противоречия/эксперты/рекомендации;
#   над колонками — чипы разбора вопроса и, при противоречиях, всегда видимый
#   красный баннер У-3 (ревью A-24: сигнал не должен жить только во вкладке).
#   Текст ответа рисуется ДО подбора рекомендаций (ревью A-24: готовый ответ
#   не ждёт стенд), кнопка экспорта заполняет плейсхолдер шапки после подбора —
#   MD-отчёт включает свежие рекомендации (A-15), даже если Answer из кэша.
# Уровень: ✅ реализовано (A-13, экспорт и фильтры — A-23, рекомендации — A-15,
#   двухколоночная раскладка и вкладки — A-24, баннер У-3 и порядок — ревью)
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

    chips, compare_mode = _cached_question_chips(answer.question)
    _render_question_chips(chips)

    if answer.contradictions:
        st.warning(
            f"⚠ В источниках этого ответа найдены противоречащие данные "
            f"({len(answer.contradictions)}) — подробности во вкладке «⚠ Противоречия» справа."
        )

    col_left, col_right = st.columns([11, 9], gap="medium")
    with col_left:
        export_slot = st.container()
        st.markdown(answer.text)

    # Подбор рекомендаций — ПОСЛЕ отрисовки текста (готовый ответ виден сразу,
    # спиннер крутится под колонками) и до экспорта/вкладок, которым нужен
    # результат. Дедуп экспертов здесь, один раз — экран и MD видят одно и то же.
    with st.spinner(RECOMMENDATIONS_WAIT_NOTICE):
        recommendations = dedupe_expert_titles(backend.get_recommendations(answer.question, answer))
    export_answer = answer.model_copy(update={"recommendations": recommendations})

    with export_slot:
        st.download_button(
            "Скачать отчёт (.md)",
            data=answer_to_markdown(export_answer, generated_at=datetime.now().strftime("%d.%m.%Y %H:%M")),
            file_name="ariadna_answer.md",
            mime="text/markdown",
        )
    with col_left:
        _render_answer_left_tabs(answer, compare_mode=compare_mode, filters=filters)
    with col_right:
        _render_answer_right(answer, recommendations, filters)


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


# Назначение: компактный список тем гео-блока карты пробелов (правка PM
#   04.07 ~20:10) — счётчик тем + прокручиваемый контейнер фиксированной
#   высоты (листается сам список, а не вся страница); темы одним markdown-
#   списком (межстрочные отступы меньше, чем у отдельных абзацев).
# Уровень: ✅ реализовано (A-24)
def _render_topic_list(topics: list[str]) -> None:
    if not topics:
        st.caption("Пусто.")
        return
    st.caption(f"Тем в списке: {len(topics)}")
    with st.container(height=420, border=True):
        st.markdown("\n".join(f"- {topic}" for topic in topics))


# Короткие ярлыки подвкладок гео-блоков карты пробелов — заголовки блоков
# select_geography_topics слишком длинные для вкладки.
_GEO_BLOCK_TAB_LABELS: dict[str, str] = {
    "Только в отечественной литературе": "🇷🇺 отечественная",
    "Только в зарубежной литературе": "🌍 зарубежная",
}


# Назначение: вкладка «Карта пробелов ⭐» — с A-24 экспорт и чекбокс «только
#   пробелы» в шапке (не листать вниз), содержимое двумя вкладками: «Таблица»
#   (ячейки, пробелы n_sources=0 выделены) и «Только в 🇷🇺 / только в 🌍»
#   (блоки only_ru/only_foreign по гео-фильтру сайдбара; правка PM — RU и
#   зарубеж разведены по подвкладкам со счётчиками, списки в прокручиваемых
#   контейнерах). Фолбэк «раздел готовится», если analytics.gap_map ещё не
#   приземлился/стенд недоступен.
# Уровень: ✅ реализовано (A-13, экспорт и чекбокс — A-23, шапка и вкладки — A-24)
def _render_gap_tab(geography_filter: str) -> None:
    st.subheader("Карта пробелов ⭐")
    st.caption("Комбинации «материал–процесс–условие», для которых в корпусе не нашлось источников.")

    report = _get_gap_report()
    if report is None:
        st.info("Раздел готовится — обратитесь к администратору демо.")
        return

    head_export, head_filter = st.columns([1, 2], gap="medium")
    with head_export:
        st.download_button(
            "Скачать карту пробелов (.md)",
            data=gap_report_to_markdown(report, generated_at=datetime.now().strftime("%d.%m.%Y %H:%M")),
            file_name="ariadna_gap_report.md",
            mime="text/markdown",
        )
    with head_filter:
        gaps_only = st.checkbox("Показывать только пробелы (0 источников)", value=False)

    tab_table, tab_geo = st.tabs(["Таблица комбинаций", "Только в 🇷🇺 / только в 🌍"])

    with tab_table:
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

    with tab_geo:
        blocks = select_geography_topics(report, geography_filter)
        if len(blocks) > 1:
            labels = [
                f"{_GEO_BLOCK_TAB_LABELS.get(title, title)} ({len(topics)})"
                for title, topics in blocks.items()
            ]
            for sub_tab, (title, topics) in zip(st.tabs(labels), blocks.items()):
                with sub_tab:
                    st.markdown(f"**{title}**")
                    _render_topic_list(topics)
        else:
            for title, topics in blocks.items():
                st.markdown(f"**{title}**")
                _render_topic_list(topics)


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
