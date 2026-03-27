"""
series_meta.csv를 읽어 code별 주기(M/D/Q/W)에 맞춰 약 5년치 로값을 series_values.csv로 생성합니다.
실행: 프로젝트 루트에서  python tools/generate_series_values.py
"""
from __future__ import annotations

import csv
import hashlib
import random
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
META_PATH = REPO_ROOT / "data" / "series_meta.csv"
OUT_PATH = REPO_ROOT / "data" / "series_values.csv"

START = date(2020, 1, 1)
END = date(2024, 12, 31)


def daterange_monthly(start: date, end: date):
    y, m = start.year, start.month
    while True:
        d = date(y, m, 1)
        if d > end:
            break
        yield d
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1


def daterange_daily(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def daterange_quarterly(start: date, end: date):
    for y in range(start.year, end.year + 1):
        for mo, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            d = date(y, mo, day)
            if start <= d <= end:
                yield d


def daterange_weekly(start: date, end: date):
    d = start
    while d.weekday() != 0:
        d += timedelta(days=1)
    while d <= end:
        yield d
        d += timedelta(days=7)


def dates_for_freq(freq: str):
    f = (freq or "M").strip().upper()
    if f == "D":
        return list(daterange_daily(START, END))
    if f == "Q":
        return list(daterange_quarterly(START, END))
    if f == "W":
        return list(daterange_weekly(START, END))
    return list(daterange_monthly(START, END))


def rng_for_code(code: str) -> random.Random:
    h = hashlib.md5(code.encode("utf-8")).hexdigest()
    return random.Random(int(h[:16], 16))


def gen_values(code: str, dates: list[date]) -> list[tuple[str, float]]:
    rng = rng_for_code(code)
    base = rng.uniform(20.0, 200.0)
    slope = rng.uniform(-0.05, 0.08)
    sigma = rng.uniform(0.2, 3.0)
    out: list[tuple[str, float]] = []
    for i, d in enumerate(dates):
        v = base + slope * i + rng.gauss(0, sigma)
        out.append((d.isoformat(), round(float(v), 6)))
    return out


def main():
    rows: list[dict] = []
    with open(META_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = (row.get("code") or "").strip()
            if not code:
                continue
            freq = row.get("데이터포인트_주기", "M")
            dates = dates_for_freq(freq)
            for d_str, v in gen_values(code, dates):
                rows.append({"code": code, "date": d_str, "value": v})

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["code", "date", "value"])
        w.writeheader()
        w.writerows(rows)

    print(f"wrote {len(rows)} rows -> {OUT_PATH}")


if __name__ == "__main__":
    main()
