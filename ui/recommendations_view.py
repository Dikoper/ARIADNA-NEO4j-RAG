"""Панель «Рекомендации» (У-1, A-15) — рендер `list[contracts.Recommendation]`
рядом с ответом чата: похожие кейсы / эксперты и команды / смежные темы.

Вход: список `Recommendation` (см. `contracts.py` — kind/title/reason/citations),
уже посчитанный `ui.backend.get_recommendations` (сам модуль сюда не заглядывает —
только отображает готовые данные, как и остальные `ui/*_view.py`).
Выход: секции Streamlit — заголовок вида, карточка (иконка+title+reason),
цитаты-источники в `st.expander` (формат — `ui.citations_view.format_citation`,
тот же вид, что у цитат ответа).

Зависимости: `streamlit`, `ariadna.contracts.Recommendation/RecommendationKind`,
`ui.citations_view.format_citation`. Инвариант: порядок групп фиксирован —
similar_case -> expert -> adjacent_topic (порядок задания У-1); пустые группы
не отображаются; полностью пустой список — мягкая подпись, без выдумывания
данных. `KIND_ORDER`/`KIND_TITLES_RU`/`group_recommendations` — публичные,
переиспользуются `ui.export_md` для секции «Рекомендации» в MD-отчёте (тот же
порядок и заголовки групп, что на экране).
"""
from __future__ import annotations

import streamlit as st

from ariadna.contracts import Recommendation, RecommendationKind
from ui.citations_view import format_citation

# Порядок групп фиксирован постановкой задачи (У-1): похожий кейс -> эксперт ->
# смежная тема. Ключ словаря = RecommendationKind.value (строка контракта).
KIND_ORDER: list[RecommendationKind] = [
    RecommendationKind.SIMILAR_CASE,
    RecommendationKind.EXPERT,
    RecommendationKind.ADJACENT_TOPIC,
]

KIND_TITLES_RU: dict[RecommendationKind, str] = {
    RecommendationKind.SIMILAR_CASE: "Похожие кейсы",
    RecommendationKind.EXPERT: "Эксперты и команды",
    RecommendationKind.ADJACENT_TOPIC: "Смежные темы",
}

KIND_ICONS: dict[RecommendationKind, str] = {
    RecommendationKind.SIMILAR_CASE: "📄",
    RecommendationKind.EXPERT: "👤",
    RecommendationKind.ADJACENT_TOPIC: "🧭",
}

NO_RECOMMENDATIONS_NOTE = "Рекомендации не найдены — по этому вопросу подсказок пока нет."


# ─── group_recommendations ───────────────────────────────────────────────
# Назначение: группирует рекомендации по виду в фиксированном порядке
#   (similar_case -> expert -> adjacent_topic); виды без рекомендаций в
#   результат не попадают (пустые группы не рисуются вызывающим кодом).
# Входные связи: contracts.Recommendation.kind
# Выходные данные: список пар (RecommendationKind, list[Recommendation]) —
#   список, а не dict, чтобы порядок групп не зависел от версии Python/PYTHONHASHSEED
# Уровень: ✅ реализовано (A-15)
def group_recommendations(
    recommendations: list[Recommendation],
) -> list[tuple[RecommendationKind, list[Recommendation]]]:
    groups: list[tuple[RecommendationKind, list[Recommendation]]] = []
    for kind in KIND_ORDER:
        items = [r for r in recommendations if r.kind == kind]
        if items:
            groups.append((kind, items))
    return groups


# ─── _render_card ─────────────────────────────────────────────────────────
# Назначение: одна карточка рекомендации — иконка вида + title, reason курсивом
#   (если есть), источники-цитаты в st.expander (формат — как у цитат ответа).
# Уровень: ✅ реализовано (A-15)
def _render_card(kind: RecommendationKind, rec: Recommendation) -> None:
    st.markdown(f"{KIND_ICONS[kind]} **{rec.title}**")
    if rec.reason.strip():
        st.caption(rec.reason.strip())
    if rec.citations:
        with st.expander(f"Источники ({len(rec.citations)})"):
            for cit in rec.citations:
                st.caption(format_citation(cit))


