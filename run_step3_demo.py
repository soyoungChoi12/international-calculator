"""STEP 3 국가·도시 등급 판정 확인용."""

from services.destination_grade_service import resolve_destination_grade


EXAMPLES = [
    ("미국", "샌프란시스코"),
    ("미국", "시애틀"),
    ("프랑스", "파리"),
    ("프랑스", "리옹"),
    ("일본", "도쿄"),
    ("일본", "오사카"),
    ("중국", "베이징"),
    ("중국", "상하이"),
    ("베트남", ""),
    ("싱가포르", ""),
    ("아틀란티스", "가상도시"),
]


def main() -> None:
    print("도시 지정등급 우선 / 없으면 국가등급 / 없으면 수동선택")
    print("=" * 64)
    for country, city in EXAMPLES:
        result = resolve_destination_grade(country, city)
        dest = f"{country} {city}".strip()
        grade = result.grade or "(판정 실패)"
        print(f"{dest:20} → {grade:6}  {result.message}")


if __name__ == "__main__":
    main()
