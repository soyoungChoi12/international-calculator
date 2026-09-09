"""국외출장 심사신청서 HWP — 템플릿 사본에 계산 결과를 넣는다."""

from __future__ import annotations

from datetime import date
from io import BytesIO

import olefile

from services.hwp_export import TEMPLATE_PATH, build_hwp_bytes, build_hwp_fields, hwp_filename
from services.plan_parser import PlanDocument, parse_plan_pdf, parse_plan_text
from services.travel_calculator import StayInput, TravelInput, calculate_travel
from tests.test_plan_parser import SAMPLE_PLAN


def _kyoto_input() -> TravelInput:
    return TravelInput(
        role="팀장 및 팀원",
        grade="나",
        departure_date=date(2026, 6, 30),
        return_date=date(2026, 7, 3),
        lodging_nights=3,
        exchange_rate=1535,
        airfare_krw=520_300,
        lodging_actual_krw=0,
        preparation_krw=150_000,
        stays=[StayInput("일본", "교토", 3, "나", stay_days=4)],
    )


def _prv_text(data: bytes) -> str:
    ole = olefile.OleFileIO(BytesIO(data))
    text = ole.openstream("PrvText").read().decode("utf-16le")
    ole.close()
    return text


def test_hwp_filename_uses_date_name_title():
    assert hwp_filename("이한주", "전임", date(2026, 6, 22)) == "(260622) 국외출장 심사신청서 (이한주 전임).hwp"
    assert hwp_filename("", "", date(2026, 9, 4)) == "(260904) 국외출장 심사신청서 (미기재).hwp"


def test_template_is_not_modified_by_export():
    before = TEMPLATE_PATH.read_bytes()
    result = calculate_travel(_kyoto_input())
    build_hwp_bytes(result, "이한주", title="전임", team="글로벌전략협업팀")
    assert TEMPLATE_PATH.read_bytes() == before


def test_kyoto_fields_match_example_shape():
    plan = parse_plan_text(SAMPLE_PLAN)
    plan = PlanDocument(
        **{
            **plan.__dict__,
            "domestic_krw": 30_000,
            "headquarters": "글로벌본부",
            "event_name": "IVS 2026 Kyoto",
            "purpose_lines": (
                "2026 AroundX 정글 참여기업 5개사 일본 GTM 지원",
                "IVS 2026 Kyoto 참관 및 현지 VC/CVC/스타트업 지원 기관 네트워크 확대",
            ),
            "budget_category": "2026년 글로벌기업 협업 프로그램(AroundX) 사업 – 주관기관 운영 - 운영비 - 여비",
            "airfare_note": " (김포 - 간사이공항)",
            "preparation_items": (("여행자보험비", 70_000), ("오사카-교토 왕복기차비", 80_000)),
        }
    )
    result = calculate_travel(_kyoto_input())
    fields = build_hwp_fields(
        result,
        "이한주",
        title="전임",
        team="글로벌전략협업팀",
        plan=plan,
        departure=date(2026, 6, 30),
        return_on=date(2026, 7, 3),
    )
    assert "2026 AroundX 정글 참여기업 5개사 일본 GTM 지원" in fields["purpose"]
    assert "IVS 2026 Kyoto 참관" in fields["purpose"]
    assert fields["region"] == "◦ 일본 교토(출장지역 ‘나’ 지역)"
    assert "2026.6.30.(화).~2026.7.3(금), 3박 4일 일정" in fields["schedule"]
    assert "IVS 2026 Kyoto" in fields["event"]
    assert fields["traveler"] == "◦ 글로벌본부 글로벌전략협업팀 이한주 전임"
    assert fields["budget_who"] == "이한주 전임"
    assert "총 1,727천원(나 지역, 3박 4일)" in fields["budget"]
    assert "왕복항공료 : 520천원 (김포 - 간사이공항)" in fields["budget"]
    assert "대중교통운임비(공항) : 30천원" in fields["budget"]
    assert "숙박비 : 566천원($123x3=$369)" in fields["budget"]
    assert "일  비 : 160천원($26x4=$104)" in fields["budget"]
    assert "식  비 : 301천원($49x4=$196)" in fields["budget"]
    assert "여행자보험비 : 70천원" in fields["budget"]
    assert "오사카-교토 왕복기차비 : 80천원" in fields["budget"]
    assert "실비 정산 예정" in fields["budget"]
    assert "AroundX" in fields["category"]


def test_build_hwp_bytes_writes_preview_and_body():
    plan = parse_plan_text(SAMPLE_PLAN)
    result = calculate_travel(_kyoto_input())
    data = build_hwp_bytes(
        result,
        "이한주",
        title="전임",
        team="글로벌전략협업팀",
        plan=plan,
        departure=date(2026, 6, 30),
        return_on=date(2026, 7, 3),
    )
    assert data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    preview = _prv_text(data)
    assert "이한주" in preview
    assert "교토" in preview
    assert "국외출장 심사신청서" in preview
    ole = olefile.OleFileIO(BytesIO(data))
    assert ole.exists("BodyText/Section0")
    assert ole.exists("FileHeader")
    ole.close()


def test_sample_pdf_fills_hwp_like_example():
    path = (
        __import__("pathlib").Path(r"c:\Users\ccei\Desktop\참고\해외여비계산기\피드백")
        / "2026년 AroundX 정글 - 해외GTM 프로그램 참가기업 지원 계획(안) (수정).pdf"
    )
    if not path.exists():
        return
    plan = parse_plan_pdf(path.read_bytes())
    result = calculate_travel(_kyoto_input())
    fields = build_hwp_fields(
        result,
        plan.travelers[0].name,
        title=plan.travelers[0].title,
        team=plan.travelers[0].team,
        plan=plan,
        departure=plan.departure,
        return_on=plan.return_on,
    )
    assert plan.event_name == "IVS 2026 Kyoto"
    assert plan.domestic_krw == 30_000
    assert plan.headquarters == "글로벌본부"
    assert "AroundX" in fields["purpose"]
    assert "IVS 2026 Kyoto" in fields["event"]
    assert "이한주 전임" in fields["traveler"]
    assert "김포에서 교토" in fields["schedule"]
    assert "The Super Night" in fields["schedule"]
    assert "East Asia Startup Nexus" in fields["schedule"]
    assert "김포 - 간사이공항" in fields["budget"]
    assert "주관기관 운영" in fields["category"]
    data = build_hwp_bytes(
        result,
        plan.travelers[0].name,
        title=plan.travelers[0].title,
        team=plan.travelers[0].team,
        plan=plan,
        departure=plan.departure,
        return_on=plan.return_on,
    )
    preview = _prv_text(data)
    assert "IVS 2026 Kyoto" in preview
    assert "1,727천원" in preview


def test_stale_plan_without_purpose_lines_still_builds():
    from types import SimpleNamespace

    result = calculate_travel(_kyoto_input())
    old = SimpleNamespace(
        purpose="정글 지원",
        event_name="IVS 2026 Kyoto",
        travelers=(),
        country="일본",
        city="교토",
        grade="나",
        departure=date(2026, 6, 30),
        return_on=date(2026, 7, 3),
        budget_category="AroundX",
    )
    data = build_hwp_bytes(
        result,
        "이한주",
        title="전임",
        plan=old,
        departure=old.departure,
        return_on=old.return_on,
    )
    assert data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    assert "이한주" in _prv_text(data)
