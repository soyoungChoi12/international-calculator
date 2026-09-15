"""국외여비 자동계상 — 입력·계산 화면 (STEP 4)."""

from __future__ import annotations

import importlib
from dataclasses import replace
from datetime import date, datetime

import streamlit as st

from config.excel_mapping import FX_SOURCE_CAPTION
from data import travel_rates
from data.travel_rates import GRADES, PAYMENT_CORPORATE, PAYMENT_PERSONAL, PAYMENT_METHODS, ROLES
from services.destination_grade_service import list_countries, resolve_destination_grade
from services.hana_fx import quote_caption
from services import cfb_writer, excel_export, hana_fx, hwp_export, plan_excel_export, plan_parser, travel_calculator

importlib.reload(travel_rates)
importlib.reload(travel_calculator)
importlib.reload(excel_export)
importlib.reload(plan_parser)
importlib.reload(cfb_writer)
importlib.reload(hwp_export)
importlib.reload(plan_excel_export)

from services.excel_export import (
    build_party_excel_bytes,
    excel_filename_for_party,
)
from services.hwp_export import build_hwp_bytes, hwp_filename_for_party
from services.plan_excel_export import (
    build_party_plan_excel_bytes,
    plan_excel_filename_for_party,
)
from services.plan_parser import PlanDocument, parse_plan_pdf, parse_plan_text
from services.travel_calculator import (
    PartyMember,
    PartyResult,
    StayInput,
    TravelInput,
    calculate_for_members,
    calculate_trip_days,
    validate_travel_input,
)

st.set_page_config(page_title="국외여비 자동계상", page_icon="✈️", layout="centered")

st.markdown(
    """
    <style>
    header[data-testid="stHeader"] {display: none;}
    .stAppDeployButton {display: none;}
    .block-container {max-width: 860px; padding-top: 1.4rem;}
    h1 {font-size: 1.45rem; margin-bottom: 0.2rem;}
    .hint {color: #5c5c5c; font-size: 0.9rem; margin-bottom: 1.2rem;}
    div[data-testid="stMetricValue"] {font-size: 1.2rem; font-weight: 700;}
    </style>
    """,
    unsafe_allow_html=True,
)


def _won(value: int) -> str:
    return f"{value:,}원"


def _amount(value: int) -> str:
    return f"**{_won(value)}**"


def _or_default(value, default):
    return default if value is None else value


def _payment_index(method: str) -> int:
    return list(PAYMENT_METHODS).index(method)


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value


def _fx_input_key() -> str:
    return f"exchange_rate_{st.session_state.get('fx_widget_id', 0)}"


def _fx_is_loaded(approval: date) -> bool:
    approval = _as_date(approval)
    return (
        st.session_state.get("fx_loaded_for") == approval.isoformat()
        and st.session_state.get("fx_quote") is not None
    )


def _apply_fx_quote(approval: date, quote: hana_fx.HanaFxQuote) -> None:
    approval = _as_date(approval)
    st.session_state.fx_quote = quote
    st.session_state.fx_loaded_for = approval.isoformat()
    st.session_state.pop("fx_fetch_failed_for", None)
    next_id = int(st.session_state.get("fx_widget_id", 0)) + 1
    st.session_state.fx_widget_id = next_id
    st.session_state[f"exchange_rate_{next_id}"] = float(quote.rate)


def _apply_cached_fx(approval: date) -> None:
    """이미 받아 둔 환율만 즉시 반영한다. 네트워크는 타지 않는다."""
    if _fx_is_loaded(approval):
        return
    cached = hana_fx.peek_cached_usd_cash_buy(approval)
    if cached:
        _apply_fx_quote(approval, cached)


def _sync_fx_for_approval(approval: date) -> None:
    """결재일에 맞는 하나은행 현찰 살 때를 적용환율 칸에 넣는다."""
    approval = _as_date(approval)
    if _fx_is_loaded(approval):
        return
    quote = hana_fx.fetch_usd_cash_buy(approval)
    if quote is None:
        st.session_state.fx_quote = None
        st.session_state.fx_fetch_failed_for = approval.isoformat()
        return
    _apply_fx_quote(approval, quote)


def _init_stay_ids() -> None:
    if "stay_ids" not in st.session_state:
        st.session_state.stay_ids = [0]
        st.session_state.next_stay_id = 1
    if "rental_ids" not in st.session_state:
        st.session_state.rental_ids = []
        st.session_state.next_rental_id = 0
    if "calc_results" not in st.session_state:
        st.session_state.calc_results = {}
    if "traveler_ids" not in st.session_state:
        st.session_state.traveler_ids = [0]
        st.session_state.next_traveler_id = 1


def _plan_traveler_label(traveler) -> str:
    extra = traveler.title or traveler.role
    return f"{traveler.name} ({extra})" if extra else traveler.name


