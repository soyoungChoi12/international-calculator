"""국외출장 심사신청서 HWP 생성.

원본 템플릿은 복사만 하고 수정하지 않는다.
"""

from __future__ import annotations

import re
import struct
import zlib
from datetime import date
from pathlib import Path

from services.cfb_writer import rebuild_ole
from services.plan_parser import PlanDocument
from services.travel_calculator import TravelResult, truncate_to_ten

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "국외출장 심사신청서.hwp"
HWPTAG_PARA_HEADER = 66
HWPTAG_PARA_TEXT = 67
HWPTAG_PARA_CHAR_SHAPE = 68
HWPTAG_PARA_LINE_SEG = 69
HWPTAG_CTRL_HEADER = 71
HWPTAG_LIST_HEADER = 72
_DOW = "월화수목금토일"


def hwp_filename(traveler_name: str, title: str, when: date) -> str:
    safe_name = re.sub(r'[\\/:*?"<>|]', "", (traveler_name or "").strip()) or "미기재"
    safe_title = re.sub(r'[\\/:*?"<>|]', "", (title or "").strip())
    extra = f" {safe_title}" if safe_title else ""
    return f"({when.strftime('%y%m%d')}) 국외출장 심사신청서 ({safe_name}{extra}).hwp"


def build_hwp_bytes(
    result: TravelResult,
    traveler_name: str,
    *,
    title: str = "",
    team: str = "",
    plan: PlanDocument | None = None,
    departure: date | None = None,
    return_on: date | None = None,
    approval_date: date | None = None,
) -> bytes:
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"HWP 템플릿을 찾을 수 없습니다: {TEMPLATE_PATH}")
    fields = build_hwp_fields(
        result,
        traveler_name,
        title=title,
        team=team,
        plan=plan,
        departure=departure,
        return_on=return_on,
    )
    return rebuild_ole(TEMPLATE_PATH, _fill_template(TEMPLATE_PATH, fields))


def build_hwp_fields(
    result: TravelResult,
    traveler_name: str,
    *,
    title: str = "",
    team: str = "",
    plan: PlanDocument | None = None,
    departure: date | None = None,
    return_on: date | None = None,
) -> dict[str, str]:
    stay = result.stays[0] if result.stays else None
    country = stay.country if stay else (plan.country if plan else "")
    city = stay.city if stay else (plan.city if plan else "")
    grade = (stay.grade if stay else "") or (plan.grade if plan else "") or (result.grade.split("/")[0] if result.grade else "")
    place = " ".join(part for part in (country, city) if part)
    nights = result.lodging_nights
    days = result.trip_days
    start = departure or (plan.departure if plan else None)
    end = return_on or (plan.return_on if plan else None)
    return {
        "purpose": _purpose_text(plan),
        "region": f"◦ {place}(출장지역 ‘{grade}’ 지역)" if place else "◦",
        "schedule": _schedule_text(start, end, nights, days, plan),
        "event": _event_text(plan),
        "traveler": _traveler_text(traveler_name, title, team, plan),
        "budget_who": _budget_who_text(traveler_name, title),
        "budget": _budget_text(result, plan, grade, nights, days),
        "category": _category_text(plan),
        "category_who": "출장자 1인",
    }


def _plan_get(plan: PlanDocument | None, name: str, default=""):
    if plan is None:
        return default
    return getattr(plan, name, default)


def _purpose_text(plan: PlanDocument | None) -> str:
    if not plan:
        return "◦"
    lines = [line.strip() for line in _plan_get(plan, "purpose_lines", ()) if str(line).strip()]
    if not lines:
        purpose = (plan.purpose or "").strip()
        if purpose:
            lines.append(purpose)
        event = (plan.event_name or "").strip()
        if event and event not in purpose:
            lines.append(f"{event} 참관 및 현지 네트워킹 지원")
    return "\n".join(f"◦ {line.lstrip('◦').strip()}" for line in lines) if lines else "◦"


def _event_text(plan: PlanDocument | None) -> str:
    if not plan or not plan.event_name:
        return "◦"
    return f"◦ {plan.event_name.strip()}"


def _traveler_text(name: str, title: str, team: str, plan: PlanDocument | None) -> str:
    person = plan.travelers[0] if plan and plan.travelers else None
    use_name = name or (person.name if person else "")
    use_title = title or (person.title if person else "")
    use_team = team or (person.team if person else "")
    hq = (_plan_get(plan, "headquarters") if plan else "") or ""
    parts = [part for part in (hq, use_team, use_name, use_title) if part]
    return "◦ " + " ".join(parts) if parts else "◦"


def _dow(value: date) -> str:
    return _DOW[value.weekday()]


