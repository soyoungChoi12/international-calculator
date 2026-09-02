"""국가·도시 등급 판정 검증. 지침 2026.08.10. 4쪽 기준."""

from services.destination_grade_service import resolve_destination_grade


def test_designated_city_overrides_country():
    assert resolve_destination_grade("미국", "샌프란시스코").grade == "가"
    assert resolve_destination_grade("미국", "샌프란시스코").source == "city"
    assert resolve_destination_grade("프랑스", "파리").grade == "가"
    assert resolve_destination_grade("일본", "도쿄").grade == "가"
    assert resolve_destination_grade("중국", "베이징").grade == "나"


def test_country_grade_when_city_not_designated():
    seattle = resolve_destination_grade("미국", "시애틀")
    assert seattle.grade == "나"
    assert seattle.source == "country"
    assert resolve_destination_grade("프랑스", "리옹").grade == "나"
    assert resolve_destination_grade("일본", "오사카").grade == "나"
    assert resolve_destination_grade("중국", "상하이").grade == "다"


def test_country_only():
    assert resolve_destination_grade("베트남", "").grade == "라"
    assert resolve_destination_grade("싱가포르", "").grade == "가"
    assert resolve_destination_grade("홍콩", "").grade == "가"
    assert resolve_destination_grade("태국", "").grade == "다"
    assert resolve_destination_grade("호주", "").grade == "나"


def test_aliases():
    assert resolve_destination_grade("USA", "San Francisco").grade == "가"
    assert resolve_destination_grade("일본", "동경").grade == "가"
    assert resolve_destination_grade("미국", "워싱턴DC").grade == "가"
    assert resolve_destination_grade("대만", "").grade == "나"


def test_unknown_does_not_guess():
    result = resolve_destination_grade("아틀란티스", "가상도시")
    assert result.grade is None
    assert result.ok is False
    assert "직접 선택" in result.message