def _refresh_plan_document(plan: PlanDocument | None) -> PlanDocument | None:
    """세션에 남은 이전 계획안도 현재 필드로 다시 읽는다."""
    if plan is None:
        return None
    if hasattr(plan, "purpose_lines") and hasattr(plan, "itinerary") and hasattr(plan, "airfare_note"):
        return plan
    raw = getattr(plan, "raw_text", "") or ""
    if raw:
        return parse_plan_text(raw)
    return plan


def _fill_traveler_rows(travelers) -> None:
    if not travelers:
        return
    first_id = (st.session_state.traveler_ids or [0])[0]
    ids = [first_id]
    first = travelers[0]
    st.session_state.traveler_name = first.name
    st.session_state.traveler_role = first.role
    st.session_state.plan_traveler_pick = _plan_traveler_label(first)
    st.session_state.plan_traveler_title = first.title
    st.session_state.plan_traveler_team = first.team
    st.session_state[f"traveler_title_{first_id}"] = first.title
    st.session_state[f"traveler_team_{first_id}"] = first.team
    for person in travelers[1:]:
        tid = st.session_state.next_traveler_id
        st.session_state.next_traveler_id += 1
        ids.append(tid)
        st.session_state[f"traveler_name_{tid}"] = person.name
        st.session_state[f"traveler_role_{tid}"] = person.role
        st.session_state[f"traveler_title_{tid}"] = person.title
        st.session_state[f"traveler_team_{tid}"] = person.team or first.team
    st.session_state.traveler_ids = ids
    st.session_state.plan_travelers_applied_for = tuple(
        (item.name, item.role, item.title) for item in travelers
    )


def _apply_plan_to_form(plan: PlanDocument, traveler_index: int = 0) -> None:
    _init_stay_ids()
    travelers = plan.travelers
    if travelers:
        traveler_index = max(0, min(traveler_index, len(travelers) - 1))
        _fill_traveler_rows(travelers)
    st.session_state.plan_document = plan
    st.session_state.plan_traveler_index = traveler_index
    if plan.departure:
        st.session_state.departure_date = plan.departure
    if plan.return_on:
        st.session_state.return_date = plan.return_on
    stay_id = st.session_state.stay_ids[0]
    if plan.country:
        st.session_state[f"stay_country_{stay_id}"] = plan.country
    if plan.city:
        st.session_state[f"stay_city_{stay_id}"] = plan.city
    if plan.airfare_krw:
        st.session_state.airfare = int(plan.airfare_krw)
    if plan.preparation_krw:
        st.session_state.preparation = int(plan.preparation_krw)
    departure = plan.departure or st.session_state.get("departure_date")
    return_on = plan.return_on or st.session_state.get("return_date")
    if departure and return_on:
        trip_days = calculate_trip_days(departure, return_on) if return_on >= departure else 0
        nights = plan.nights if plan.nights is not None else max(trip_days - 1, 0)
        st.session_state[f"stay_nights_{stay_id}_{departure}_{return_on}"] = nights
        st.session_state[f"stay_days_{stay_id}_{departure}_{return_on}"] = trip_days


def _render_plan_loader() -> None:
    with st.container(border=True):
        st.subheader("0. 계획안")
        st.caption("계획안 PDF를 첨부하면 출장자·일정·출장지·항공료·준비금을 채웁니다. 출장자가 여러 명이면 한 번에 계산하고, Excel·해외출장 계획 엑셀은 인원별 시트로, 심사신청서는 합산 예산으로 받습니다.")
        uploaded = st.file_uploader("계획안 PDF 첨부", type=["pdf"])
        load_clicked = st.button(
            "계획안 불러오기",
            type="primary",
            use_container_width=True,
            disabled=uploaded is None,
        )
        if load_clicked and uploaded is not None:
            try:
                plan = parse_plan_pdf(uploaded.getvalue())
            except Exception as exc:
                st.error(f"PDF를 읽지 못했습니다. {exc}")
            else:
                _apply_plan_to_form(plan)
                st.rerun()

        plan = _refresh_plan_document(st.session_state.get("plan_document"))
        if plan is not None:
            st.session_state.plan_document = plan
            travelers = getattr(plan, "travelers", ()) or ()
            token = tuple((item.name, item.role, item.title) for item in travelers)
            if travelers and st.session_state.get("plan_travelers_applied_for") != token:
                _init_stay_ids()
                _fill_traveler_rows(travelers)
        if not plan:
            return
        lines = []
        if plan.purpose:
            lines.append(f"- 출장목적: {plan.purpose}")
        if plan.region:
            lines.append(f"- 출장지역: {plan.region}")
        if plan.schedule_text:
            lines.append(f"- 출장일정: {plan.schedule_text}")
        if plan.event_name:
            lines.append(f"- 방문기관/행사명: {plan.event_name}")
        if plan.travelers:
            people = ", ".join(_plan_traveler_label(item) for item in plan.travelers)
            lines.append(f"- 출장자: {people}")
        if plan.budget_category:
            lines.append(f"- 예산과목: {plan.budget_category}")
        place = " ".join(part for part in (plan.country, plan.city) if part) or "-"
        grade = f" ({plan.grade}급)" if plan.grade else ""
        lines.append(f"- 계산 반영: {place}{grade} · 항공료 {plan.airfare_krw:,}원 · 준비금 {plan.preparation_krw:,}원")
        st.markdown("**불러온 항목**")
        st.write("\n".join(lines))
        if plan.grade_message:
            st.success(plan.grade_message)
        for warn in plan.warnings:
            st.warning(warn)


