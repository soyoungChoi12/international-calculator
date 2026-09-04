"""하나은행 미국달러 현찰 살 때 파싱."""

from __future__ import annotations

from datetime import date

from services.hana_fx import parse_usd_cash_buy, quote_caption

SAMPLE_HTML = """
<script>
	var regYmdt = "20260903";
</script>
<p class="txtRateBox">
	<em>고시일시</em> : <strong>2026년09월03일</strong>
	<strong>11시13분00초 </strong><strong>(208회차)</strong>
</p>
<table>
	<tbody>
		<tr>
			<td class="tc"><a>미국 USD</a></td>
			<td class="txtAr">1,383.08</td>
			<td class="txtAr">1.75</td>
			<td class="txtAr">1,335.52</td>
			<td class="txtAr">1.75</td>
			<td class="txtAr">1,372.60</td>
			<td class="txtAr">1,346.00</td>
			<td class="txtAr">1,343.89</td>
			<td class="txtAr">1,359.30</td>
		</tr>
		<tr>
			<td class="tc"><a>일본 JPY (100)</a></td>
			<td class="txtAr">873.02</td>
		</tr>
	</tbody>
</table>
"""


def test_parse_usd_cash_buy_uses_first_rate_column():
    quote = parse_usd_cash_buy(SAMPLE_HTML)
    assert quote is not None
    assert quote.rate == 1383.08
    assert quote.posted_on == date(2026, 9, 3)
    assert quote.round_no == 208
    assert quote.posted_at == "11:13"


def test_parse_usd_cash_buy_returns_none_without_usd():
    assert parse_usd_cash_buy("<table><tr><td>EUR</td><td>1,500.00</td></tr></table>") is None


def test_quote_caption_notes_previous_business_day():
    quote = parse_usd_cash_buy(SAMPLE_HTML)
    text = quote_caption(quote, date(2026, 9, 5))
    assert "1,383.08원" in text
    assert "208회차" in text
    assert "2026-09-03 고시를 사용" in text


def test_fetch_is_skipped_during_pytest():
    from services.hana_fx import fetch_usd_cash_buy

    assert fetch_usd_cash_buy(date(2026, 9, 3)) is None


def test_peek_cached_usd_cash_buy_does_not_hit_network(monkeypatch):
    from services import hana_fx

    quote = hana_fx.parse_usd_cash_buy(SAMPLE_HTML)
    original = dict(hana_fx._QUOTE_CACHE)
    hana_fx._QUOTE_CACHE.clear()
    hana_fx._QUOTE_CACHE[date(2026, 9, 3).isoformat()] = quote
    monkeypatch.setattr(hana_fx, "_post_rate_html", lambda when: (_ for _ in ()).throw(AssertionError("network")))
    try:
        assert hana_fx.peek_cached_usd_cash_buy(date(2026, 9, 3)) == quote
        assert hana_fx.fetch_usd_cash_buy(date(2026, 9, 3)) == quote
    finally:
        hana_fx._QUOTE_CACHE.clear()
        hana_fx._QUOTE_CACHE.update(original)


SAMPLE_NAVER_HTML = """
<table>
<thead>
<tr><th>날짜</th><th>매매기준율</th><th>전일대비</th><th>사실 때</th><th>파실 때</th></tr>
</thead>
<tbody>
<tr>
<td class="date">2026.09.04</td>
<td class="num">1,358.00</td>
<td class="num">0.50</td>
<td>1,381.76</td>
<td>1,334.24</td>
</tr>
<tr>
<td class="date">2026.09.03</td>
<td class="num">1,358.50</td>
<td class="num">1.80</td>
<td>1,382.27</td>
<td>1,334.73</td>
</tr>
</tbody>
</table>
"""


def test_parse_naver_usd_cash_buy_uses_cash_buy_column():
    from services.hana_fx import parse_naver_usd_cash_buy_rows

    rows = parse_naver_usd_cash_buy_rows(SAMPLE_NAVER_HTML)
    assert rows[0].rate == 1381.76
    assert rows[0].posted_on == date(2026, 9, 4)
    assert rows[0].source == "naver"


def test_fetch_falls_back_to_naver_when_hana_fails(monkeypatch):
    from services import hana_fx
    from services.hana_fx import HanaFxQuote

    original = dict(hana_fx._QUOTE_CACHE)
    hana_fx._QUOTE_CACHE.clear()
    fallback = HanaFxQuote(rate=1381.76, posted_on=date(2026, 9, 4), source="naver")
    monkeypatch.setattr(hana_fx, "_should_skip_network", lambda: False)
    monkeypatch.setattr(hana_fx, "_running_on_streamlit_cloud", lambda: True)
    monkeypatch.setattr(hana_fx, "_fetch_hana_usd_cash_buy", lambda when: (_ for _ in ()).throw(AssertionError("hana")))
    monkeypatch.setattr(hana_fx, "_fetch_naver_usd_cash_buy", lambda when: fallback)
    try:
        assert hana_fx.fetch_usd_cash_buy(date(2026, 9, 4)) == fallback
    finally:
        hana_fx._QUOTE_CACHE.clear()
        hana_fx._QUOTE_CACHE.update(original)
