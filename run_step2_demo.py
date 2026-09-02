"""STEP 2 계산 결과 확인용 실행 스크립트."""

from datetime import date

from data.travel_rates import PAYMENT_CORPORATE, PAYMENT_PERSONAL
from services.travel_calculator import TravelInput, calculate_travel, validate_travel_input


def show(title: str, inp: TravelInput) -> None:
    print("=" * 60)
    print(title)
    print("=" * 60)
    validation = validate_travel_input(inp)
    if validation.errors:
        print("[오류]")
        for item in validation.errors:
            print(f"  - {item}")
        print()
        return

    result = calculate_travel(inp)
    print(f"출장자 구분 : {result.role}  (Excel B2: {result.role_excel})")
    print(f"지역등급    : {result.grade}")
    print(f"출장일수    : {result.trip_days}일")
    print(f"숙박일수    : {result.lodging_nights}박")
    print(f"적용환율    : {result.exchange_rate}")
    print()
    print(f"항공료      : {result.airfare_krw:>12,}원  / {result.airfare_payment_method}")
    print(
        f"일비        : {result.daily.amount_krw:>12,}원  "
        f"({result.daily.rate_usd} USD x {result.daily.days}일 = {result.daily.amount_usd} USD)"
    )
    print(
        f"식비        : {result.meal.amount_krw:>12,}원  "
        f"({result.meal.rate_usd} USD x {result.meal.days}일 = {result.meal.amount_usd} USD)"
    )
    print(
        f"숙박비 상한 : {result.lodging.ceiling_krw:>12,}원  "
        f"({result.lodging.rate_usd} USD x {result.lodging.nights}박 = {result.lodging.ceiling_usd} USD)"
    )
    print(f"숙박비 실비 : {result.lodging.actual_krw:>12,}원  → C9 입력액 {result.lodging.payable_krw:,}원")
    if result.lodging.note:
        print(f"숙박비 비고 : {result.lodging.note}")
    print(f"준비금      : {result.preparation_krw:>12,}원")
    print()
    print(f"총액        : {result.total_krw:>12,}원")
    print(f"법인카드    : {result.corporate_card_total:>12,}원")
    print(f"개인지급    : {result.personal_transfer_total:>12,}원")
    if result.warnings:
        print()
        print("[경고]")
        for item in result.warnings:
            print(f"  - {item}")
    print()


def main() -> None:
    base = dict(
        role="팀장 및 팀원",
        grade="가",
        departure_date=date(2026, 9, 3),
        return_date=date(2026, 9, 7),
        lodging_nights=4,
        exchange_rate=1400,
        airfare_krw=1_200_000,
        preparation_krw=70_000,
        airfare_payment_method=PAYMENT_CORPORATE,
        daily_payment_method=PAYMENT_PERSONAL,
        meal_payment_method=PAYMENT_PERSONAL,
        lodging_payment_method=PAYMENT_CORPORATE,
        preparation_payment_method=PAYMENT_CORPORATE,
    )
    show("예시 1) 실비 750,000원 / 상한 이내", TravelInput(lodging_actual_krw=750_000, **base))
    show("예시 2) 실비 1,000,000원 / 상한 초과", TravelInput(lodging_actual_krw=1_000_000, **base))
    show(
        "예시 3) 검증 / 귀국일이 출국일보다 빠름",
        TravelInput(
            lodging_actual_krw=750_000,
            **{**base, "return_date": date(2026, 9, 1)},
        ),
    )


if __name__ == "__main__":
    main()