def _add_traveler() -> None:
    new_id = st.session_state.next_traveler_id
    st.session_state.next_traveler_id += 1
    prev_id = st.session_state.traveler_ids[-1]
    prev_role = (
        st.session_state.get("traveler_role")
        if prev_id == st.session_state.traveler_ids[0]
        else st.session_state.get(f"traveler_role_{prev_id}")
    )
    if prev_role:
        st.session_state[f"traveler_role_{new_id}"] = prev_role
    prev_team = st.session_state.get(f"traveler_team_{prev_id}") or st.session_state.get("plan_traveler_team")
    if prev_team:
        st.session_state[f"traveler_team_{new_id}"] = prev_team
    st.session_state.traveler_ids.append(new_id)


def _remove_traveler(traveler_id: int) -> None:
    ids = list(st.session_state.traveler_ids)
    if len(ids) <= 1 or traveler_id == ids[0]:
        return
    st.session_state.traveler_ids = [item for item in ids if item != traveler_id]


def _render_traveler(traveler_id: int, index: int, total: int, first_id: int) -> PartyMember:
    if total > 1:
        title_col, del_col = st.columns([5, 1])
        with title_col:
            st.markdown(f"**출장자 {index}**")
        with del_col:
            if traveler_id != first_id:
                st.button(
                    "삭제",
                    key=f"traveler_del_{traveler_id}",
                    on_click=_remove_traveler,
                    args=(traveler_id,),
                    use_container_width=True,
                )
    name_label = "출장자명" if total == 1 else f"출장자명 {index}"
    role_label = "출장자 구분" if total == 1 else f"출장자 구분 {index}"
    name_key = "traveler_name" if traveler_id == first_id else f"traveler_name_{traveler_id}"
    role_key = "traveler_role" if traveler_id == first_id else f"traveler_role_{traveler_id}"
    title_key = "plan_traveler_title" if traveler_id == first_id else f"traveler_title_{traveler_id}"
    name = st.text_input(
        name_label,
        placeholder="예: 홍길동",
        key=name_key,
        help="Excel 본문에는 넣지 않고, 다운로드 파일명과 심사신청서 출장자에 사용합니다.",
    )
    role_kwargs = {"options": list(ROLES), "key": role_key}
    if role_key not in st.session_state:
        role_kwargs["index"] = 2
    role = st.selectbox(role_label, **role_kwargs)
    title = ""
    if total > 1:
        title = st.text_input("직함", placeholder="예: 전임", key=title_key)
    else:
        title = (st.session_state.get(title_key) or "").strip()
    team = (
        st.session_state.get(f"traveler_team_{traveler_id}")
        or st.session_state.get("plan_traveler_team")
        or ""
    )
    return PartyMember(
        name=(name or "").strip(),
        role=role,
        title=(title or "").strip(),
        team=(team or "").strip(),
    )


def _party_members_from_plan(plan: PlanDocument | None, name: str, role: str) -> list[PartyMember]:
    travelers = getattr(plan, "travelers", ()) if plan else ()
    if travelers and len(travelers) > 1:
        return [
            PartyMember(name=item.name, role=item.role, title=item.title, team=item.team)
            for item in travelers
        ]
    return [
        PartyMember(
            name=(name or "").strip(),
            role=role,
            title=(st.session_state.get("plan_traveler_title") or "").strip(),
            team=(st.session_state.get("plan_traveler_team") or "").strip(),
        )
    ]


def _to_party_results(packed_party: list[dict]) -> list[PartyResult]:
    results = []
    for item in packed_party:
        member = item.get("member")
        if not isinstance(member, PartyMember):
            member = PartyMember(
                name=item.get("name") or "",
                role=item["result"].role,
                title=item.get("title") or "",
                team=item.get("team") or "",
            )
        results.append(PartyResult(member=member, result=item["result"]))
    return results


def _add_stay() -> None:
    new_id = st.session_state.next_stay_id
    st.session_state.next_stay_id += 1
    prev_id = st.session_state.stay_ids[-1]
    prev_country = st.session_state.get(f"stay_country_{prev_id}")
    if prev_country:
        st.session_state[f"stay_country_{new_id}"] = prev_country
    st.session_state.stay_ids.append(new_id)


def _remove_stay(stay_id: int) -> None:
    if len(st.session_state.stay_ids) <= 1:
        return
    st.session_state.stay_ids = [item for item in st.session_state.stay_ids if item != stay_id]


def _add_rental() -> None:
    new_id = st.session_state.next_rental_id
    st.session_state.next_rental_id += 1
    st.session_state.rental_ids.append(new_id)


def _remove_rental(rental_id: int) -> None:
    st.session_state.rental_ids = [item for item in st.session_state.rental_ids if item != rental_id]


