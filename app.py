"""국외여비 자동계상 — 입력·계산 화면 (STEP 4)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

import streamlit as st

from config.excel_mapping import FX_SOURCE_CAPTION
from data.travel_rates import GRADES, PAYMENT_CORPORATE, PAYMENT_PERSONAL, PAYMENT_METHODS, ROLES
from services.destination_grade_service import list_countries, resolve_destination_grade
from services.excel_export import build_excel_bytes, excel_filename
from services.hana_fx import quote_caption
from services import hana_fx
from services.travel_calculator import (
    StayInput,
    TravelInput,
    calculate_travel,
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


def _sync_fx_for_approval(approval: date) -> None:
    """결재일에 맞는 하나은행 현찰 살 때를 적용환율 칸에 넣는다.

    환율 number_input은 한 번 만들어진 뒤에는 값을 덮어쓸 수 없어서,
    날짜가 바뀔 때마다 위젯 키를 바꿔 새로 만든다.
    """
    approval = _as_date(approval)
    loaded = st.session_state.get("fx_loaded_for")
    if loaded == approval.isoformat() and st.session_state.get("fx_quote") is not None:
        return
    quote = hana_fx.fetch_usd_cash_buy(approval)
    st.session_state.fx_quote = quote
    if quote is None:
        return
    st.session_state.fx_loaded_for = approval.isoformat()
    next_id = int(st.session_state.get("fx_widget_id", 0)) + 1
    st.session_state.fx_widget_id = next_id
    st.session_state[f"exchange_rate_{next_id}"] = float(quote.rate)


def _init_stay_ids() -> None:
    if "stay_ids" not in st.session_state:
        st.session_state.stay_ids = [0]
        st.session_state.next_stay_id = 1
    if "rental_ids" not in st.session_state:
        st.session_state.rental_ids = []
        st.session_state.next_rental_id = 0


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
        )
    return StayInput(
        country=country_text,
        city=city_text,
        nights=int(nights),
        grade=grade or "",
        grade_message=grade_message,
        stay_days=stay_days,
        actual_krw=actual_krw,
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

    with st.container(border=True):
        st.subheader("1. 출장 기본정보")
        name = st.text_input("출장자명", placeholder="예: 홍길동", help="Excel 본문에는 넣지 않고, 다운로드 파일명에 사용합니다.")
        role = st.selectbox("출장자 구분", options=list(ROLES), index=2)

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
        _sync_fx_for_approval(approval)

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
        lodging_total = sum(stay.actual_krw for stay in rendered_stays)
        lodging_caption = f" · 숙박실비 {lodging_total:,}원" if lodging_total else ""
        st.caption(
            f"출장일수 {trip_days}일 (출국일·귀국일 포함) · 숙박 {total_nights}박 · "
            f"체류 {total_stay_days}일{rental_caption}{lodging_caption}{extra} · "
            f"환율 기준일 {approval.isoformat()} · 출처: {FX_SOURCE_CAPTION}"
        )
        if len(stay_ids) > 1:
            st.caption(
                "일비·식비는 도시별 체류일, 숙박상한은 도시별 숙박일수로 계산합니다. "
                "숙박비 실비는 출장지마다 입력하며, 같은 등급이면 합산되어 엑셀 상한 확인란에 들어갑니다. "
                "체류일 합계는 출장일수와 같아야 하며, 엑셀에서는 등급별로 칸이 나뉩니다."
            )

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
        elif st.session_state.get("fx_loaded_for") != approval.isoformat():
            st.caption("하나은행 환율을 가져오지 못했습니다. 미국달러 현찰 살 때를 직접 입력하거나, 결재일을 다시 선택해 주세요.")

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
            return
        if _or_default(exchange_rate, 0) <= 0:
            st.error("적용환율을 입력해 주세요.")
            st.session_state.pop("calc_result", None)
            return

        inp = TravelInput(
            role=role,
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
            return

        result = calculate_travel(inp)
        approval_date = approval
        st.session_state["calc_result"] = {
            "name": name.strip(),
            "approval": approval_date.isoformat(),
            "warnings": result.warnings,
            "result": result,
        }

    packed = st.session_state.get("calc_result")
    if not packed:
        return

    result = packed["result"]
    dest = " / ".join(
        f"{stay.place_label} {stay.nights}박 {stay.stay_days}일 ({stay.grade}"
        + (f", {stay.grade_message}" if stay.grade_message else "")
        + (f", 차량임차 {stay.rental_days}일" if stay.rental_days else "")
        + (f", 숙박실비 {stay.actual_krw:,}원" if stay.actual_krw else "")
        + ")"
        for stay in result.stays
    ) or "-"

    with st.container(border=True):
        st.subheader("3. 자동계산 결과")
        st.markdown("**출장정보**")
        st.write(
            f"- 출장자: {packed['name'] or '-'} · {result.role}\n"
            f"- 출장지: {dest}\n"
            f"- 지역등급: {result.grade}\n"
            f"- 출장일수: {result.trip_days}일 · 숙박일수: {result.lodging_nights}박"
            f" · 체류일: {sum(stay.stay_days or 0 for stay in result.stays)}일"
            + (f" · 차량임차: {result.rental_days}일 (일비 1/2)" if result.rental_days else "")
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

        m1, m2, m3 = st.columns(3)
        m1.metric("총액", _won(result.total_krw))
        m2.metric("법인카드 결제", _won(result.corporate_card_total))
        m3.metric("개인지급(계좌이체)", _won(result.personal_transfer_total))

        for warn in packed["warnings"]:
            st.warning(warn)

        excel_name = excel_filename(packed["name"], date.fromisoformat(packed["approval"]))
        try:
            excel_bytes = build_excel_bytes(result, packed["name"], date.fromisoformat(packed["approval"]))
        except FileNotFoundError:
            excel_bytes = None
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


main()
