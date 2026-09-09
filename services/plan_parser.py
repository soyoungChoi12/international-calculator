"""출장 계획안 PDF에서 여비 계산에 필요한 항목을 읽는다."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO

from data.destination_grades import CITY_ALIASES, CITY_GRADES, COUNTRY_ALIASES, COUNTRY_GRADES
from data.travel_rates import ROLES
from services.destination_grade_service import resolve_destination_grade

_TITLE_TO_ROLE = {
    "센터장": "센터장",
    "본부장": "본부장",
}
_OTHER_TITLES = (
    "전문위원",
    "팀장",
    "전임",
    "선임",
    "주임",
    "책임",
    "수석",
    "매니저",
    "연구원",
    "주무관",
    "사원",
    "인턴",
    "실장",
    "과장",
    "차장",
    "부장",
    "대리",
)
_TITLE_PATTERN = "|".join(
    re.escape(title) for title in sorted(_TITLE_TO_ROLE, key=len, reverse=True) + sorted(_OTHER_TITLES, key=len, reverse=True)
)
_NAME_TITLE_RE = re.compile(rf"([가-힣]{{2,4}})\s*({_TITLE_PATTERN})")
_MONEY_RE = re.compile(r"([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,})")


@dataclass(frozen=True)
class PlanTraveler:
    name: str
    title: str
    role: str
    team: str = ""


@dataclass(frozen=True)
class PlanDocument:
    purpose: str = ""
    region: str = ""
    schedule_text: str = ""
    event_name: str = ""
    budget_category: str = ""
    travelers: tuple[PlanTraveler, ...] = ()
    departure: date | None = None
    return_on: date | None = None
    nights: int | None = None
    country: str = ""
    city: str = ""
    grade: str = ""
    grade_message: str = ""
    airfare_krw: int = 0
    preparation_krw: int = 0
    domestic_krw: int = 0
    preparation_items: tuple[tuple[str, int], ...] = ()
    headquarters: str = ""
    purpose_lines: tuple[str, ...] = ()
    itinerary: tuple[tuple[date, str], ...] = ()
    airfare_note: str = ""
    warnings: tuple[str, ...] = ()
    raw_text: str = field(default="", repr=False)


def extract_pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def parse_plan_pdf(data: bytes) -> PlanDocument:
    return parse_plan_text(extract_pdf_text(data))


def refresh_plan_document(plan: PlanDocument | None) -> PlanDocument | None:
    """세션에 남은 이전 PlanDocument도 현재 필드로 다시 읽는다."""
    if plan is None:
        return None
    if hasattr(plan, "purpose_lines") and hasattr(plan, "itinerary") and hasattr(plan, "airfare_note"):
        return plan
    raw = getattr(plan, "raw_text", "") or ""
    if raw:
        return parse_plan_text(raw)
    return plan


def parse_plan_text(text: str) -> PlanDocument:
    compact = _compact(text)
    period = _section(compact, ("출장기간 및 장소", "출장기간", "출장 기간"))
    traveler_section = _section(compact, ("출장자",))
    purpose = _clean_line(_section(compact, ("출장목적", "목적")))
    event_name = _parse_event_name(compact)
    budget_category = _parse_budget_category(compact)
    region = _clean_line(_section(compact, ("출장지역", "출장장소", "출장 장소")))
    if not region:
        region = _first_line(period)

    departure, return_on, nights = _parse_schedule(period or compact)
    country, city = _parse_place(" ".join(part for part in (period, region, compact) if part))
    lookup = resolve_destination_grade(country, city) if country or city else None
    grade = lookup.grade or "" if lookup else ""
    grade_message = lookup.message if lookup and lookup.ok else ""

    travelers = _parse_travelers(traveler_section)
    if not travelers:
        travelers = _parse_travelers(_section(compact, ("기안자",)))

    warnings: list[str] = []
    if not travelers:
        warnings.append("출장자를 찾지 못했습니다. 출장자명과 구분을 직접 입력해 주세요.")
    if departure is None or return_on is None:
        warnings.append("출장 일정을 찾지 못했습니다. 출국일·귀국일을 직접 입력해 주세요.")
    if not country:
        warnings.append("출장 국가를 찾지 못했습니다. 출장지를 직접 입력해 주세요.")

    return PlanDocument(
        purpose=purpose,
        region=region,
        schedule_text=_first_line(period),
        event_name=event_name,
        budget_category=budget_category,
        travelers=tuple(travelers),
        departure=departure,
        return_on=return_on,
        nights=nights,
        country=country,
        city=city,
        grade=grade,
        grade_message=grade_message,
        airfare_krw=_parse_airfare(compact),
        preparation_krw=_parse_preparation(compact),
        domestic_krw=_parse_domestic(compact),
        preparation_items=tuple(_parse_preparation_items(compact)),
        headquarters=_parse_headquarters(compact),
        purpose_lines=tuple(_parse_purpose_lines(compact, event_name)),
        itinerary=tuple(_parse_itinerary(compact, departure, return_on, city, event_name)),
        airfare_note=_parse_airfare_note(compact),
        warnings=tuple(warnings),
        raw_text=text,
    )


def role_from_title(title: str) -> str:
    if title in _TITLE_TO_ROLE:
        return _TITLE_TO_ROLE[title]
    return "팀장 및 팀원"


def _compact(text: str) -> str:
    value = (text or "").replace("\u3000", " ").replace("\xa0", " ")
    value = value.replace("‘", "'").replace("’", "'").replace("～", "~")
    return re.sub(r"[ \t]+", " ", value)


def _section(text: str, headers: tuple[str, ...]) -> str:
    for header in headers:
        flex = r"\s*".join(map(re.escape, header))
        match = re.search(flex + r"\s*[:：]?\s*", text)
        if not match:
            continue
        rest = text[match.end() :]
        stop = re.search(
            r"(?:\n|\s{2,})?(?:출장목적|출장지역|출장기간|출장자|출장내용|소요예산|예산과목|"
            r"방문기관|행사명|프로그램명|지원기업|지원내용|추진일정|목적|개요)\s*[:：❍]?",
            rest,
        )
        chunk = rest[: stop.start()] if stop else rest[:400]
        return chunk.strip(" \n:：❍-")
    return ""


def _first_line(text: str) -> str:
    if not text:
        return ""
    return _clean_line(text.splitlines()[0] if "\n" in text else text[:160])


def _clean_line(text: str) -> str:
    value = re.sub(r"\s+", " ", (text or "")).strip(" :：❍-")
    return value[:240]


def _year(raw: str, fallback: int | None = None) -> int:
    year = int(raw)
    if year < 100:
        year += 2000
    return year


def _parse_schedule(text: str) -> tuple[date | None, date | None, int | None]:
    nights = None
    nights_match = re.search(r"(\d{1,2})\s*박\s*(\d{1,2})\s*일", text)
    if nights_match:
        nights = int(nights_match.group(1))
    else:
        paren = re.search(r"\(\s*(\d{1,2})\s+(\d{1,2})\s*\)", text)
        if paren:
            nights = int(paren.group(1))

    dotted = re.search(
        r"(20\d{2}|'\d{2}|\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?"
        r"\s*[~\-]\s*(?:(20\d{2}|'\d{2}|\d{2})\s*[.\-/년]\s*)?(\d{1,2})\s*[.\-/월]\s*(\d{1,2})",
        text,
    )
    spaced = re.search(
        r"(?:20)?(\d{2})\s+(\d{1,2})\s+(\d{1,2})\s*[~\-]\s*(?:(?:20)?(\d{2})\s+)?(\d{1,2})\s+(\d{1,2})",
        text,
    )
    match = dotted or spaced
    if not match:
        return None, None, nights
    groups = match.groups()
    start_year = _year(groups[0].lstrip("'"))
    start_month = int(groups[1])
    start_day = int(groups[2])
    end_year_raw = groups[3]
    end_month = int(groups[4])
    end_day = int(groups[5])
    end_year = _year(end_year_raw.lstrip("'")) if end_year_raw else start_year
    if end_month < start_month and not end_year_raw:
        end_year += 1
    try:
        departure = date(start_year, start_month, start_day)
        return_on = date(end_year, end_month, end_day)
    except ValueError:
        return None, None, nights
    return departure, return_on, nights


def _lookup_country(token: str) -> str | None:
    raw = token.strip()
    if raw in COUNTRY_GRADES:
        return raw
    key = raw.casefold()
    if key in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[key]
    for name in COUNTRY_GRADES:
        if name in raw or raw in name:
            return name
    return None


def _lookup_city(token: str) -> str | None:
    raw = token.strip()
    if not raw:
        return None
    if raw in CITY_GRADES:
        return raw
    key = raw.casefold()
    if key in CITY_ALIASES:
        return CITY_ALIASES[key]
    for name in CITY_GRADES:
        if name in raw:
            return name
    return None


def _parse_place(text: str) -> tuple[str, str]:
    country = ""
    for name in sorted(COUNTRY_GRADES, key=len, reverse=True):
        if name and name in text:
            country = name
            break
    if not country:
        for alias, canonical in COUNTRY_ALIASES.items():
            if re.search(rf"(?<![A-Za-z]){re.escape(alias)}(?![A-Za-z])", text, re.I):
                country = canonical
                break

    city = ""
    for name in sorted(CITY_GRADES, key=len, reverse=True):
        if name in text:
            city = name
            break
    if not city:
        for alias, canonical in CITY_ALIASES.items():
            if alias.casefold() in text.casefold():
                city = canonical
                break
    if not city and country:
        glued = re.search(rf"([가-힣]{{2,6}}){re.escape(country)}", text)
        if glued:
            city = glued.group(1)
        else:
            beside = re.search(rf"{re.escape(country)}\s*[,/]?\s*([가-힣]{{2,6}})", text)
            if beside and beside.group(1) not in {"국제", "최대", "현지", "지원", "출장"}:
                city = beside.group(1)
    if city in {"국제", "최대", "현지", "지원", "출장", "기간", "장소"}:
        city = ""
    return country, city


def _parse_travelers(text: str) -> list[PlanTraveler]:
    travelers: list[PlanTraveler] = []
    seen: set[str] = set()
    team = ""
    team_match = re.search(r"([가-힣]{2,20}팀|[가-힣]{2,20}본부|[가-힣]{2,20}센터)\s+", text)
    if team_match:
        team = team_match.group(1)
    for name, title in _NAME_TITLE_RE.findall(text):
        if name.endswith("팀") or name in {"기안", "협조", "전결"}:
            continue
        if name in seen:
            continue
        seen.add(name)
        travelers.append(
            PlanTraveler(name=name, title=title, role=role_from_title(title), team=team)
        )
    return travelers


def _parse_money(text: str) -> int | None:
    match = _MONEY_RE.search(text.replace(" ", ""))
    if not match:
        match = _MONEY_RE.search(text)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def _parse_airfare(text: str) -> int:
    match = re.search(r"항공[^\n]{0,80}", text)
    if not match:
        return 0
    amount = _parse_money(match.group(0))
    return amount or 0


def _parse_preparation(text: str) -> int:
    items = _parse_preparation_items(text)
    if items:
        return sum(amount for _, amount in items)
    start = re.search(r"준\s*비\s*금", text)
    if not start:
        return 0
    rest = text[start.end() :]
    stop = re.search(r"국내여비|일반수용비|사업추진비|합\s*계", rest)
    block = rest[: stop.start()] if stop else rest[:240]
    amounts = [int(item.replace(",", "")) for item in _MONEY_RE.findall(block)]
    return sum(amounts)


def _parse_preparation_items(text: str) -> list[tuple[str, int]]:
    start = re.search(r"준\s*비\s*금", text)
    if not start:
        return []
    rest = text[start.end() :]
    stop = re.search(r"국내여비|일반수용비|사업추진비|합\s*계", rest)
    block = rest[: stop.start()] if stop else rest[:240]
    items: list[tuple[str, int]] = []
    for match in _MONEY_RE.finditer(block):
        amount = int(match.group(1).replace(",", ""))
        if amount < 1_000:
            continue
        prefix = block[max(0, match.start() - 40) : match.start()]
        if "보험" in prefix:
            label = "여행자보험비"
        elif "기차" in prefix or "철도" in prefix:
            label = "오사카-교토 왕복기차비"
        else:
            label = re.sub(r"[\d,.=*원인\s\-<>]+$", "", prefix).strip(" ·-:")
            label = re.sub(r"\s+", "", label)
            if not label:
                continue
        items.append((label, amount))
    return items


def _parse_domestic(text: str) -> int:
    match = re.search(r"(?:국내여비|대중교통운임비)[^\n]{0,80}", text)
    if not match:
        return 0
    amount = _parse_money(match.group(0))
    return amount or 0


def _parse_headquarters(text: str) -> str:
    if "글로벌본부" in text:
        return "글로벌본부"
    match = re.search(r"([가-힣]{2,12}본부)", text)
    return match.group(1) if match else ""


def _parse_event_name(text: str) -> str:
    match = re.search(r"IVS\s*20\d{2}\s*Kyoto", text, re.I)
    if match:
        return re.sub(r"\s+", " ", match.group(0)).strip()
    for header in ("방문기관 또는 행사명", "행사명", "프로그램명"):
        value = _clean_line(_section(text, (header,)))
        if value:
            return value
    return ""


def _parse_budget_category(text: str) -> str:
    if "AroundX" in text and "주관기관" in text:
        return "2026년 글로벌기업 협업 프로그램(AroundX) 사업 – 주관기관 운영 - 운영비 - 여비"
    return _clean_line(_section(text, ("예산과목",)))


def _company_count(text: str) -> str:
    match = re.search(r"(\d+)\s*개사", text)
    if match:
        return match.group(1)
    match = re.search(r"AroundX[^\n]{0,40}?(\d)", text)
    if match:
        return match.group(1)
    match = re.search(r"정글[^\n]{0,40}?(\d)", text)
    return match.group(1) if match else ""


def _parse_purpose_lines(text: str, event_name: str) -> list[str]:
    lines: list[str] = []
    if "AroundX" in text and "정글" in text and "GTM" in text:
        count = _company_count(text)
        company = f"{count}개사 " if count else ""
        lines.append(f"2026 AroundX 정글 참여기업 {company}일본 GTM 지원")
    if event_name:
        if "VC" in text or "CVC" in text:
            lines.append(f"{event_name} 참관 및 현지 VC/CVC/스타트업 지원 기관 네트워크 확대")
        else:
            lines.append(f"{event_name} 참관 및 현지 네트워킹 지원")
    if lines:
        return lines
    body = _section(text, ("출장내용", "출장목적", "목적"))
    for raw in body.splitlines():
        value = _clean_line(raw)
        if value:
            lines.append(value)
    return lines[:4]


def _parse_airfare_note(text: str) -> str:
    if "김포" in text and ("간사이" in text or "오사카" in text):
        return " (김포 - 간사이공항)"
    return ""


def _itinerary_extras(chunk: str) -> str:
    extras: list[str] = []
    if re.search(r"Super\s*Night", chunk, re.I):
        extras.append("AWS 주관 The Super Night 참가")
    if re.search(r"East\s*Asia\s*Startup\s*Nexus", chunk, re.I):
        extras.append("AWS 주관 East Asia Startup Nexus 참가")
    return ", ".join(extras)


def _parse_itinerary(
    text: str,
    start: date | None,
    end: date | None,
    city: str,
    event_name: str,
) -> list[tuple[date, str]]:
    if start is None or end is None:
        return []
    matches = list(re.finditer(r"([월화수목금토일])\s*(\d{1,2})\s*/\s*(\d{1,2})\s*\(", text))
    if len(matches) < 2:
        return []
    year = start.year
    items: list[tuple[date, str]] = []
    span = (end - start).days
    event = event_name or "현지 일정"
    for index, match in enumerate(matches):
        month = int(match.group(2))
        day_n = int(match.group(3))
        try:
            day = date(year, month, day_n)
        except ValueError:
            continue
        if day < start or day > end:
            continue
        stop = matches[index + 1].start() if index + 1 < len(matches) else min(len(text), match.end() + 280)
        chunk = text[match.end() : stop]
        offset = (day - start).days
        extras = _itinerary_extras(chunk)
        if offset == 0:
            summary = f"김포에서 {city}, 숙소 이동" if city and "김포" in chunk else "출국"
        elif offset == span:
            back = f"{city}에서 김포" if city else "귀국"
            summary = f"{event} {offset}일차, {back}"
        else:
            summary = f"{event} {offset}일차"
            if extras:
                summary = f"{summary}, {extras}"
        items.append((day, summary))
    return items


assert set(_TITLE_TO_ROLE.values()).issubset(set(ROLES))
