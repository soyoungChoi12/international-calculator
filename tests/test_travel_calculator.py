"""STEP 2 계산 로직 검증."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from data.travel_rates import PAYMENT_CORPORATE, PAYMENT_PERSONAL, get_daily_rate_usd
from services.travel_calculator import (
    StayInput,
    TravelInput,
    calculate_trip_days,
    calculate_travel,
    lodging_actual_by_grade,
    round_to_ten,
    settle_lodging,
    truncate_to_ten,
    truncate_to_won,
    usd_to_krw_truncated,
    validate_travel_input,
)


def _sample(**overrides) -> TravelInput:
    data = dict(
        role="팀장 및 팀원",
        grade="가",
        departure_date=date(2026, 9, 3),
        return_date=date(2026, 9, 7),
        lodging_nights=4,
        exchange_rate=1400,
        airfare_krw=1_200_000,
        lodging_actual_krw=750_000,
        preparation_krw=70_000,
        airfare_payment_method=PAYMENT_CORPORATE,
        daily_payment_method=PAYMENT_PERSONAL,
        meal_payment_method=PAYMENT_PERSONAL,
        lodging_payment_method=PAYMENT_CORPORATE,
        preparation_payment_method=PAYMENT_CORPORATE,
    )
    data.update(overrides)
    return TravelInput(**data)


def test_trip_days_includes_both_ends():
    assert calculate_trip_days(date(2026, 9, 3), date(2026, 9, 7)) == 5


def test_round_to_ten_half_up():
    assert round_to_ten(163809) == 163810
    assert round_to_ten(163804) == 163800
    assert round_to_ten(163805) == 163810
    assert round_to_ten(Decimal("163809.36")) == 163810


def test_truncate_to_won_drops_fraction():
    assert truncate_to_won(Decimal("510205.9")) == 510_205
    assert truncate_to_won(510_205) == 510_205
    assert truncate_to_won(Decimal("163809.36")) == 163_809


def test_truncate_to_ten_drops_ones():
    assert truncate_to_ten(163809) == 163800
    assert truncate_to_ten(163804) == 163800
    assert truncate_to_ten(163805) == 163800
    assert truncate_to_ten(Decimal("163809.36")) == 163800
    assert truncate_to_ten(163800) == 163800


def test_allowance_krw_keeps_won_units():
    assert usd_to_krw_truncated(104, 1575.09) == 163_809
    result = calculate_travel(_sample(exchange_rate=1575.09, lodging_nights=3))
    assert result.daily.amount_usd == 130
    assert result.daily.amount_krw == truncate_to_won(130 * 1575.09)
    assert result.meal.amount_krw == truncate_to_won(335 * 1575.09)
    assert result.lodging.ceiling_krw == truncate_to_won(155 * 3 * 1575.09)


def test_fx_1523_keeps_ones_place():
    result = calculate_travel(_sample(exchange_rate=1523))
    assert result.daily.amount_krw == 197_990
    assert result.meal.amount_krw == 510_205
    assert result.lodging.ceiling_krw == 944_260


def test_spec_example_under_ceiling():
    result = calculate_travel(_sample())
    assert result.trip_days == 5
    assert result.role_excel == "그외직원(팀장및팀원)"
    assert result.daily.rate_usd == 26
    assert result.daily.amount_usd == 130
    assert result.daily.amount_krw == 182_000
    assert result.meal.rate_usd == 67
    assert result.meal.amount_usd == 335
    assert result.meal.amount_krw == 469_000
    assert result.lodging.rate_usd == 155
    assert result.lodging.ceiling_usd == 620
    assert result.lodging.ceiling_krw == 868_000
    assert result.lodging.payable_krw == 750_000
    assert result.lodging.exceeded is False
    assert result.lodging.note == ""
    assert result.total_krw == 2_671_000
    assert result.corporate_card_total == 2_020_000
    assert result.personal_transfer_total == 651_000
    assert result.grade_quantities == {
        "가": {"daily": 5, "lodging": 4, "meal": 5},
    }
    assert result.stays[0].stay_days == 5


def test_lodging_over_ceiling_keeps_actual_and_notes_excess():
    result = calculate_travel(_sample(lodging_actual_krw=1_000_000))
    assert result.lodging.payable_krw == 1_000_000
    assert result.lodging.excess_krw == 132_000
    assert result.lodging.note == "상한액 868,000원 대비 132,000원 초과"
    assert any("상한액을 초과" in w for w in result.warnings)
    assert result.total_krw == 2_921_000


def test_settle_lodging_is_isolated():
    settlement = settle_lodging(
        rate_usd=155,
        nights=4,
        ceiling_usd=620,
        ceiling_krw=868_000,
        actual_krw=1_000_000,
        payment_method=PAYMENT_CORPORATE,
    )
    assert settlement.payable_krw == 1_000_000
    assert settlement.excess_krw == 132_000


def test_return_before_departure_is_error():
    validation = validate_travel_input(
        _sample(return_date=date(2026, 9, 1))
    )
    assert not validation.ok
    assert any("귀국일" in e for e in validation.errors)


def test_negative_amounts_are_errors():
    validation = validate_travel_input(_sample(airfare_krw=-1, lodging_actual_krw=-2, preparation_krw=-3))
    assert not validation.ok
    assert len(validation.errors) == 3


def test_too_many_nights_is_warning_not_error():
    validation = validate_travel_input(_sample(lodging_nights=8))
    assert validation.ok
    assert validation.warnings


def test_unknown_grade_is_error():
    validation = validate_travel_input(_sample(grade="마"))
    assert not validation.ok


def test_multi_city_uses_each_stay_grade():
    """영국 런던 1박(가) + 버밍엄 2박(나), 4일. 식비 남는 1일은 마지막 숙박지."""
    result = calculate_travel(
        _sample(
            lodging_nights=3,
            departure_date=date(2026, 8, 31),
            return_date=date(2026, 9, 3),
            lodging_actual_krw=500_000,
            stays=[
                StayInput("영국", "런던", 1, "가", "도시 지정등급 적용: 런던 → 가"),
                StayInput("영국", "버밍엄", 2, "나", "국가등급 적용: 영국 → 나"),
            ],
        )
    )
    assert result.trip_days == 4
    assert result.lodging_nights == 3
    assert result.grade == "가/나"
    assert result.daily.amount_usd == 104
    assert result.daily.amount_krw == 145_600
    assert [item.grade for item in result.daily.slices] == ["가", "나"]
    assert result.daily.slices[0].quantity == 1
    assert result.daily.slices[0].rate_usd == 26
    assert result.daily.slices[1].quantity == 3
    assert result.daily.slices[1].rate_usd == 26
    assert [item.grade for item in result.meal.slices] == ["가", "나"]
    assert result.meal.slices[0].quantity == 1
    assert result.meal.slices[0].rate_usd == 67
    assert result.meal.slices[1].quantity == 3
    assert result.meal.slices[1].rate_usd == 49
    assert result.meal.amount_krw == 93_800 + 205_800
    assert [item.grade for item in result.lodging.slices] == ["가", "나"]
    assert result.lodging.slices[0].quantity == 1
    assert result.lodging.slices[0].rate_usd == 155
    assert result.lodging.slices[1].quantity == 2
    assert result.lodging.slices[1].rate_usd == 123
    assert result.lodging.ceiling_krw == 217_000 + 344_400
    assert result.lodging.payable_krw == 500_000
    assert result.lodging.exceeded is False
    assert result.grade_quantities == {
        "가": {"daily": 1, "lodging": 1, "meal": 1},
        "나": {"daily": 3, "lodging": 2, "meal": 3},
    }
    assert result.stays[0].stay_days == 1
    assert result.stays[1].stay_days == 3


def test_lodging_actual_sums_stay_amounts_by_grade():
    stays = [
        StayInput("영국", "런던", 1, "가", stay_days=1, actual_krw=300_000),
        StayInput("영국", "버밍엄", 2, "나", stay_days=3, actual_krw=200_000),
    ]
    assert lodging_actual_by_grade(stays) == {"가": 300_000, "나": 200_000}
    result = calculate_travel(
        _sample(
            lodging_nights=3,
            departure_date=date(2026, 8, 31),
            return_date=date(2026, 9, 3),
            lodging_actual_krw=0,
            stays=stays,
        )
    )
    assert result.lodging.actual_krw == 500_000
    assert result.lodging.payable_krw == 500_000


def test_same_grade_lodging_actuals_are_combined():
    stays = [
        StayInput("미국", "샌프란시스코", 2, "가", stay_days=2, actual_krw=400_000),
        StayInput("미국", "로스앤젤레스", 2, "가", stay_days=3, actual_krw=350_000),
    ]
    assert lodging_actual_by_grade(stays) == {"가": 750_000}


def test_explicit_stay_days_split_daily_by_grade():
    """체류일을 직접 넣으면 일비 단가가 같아도 등급별 일수가 나뉜다."""
    result = calculate_travel(
        _sample(
            lodging_nights=3,
            departure_date=date(2026, 8, 31),
            return_date=date(2026, 9, 3),
            lodging_actual_krw=500_000,
            stays=[
                StayInput("영국", "런던", 1, "가", stay_days=2),
                StayInput("영국", "버밍엄", 2, "나", stay_days=2),
            ],
        )
    )
    assert result.daily.slices[0].quantity == 2
    assert result.daily.slices[1].quantity == 2
    assert result.meal.slices[0].quantity == 2
    assert result.meal.slices[1].quantity == 2
    assert result.grade_quantities == {
        "가": {"daily": 2, "lodging": 1, "meal": 2},
        "나": {"daily": 2, "lodging": 2, "meal": 2},
    }


def test_stay_days_mismatch_is_warning():
    validation = validate_travel_input(
        _sample(
            stays=[StayInput("영국", "런던", 4, "가", stay_days=3)],
        )
    )
    assert validation.ok
    assert any("체류일" in warning for warning in validation.warnings)


def test_director_rates():
    assert get_daily_rate_usd("센터장") == 40
    result = calculate_travel(_sample(role="센터장", lodging_actual_krw=0))
    assert result.daily.rate_usd == 40
    assert result.meal.rate_usd == 133
    assert result.lodging.rate_usd == 282


def test_rental_days_use_half_daily_rate():
    """차량 임차 2일은 일비 기준액의 1/2."""
    result = calculate_travel(
        _sample(
            stays=[StayInput("미국", "샌프란시스코", 4, "가", stay_days=5, rental_days=2)],
        )
    )
    assert result.rental_days == 2
    assert result.daily.amount_usd == 26 * 3 + 13 * 2
    assert result.daily.amount_krw == 109_200 + 36_400
    assert result.daily.slices[0].rate_usd == 26
    assert result.daily.slices[0].quantity == 3
    assert result.daily.slices[1].rate_usd == 13
    assert result.daily.slices[1].quantity == 2
    assert result.daily.slices[1].label == "차량임차 1/2"
    assert result.total_krw == 2_671_000 - 36_400


def test_rental_days_over_stay_days_is_warning():
    validation = validate_travel_input(
        _sample(stays=[StayInput("미국", "샌프란시스코", 4, "가", stay_days=5, rental_days=6)])
    )
    assert validation.ok
    assert any("차량 임차" in warning for warning in validation.warnings)