def _fmt_date(value: date, with_year: bool = True) -> str:
    dow = _dow(value)
    if with_year:
        return f"{value.year}.{value.month}.{value.day}.({dow})"
    return f"{value.year % 100}.{value.month}.{value.day}({dow})"


def _schedule_text(
    start: date | None,
    end: date | None,
    nights: int,
    days: int,
    plan: PlanDocument | None,
) -> str:
    if start is None or end is None:
        return "◦"
    lines = [f"◦ {_fmt_date(start)}.~{end.year}.{end.month}.{end.day}({_dow(end)}), {nights}박 {days}일 일정"]
    by_day = {item[0]: item[1] for item in _plan_get(plan, "itinerary", ())}
    event = (plan.event_name.strip() if plan and plan.event_name else "") or "현지 일정"
    city = (plan.city.strip() if plan and plan.city else "")
    from_gimpo = bool(plan and "김포" in (_plan_get(plan, "airfare_note") or ""))
    span = (end - start).days
    for offset in range(span + 1):
        day = date.fromordinal(start.toordinal() + offset)
        summary = by_day.get(day) or _default_day_summary(offset, span, event, city, from_gimpo)
        lines.append(f" - {_fmt_date(day, with_year=False)} : {summary}")
    lines.append("※추후 일정이 변경 또는 추가 될 수도 있음")
    return "\n".join(lines)


def _default_day_summary(offset: int, span: int, event: str, city: str, from_gimpo: bool) -> str:
    if offset == 0:
        return f"김포에서 {city}, 숙소 이동" if from_gimpo and city else "출국"
    if offset == span:
        extra = f"{city}에서 김포" if from_gimpo and city else "귀국"
        return f"{event} {offset}일차, {extra}" if event else extra
    return f"{event} {offset}일차"


def _won(amount: int) -> str:
    return f"{int(amount):,}원"


def _budget_text(
    result: TravelResult,
    plan: PlanDocument | None,
    grade: str,
    nights: int,
    days: int,
) -> str:
    airfare = truncate_to_ten(result.airfare_krw)
    domestic = truncate_to_ten(int(_plan_get(plan, "domestic_krw", 0) or 0) if plan else 0)
    transport = airfare + domestic
    lodging_krw = truncate_to_ten(result.lodging.ceiling_krw)
    daily_krw = truncate_to_ten(result.daily.amount_krw)
    meal_krw = truncate_to_ten(result.meal.amount_krw)
    allow = lodging_krw + daily_krw + meal_krw
    prep = truncate_to_ten(result.preparation_krw)
    total = transport + allow + prep
    rate = result.lodging.rate_usd
    lodging_usd = rate * nights if rate and nights else 0
    grade_label = f"{grade} 지역" if grade else ""
    head = f"◦ 총 {_won(total)}"
    if grade_label and nights and days:
        head += f"({grade_label}, {nights}박 {days}일)"
    lines = [
        head,
        f"  - 교통비 : {_won(transport)}",
        f"   ·왕복항공료 : {_won(airfare)}{_plan_get(plan, 'airfare_note') if plan else ''}",
    ]
    if domestic:
        lines.append(f"   ·대중교통운임비(공항) : {_won(domestic)}")
    lines.append(f"  - 출장비 : {_won(allow)}")
    usd_note = f"(${rate}x{nights}=${lodging_usd})" if rate and nights else ""
    lines.append(f"   ·숙박비 : {_won(lodging_krw)}{usd_note}")
    lines.append("    ※실비 정산 예정")
    daily_rate = result.daily.rate_usd
    meal_rate = result.meal.rate_usd
    if daily_rate and days:
        lines.append(f"   ·일  비 : {_won(daily_krw)}(${daily_rate}x{days}=${daily_rate * days})")
    else:
        lines.append(f"   ·일  비 : {_won(daily_krw)}")
    if meal_rate and days:
        lines.append(f"   ·식  비 : {_won(meal_krw)}(${meal_rate}x{days}=${meal_rate * days})")
    else:
        lines.append(f"   ·식  비 : {_won(meal_krw)}")
    lines.append(f"  - 준비금 : {_won(prep)}")
    items = _plan_get(plan, "preparation_items", ()) if plan else ()
    if items:
        for label, amount in items:
            lines.append(f"    ·{label} : {_won(truncate_to_ten(amount))}")
    return "\n".join(lines)


def _budget_who_text(name: str, title: str) -> str:
    name = (name or "").strip()
    title = (title or "").strip()
    if name and title:
        return f"{name}\n{title}"
    return _wrap_text(name or title, 4)


