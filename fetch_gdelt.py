"""GDELT daily fetcher — runs on GitHub Actions (no external deps, stdlib only).

Backfills 2017-01-01 -> today on first run, then incrementally appends.
Per-series resume: each of the 12 series continues from its own last stored day,
and the CSV is checkpointed after every series completes.
Output: data/gdelt_daily.csv  (wide: date + 12 series columns)
Exit code != 0 on hard failure so the Action run shows red.
"""
import csv, json, os, sys, time, urllib.request, urllib.parse
from datetime import date, datetime, timedelta, timezone

BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
START = date(2017, 1, 1)
OUT = "data/gdelt_daily.csv"
PACE_S = 6.0     # GDELT asks for max 1 request per 5 seconds — stay under it
RETRIES = 5
REFRESH_TAIL_DAYS = 3          # re-pull the last few days each run

QUERIES = {
    "market":    '(nasdaq OR "stock market" OR "wall street") sourcelang:english',
    "fed":       '("federal reserve" OR "interest rates" OR "jerome powell") sourcelang:english',
    "macro":     '(inflation OR recession) sourcelang:english',
    "trade_geo": '(tariffs OR "trade war" OR sanctions) sourcelang:english',
    "big_tech":  '("big tech" OR nvidia OR microsoft OR apple OR amazon) sourcelang:english',
    "ai_chips":  '("artificial intelligence" OR semiconductor OR "chip maker") sourcelang:english',
}
MODES = {"tone": "timelinetone", "vol": "timelinevol"}
SERIES = [f"{q}_{m}" for q in QUERIES for m in MODES]
TONE_RANGE, VOL_RANGE = (-25.0, 25.0), (0.0, 100.0)


def fetch_chunk(query, mode, sdt, edt):
    qs = urllib.parse.urlencode({
        "query": query, "mode": mode, "format": "json",
        "STARTDATETIME": sdt, "ENDDATETIME": edt,
    })
    url = f"{BASE}?{qs}"
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "nas100-research-relay/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read().decode("utf-8", "replace")
            if "limit requests" in body[:300].lower():
                raise RuntimeError("throttled: " + body[:60].strip())
            obj = json.loads(body)
            data = obj["timeline"][0]["data"]
            if not isinstance(data, list):
                raise ValueError("unexpected shape")
            return data
        except Exception as e:
            last = e
            wait = 30 if ("throttled" in str(e) or "429" in str(e)) else 5 * attempt
            print(f"    retry {attempt}/{RETRIES}: {str(e)[:90]} (wait {wait}s)")
            time.sleep(wait)
    raise RuntimeError(f"chunk failed permanently: {last}")


def day_key(raw):
    d = "".join(ch for ch in str(raw) if ch.isdigit())
    return f"{d[0:4]}-{d[4:6]}-{d[6:8]}" if len(d) >= 8 else None


def fetch_series(name, start, end):
    """Fetch [start, end] in <=370-day chunks; return {date: value}."""
    q_key, m_key = name.rsplit("_", 1)
    query, mode = QUERIES[q_key], MODES[m_key]
    lo, hi = TONE_RANGE if m_key == "tone" else VOL_RANGE
    acc = {}
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=369), end)
        sdt = cur.strftime("%Y%m%d") + "000000"
        edt = chunk_end.strftime("%Y%m%d") + "235959"
        data = fetch_chunk(query, mode, sdt, edt)
        agg = {}
        for p in data:
            k = day_key(p.get("date"))
            if not k:
                continue
            try:
                v = float(p.get("value"))
            except (TypeError, ValueError):
                continue
            agg.setdefault(k, []).append(v)
        for k, vs in agg.items():
            acc[k] = min(hi, max(lo, sum(vs) / len(vs)))
        print(f"    {name} {cur} -> {chunk_end}: {len(agg)} days", flush=True)
        cur = chunk_end + timedelta(days=1)
        time.sleep(PACE_S)
    return acc


def load_existing():
    if not os.path.exists(OUT):
        return {}
    table = {}
    with open(OUT) as f:
        for row in csv.DictReader(f):
            table[row["date"]] = {s: row.get(s, "") for s in SERIES}
    return table


def series_start(table, name, end):
    """Resume point for one series: its own last stored day minus refresh tail."""
    filled = [d for d, r in table.items() if r.get(name, "")]
    if not filled:
        return START
    last = datetime.strptime(max(filled), "%Y-%m-%d").date()
    return min(last - timedelta(days=REFRESH_TAIL_DAYS - 1), end)


def write_csv(table):
    dates = sorted(table)
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date"] + SERIES)
        for d in dates:
            w.writerow([d] + [table[d].get(s, "") for s in SERIES])


def main():
    os.makedirs("data", exist_ok=True)
    today_utc = datetime.now(timezone.utc).date()
    end = today_utc - timedelta(days=1)          # last COMPLETE UTC day
    table = load_existing()
    print(f"existing rows: {len(table)}; target end: {end}", flush=True)

    failures = []
    for name in SERIES:
        start_s = series_start(table, name, end)
        if start_s > end:
            print(f"[{name}] up to date")
            continue
        print(f"[{name}] {start_s} -> {end}", flush=True)
        try:
            vals = fetch_series(name, start_s, end)
        except Exception as e:
            print(f"  SERIES FAILED: {e}", flush=True)
            failures.append(name)
            continue
        for k, v in vals.items():
            table.setdefault(k, {s: "" for s in SERIES})[name] = f"{v:.4f}"
        write_csv(table)                          # checkpoint after every series
        time.sleep(PACE_S)

    if not table:
        sys.exit("no data at all — aborting")
    write_csv(table)
    dates = sorted(table)
    n = len(dates)
    empty = sum(1 for d in dates for s in SERIES if not table[d].get(s, ""))
    print(f"wrote {OUT}: {n} days ({dates[0]} -> {dates[-1]}), empty cells: {empty}/{n*len(SERIES)}")
    if failures:
        sys.exit(f"failed series this run: {failures}")


if __name__ == "__main__":
    main()
