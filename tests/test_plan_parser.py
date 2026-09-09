"""계획안 텍스트에서 출장자·일정·항공료·준비금을 읽는지 검증."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from services.plan_parser import parse_plan_pdf, parse_plan_text, refresh_plan_document, role_from_title

SAMPLE_PLAN = """
년 정글 해외 프로그램 2026 AroundX - GTM
참가기업 지원 계획 안( )
프로그램명 정글 일본 지원   : 2026 AroundX GTM
행 사 명 교토  : 2026 IVS
목 적: 협력하여 년 정글 선정기업 개사AWS Korea/Japan 26 AroundX 5
의 일본 지원 교토 참관 및 현지 네트워킹 지원GTM , IVS
출 장 기 간 및 장 소   : 년 월 일 월 일 박 일 교토일본‘26 6 30 ~ 7 3 (3 4 )/ ,
  출장자 : 글로벌전략협업팀 이한주 전임
  출장내용
정글 참여기업 일본 지원
소요예산 안( )
예산과목 : 년 글기협 주관기관 운영비 여비26 AroundX -
ㅇ 항공이코노미 인 원( , 1 ) : 520,300
1,697,215
ㅇ 숙박비 원 여비규정 준수 : 566,415
나 지역 박  - ( ) $123*3 =$369
ㅇ 식비 원: 300,860
ㅇ 일비 원: 159,640
ㅇ 준비금 :
여행자보험 인 원  - *1 =70,000
  간사이공항 교토 왕복기차비 원- <-> = 80,000
국내여비 ㅇ 대중교통운임비 원: 30,000
"""

MULTI_TRAVELER_PLAN = """
출장기간 및 장소 : 2026.09.01 ~ 2026.09.05 (4박 5일) 샌프란시스코, 미국
출장자 : 글로벌전략협업팀 이한주 전임, 김기현 팀장, 이종휘 본부장
ㅇ 항공료 : 1,200,000
ㅇ 준비금 : 70,000
"""


def test_role_from_title_maps_center_and_head():
    assert role_from_title("센터장") == "센터장"
    assert role_from_title("본부장") == "본부장"
    assert role_from_title("전임") == "팀장 및 팀원"
    assert role_from_title("팀장") == "팀장 및 팀원"


def test_sample_plan_fills_traveler_dates_place_and_costs():
    plan = parse_plan_text(SAMPLE_PLAN)
    assert len(plan.travelers) == 1
    assert plan.travelers[0].name == "이한주"
    assert plan.travelers[0].title == "전임"
    assert plan.travelers[0].role == "팀장 및 팀원"
    assert plan.departure == date(2026, 6, 30)
    assert plan.return_on == date(2026, 7, 3)
    assert plan.nights == 3
    assert plan.country == "일본"
    assert plan.city == "교토"
    assert plan.grade == "나"
    assert plan.airfare_krw == 520_300
    assert plan.preparation_krw == 150_000
    assert plan.domestic_krw == 30_000
    assert plan.headquarters == "글로벌본부" or "글로벌전략협업팀" in (plan.travelers[0].team if plan.travelers else "")
    assert "IVS" in plan.event_name or "IVS" in plan.purpose


def test_multiple_travelers_keep_roles():
    plan = parse_plan_text(MULTI_TRAVELER_PLAN)
    names = [item.name for item in plan.travelers]
    assert names == ["이한주", "김기현", "이종휘"]
    assert [item.role for item in plan.travelers] == ["팀장 및 팀원", "팀장 및 팀원", "본부장"]
    assert plan.country == "미국"
    assert plan.city == "센프란시스코"
    assert plan.grade == "가"
    assert plan.departure == date(2026, 9, 1)
    assert plan.return_on == date(2026, 9, 5)
    assert plan.airfare_krw == 1_200_000
    assert plan.preparation_krw == 70_000


def test_sample_pdf_if_present():
    path = Path(r"c:\Users\ccei\Desktop\참고\해외여비계산기\피드백") / (
        "2026년 AroundX 정글 - 해외GTM 프로그램 참가기업 지원 계획(안) (수정).pdf"
    )
    if not path.exists():
        return
    plan = parse_plan_pdf(path.read_bytes())
    assert plan.travelers[0].name == "이한주"
    assert plan.travelers[0].role == "팀장 및 팀원"
    assert plan.departure == date(2026, 6, 30)
    assert plan.return_on == date(2026, 7, 3)
    assert plan.country == "일본"
    assert plan.city == "교토"
    assert plan.grade == "나"
    assert plan.airfare_krw == 520_300
    assert plan.preparation_krw == 150_000
    assert plan.domestic_krw == 30_000
    assert plan.event_name == "IVS 2026 Kyoto"
    assert plan.headquarters == "글로벌본부"
    assert plan.airfare_note == " (김포 - 간사이공항)"
    assert "AroundX" in "".join(plan.purpose_lines)
    assert any(day == date(2026, 6, 30) for day, _ in plan.itinerary)


def test_refresh_plan_document_reparses_stale_object():
    from types import SimpleNamespace

    stale = SimpleNamespace(raw_text=SAMPLE_PLAN)
    plan = refresh_plan_document(stale)
    assert plan.travelers[0].name == "이한주"
    assert plan.purpose_lines
    assert plan.airfare_krw == 520_300
