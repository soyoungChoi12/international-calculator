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


def _body_texts(data: bytes) -> list[str]:
    import zlib

    from services.hwp_export import HWPTAG_PARA_TEXT, _iter_records, _visible_text

    ole = olefile.OleFileIO(BytesIO(data))
    raw = ole.openstream("BodyText/Section0").read()
    ole.close()
    body = None
    for wbits in (-15, 15, zlib.MAX_WBITS):
        try:
            body = zlib.decompress(raw, wbits)
            break
        except zlib.error:
            continue
    assert body is not None
    texts = []
    for _header, tag, _level, _extra, payload in _iter_records(body):
        if tag != HWPTAG_PARA_TEXT:
            continue
        visible = _visible_text(payload)
        if visible.strip():
            texts.append(visible)
    return texts


def _cell_nparas(data: bytes, needle: str) -> int | None:
    import struct
    import zlib

    from services.hwp_export import HWPTAG_LIST_HEADER, HWPTAG_PARA_TEXT, _iter_records, _visible_text

    ole = olefile.OleFileIO(BytesIO(data))
    raw = ole.openstream("BodyText/Section0").read()
    ole.close()
    body = None
    for wbits in (-15, 15, zlib.MAX_WBITS):
        try:
            body = zlib.decompress(raw, wbits)
            break
        except zlib.error:
            continue
    assert body is not None
    last_nparas = None
    for _header, tag, _level, _extra, payload in _iter_records(body):
        if tag == HWPTAG_LIST_HEADER and len(payload) >= 4:
            last_nparas = struct.unpack_from("<I", payload, 0)[0]
        elif tag == HWPTAG_PARA_TEXT and needle in _visible_text(payload):
            return last_nparas
    return None


def _cell_nparas_exact(data: bytes, needle: str) -> int | None:
    import struct
    import zlib

    from services.hwp_export import HWPTAG_LIST_HEADER, HWPTAG_PARA_TEXT, _iter_records, _visible_text

    ole = olefile.OleFileIO(BytesIO(data))
    raw = ole.openstream("BodyText/Section0").read()
    ole.close()
    body = None
    for wbits in (-15, 15, zlib.MAX_WBITS):
        try:
            body = zlib.decompress(raw, wbits)
            break
        except zlib.error:
            continue
    assert body is not None
    last_nparas = None
    for _header, tag, _level, _extra, payload in _iter_records(body):
        if tag == HWPTAG_LIST_HEADER and len(payload) >= 4:
            last_nparas = struct.unpack_from("<I", payload, 0)[0]
        elif tag == HWPTAG_PARA_TEXT and _visible_text(payload).strip() == needle:
            return last_nparas
    return None


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
    assert fields["budget_who"] == "이한주\n전임"
    assert fields["category_who"] == "출장자 1인"
    assert "총 1,727,210원(나 지역, 3박 4일)" in fields["budget"]
    assert "왕복항공료 : 520,300원 (김포 - 간사이공항)" in fields["budget"]
    assert "대중교통운임비(공항) : 30,000원" in fields["budget"]
    assert "숙박비 : 566,410원($123x3=$369)" in fields["budget"]
    assert "일  비 : 159,640원($26x4=$104)" in fields["budget"]
    assert "식  비 : 300,860원($49x4=$196)" in fields["budget"]
    assert "여행자보험비 : 70,000원" in fields["budget"]
    assert "오사카-교토 왕복기차비 : 80,000원" in fields["budget"]
    assert "실비 정산 예정" in fields["budget"]
    assert "AroundX" in fields["category"]


def test_breakfast_note_in_hwp_budget():
    result = calculate_travel(
        TravelInput(
            role="팀장 및 팀원",
            grade="나",
            departure_date=date(2026, 6, 30),
            return_date=date(2026, 7, 3),
            lodging_nights=3,
            exchange_rate=1535,
            airfare_krw=520_300,
            lodging_actual_krw=0,
            preparation_krw=150_000,
            stays=[StayInput("일본", "교토", 3, "나", stay_days=4, breakfast_included=True)],
        )
    )
    fields = build_hwp_fields(
        result,
        "이한주",
        title="전임",
        team="글로벌전략협업팀",
        plan=None,
        departure=date(2026, 6, 30),
        return_on=date(2026, 7, 3),
    )
    assert "식  비 : 227,180원($148, 조식 3일 1/3 공제)" in fields["budget"]


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
    texts = _body_texts(data)
    assert any("3박 4일" in item and "김포에서" not in item for item in texts)
    assert any("귀국" in item or "김포" in item for item in texts)
    assert all("\n" not in item for item in texts)
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
    assert "1,727,210원" in preview
    texts = _body_texts(data)
    purpose_lines = [item for item in texts if "AroundX 정글" in item or "VC/CVC" in item]
    assert len(purpose_lines) >= 2
    assert all("\n" not in item for item in purpose_lines)
    assert any("김포에서 교토" in item and "3박 4일" not in item for item in texts)
    assert any("왕복항공료" in item and "교통비 :" not in item for item in texts)
    assert any("여행자보험비" in item and "준비금" not in item for item in texts)
    nparas = _cell_nparas(data, "AroundX 정글")
    assert nparas == len(purpose_lines)
    nparas = _cell_nparas(data, "김포에서 교토")
    assert nparas is not None and nparas >= 6
    nparas = _cell_nparas(data, "왕복항공료")
    assert nparas is not None and nparas >= 10
    assert any(item.strip() == "이한주" for item in texts)
    assert any(item.strip() == "전임" for item in texts)
    assert any(item.strip() == "출장자" for item in texts)
    assert any(item.strip() == "1인" for item in texts)
    assert any("AroundX" in item and "주관기관" not in item for item in texts)
    assert any("주관기관 운영" in item and "AroundX" not in item for item in texts)
    assert _cell_nparas_exact(data, "이한주") == 2
    assert _cell_nparas_exact(data, "출장자") == 2
    nparas = _cell_nparas(data, "주관기관 운영")
    assert nparas is not None and nparas >= 2


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
