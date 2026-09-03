"""하나은행 고시환율에서 미국달러 현찰 살 때 조회."""

from __future__ import annotations

import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser

HANA_FX_PAGE_URL = (
    "https://biz.kebhana.com/foex/rate/index.do?menuItemId=wcfxd740_101i#//HanaBank"
)
HANA_FX_LOOKUP_URL = "https://www.kebhana.com/cms/rate/wpfxd651_01i_01.do"
HANA_FX_REFERER = "https://www.kebhana.com/cms/rate/index.do?contentUrl=/cms/rate/wpfxd651_01i.do"
_TIMEOUT_SEC = 8


@dataclass(frozen=True)
class HanaFxQuote:
    rate: float
    posted_on: date
    round_no: int | None = None
    posted_at: str | None = None


class _RateTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_td = False
        self._cell: list[str] = []
        self._row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag == "td":
            self._in_td = True
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._in_td:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._in_td = False
        elif tag == "tr" and self._row:
            self.rows.append(self._row)

    def handle_data(self, data: str) -> None:
        if self._in_td:
            self._cell.append(data)


def _parse_number(text: str) -> float | None:
    cleaned = text.replace(",", "").replace(" ", "")
    if re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        return float(cleaned)
    return None


def parse_usd_cash_buy(html: str) -> HanaFxQuote | None:
    """HTML 표에서 미국 USD의 현찰 살 때(첫 번째 환율 칸)를 읽는다."""
    parser = _RateTableParser()
    parser.feed(html)
    cash_buy = None
    for row in parser.rows:
        if not row or not re.search(r"\bUSD\b", row[0]):
            continue
        numbers = [_parse_number(cell) for cell in row[1:]]
        numbers = [value for value in numbers if value is not None]
        if numbers:
            cash_buy = numbers[0]
            break
    if cash_buy is None:
        return None

    posted_on = date.today()
    posted_match = re.search(r'regYmdt\s*=\s*"(\d{8})"', html)
    if posted_match:
        raw = posted_match.group(1)
        posted_on = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))

    round_no = None
    round_match = re.search(r"\((\d+)\s*회차\)", html)
    if round_match:
        round_no = int(round_match.group(1))

    posted_at = None
    time_match = re.search(r"(\d{1,2})\s*시\s*(\d{1,2})\s*분", html)
    if time_match:
        posted_at = f"{int(time_match.group(1)):02d}:{int(time_match.group(2)):02d}"

    return HanaFxQuote(rate=cash_buy, posted_on=posted_on, round_no=round_no, posted_at=posted_at)


def _should_skip_network() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) and not os.environ.get("HANA_FX_LIVE")


def fetch_usd_cash_buy(when: date) -> HanaFxQuote | None:
    """결재일 기준 하나은행 미국달러 현찰 살 때. 휴일이면 직전 영업일 고시."""
    if _should_skip_network():
        return None
    payload = urllib.parse.urlencode(
        {
            "ajax": "true",
            "curCd": "USD",
            "tmpInqStrDt": when.strftime("%Y-%m-%d"),
            "pbldDvCd": "0",
            "pbldSqn": "",
            "hid_key_data": "",
            "inqStrDt": when.strftime("%Y%m%d"),
            "inqKindCd": "1",
            "hid_enc_data": "",
            "requestTarget": "searchContentDiv",
        }
    ).encode()
    request = urllib.request.Request(
        HANA_FX_LOOKUP_URL,
        data=payload,
        method="POST",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": HANA_FX_REFERER,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SEC) as response:
            html = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    return parse_usd_cash_buy(html)


def quote_caption(quote: HanaFxQuote, requested_on: date) -> str:
    parts = [f"하나은행 미국달러 현찰 살 때 {quote.rate:,.2f}원"]
    stamp = quote.posted_on.isoformat()
    if quote.posted_at:
        stamp = f"{stamp} {quote.posted_at}"
    if quote.round_no:
        stamp = f"{stamp} · {quote.round_no}회차"
    parts.append(stamp)
    if quote.posted_on != requested_on:
        parts.append(f"결재일이 비영업일이라 {quote.posted_on.isoformat()} 고시를 사용")
    return " · ".join(parts)