def _stay_option_label(index: int, stay: StayInput) -> str:
    place = stay.city.strip() or stay.country.strip()
    if place:
        return f"출장지 {index} · {place}"
    return f"출장지 {index}"


def _slice_line(slices, unit: str) -> str:
    if not slices:
        return ""
    show_grade = len({item.grade for item in slices}) > 1

    def _part(item) -> str:
        prefix = f"{item.grade}급 " if show_grade else ""
        extra = f" ({item.label})" if item.label else ""
        return f"{prefix}{item.rate_usd} USD × {item.quantity}{unit}{extra} = {item.amount_usd} USD"

    if len(slices) == 1 and not slices[0].label:
        item = slices[0]
        return f"{item.rate_usd} USD × {item.quantity}{unit} = {item.amount_usd} USD, 원 미만 절사"
    return " + ".join(_part(item) for item in slices) + ", 원 미만 절사"


def _render_stay(
    stay_id: int,
    index: int,
    total: int,
    suggested_nights: int,
    departure: date,
    return_on: date,
    trip_days: int,
    nights_before: int,
) -> StayInput:
    if total > 1:
        title_col, del_col = st.columns([5, 1])
        with title_col:
            st.markdown(f"**출장지 {index}**")
        with del_col:
            st.button("삭제", key=f"stay_del_{stay_id}", on_click=_remove_stay, args=(stay_id,), use_container_width=True)

    country_col, city_col = st.columns(2)
    with country_col:
        country = st.selectbox(
            "출장 국가",
            options=list_countries(),
            index=None,
            placeholder="선택하거나 직접 입력",
            accept_new_options=True,
            key=f"stay_country_{stay_id}",
        )
    with city_col:
        city = st.text_input("출장 도시", placeholder="예: 샌프란시스코", key=f"stay_city_{stay_id}")

    country_text = (country or "").strip()
    city_text = (city or "").strip()
    grade_lookup = resolve_destination_grade(country_text, city_text)
    grade_ready = bool(country_text and city_text)
    grade = None
    grade_message = ""
    if grade_ready and grade_lookup.ok:
        st.success(grade_lookup.message)
        grade = grade_lookup.grade
        grade_message = grade_lookup.message
    elif grade_ready:
        st.warning(grade_lookup.message)
        grade_label = "지역등급 직접 선택" if index == 1 else f"지역등급 직접 선택 {index}"
        grade = st.selectbox(
            grade_label,
            options=list(GRADES),
            index=None,
            placeholder="가 / 나 / 다 / 라",
            key=f"stay_grade_{stay_id}",
        )
        if grade:
            grade_message = f"직접 선택: {grade}"

    nights_key = f"stay_nights_{stay_id}"
    if index == 1:
        nights_key = f"stay_nights_{stay_id}_{departure}_{return_on}"
    default_nights = suggested_nights if index == 1 else 1
    if nights_key not in st.session_state:
        st.session_state[nights_key] = default_nights
    nights = int(
        st.number_input(
            "숙박일수 (박)",
            min_value=0,
            step=1,
            key=nights_key,
        )
    )
    breakfast_col, boost_col = st.columns(2)
    with breakfast_col:
        breakfast_included = st.checkbox(
            "조식 포함",
            key=f"stay_breakfast_{stay_id}",
            help="체크하면 이 출장지의 숙박일수만큼 식비 1/3을 공제합니다.",
        )
    with boost_col:
        lodging_boosted = st.checkbox(
            "숙박비 1.5배 적용",
            key=f"stay_boost_{stay_id}",
            help="체크하면 이 출장지의 숙박상한을 기준액의 1.5배로 계상합니다.",
        )

    is_last = index == total
    if is_last:
        default_days = max(trip_days - nights_before, nights)
    else:
        default_days = nights
    days_key = f"stay_days_{stay_id}"
    if index == 1:
        days_key = f"stay_days_{stay_id}_{departure}_{return_on}"
    if days_key not in st.session_state:
        st.session_state[days_key] = default_days
    stay_days = int(
        st.number_input(
            "체류일 (일)",
            min_value=0,
            step=1,
            key=days_key,
            help="일비·식비 일수입니다. 엑셀 오른쪽 등급별 칸(J열)에 들어갑니다.",
        )
    )

    actual_label = "숙박비 실비 (원)" if total == 1 else f"숙박비 실비 (원) {index}"
    actual_raw = st.number_input(
        actual_label,
        min_value=0,
        step=1000,
        value=None,
        placeholder="0",
        key=f"stay_actual_{stay_id}",
        help="출장지에서 실제 지출한 숙박비입니다. 엑셀 숙박비 상한 확인란의 실지출액(원화)에 등급별로 들어갑니다.",
    )
    actual_krw = int(actual_raw or 0)

    if not country_text and not city_text:
        return StayInput(
            country="",
            city="",
            nights=int(nights),
            grade="",
            grade_message="",
            stay_days=stay_days,
            actual_krw=actual_krw,
            breakfast_included=breakfast_included,
            lodging_boosted=lodging_boosted,
        )
    return StayInput(
        country=country_text,
        city=city_text,
        nights=int(nights),
        grade=grade or "",
        grade_message=grade_message,
        stay_days=stay_days,
        actual_krw=actual_krw,
        breakfast_included=breakfast_included,
        lodging_boosted=lodging_boosted,
    )


