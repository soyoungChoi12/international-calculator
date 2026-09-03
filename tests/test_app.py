"""STEP 4 입력 화면 — 예시 계산이 결과 구역에 표시되는지 검증."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _by_label(widgets, label: str):
    return next(w for w in widgets if w.label == label)


def test_empty_destination_does_not_warn():
    at = AppTest.from_file(str(APP_PATH))
    at.run()

    assert not at.warning
    assert all(w.label != "지역등급 직접 선택" for w in at.selectbox)


def test_nights_and_stay_days_are_editable_before_adding_stay():
    at = AppTest.from_file(str(APP_PATH))
    at.run()
    today = date.today()
    trip_days = (today - today).days + 1
    nights = _by_label(at.number_input, "숙박일수 (박)")
    stay_days = _by_label(at.number_input, "체류일 (일)")
    assert nights.value == max(trip_days - 1, 0)
    assert stay_days.value == trip_days
    nights.set_value(2)
    stay_days.set_value(3)
    at.run()
    assert _by_label(at.number_input, "숙박일수 (박)").value == 2
    assert _by_label(at.number_input, "체류일 (일)").value == 3


def test_first_stay_nights_can_change_after_adding_second():
    at = AppTest.from_file(str(APP_PATH))
    at.run()
    next(button for button in at.button if button.label == "출장지 추가").click().run()
    nights = [widget for widget in at.number_input if widget.label == "숙박일수 (박)"]
    stay_days = [widget for widget in at.number_input if widget.label == "체류일 (일)"]
    nights[0].set_value(1)
    stay_days[0].set_value(2)
    at.run()
    nights = [widget for widget in at.number_input if widget.label == "숙박일수 (박)"]
    stay_days = [widget for widget in at.number_input if widget.label == "체류일 (일)"]
    assert nights[0].value == 1
    assert stay_days[0].value == 2


def test_add_stay_shows_second_destination():
    at = AppTest.from_file(str(APP_PATH))
    at.run()
    next(button for button in at.button if button.label == "출장지 추가").click().run()

    countries = [widget for widget in at.selectbox if widget.label == "출장 국가"]
    cities = [widget for widget in at.text_input if widget.label == "출장 도시"]
    nights = [widget for widget in at.number_input if widget.label == "숙박일수 (박)"]
    stay_days = [widget for widget in at.number_input if widget.label == "체류일 (일)"]
    assert len(countries) == 2
    assert len(cities) == 2
    assert len(nights) == 2
    assert len(stay_days) == 2
    lodging_actuals = [widget for widget in at.number_input if str(widget.label).startswith("숙박비 실비")]
    assert len(lodging_actuals) == 2
    assert any("체류일 합계" in caption.value for caption in at.caption)


def test_add_rental_shows_rental_days():
    at = AppTest.from_file(str(APP_PATH))
    at.run()
    next(button for button in at.button if button.label == "차량 임차 추가").click().run()

    assert any(widget.label == "차량 임차 일수 (일)" for widget in at.number_input)
    assert _by_label(at.number_input, "차량 임차 일수 (일)").value == 1
    assert any("일비 기준액의 1/2" in caption.value for caption in at.caption)


def test_undesignated_city_shows_country_grade():
    at = AppTest.from_file(str(APP_PATH))
    at.run()
    _by_label(at.selectbox, "출장 국가").select("미국")
    _by_label(at.text_input, "출장 도시").set_value("시애틀")
    at.run()

    assert any("국가등급 적용: 미국 → 나" in s.value for s in at.success)
    assert not at.warning


def test_country_alone_does_not_show_grade():
    at = AppTest.from_file(str(APP_PATH))
    at.run()
    _by_label(at.selectbox, "출장 국가").select("미국")
    at.run()

    assert not at.success
    assert not at.warning
    assert all(w.label != "지역등급 직접 선택" for w in at.selectbox)


def test_city_alone_does_not_show_grade():
    at = AppTest.from_file(str(APP_PATH))
    at.run()
    _by_label(at.text_input, "출장 도시").set_value("샌프란시스코")
    at.run()

    assert not at.success
    assert not at.warning
    assert all(w.label != "지역등급 직접 선택" for w in at.selectbox)


def test_country_and_city_shows_city_grade():
    at = AppTest.from_file(str(APP_PATH))
    at.run()
    _by_label(at.selectbox, "출장 국가").select("미국")
    _by_label(at.text_input, "출장 도시").set_value("샌프란시스코")
    at.run()

    assert any("센프란시스코 → 가" in s.value for s in at.success)


def test_dates_default_to_today():
    at = AppTest.from_file(str(APP_PATH))
    at.run()
    today = date.today()
    assert _by_label(at.date_input, "출국일").value == today
    assert _by_label(at.date_input, "귀국일").value == today
    assert _by_label(at.date_input, "출장신청서 결재일").value == today


def test_changing_approval_date_loads_that_days_fx(monkeypatch):
    from services.hana_fx import HanaFxQuote
    from services import hana_fx

    def fake_fetch(when: date):
        rate = 1404.65 if when == date(2026, 8, 28) else 1382.88
        return HanaFxQuote(rate=rate, posted_on=when, round_no=1, posted_at="10:00")

    monkeypatch.setattr(hana_fx, "fetch_usd_cash_buy", fake_fetch)
    at = AppTest.from_file(str(APP_PATH))
    at.run()
    _by_label(at.date_input, "출장신청서 결재일").set_value(date(2026, 8, 28))
    at.run()
    assert not at.exception
    assert _by_label(at.number_input, "적용환율 (USD/KRW)").value == 1404.65
    assert any("1,404.65원" in caption.value and "2026-08-28" in caption.value for caption in at.caption)


def test_example_trip_shows_expected_totals():
    at = AppTest.from_file(str(APP_PATH))
    at.run()

    _by_label(at.date_input, "출국일").set_value(date(2026, 8, 31))
    _by_label(at.date_input, "귀국일").set_value(date(2026, 9, 4))
    at.run()

    _by_label(at.text_input, "출장자명").set_value("홍길동")
    _by_label(at.selectbox, "출장 국가").select("미국")
    _by_label(at.text_input, "출장 도시").set_value("샌프란시스코")
    _by_label(at.number_input, "적용환율 (USD/KRW)").set_value(1400.0)
    _by_label(at.number_input, "항공료 (원)").set_value(1_200_000)
    _by_label(at.number_input, "숙박비 실비 (원)").set_value(750_000)
    _by_label(at.number_input, "준비금 (원)").set_value(70_000)
    next(button for button in at.button if button.label == "여비 계산").click()
    at.run()

    assert not at.exception
    assert not at.error
    assert any("센프란시스코 → 가" in s.value for s in at.success)
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["총액"] == "2,671,000원"
    assert metrics["법인카드 결제"] == "2,020,000원"
    assert metrics["개인지급(계좌이체)"] == "651,000원"
    assert "3. 자동계산 결과" in [s.value for s in at.subheader]
    markdown = "\n".join(item.value for item in at.markdown)
    assert "**1,200,000원**" in markdown
    assert "**182,000원**" in markdown
    assert "**469,000원**" in markdown
    assert "**868,000원**" in markdown
    assert "**750,000원**" in markdown
    assert "**70,000원**" in markdown
    assert any(button.label == "Excel 다운로드" for button in at.download_button)