# ─── expert_title_key ─────────────────────────────────────────────────────
# Назначение: нормализованный ключ имени эксперта — отсортированное множество
#   слов без точек, регистронезависимо; общий для дедупа рекомендаций и
#   фильтра дублей с людьми подграфа во вкладке «Эксперты» (app.py).
# Входные связи: title рекомендации / имя узла (без иконки)
# Выходные данные: tuple[str, ...] — hashable-ключ
# Уровень: ✅ реализовано (A-24, ревью)
def expert_title_key(title: str) -> tuple[str, ...]:
    return tuple(sorted(title.lower().replace(".", " ").split()))


# ─── dedupe_expert_titles ─────────────────────────────────────────────────
# Назначение: убирает дубли экспертов с разным порядком слов/инициалов
#   («Кузнецов Р.А.» и «Р.А. Кузнецов» — один человек; артефакт извлечения
#   авторов, виден на вопросе №4 жюри) — ключ expert_title_key. Остаётся
#   ПЕРВАЯ запись (порядок значимости A-14). Принятый компромисс (ревью
#   A-24): ключ нечувствителен к порядку слов, поэтому два РАЗНЫХ человека,
#   различающиеся только порядком инициалов («Кузнецов Р.А.»/«Кузнецов А.Р.»),
#   склеились бы — в данных корпуса такой пары нет, а артефакт перестановки
#   ФИО одного человека есть. Записи других видов проходят без изменений —
#   функцию можно применять к полному списку рекомендаций один раз (экран и
#   MD-экспорт видят одинаковый список).
# Входные связи: список Recommendation (любых видов)
# Выходные данные: новый список без дублей экспертов (вход не мутируется)
# Уровень: ✅ реализовано (A-24, правка PM 04.07 ~20:00; ревью — общий список)
def dedupe_expert_titles(items: list[Recommendation]) -> list[Recommendation]:
    seen: set[tuple[str, ...]] = set()
    result = []
    for rec in items:
        if rec.kind == RecommendationKind.EXPERT:
            key = expert_title_key(rec.title)
            if key in seen:
                continue
            seen.add(key)
        result.append(rec)
    return result


# ─── render_kind_cards ────────────────────────────────────────────────────
# Назначение: карточки ОДНОГО вида рекомендаций без заголовка группы (A-24) —
#   вкладка «👥 Эксперты» правой колонки показывает рекомендации вида expert
#   у себя (заголовок ей даёт ярлык вкладки), не дублируя их в «Рекомендациях».
# Входные связи: kind + список Recommendation этого вида (фильтрует вызывающий)
# Выходные данные: нет (побочный эффект — виджеты Streamlit)
# Уровень: ✅ реализовано (A-24)
def render_kind_cards(kind: RecommendationKind, items: list[Recommendation]) -> None:
    for rec in items:
        _render_card(kind, rec)


# ─── render_recommendations ──────────────────────────────────────────────
# Назначение: рендер всей панели «Рекомендации» — три группы с RU-заголовками,
#   пустые группы опускаются; если рекомендаций нет вовсе — мягкая подпись
#   NO_RECOMMENDATIONS_NOTE вместо пустого раздела.
# Входные связи: list[contracts.Recommendation] (Answer.recommendations или
#   результат ui.backend.get_recommendations)
# Выходные данные: нет (побочный эффект — виджеты Streamlit)
# Уровень: ✅ реализовано (A-15)
def render_recommendations(recommendations: list[Recommendation]) -> None:
    if not recommendations:
        st.caption(NO_RECOMMENDATIONS_NOTE)
        return
    for kind, items in group_recommendations(recommendations):
        st.markdown(f"**{KIND_TITLES_RU[kind]}**")
        for rec in items:
            _render_card(kind, rec)
