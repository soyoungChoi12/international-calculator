"""국외여비지급내역서 셀 매핑.

셀 주소는 (기본)국외여비템플릿.xlsx 분석 결과를 따른다.
Excel 생성 코드 곳곳에 주소를 하드코딩하지 말고 이 파일만 수정한다.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATE_FILENAME = "국외여비지급내역서.xlsx"
TEMPLATE_SHEET_NAME = "국외여비지급내역서(이한주 전임)"
OUTPUT_SHEET_NAME = "국외여비지급내역서"
LOOKUP_SHEET_NAME = "드롭다운(국외)"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = PROJECT_ROOT / "templates" / TEMPLATE_FILENAME

EXCEL_CELL_MAP = {
    "traveler_grade": "B2",
    "fx_source_link": "K3",
    "fx_guide": "M3",
    "fx_rate": "L4",
    "fx_date_label": "M4",
    "airfare_amount": "C6",
    "airfare_payment_method": "D6",
    "airfare_note": "E6",
    "daily_amount": "C7",
    "daily_payment_method": "D7",
    "daily_note": "E7",
    "meal_amount": "C8",
    "meal_payment_method": "D8",
    "meal_note": "E8",
    "lodging_amount": "C9",
    "lodging_payment_method": "D9",
    "lodging_note": "E9",
    "preparation_amount": "C10",
    "preparation_payment_method": "D10",
    "preparation_note": "E10",
    "total_amount": "C11",
    "budget_item": "C12",
    "corporate_card_total": "C18",
    "personal_transfer_total": "C19",
    "bank_account_note": "D19",
    "payee_grand_total": "C20",
}

# 오른쪽 여비 계산 영역. 출장지 등급에 해당하는 블록만 채운다.
# J열(days): 일비·식비는 도시별 체류일 합, 숙박비는 도시별 숙박일수 합.
# 일비 단가가 같아도 등급 칸은 나눠 적는다.
# 차량 임차일은 일비 기준액 1/2. 금액은 할인을 반영하고, 비고(E7)에 임차 일수를 적는다.
GRADE_CALC_ROWS = {
    "가": {"daily": 7, "lodging": 8, "meal": 9},
    "나": {"daily": 11, "lodging": 12, "meal": 13},
    "다": {"daily": 14, "lodging": 15, "meal": 16},
    "라": {"daily": 17, "lodging": 18, "meal": 19},
}

GRADE_CALC_COLUMNS = {
    "rate_usd": "I",
    "days": "J",
    "amount_usd": "K",
    "amount_krw": "L",
    "amount_krw_rounded": "M",
}

# 나·다·라 등급 USD 단가 열. 템플릿 회색·볼드를 출력 파일에서 해제한다.
RATE_PLAIN_ROWS = range(11, 20)

# 숙박비 상한 초과 확인 영역
LODGING_CHECK_ROWS = {
    "가": 26,
    "나": 27,
    "다": 28,
    "라": 29,
}

LODGING_CHECK_COLUMNS = {
    "ceiling_krw": "H",
    "actual_krw": "I",
    "difference": "J",
    "over_flag": "K",
    "final_krw": "L",
}

BLANK_CELLS = (
    "E6",
    "E7",
    "E8",
    "E10",
    "C12",
    "D19",
)

FX_SOURCE_TEXT = "하나은행 환율정보"
FX_SOURCE_URL = "https://www.kebhana.com/cont/mall/mall15/mall1501/index.jsp"
FX_GUIDE_TEXT = "※ 기준액을 원화로 환산시 출장신청서 결재일 하나은행 환율정보 미국달러 현찰 살 때를 기준으로 함"
FX_RATE_KIND = "현찰 살 때"
FX_SOURCE_CAPTION = "하나은행 환율정보(미국달러 현찰 살 때)"
UNENTERED_PAYMENT_MARK = "-"
TOTAL_FORMULA = "SUM(C6:C10)"


def grade_calc_cell(grade: str, kind: str, column_key: str) -> str:
    """오른쪽 등급 블록 셀 주소. kind=daily|lodging|meal."""
    return f"{GRADE_CALC_COLUMNS[column_key]}{GRADE_CALC_ROWS[grade][kind]}"


def lodging_check_cell(grade: str, column_key: str) -> str:
    return f"{LODGING_CHECK_COLUMNS[column_key]}{LODGING_CHECK_ROWS[grade]}"