def _category_text(plan: PlanDocument | None) -> str:
    if not plan or not plan.budget_category:
        return "◦"
    return _wrap_text("◦" + plan.budget_category.strip().lstrip("◦").strip(), 40)


def _wrap_text(text: str, width: int) -> str:
    parts = []
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        parts.extend(_wrap_one(raw, width))
    return "\n".join(parts) if parts else (text or "")


def _wrap_one(text: str, width: int) -> list[str]:
    if width < 1 or len(text) <= width:
        return [text] if text else [""]
    out: list[str] = []
    rest = text
    seps = (" – ", " - ", "–", "-", " ")
    min_keep = max(1, width // 3)
    while len(rest) > width:
        window = rest[: width + 1]
        cut = None
        for sep in seps:
            pos = window.rfind(sep)
            if pos >= min_keep:
                cut = pos
                break
        if cut is None:
            cut = width
            out.append(rest[:cut])
            rest = rest[cut:]
        else:
            out.append(rest[:cut].rstrip())
            rest = rest[cut:].lstrip(" –-")
    if rest:
        out.append(rest)
    return out


def _fill_template(path: Path, fields: dict[str, str]) -> dict[str, bytes]:
    import olefile

    ole = olefile.OleFileIO(str(path))
    header = ole.openstream("FileHeader").read()
    raw = ole.openstream("BodyText/Section0").read()
    ole.close()
    compressed = bool(header[36] & 1)
    body = _decompress(raw) if compressed else raw
    body = _replace_content(body, fields)
    section = _compress(body) if compressed else body
    prv = _prv_text(fields).encode("utf-16le")
    if not prv.endswith(b"\x00\x00"):
        prv += b"\x00\x00"
    return {"BodyText/Section0": section, "PrvText": prv}


def _decompress(raw: bytes) -> bytes:
    for wbits in (-15, 15, zlib.MAX_WBITS):
        try:
            return zlib.decompress(raw, wbits)
        except zlib.error:
            continue
    raise RuntimeError("HWP 본문을 풀지 못했습니다.")


def _compress(data: bytes) -> bytes:
    compressor = zlib.compressobj(wbits=-15)
    return compressor.compress(data) + compressor.flush()


def _iter_records(data: bytes):
    pos = 0
    while pos + 4 <= len(data):
        header = struct.unpack_from("<I", data, pos)[0]
        tag = header & 0x3FF
        level = (header >> 10) & 0x3FF
        size = (header >> 20) & 0xFFF
        pos += 4
        extra = False
        if size == 0xFFF:
            extra = True
            size = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        payload = data[pos : pos + size]
        pos += size
        yield header, tag, level, extra, payload


def _pack_record(tag: int, level: int, payload: bytes) -> bytes:
    size = len(payload)
    if size < 0xFFF:
        header = tag | (level << 10) | (size << 20)
        return struct.pack("<I", header) + payload
    header = tag | (level << 10) | (0xFFF << 20)
    return struct.pack("<I", header) + struct.pack("<I", size) + payload


def _visible_text(payload: bytes) -> str:
    chars: list[str] = []
    i = 0
    while i + 1 < len(payload):
        code = struct.unpack_from("<H", payload, i)[0]
        if 1 <= code <= 8:
            i += 16
            continue
        if code in (0, 13):
            i += 2
            continue
        if code == 10:
            chars.append("\n")
            i += 2
            continue
        chars.append(chr(code))
        i += 2
    return "".join(chars)


def _has_table_control(payload: bytes) -> bool:
    i = 0
    while i + 1 < len(payload):
        code = struct.unpack_from("<H", payload, i)[0]
        if 1 <= code <= 8:
            if code == 2:
                return True
            i += 16
            continue
        i += 2
    return False


def _split_prefix_suffix(payload: bytes) -> tuple[bytes, bytes]:
    i = 0
    while i + 1 < len(payload):
        code = struct.unpack_from("<H", payload, i)[0]
        if 1 <= code <= 8:
            i += 16
            continue
        break
    prefix = payload[:i]
    rest = payload[i:]
    suffix = b""
    if len(rest) >= 2 and struct.unpack_from("<H", rest, len(rest) - 2)[0] == 0x000D:
        suffix = rest[-2:]
    return prefix, suffix


def _encode_text(text: str) -> bytes:
    out = bytearray()
    for char in text.replace("\r\n", "\n").replace("\r", "\n"):
        if char == "\n":
            continue
        out.extend(char.encode("utf-16le"))
    return bytes(out)


def _field_lines(value: str) -> list[str]:
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return lines or [""]


def _header_with_nchars(header_rec: tuple, nchars: int, *, last: bool, extra: bool) -> bytes:
    hdr = bytearray(header_rec[4])
    flag = 0x80000000 if last else 0
    struct.pack_into("<I", hdr, 0, flag | nchars)
    if extra:
        struct.pack_into("<I", hdr, 4, 0)
        if len(hdr) >= 12:
            hdr[10] = 0
            hdr[11] = 0
        if len(hdr) >= 24:
            struct.pack_into("<I", hdr, 20, 0)
    return _pack_record(header_rec[1], header_rec[2], bytes(hdr))


def _shape_payload(nchars: int, original: bytes, *, extra: bool = False) -> bytes:
    pairs = []
    for offset in range(0, len(original) - 7, 8):
        pos, sid = struct.unpack_from("<II", original, offset)
        pairs.append((pos, sid))
    if not pairs:
        return original
    first_id = pairs[0][1]
    last_id = first_id if extra else pairs[-1][1]
    new_pairs = [(0, first_id)]
    if last_id != first_id and nchars > 1:
        new_pairs.append((nchars - 1, last_id))
    return b"".join(struct.pack("<II", pos, sid) for pos, sid in new_pairs)


def _line_seg_payload(original: bytes, line_index: int) -> bytes:
    if len(original) < 8:
        return original
    payload = bytearray(original)
    line_height = struct.unpack_from("<i", original, 8)[0] if len(original) >= 12 else 1000
    spacing = struct.unpack_from("<i", original, 20)[0] if len(original) >= 24 else 500
    step = max(line_height, 0) + max(spacing, 0)
    struct.pack_into("<i", payload, 4, line_index * step)
    return bytes(payload)


def _unpack_packed(blob: bytes) -> tuple[int, int, bytes]:
    header = struct.unpack_from("<I", blob, 0)[0]
    tag = header & 0x3FF
    level = (header >> 10) & 0x3FF
    size = (header >> 20) & 0xFFF
    offset = 4
    if size == 0xFFF:
        size = struct.unpack_from("<I", blob, 4)[0]
        offset = 8
    return tag, level, blob[offset : offset + size]


def _set_list_nparas(packed: bytes, nparas: int) -> bytes:
    tag, level, payload = _unpack_packed(packed)
    payload = bytearray(payload)
    struct.pack_into("<I", payload, 0, nparas)
    return _pack_record(tag, level, bytes(payload))


def _take_para_tail(records: list, start: int):
    shape_rec = line_seg_rec = ctrl_rec = None
    j = start
    if j < len(records) and records[j][1] == HWPTAG_PARA_CHAR_SHAPE:
        shape_rec = records[j]
        j += 1
    if j < len(records) and records[j][1] == HWPTAG_PARA_LINE_SEG:
        line_seg_rec = records[j]
        j += 1
    if j < len(records) and records[j][1] == HWPTAG_CTRL_HEADER:
        ctrl_rec = records[j]
        j += 1
    return shape_rec, line_seg_rec, ctrl_rec, j


def _emit_paragraphs(
    rebuilt: list[bytes],
    header_rec,
    text_tag: int,
    text_level: int,
    prefix: bytes,
    suffix: bytes,
    lines: list[str],
    shape_rec,
    line_seg_rec,
    ctrl_rec,
) -> None:
    extra_count = max(len(lines) - 1, 0)
    if extra_count and rebuilt:
        list_tag, _, list_payload = _unpack_packed(rebuilt[-1])
        if list_tag == HWPTAG_LIST_HEADER and len(list_payload) >= 4:
            old_nparas = struct.unpack_from("<I", list_payload, 0)[0]
            rebuilt[-1] = _set_list_nparas(rebuilt[-1], old_nparas + extra_count)
    first_payload = prefix + _encode_text(lines[0]) + suffix
    nchars = len(first_payload) // 2
    if header_rec and header_rec[1] == HWPTAG_PARA_HEADER:
        rebuilt.append(_header_with_nchars(header_rec, nchars, last=extra_count == 0, extra=False))
    rebuilt.append(_pack_record(text_tag, text_level, first_payload))
    if shape_rec:
        rebuilt.append(
            _pack_record(shape_rec[1], shape_rec[2], _shape_payload(nchars, shape_rec[4]))
        )
    if line_seg_rec:
        rebuilt.append(
            _pack_record(
                line_seg_rec[1],
                line_seg_rec[2],
                _line_seg_payload(line_seg_rec[4], 0),
            )
        )
    if ctrl_rec:
        rebuilt.append(_pack_record(ctrl_rec[1], ctrl_rec[2], ctrl_rec[4]))
    for extra_i, extra in enumerate(lines[1:], start=1):
        extra_payload = _encode_text(extra) + suffix
        extra_nchars = len(extra_payload) // 2
        if header_rec and header_rec[1] == HWPTAG_PARA_HEADER:
            rebuilt.append(
                _header_with_nchars(
                    header_rec,
                    extra_nchars,
                    last=extra_i == extra_count,
                    extra=True,
                )
            )
        rebuilt.append(_pack_record(text_tag, text_level, extra_payload))
        if shape_rec:
            rebuilt.append(
                _pack_record(
                    shape_rec[1],
                    shape_rec[2],
                    _shape_payload(extra_nchars, shape_rec[4], extra=True),
                )
            )
        if line_seg_rec:
            rebuilt.append(
                _pack_record(
                    line_seg_rec[1],
                    line_seg_rec[2],
                    _line_seg_payload(line_seg_rec[4], extra_i),
                )
            )


def _replace_content(body: bytes, fields: dict[str, str]) -> bytes:
    records = list(_iter_records(body))
    texts: list[tuple[int, str, bytes]] = []
    for index, (_header, tag, _level, _extra, payload) in enumerate(records):
        if tag != HWPTAG_PARA_TEXT:
            continue
        texts.append((index, _visible_text(payload), payload))

    bullet_ids = []
    empty_ids = []
    for rec_index, visible, payload in texts:
        stripped = visible.replace(" ", "").strip()
        if stripped in {"◦", "◦I"}:
            bullet_ids.append(rec_index)
        elif visible.strip() == "" and _has_table_control(payload):
            empty_ids.append(rec_index)

    mapping: dict[int, str] = {}
    keys = ["purpose", "region", "schedule", "event", "traveler", "budget", "category"]
    for rec_index, key in zip(bullet_ids, keys):
        mapping[rec_index] = fields[key]
    if empty_ids:
        mapping[empty_ids[0]] = fields["budget_who"]

    header_inserts: dict[int, str] = {}
    for index, (_header, tag, _level, _extra, _payload) in enumerate(records):
        if tag != HWPTAG_PARA_HEADER:
            continue
        nxt = records[index + 1][1] if index + 1 < len(records) else None
        if nxt != HWPTAG_PARA_TEXT:
            header_inserts[index] = _wrap_text(fields.get("category_who", "출장자 1인"), 4)

    rebuilt: list[bytes] = []
    i = 0
    while i < len(records):
        _header, tag, level, _extra, payload = records[i]
        orig_index = i
        if tag == HWPTAG_PARA_HEADER and orig_index in header_inserts:
            lines = _field_lines(header_inserts[orig_index])
            shape_rec, line_seg_rec, ctrl_rec, j = _take_para_tail(records, i + 1)
            _emit_paragraphs(
                rebuilt,
                records[i],
                HWPTAG_PARA_TEXT,
                3,
                b"",
                b"\r\x00",
                lines,
                shape_rec,
                line_seg_rec,
                ctrl_rec,
            )
            i = j
            continue
        if tag == HWPTAG_PARA_TEXT and orig_index in mapping:
            lines = _field_lines(mapping[orig_index])
            header_rec = records[i - 1] if i else None
            if rebuilt and header_rec and header_rec[1] == HWPTAG_PARA_HEADER:
                rebuilt.pop()
            prefix, suffix = _split_prefix_suffix(payload)
            if not suffix:
                suffix = b"\r\x00"
            shape_rec, line_seg_rec, ctrl_rec, j = _take_para_tail(records, i + 1)
            _emit_paragraphs(
                rebuilt,
                header_rec,
                tag,
                level,
                prefix,
                suffix,
                lines,
                shape_rec,
                line_seg_rec,
                ctrl_rec,
            )
            i = j
            continue
        rebuilt.append(_pack_record(tag, level, payload))
        i += 1
    return b"".join(rebuilt)


def _prv_text(fields: dict[str, str]) -> str:
    def one_line(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    return (
        " <국외출장 심사신청서>\r\n"
        "<구  분><내        용>\r\n"
        f"<1. 출장목적><{one_line(fields['purpose'])}>\r\n"
        f"<2. 출장지역><{one_line(fields['region'])}>\r\n"
        f"<3. 출장일정><{one_line(fields['schedule'])}>\r\n"
        f"<4. 방문기관   또는 행사명><{one_line(fields['event'])}>\r\n"
        f"<5. 출 장 자><{one_line(fields['traveler'])}>\r\n"
        f"<6. 소요예산><{one_line(fields['budget_who'])}><{one_line(fields['budget'])}>\r\n"
        f"<7. 예산과목><{one_line(fields.get('category_who', '출장자 1인'))}><{one_line(fields['category'])}>\r\n"
    )
