"""출장 국가·도시로 지역등급(가/나/다/라)을 판정한다.

도시 지정등급을 먼저 보고, 없으면 국가등급을 적용한다.
지침에 없는 국가는 임의로 지정하지 않고 실패를 반환한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from data.destination_grades import (
    CITY_ALIASES,
    CITY_GRADES,
    COUNTRY_ALIASES,
    COUNTRY_GRADES,
)


@dataclass(frozen=True)
class GradeLookupResult:
    grade: str | None
    source: str | None
    matched_name: str | None
    country_canonical: str | None
    city_canonical: str | None
    message: str

    @property
    def ok(self) -> bool:
        return self.grade is not None


def _normalize(text: str) -> str:
    value = (text or "").strip()
    value = value.replace("·", "").replace(".", " ").replace(",", " ")
    value = re.sub(r"\s+", " ", value)
    return value.casefold()


def _canonical(name: str, aliases: dict[str, str], official: dict[str, str]) -> str | None:
    if not name:
        return None
    raw = name.strip()
    if raw in official:
        return raw
    key = _normalize(raw)
    if key in aliases:
        return aliases[key]
    for official_name in official:
        if _normalize(official_name) == key:
            return official_name
    for alias, canonical in aliases.items():
        if _normalize(alias) == key:
            return canonical
    return None


def resolve_destination_grade(country: str, city: str = "") -> GradeLookupResult:
    city_canonical = _canonical(city, CITY_ALIASES, CITY_GRADES)
    country_canonical = _canonical(country, COUNTRY_ALIASES, COUNTRY_GRADES)

    if city_canonical:
        grade = CITY_GRADES[city_canonical]
        return GradeLookupResult(
            grade=grade,
            source="city",
            matched_name=city_canonical,
            country_canonical=country_canonical,
            city_canonical=city_canonical,
            message=f"도시 지정등급 적용: {city_canonical} → {grade}",
        )

    if country_canonical:
        grade = COUNTRY_GRADES[country_canonical]
        city_note = f", 도시 '{city.strip()}' 지정등급 없음" if city.strip() else ""
        return GradeLookupResult(
            grade=grade,
            source="country",
            matched_name=country_canonical,
            country_canonical=country_canonical,
            city_canonical=None,
            message=f"국가등급 적용: {country_canonical} → {grade}{city_note}",
        )

    missing = []
    if country.strip():
        missing.append(f"국가 '{country.strip()}'")
    if city.strip():
        missing.append(f"도시 '{city.strip()}'")
    target = " / ".join(missing) if missing else "출장지"
    return GradeLookupResult(
        grade=None,
        source=None,
        matched_name=None,
        country_canonical=None,
        city_canonical=None,
        message=(
            f"{target}를 지침의 국가·도시 등급표에서 찾을 수 없습니다. "
            "지역등급(가/나/다/라)을 직접 선택해 주세요."
        ),
    )


def list_countries() -> list[str]:
    return sorted(COUNTRY_GRADES.keys())


def list_designated_cities() -> list[str]:
    return sorted(CITY_GRADES.keys())
