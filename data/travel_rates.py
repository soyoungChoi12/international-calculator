"""국외여비 지급 기준 (USD).

지침 개정 시 이 파일만 수정한다.
키는 화면 표시명(센터장 / 본부장 / 팀장 및 팀원)을 사용한다.
Excel B2에 넣을 값은 ROLE_EXCEL_LABEL 을 따른다.
"""

from __future__ import annotations

ROLES = ("센터장", "본부장", "팀장 및 팀원")
GRADES = ("가", "나", "다", "라")

ROLE_EXCEL_LABEL = {
    "센터장": "센터장",
    "본부장": "본부장",
    "팀장 및 팀원": "그외직원(팀장및팀원)",
}

PAYMENT_CORPORATE = "법인카드 결제"
PAYMENT_PERSONAL = "개인지급(계좌이체)"
PAYMENT_METHODS = (PAYMENT_CORPORATE, PAYMENT_PERSONAL)

# 일비는 지역등급과 무관하게 동일
# 차량 임차 사용일은 기준액의 1/2
DAILY_RENTAL_DIVISOR = 2
DAILY_RENTAL_LABEL = "차량임차 1/2"

DAILY_ALLOWANCE_USD = {
    "센터장": 40,
    "본부장": 30,
    "팀장 및 팀원": 26,
}

LODGING_USD = {
    "센터장": {"가": 282, "나": 207, "다": 162, "라": 108},
    "본부장": {"가": 176, "나": 137, "다": 106, "라": 81},
    "팀장 및 팀원": {"가": 155, "나": 123, "다": 90, "라": 77},
}

MEAL_USD = {
    "센터장": {"가": 133, "나": 99, "다": 72, "라": 61},
    "본부장": {"가": 81, "나": 59, "다": 44, "라": 37},
    "팀장 및 팀원": {"가": 67, "나": 49, "다": 37, "라": 30},
}


def get_daily_rate_usd(role: str) -> int:
    return DAILY_ALLOWANCE_USD[role]


def get_daily_rental_rate_usd(role: str) -> int:
    """차량 임차 사용일의 일비. 기준액의 1/2."""
    return DAILY_ALLOWANCE_USD[role] // DAILY_RENTAL_DIVISOR


def get_lodging_rate_usd(role: str, grade: str) -> int:
    return LODGING_USD[role][grade]


def get_meal_rate_usd(role: str, grade: str) -> int:
    return MEAL_USD[role][grade]