def _render_rental(
    rental_id: int,
    stays: list[StayInput],
    rental_index: int,
    rental_total: int,
) -> tuple[int, int]:
    title = "차량 임차" if rental_total == 1 else f"차량 임차 {rental_index}"
    title_col, del_col = st.columns([5, 1])
    with title_col:
        st.markdown(f"**{title}**")
    with del_col:
        st.button(
            "삭제",
            key=f"rental_del_{rental_id}",
            on_click=_remove_rental,
            args=(rental_id,),
            use_container_width=True,
        )

    stay_index = 0
    if len(stays) > 1:
        stay_label = "적용 출장지" if rental_total == 1 else f"적용 출장지 {rental_index}"
        options = [_stay_option_label(i + 1, stay) for i, stay in enumerate(stays)]
        selected = st.selectbox(stay_label, options=options, key=f"rental_stay_{rental_id}")
        stay_index = options.index(selected)

    days_key = f"rental_days_{rental_id}"
    if days_key not in st.session_state:
        st.session_state[days_key] = 1
    days_label = "차량 임차 일수 (일)" if rental_total == 1 else f"차량 임차 일수 (일) {rental_index}"
    days = int(
        st.number_input(
            days_label,
            min_value=0,
            step=1,
            key=days_key,
            help="별도의 차량을 임차하여 사용한 일수는 일비 기준액의 1/2을 지급합니다.",
        )
    )
    return int(stay_index), days


