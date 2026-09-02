"""국외여비 계산 로직.

UI와 Excel 생성과 분리한다.
숙박비 정산(실비 vs 상한)은 settle_lodging()으로 분리해
이후 예외승인 규칙을 추가하기 쉽게 둔다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from data.travel_rates import (
    DAILY_RENTAL_LABEL,
    GRADES,
    PAYMENT_CORPORATE,
    PAYMENT_METHODS,
    PAYMENT_PERSONAL,
    ROLE_EXCEL_LABEL,
    ROLES,
    get_daily_rate_usd,
    get_daily_rental_rate_usd,
    get_lodging_rate_usd,
    get_meal_rate_usd,
)


def round_to_ten(amount: float | int | Decimal) -> int:
    """십원 이하 반올림. (할인정액 등 별도 규정용)"""
    value = Decimal(str(amount))
    return int(value.quantize(Decimal("1E1"), rounding=ROUND_HALF_UP))


def truncate_to_won(amount: float | int | Decimal) -> int:
    """원 미만 절사. (1원 아래 소수만 버림)"""
    value = Decimal(str(amount))
    return int(value.to_integral_value(rounding=ROUND_DOWN))


def truncate_to_ten(amount: float | int | Decimal) -> int:
    """십원 단위 절사. (1원 자리를 버림)"""
    value = Decimal(str(amount))
    return int(value.quantize(Decimal("1E1"), rounding=ROUND_DOWN))


def execution_krw(amount: float | int | Decimal) -> int:
    """집행 금액: 원화 1원 단위 계산 후 원단위(1원 자리) 절사."""
    return truncate_to_ten(amount)


def calculate_trip_days(departure: date, return_on: date) -> int:
    """출국일·귀국일을 모두 포함한 출장일수."""
    return (return_on - departure).days + 1


def usd_to_krw(usd_amount: float | int | Decimal, exchange_rate: float) -> int:
    """USD × 환율 후 십원 반올림. (할인정액 등 별도 규정용)"""
    return round_to_ten(Decimal(str(usd_amount)) * Decimal(str(exchange_rate)))


def usd_to_krw_truncated(usd_amount: float | int | Decimal, exchange_rate: float) -> int:
    """일비·식비·숙박비 상한: USD × 환율 후 원 미만 절사."""
    return truncate_to_won(Decimal(str(usd_amount)) * Decimal(str(exchange_rate)))


@dataclass(frozen=True)
class RateSlice:
    """등급별로 나눈 일비·식비·숙박 상한 조각."""

    grade: str
    rate_usd: int
    quantity: int
    amount_usd: int
    amount_krw: int
    label: str = ""


@dataclass(frozen=True)
class StayInput:
    """한 도시(또는 국가)에서의 숙박."""

    country: str
    city: str
    nights: int
    grade: str
    grade_message: str = ""
    stay_days: int | None = None
    rental_days: int = 0
    actual_krw: int = 0

    @property
    def place_label(self) -> str:
        city = self.city.strip()
        country = self.country.strip()
        if city and country:
            return f"{country} {city}"
        return city or country or self.grade


@dataclass(frozen=True)
class AllowanceLine:
    rate_usd: int
    days: int
    amount_usd: int
    amount_krw: int
    payment_method: str
    slices: tuple[RateSlice, ...] = ()


@dataclass(frozen=True)
class LodgingSettlement:
    """숙박비 정산 결과. payable_krw 는 Excel C9에 들어간다."""

    rate_usd: int
    nights: int
    ceiling_usd: int
    ceiling_krw: int
    actual_krw: int
    payable_krw: int
    excess_krw: int
    exceeded: bool
    note: str
    warning: str | None
    payment_method: str
    slices: tuple[RateSlice, ...] = ()


@dataclass
class TravelInput:
    role: str
    grade: str
    departure_date: date
    return_date: date
    lodging_nights: int
    exchange_rate: float
    airfare_krw: int
    lodging_actual_krw: int
    preparation_krw: int
    airfare_payment_method: str = PAYMENT_CORPORATE
    daily_payment_method: str = PAYMENT_PERSONAL
    meal_payment_method: str = PAYMENT_PERSONAL
    lodging_payment_method: str = PAYMENT_CORPORATE
    preparation_payment_method: str = PAYMENT_CORPORATE
    stays: list[StayInput] | None = None


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class TravelResult:
    role: str
    role_excel: str
    grade: str
    trip_days: int
    lodging_nights: int
    exchange_rate: float
    daily: AllowanceLine
    meal: AllowanceLine
    lodging: LodgingSettlement
    airfare_krw: int
    airfare_payment_method: str
    preparation_krw: int
    preparation_payment_method: str
    total_krw: int
    corporate_card_total: int
    personal_transfer_total: int
    warnings: list[str]
    stays: tuple[StayInput, ...] = ()
    grade_quantities: dict[str, dict[str, int]] = field(default_factory=dict)
    rental_days: int = 0


def resolved_stays(inp: TravelInput) -> list[StayInput]:
    if inp.stays:
        return list(inp.stays)
    return [
        StayInput(
            country="",
            city="",
            nights=inp.lodging_nights,
            grade=inp.grade,
        )
    ]


def total_lodging_nights(inp: TravelInput) -> int:
    return sum(stay.nights for stay in resolved_stays(inp))


def apply_default_stay_days(stays: list[StayInput], trip_days: int) -> list[StayInput]:
    """체류일이 비어 있으면 숙박일수를 쓰고, 남는 출장일수는 마지막 숙박지에 더한다."""
    leftover = max(trip_days - sum(stay.nights for stay in stays), 0)
    filled: list[StayInput] = []
    for index, stay in enumerate(stays):
        days = stay.stay_days
        if days is None:
            days = stay.nights
            if index == len(stays) - 1:
                days += leftover
        filled.append(replace(stay, stay_days=days))
    return filled


def grade_block_quantities(stays: list[StayInput]) -> dict[str, dict[str, int]]:
    """Excel 오른쪽 등급 블록용 일수. 일비·식비=체류일, 숙박=박수."""
    grouped: dict[str, dict[str, int]] = {
        grade: {"daily": 0, "lodging": 0, "meal": 0} for grade in GRADES
    }
    for stay in stays:
        days = stay.stay_days or 0
        grouped[stay.grade]["daily"] += days
        grouped[stay.grade]["meal"] += days
        grouped[stay.grade]["lodging"] += stay.nights
    return {grade: qty for grade, qty in grouped.items() if any(qty.values())}


def _grade_summary(stays: list[StayInput]) -> str:
    seen: list[str] = []
    for stay in stays:
        if stay.grade not in seen:
            seen.append(stay.grade)
    order = {grade: index for index, grade in enumerate(GRADES)}
    seen.sort(key=lambda grade: order.get(grade, 99))
    return "/".join(seen)


def validate_travel_input(inp: TravelInput) -> ValidationResult:
    result = ValidationResult()
    stays = resolved_stays(inp)

    if inp.role not in ROLES:
        result.errors.append(
            f"출장자 구분을 확인할 수 없습니다: {inp.role}. "
            "센터장 / 본부장 / 팀장 및 팀원 중에서 선택해 주세요."
        )
    if not stays:
        result.errors.append("출장지를 한 곳 이상 입력해 주세요.")
    for index, stay in enumerate(stays, start=1):
        if stay.grade not in GRADES:
            result.errors.append(
                f"출장지 {index}의 지역등급을 확인할 수 없습니다: {stay.grade}. "
                "가 / 나 / 다 / 라 중에서 선택해 주세요."
            )
        if stay.nights < 0:
            result.errors.append(f"출장지 {index}의 숙박일수는 0 이상이어야 합니다.")
        if stay.stay_days is not None and stay.stay_days < 0:
            result.errors.append(f"출장지 {index}의 체류일은 0 이상이어야 합니다.")
        if stay.rental_days < 0:
            result.errors.append(f"출장지 {index}의 차량 임차 일수는 0 이상이어야 합니다.")
        if stay.actual_krw < 0:
            result.errors.append(f"출장지 {index}의 숙박비는 0 이상이어야 합니다.")
    if inp.return_date < inp.departure_date:
        result.errors.append("귀국일이 출국일보다 빠를 수 없습니다.")
    if inp.airfare_krw < 0:
        result.errors.append("항공료는 0 이상이어야 합니다.")
    if inp.lodging_actual_krw < 0:
        result.errors.append("숙박비는 0 이상이어야 합니다.")
    if inp.preparation_krw < 0:
        result.errors.append("준비금은 0 이상이어야 합니다.")
    if inp.exchange_rate <= 0:
        result.errors.append("적용환율은 0보다 커야 합니다.")

    for label, method in (
        ("항공료", inp.airfare_payment_method),
        ("일비", inp.daily_payment_method),
        ("식비", inp.meal_payment_method),
        ("숙박비", inp.lodging_payment_method),
        ("준비금", inp.preparation_payment_method),
    ):
        if method not in PAYMENT_METHODS:
            result.errors.append(f"{label} 지급방식은 법인카드 결제 또는 개인지급(계좌이체)만 가능합니다.")

    lodging_nights = total_lodging_nights(inp)
    if inp.return_date >= inp.departure_date:
        trip_days = calculate_trip_days(inp.departure_date, inp.return_date)
        if lodging_nights > trip_days:
            result.warnings.append(
                f"숙박일수({lodging_nights}박)가 출장일수({trip_days}일)보다 깁니다. 숙박일수를 확인해 주세요."
            )
        elif lodging_nights > max(trip_days - 1, 0):
            result.warnings.append(
                f"숙박일수({lodging_nights}박)가 출장기간 대비 깁니다. "
                f"일반적으로 {max(trip_days - 1, 0)}박입니다."
            )
        stay_day_total = sum(
            stay.stay_days if stay.stay_days is not None else stay.nights
            for stay in stays
        )
        if any(stay.stay_days is not None for stay in stays) and stay_day_total != trip_days:
            result.warnings.append(
                f"체류일 합계({stay_day_total}일)가 출장일수({trip_days}일)와 다릅니다. "
                "엑셀 일비·식비 일수를 확인해 주세요."
            )
        filled = apply_default_stay_days(stays, trip_days)
        for index, stay in enumerate(filled, start=1):
            if stay.rental_days > (stay.stay_days or 0):
                result.warnings.append(
                    f"출장지 {index}의 차량 임차 일수({stay.rental_days}일)가 "
                    f"체류일({stay.stay_days}일)보다 깁니다."
                )
        rental_total = sum(stay.rental_days for stay in filled)
        if rental_total > trip_days:
            result.warnings.append(
                f"차량 임차 일수 합계({rental_total}일)가 출장일수({trip_days}일)보다 깁니다."
            )

    return result


def calculate_daily_allowance(
    role: str,
    trip_days: int,
    exchange_rate: float,
    payment_method: str,
) -> AllowanceLine:
    rate_usd = get_daily_rate_usd(role)
    amount_usd = rate_usd * trip_days
    return AllowanceLine(
        rate_usd=rate_usd,
        days=trip_days,
        amount_usd=amount_usd,
        amount_krw=usd_to_krw_truncated(amount_usd, exchange_rate),
        payment_method=payment_method,
    )


def calculate_meal_allowance(
    role: str,
    grade: str,
    trip_days: int,
    exchange_rate: float,
    payment_method: str,
) -> AllowanceLine:
    rate_usd = get_meal_rate_usd(role, grade)
    amount_usd = rate_usd * trip_days
    return AllowanceLine(
        rate_usd=rate_usd,
        days=trip_days,
        amount_usd=amount_usd,
        amount_krw=usd_to_krw_truncated(amount_usd, exchange_rate),
        payment_method=payment_method,
    )


def calculate_lodging_ceiling(
    role: str,
    grade: str,
    nights: int,
    exchange_rate: float,
) -> tuple[int, int, int]:
    """숙박비 상한. (USD 기준, USD 상한, 원화 상한)"""
    rate_usd = get_lodging_rate_usd(role, grade)
    ceiling_usd = rate_usd * nights
    ceiling_krw = usd_to_krw_truncated(ceiling_usd, exchange_rate)
    return rate_usd, ceiling_usd, ceiling_krw


def allocate_meal_days(stays: list[StayInput], trip_days: int) -> list[tuple[StayInput, int]]:
    """식비 일수: 도시별 체류일. 없으면 숙박일수 + 남는 출장일수는 마지막 숙박지."""
    filled = apply_default_stay_days(stays, trip_days)
    return [(stay, stay.stay_days or 0) for stay in filled]


def daily_items_from_stays(stays: list[StayInput], role: str) -> list[tuple[str, int, int, str]]:
    """일비 조각. 차량 임차일은 기준액 1/2."""
    rate_usd = get_daily_rate_usd(role)
    half_rate = get_daily_rental_rate_usd(role)
    items: list[tuple[str, int, int, str]] = []
    for stay in stays:
        days = stay.stay_days or 0
        rental = min(max(stay.rental_days, 0), days)
        full = days - rental
        if full:
            items.append((stay.grade, rate_usd, full, ""))
        if rental:
            items.append((stay.grade, half_rate, rental, DAILY_RENTAL_LABEL))
    return items


def _slices_by_grade(
    items: list[tuple],
    exchange_rate: float,
) -> tuple[RateSlice, ...]:
    """(grade, rate_usd, quantity[, label]) → 등급·단가별 합산 후 원 미만 절사."""
    grouped: dict[tuple[str, int, str], int] = defaultdict(int)
    for item in items:
        grade, rate_usd, quantity = item[0], item[1], item[2]
        label = item[3] if len(item) > 3 else ""
        if quantity <= 0:
            continue
        grouped[(grade, rate_usd, label)] += quantity

    slices: list[RateSlice] = []
    for grade in GRADES:
        grade_keys = [key for key in grouped if key[0] == grade]
        grade_keys.sort(key=lambda key: (-key[1], key[2]))
        for key in grade_keys:
            rate_usd = key[1]
            label = key[2]
            quantity = grouped[key]
            amount_usd = rate_usd * quantity
            slices.append(
                RateSlice(
                    grade=grade,
                    rate_usd=rate_usd,
                    quantity=quantity,
                    amount_usd=amount_usd,
                    amount_krw=usd_to_krw_truncated(amount_usd, exchange_rate),
                    label=label,
                )
            )
    return tuple(slices)


def _line_from_slices(slices: tuple[RateSlice, ...], payment_method: str) -> AllowanceLine:
    if not slices:
        return AllowanceLine(0, 0, 0, 0, payment_method, ())
    total_qty = sum(item.quantity for item in slices)
    total_usd = sum(item.amount_usd for item in slices)
    total_krw = sum(item.amount_krw for item in slices)
    rate_usd = slices[0].rate_usd if len(slices) == 1 else 0
    return AllowanceLine(
        rate_usd=rate_usd,
        days=total_qty,
        amount_usd=total_usd,
        amount_krw=total_krw,
        payment_method=payment_method,
        slices=slices,
    )


def settle_lodging(
    *,
    rate_usd: int,
    nights: int,
    ceiling_usd: int,
    ceiling_krw: int,
    actual_krw: int,
    payment_method: str,
) -> LodgingSettlement:
    """실비를 C9에 넣고, 상한 초과 시 비고에 초과액을 적는다.

    예외승인(상한 초과분 지급)은 이 함수를 확장해 처리한다.
    """
    excess_krw = max(actual_krw - ceiling_krw, 0)
    exceeded = actual_krw > ceiling_krw
    note = ""
    warning = None
    if exceeded:
        note = f"상한액 {ceiling_krw:,}원 대비 {excess_krw:,}원 초과"
        warning = (
            "숙박비가 규정상 상한액을 초과했습니다. "
            "상한액 및 추가 지급 가능 여부를 확인해 주세요."
        )

    return LodgingSettlement(
        rate_usd=rate_usd,
        nights=nights,
        ceiling_usd=ceiling_usd,
        ceiling_krw=ceiling_krw,
        actual_krw=actual_krw,
        payable_krw=actual_krw,
        excess_krw=excess_krw,
        exceeded=exceeded,
        note=note,
        warning=warning,
        payment_method=payment_method,
    )


def _sum_by_payment(
    items: list[tuple[int, str]],
    payment_method: str,
) -> int:
    return sum(amount for amount, method in items if method == payment_method)


def lodging_actual_by_grade(stays: list[StayInput], fallback_total: int = 0) -> dict[str, int]:
    """출장지별 숙박 실비를 등급별로 합산. 없으면 총액을 박수 비율로 나눈다."""
    grouped: dict[str, int] = defaultdict(int)
    for stay in stays:
        grouped[stay.grade] += stay.actual_krw
    if sum(grouped.values()) > 0:
        return {grade: amount for grade, amount in grouped.items() if amount}
    nights = [
        (grade, qty["lodging"])
        for grade, qty in grade_block_quantities(stays).items()
        if qty["lodging"] > 0
    ]
    return _split_amount(fallback_total, nights)


def _split_amount(total: int, shares: list[tuple[str, int]]) -> dict[str, int]:
    weight = sum(value for _, value in shares)
    if weight <= 0:
        return {}
    allocated: dict[str, int] = {}
    used = 0
    for index, (key, value) in enumerate(shares):
        if index == len(shares) - 1:
            allocated[key] = total - used
        else:
            part = total * value // weight
            allocated[key] = part
            used += part
    return allocated


def _resolved_lodging_actual(inp: TravelInput, stays: list[StayInput]) -> int:
    stay_total = sum(stay.actual_krw for stay in stays)
    if stay_total:
        return stay_total
    return inp.lodging_actual_krw


def calculate_travel(inp: TravelInput) -> TravelResult:
    validation = validate_travel_input(inp)
    if not validation.ok:
        raise ValueError("\n".join(validation.errors))

    trip_days = calculate_trip_days(inp.departure_date, inp.return_date)
    stays = apply_default_stay_days(resolved_stays(inp), trip_days)
    lodging_nights = sum(stay.nights for stay in stays)
    rental_days = sum(min(max(stay.rental_days, 0), stay.stay_days or 0) for stay in stays)
    daily = _line_from_slices(
        _slices_by_grade(daily_items_from_stays(stays, inp.role), inp.exchange_rate),
        inp.daily_payment_method,
    )

    meal_items = [
        (stay.grade, get_meal_rate_usd(inp.role, stay.grade), stay.stay_days or 0)
        for stay in stays
    ]
    meal = _line_from_slices(_slices_by_grade(meal_items, inp.exchange_rate), inp.meal_payment_method)

    lodging_items = [
        (stay.grade, get_lodging_rate_usd(inp.role, stay.grade), stay.nights)
        for stay in stays
    ]
    lodging_slices = _slices_by_grade(lodging_items, inp.exchange_rate)
    ceiling_usd = sum(item.amount_usd for item in lodging_slices)
    ceiling_krw = sum(item.amount_krw for item in lodging_slices)
    if len(lodging_slices) == 1:
        lodging_rate = lodging_slices[0].rate_usd
    elif stays:
        lodging_rate = get_lodging_rate_usd(inp.role, stays[0].grade)
    else:
        lodging_rate = 0
    lodging = settle_lodging(
        rate_usd=lodging_rate,
        nights=lodging_nights,
        ceiling_usd=ceiling_usd,
        ceiling_krw=ceiling_krw,
        actual_krw=_resolved_lodging_actual(inp, stays),
        payment_method=inp.lodging_payment_method,
    )
    lodging = replace(lodging, slices=lodging_slices)

    payment_items = [
        (execution_krw(inp.airfare_krw), inp.airfare_payment_method),
        (execution_krw(daily.amount_krw), daily.payment_method),
        (execution_krw(meal.amount_krw), meal.payment_method),
        (execution_krw(lodging.payable_krw), lodging.payment_method),
        (execution_krw(inp.preparation_krw), inp.preparation_payment_method),
    ]
    total_krw = sum(amount for amount, _ in payment_items)
    warnings = list(validation.warnings)
    if lodging.warning:
        warnings.append(lodging.warning)

    return TravelResult(
        role=inp.role,
        role_excel=ROLE_EXCEL_LABEL[inp.role],
        grade=_grade_summary(stays),
        trip_days=trip_days,
        lodging_nights=lodging_nights,
        exchange_rate=inp.exchange_rate,
        daily=daily,
        meal=meal,
        lodging=lodging,
        airfare_krw=inp.airfare_krw,
        airfare_payment_method=inp.airfare_payment_method,
        preparation_krw=inp.preparation_krw,
        preparation_payment_method=inp.preparation_payment_method,
        total_krw=total_krw,
        corporate_card_total=_sum_by_payment(payment_items, PAYMENT_CORPORATE),
        personal_transfer_total=_sum_by_payment(payment_items, PAYMENT_PERSONAL),
        warnings=warnings,
        stays=tuple(stays),
        grade_quantities=grade_block_quantities(stays),
        rental_days=rental_days,
    )