def main() -> None:
    _init_stay_ids()
    st.title("국외여비 자동계상")
    st.markdown(
        '<p class="hint">경기창조경제혁신센터 지침(2026.08.10.)</p>',
        unsafe_allow_html=True,
    )
    _render_plan_loader()

    with st.container(border=True):
        st.subheader("1. 출장 기본정보")
        traveler_ids = list(st.session_state.traveler_ids)
        first_traveler_id = traveler_ids[0]
        rendered_travelers: list[PartyMember] = []
        for index, traveler_id in enumerate(traveler_ids, start=1):
            rendered_travelers.append(
                _render_traveler(traveler_id, index, len(traveler_ids), first_traveler_id)
            )
        st.button("출장자 추가", on_click=_add_traveler, use_container_width=True)
        if len(rendered_travelers) > 1:
            st.caption(
                "항공료·준비금·숙박실비는 1인 기준입니다. 여비 계산 시 인원별 직급 기준액을 적용하고, "
                "심사신청서 소요예산은 인원 합산입니다."
            )
        name = rendered_travelers[0].name
        role = rendered_travelers[0].role

        date_col1, date_col2, date_col3 = st.columns(3)
        today = date.today()
        with date_col1:
            departure = st.date_input("출국일", value=today, key="departure_date")
        with date_col2:
            return_on = st.date_input("귀국일", value=today, key="return_date")
        with date_col3:
            approval = _as_date(
                st.date_input(
                    "출장신청서 결재일",
                    value=today,
                    key="approval_date",
                    help="환율 조회 기준일. 날짜를 바꾸면 해당일 하나은행 미국달러 현찰 살 때를 다시 조회합니다.",
                )
            )
        _apply_cached_fx(approval)

        trip_days = calculate_trip_days(departure, return_on) if return_on >= departure else 0
        suggested_nights = max(trip_days - 1, 0)

        stay_ids = list(st.session_state.stay_ids)
        rendered_stays: list[StayInput] = []
        for index, stay_id in enumerate(stay_ids, start=1):
            nights_before = sum(stay.nights for stay in rendered_stays)
            rendered_stays.append(
                _render_stay(
                    stay_id,
                    index,
                    len(stay_ids),
                    suggested_nights,
                    departure,
                    return_on,
                    trip_days,
                    nights_before,
                )
            )

        rental_ids = list(st.session_state.rental_ids)
        rental_assignments: list[tuple[int, int]] = []
        for rental_index, rental_id in enumerate(rental_ids, start=1):
            rental_assignments.append(
                _render_rental(rental_id, rendered_stays, rental_index, len(rental_ids))
            )
        if rental_ids:
            st.caption("차량을 임차하여 사용한 일수는 일비 기준액의 1/2을 지급합니다.")

        rental_by_stay = [0] * len(rendered_stays)
        for stay_index, days in rental_assignments:
            if 0 <= stay_index < len(rental_by_stay):
                rental_by_stay[stay_index] += days
        rendered_stays = [
            replace(stay, rental_days=days)
            for stay, days in zip(rendered_stays, rental_by_stay)
        ]

        add_col, rent_col = st.columns(2)
        with add_col:
            st.button("출장지 추가", on_click=_add_stay, use_container_width=True)
        with rent_col:
            st.button("차량 임차 추가", on_click=_add_rental, use_container_width=True)
        total_nights = sum(stay.nights for stay in rendered_stays)
        total_stay_days = sum(stay.stay_days or 0 for stay in rendered_stays)
        total_rental_days = sum(stay.rental_days for stay in rendered_stays)
        stay_caption = " · ".join(
            f"{stay.place_label} {stay.nights}박 {stay.stay_days}일({stay.grade})"
            for stay in rendered_stays
            if stay.grade
        )
        extra = f" · {stay_caption}" if stay_caption else ""
        rental_caption = f" · 차량임차 {total_rental_days}일(일비 1/2)" if total_rental_days else ""
        breakfast_nights = sum(
            min(stay.nights, stay.stay_days or 0)
            for stay in rendered_stays
            if stay.breakfast_included
        )
        breakfast_caption = f" · 조식 포함 {breakfast_nights}박(식비 1/3 공제)" if breakfast_nights else ""
        boost_nights = sum(stay.nights for stay in rendered_stays if stay.lodging_boosted)
        boost_caption = f" · 숙박 1.5배 {boost_nights}박" if boost_nights else ""
        lodging_total = sum(stay.actual_krw for stay in rendered_stays)
        lodging_caption = f" · 숙박실비 {lodging_total:,}원" if lodging_total else ""
        st.caption(
            f"출장일수 {trip_days}일 (출국일·귀국일 포함) · 숙박 {total_nights}박 · "
            f"체류 {total_stay_days}일{rental_caption}{breakfast_caption}{boost_caption}{lodging_caption}{extra} · "
            f"환율 기준일 {approval.isoformat()} · 출처: {FX_SOURCE_CAPTION}"
        )
        if len(stay_ids) > 1:
            st.caption(
                "일비·식비는 도시별 체류일, 숙박상한은 도시별 숙박일수로 계산합니다. "
                "숙박비 실비는 출장지마다 입력하며, 같은 등급이면 합산되어 엑셀 상한 확인란에 들어갑니다. "
                "체류일 합계는 출장일수와 같아야 하며, 엑셀에서는 등급별로 칸이 나뉩니다."
            )

    if not _fx_is_loaded(approval) and st.session_state.get("fx_fetch_failed_for") != approval.isoformat():
        with st.spinner("하나은행 환율 조회 중..."):
            _sync_fx_for_approval(approval)

    with st.container(border=True):
        st.subheader("2. 비용 및 지급정보")
        st.caption(
            "일비·식비 금액은 자동 계산합니다. 숙박비 실비는 출장지마다 입력합니다. "
            "환율과 비용을 입력한 뒤 계산을 누르세요."
        )
        exchange_rate = st.number_input(
            "적용환율 (USD/KRW)",
            min_value=0.0,
            step=0.01,
            format="%.2f",
            value=None,
            placeholder="0.00",
            key=_fx_input_key(),
            help="하나은행 환율정보 · 미국달러 현찰 살 때. 결재일을 바꾸면 해당 날짜 고시가 자동으로 들어옵니다.",
        )
        fx_quote = st.session_state.get("fx_quote")
        if fx_quote:
            st.caption(quote_caption(fx_quote, approval))
        elif st.session_state.get("fx_fetch_failed_for") == approval.isoformat():
            st.caption("하나은행 환율을 가져오지 못했습니다. 미국달러 현찰 살 때를 직접 입력하거나, 다시 조회해 주세요.")
            if st.button("환율 다시 조회"):
                st.session_state.pop("fx_fetch_failed_for", None)
                st.rerun()
        else:
            st.caption("하나은행 환율을 조회하는 중입니다.")

        with st.form("travel_calc"):
            air_c, air_p = st.columns([2, 2])
            with air_c:
                airfare = st.number_input(
                    "항공료 (원)",
                    min_value=0,
                    step=1000,
                    value=None,
                    placeholder="0",
                    key="airfare",
                )
            with air_p:
                airfare_pay = st.radio("항공료 지급방식", PAYMENT_METHODS, index=_payment_index(PAYMENT_CORPORATE), horizontal=True, key="air_pay")

            daily_pay = st.radio("일비 지급방식", PAYMENT_METHODS, index=_payment_index(PAYMENT_PERSONAL), horizontal=True, key="daily_pay")
            meal_pay = st.radio("식비 지급방식", PAYMENT_METHODS, index=_payment_index(PAYMENT_PERSONAL), horizontal=True, key="meal_pay")

            lodging_pay = st.radio("숙박비 지급방식", PAYMENT_METHODS, index=_payment_index(PAYMENT_CORPORATE), horizontal=True, key="lodge_pay")

            prep_c, prep_p = st.columns([2, 2])
            with prep_c:
                preparation = st.number_input(
                    "준비금 (원)",
                    min_value=0,
                    step=1000,
                    value=None,
                    placeholder="0",
                    key="preparation",
                )
            with prep_p:
                prep_pay = st.radio("준비금 지급방식", PAYMENT_METHODS, index=_payment_index(PAYMENT_CORPORATE), horizontal=True, key="prep_pay")

            calculate = st.form_submit_button("여비 계산", type="primary", use_container_width=True)

    if calculate:
        stays = [stay for stay in rendered_stays if stay.country or stay.city]
        if any(stay.grade == "" for stay in stays) or not stays:
            st.error("지역등급을 확인할 수 없습니다. 출장지를 입력하거나 가/나/다/라를 직접 선택해 주세요.")
            st.session_state.pop("calc_result", None)
            st.session_state.pop("calc_party", None)
            return
        if _or_default(exchange_rate, 0) <= 0:
            st.error("적용환율을 입력해 주세요.")
            st.session_state.pop("calc_result", None)
            st.session_state.pop("calc_party", None)
            return

        plan = _refresh_plan_document(st.session_state.get("plan_document"))
        members = rendered_travelers or _party_members_from_plan(plan, name, role)
        inp = TravelInput(
            role=members[0].role,
            grade=stays[0].grade,
            departure_date=departure,
            return_date=return_on,
            lodging_nights=sum(stay.nights for stay in stays),
            exchange_rate=float(_or_default(exchange_rate, 0)),
            airfare_krw=int(_or_default(airfare, 0)),
            lodging_actual_krw=sum(stay.actual_krw for stay in stays),
            preparation_krw=int(_or_default(preparation, 0)),
            airfare_payment_method=airfare_pay,
            daily_payment_method=daily_pay,
            meal_payment_method=meal_pay,
            lodging_payment_method=lodging_pay,
            preparation_payment_method=prep_pay,
            stays=stays,
        )
        validation = validate_travel_input(inp)
        if not validation.ok:
            for err in validation.errors:
                st.error(err)
            st.session_state.pop("calc_result", None)
            st.session_state.pop("calc_party", None)
            return

        party_calcs = calculate_for_members(inp, members)
        packed_party = []
        for item in party_calcs:
            packed_party.append(
                {
                    "name": item.member.name,
                    "title": item.member.title,
                    "team": item.member.team,
                    "member": item.member,
                    "approval": approval.isoformat(),
                    "departure": departure.isoformat(),
                    "return_on": return_on.isoformat(),
                    "warnings": item.result.warnings,
                    "result": item.result,
                }
            )
        st.session_state["calc_party"] = packed_party
        st.session_state["calc_result"] = packed_party[0]
        st.session_state.calc_results[packed_party[0]["name"].strip() or "_"] = packed_party[0]

    packed_party = list(st.session_state.get("calc_party") or [])
    if not packed_party:
        current_name = (name or "").strip() or "_"
        packed = st.session_state.get("calc_results", {}).get(current_name)
        if packed is None and not st.session_state.get("plan_document"):
            packed = st.session_state.get("calc_result")
        if packed:
            packed_party = [packed]
    if not packed_party:
        return

    packed = packed_party[0]
    result = packed["result"]
    dest = " / ".join(
        f"{stay.place_label} {stay.nights}박 {stay.stay_days}일 ({stay.grade}"
        + (f", {stay.grade_message}" if stay.grade_message else "")
        + (f", 차량임차 {stay.rental_days}일" if stay.rental_days else "")
        + (f", 조식 포함 {stay.nights}박" if stay.breakfast_included and stay.nights else "")
        + (f", 숙박 1.5배" if stay.lodging_boosted and stay.nights else "")
        + (f", 숙박실비 {stay.actual_krw:,}원" if stay.actual_krw else "")
        + ")"
        for stay in result.stays
    ) or "-"

    with st.container(border=True):
        st.subheader("3. 자동계산 결과")
        st.markdown("**출장정보**")
        traveler_line = " / ".join(
            f"{item.get('name') or '-'} · {item['result'].role}"
            for item in packed_party
        )
        st.write(
            f"- 출장자: {traveler_line}\n"
            f"- 출장지: {dest}\n"
            f"- 지역등급: {result.grade}\n"
            f"- 출장일수: {result.trip_days}일 · 숙박일수: {result.lodging_nights}박"
            f" · 체류일: {sum(stay.stay_days or 0 for stay in result.stays)}일"
            + (f" · 차량임차: {result.rental_days}일 (일비 1/2)" if result.rental_days else "")
            + (f" · 조식 포함: {result.breakfast_nights}일 (식비 1/3 공제)" if result.breakfast_nights else "")
            + (f" · 숙박 1.5배: {result.lodging_boost_nights}박" if result.lodging_boost_nights else "")
            + (f" · {len(packed_party)}인 합산" if len(packed_party) > 1 else "")
        )
        st.markdown("**적용환율**")
        st.markdown(
            f"- 기준일: {packed['approval']}\n"
            f"- USD/KRW: **{result.exchange_rate:,.2f}원**\n"
            f"- 출처: {FX_SOURCE_CAPTION}"
        )

        daily_detail = _slice_line(result.daily.slices, "일")
        meal_detail = _slice_line(result.meal.slices, "일")
        lodging_detail = _slice_line(result.lodging.slices, "박")
        lodging_note = ""
        if result.lodging.note:
            lodging_note = (
                f"\n- 숙박비 비고: 상한액 {_amount(result.lodging.ceiling_krw)} 대비 "
                f"{_amount(result.lodging.excess_krw)} 초과"
            )
        st.markdown("**여비 계산**")
        if len(packed_party) > 1:
            st.markdown(
                "\n".join(
                    f"- {item.get('name') or '-'} ({item['result'].role}): {_amount(item['result'].total_krw)}"
                    for item in packed_party
                )
            )
            st.caption("아래 금액은 1인 기준입니다. 심사신청서 소요예산은 인원 합산입니다.")
        st.markdown(
            f"- 항공료: {_amount(result.airfare_krw)} ({result.airfare_payment_method})\n"
            f"- 일비: {_amount(result.daily.amount_krw)} ({daily_detail})\n"
            f"- 식비: {_amount(result.meal.amount_krw)} ({meal_detail})\n"
            f"- 숙박비 상한: {_amount(result.lodging.ceiling_krw)} ({lodging_detail})\n"
            f"- 숙박비 실비(C9): {_amount(result.lodging.payable_krw)} ({result.lodging.payment_method})"
            + lodging_note
            + f"\n- 준비금: {_amount(result.preparation_krw)} ({result.preparation_payment_method})\n"
            f"- 엑셀: L열 원화금액은 1원 단위, C열 집행금액·합계와 M열은 원단위 절사"
        )

        total_krw = sum(item["result"].total_krw for item in packed_party)
        corp_krw = sum(item["result"].corporate_card_total for item in packed_party)
        personal_krw = sum(item["result"].personal_transfer_total for item in packed_party)
        m1, m2, m3 = st.columns(3)
        m1.metric("총액", _won(total_krw))
        m2.metric("법인카드 결제", _won(corp_krw))
        m3.metric("개인지급(계좌이체)", _won(personal_krw))

        seen_warn = set()
        for item in packed_party:
            for warn in item["warnings"]:
                if warn in seen_warn:
                    continue
                seen_warn.add(warn)
                st.warning(warn)

        party_results = _to_party_results(packed_party)
        approval_date = date.fromisoformat(packed["approval"])
        excel_name = excel_filename_for_party(party_results, approval_date)
        try:
            excel_bytes = build_party_excel_bytes(party_results, approval_date)
        except FileNotFoundError:
            excel_bytes = None
        plan = _refresh_plan_document(st.session_state.get("plan_document"))
        if plan is not None:
            st.session_state.plan_document = plan
        title = packed.get("title") or ""
        team = packed.get("team") or ""
        if (not title or not team) and plan and plan.travelers:
            person = next((item for item in plan.travelers if item.name == packed["name"]), plan.travelers[0])
            title = title or person.title
            team = team or person.team
        departure = date.fromisoformat(packed["departure"]) if packed.get("departure") else None
        return_on = date.fromisoformat(packed["return_on"]) if packed.get("return_on") else None
        hwp_error = ""
        try:
            hwp_bytes = build_hwp_bytes(
                result,
                packed["name"],
                title=title,
                team=team,
                plan=plan,
                departure=departure,
                return_on=return_on,
                approval_date=approval_date,
                party=party_results,
            )
        except Exception as exc:
            hwp_bytes = None
            hwp_error = str(exc)
        hwp_name = hwp_filename_for_party(party_results, approval_date)
        plan_excel_error = ""
        try:
            plan_excel_bytes = build_party_plan_excel_bytes(
                party_results,
                plan=plan,
                departure=departure,
                return_on=return_on,
                approval_date=approval_date,
            )
        except Exception as exc:
            plan_excel_bytes = None
            plan_excel_error = str(exc)
        plan_excel_name = plan_excel_filename_for_party(party_results, approval_date)
        down_col1, down_col2 = st.columns(2)
        with down_col1:
            if excel_bytes:
                st.download_button(
                    "Excel 다운로드",
                    data=excel_bytes,
                    file_name=excel_name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary",
                    use_container_width=True,
                )
            else:
                st.info("Excel 파일을 만들 수 없습니다. 여비 계산을 다시 실행해 주세요.")
        with down_col2:
            if hwp_bytes:
                st.download_button(
                    "심사신청서(HWP) 다운로드",
                    data=hwp_bytes,
                    file_name=hwp_name,
                    mime="application/x-hwp",
                    use_container_width=True,
                )
            elif hwp_error:
                st.error(f"심사신청서를 만들지 못했습니다. {hwp_error}")
            else:
                st.info("심사신청서 파일을 만들 수 없습니다. 여비 계산을 다시 실행해 주세요.")
        if plan_excel_bytes:
            st.download_button(
                "해외출장 계획 엑셀 다운로드",
                data=plan_excel_bytes,
                file_name=plan_excel_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="plan_excel_download",
            )
        elif plan_excel_error:
            st.error(f"해외출장 계획 엑셀을 만들지 못했습니다. {plan_excel_error}")
        else:
            st.info("해외출장 계획 엑셀을 만들 수 없습니다. 여비 계산을 다시 실행해 주세요.")


main()
