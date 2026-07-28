"""NAS100 price fetcher — runs on GitHub Actions (stdlib only).

Outputs:
  data/prices_stooq.csv  (Date,Open,High,Low,Close,Volume — ^NDX daily, 2016+)
  data/prices_fred.csv   (observation_date,NASDAQ100 — official close, cross-check)
Fails the run only if BOTH sources fail.
"""
import os, sys, time, urllib.request
from datetime import datetime, timezone

UA = {"User-Agent": "nas100-research-relay/1.0 (personal research; 1 request/day)"}


def get(url, tries=3):
    last = None
    for a in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(5 * a)
    raise RuntimeError(f"{url}: {last}")


def stooq():
    txt = get("https://stooq.com/q/d/l/?s=%5Endx&i=d")
    lines = [l for l in txt.strip().splitlines() if l.strip()]
    if len(lines) < 1000 or not lines[0].lower().startswith("date"):
        raise ValueError(f"stooq response looks wrong ({len(lines)} lines): {lines[0][:60]!r}")
    header, rows = lines[0], lines[1:]
    rows = [r for r in rows if r[:4] >= "2016"]
    with open("data/prices_stooq.csv", "w") as f:
        f.write(header + "\n" + "\n".join(rows) + "\n")
    print(f"stooq: {len(rows)} rows ({rows[0][:10]} -> {rows[-1][:10]})")


def fred():
    today = datetime.now(timezone.utc).date().isoformat()
    txt = get("https://fred.stlouisfed.org/graph/fredgraph.csv"
              f"?id=NASDAQ100&cosd=2016-06-01&coed={today}")
    lines = [l for l in txt.strip().splitlines() if l.strip()]
    if len(lines) < 1000 or "NASDAQ100" not in lines[0]:
        raise ValueError(f"fred response looks wrong ({len(lines)} lines): {lines[0][:60]!r}")
    with open("data/prices_fred.csv", "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"fred: {len(lines)-1} rows (through {lines[-1].split(',')[0]})")


def main():
    os.makedirs("data", exist_ok=True)
    ok = 0
    for fn in (stooq, fred):
        try:
            fn(); ok += 1
        except Exception as e:
            print(f"{fn.__name__} FAILED: {e}")
    if ok == 0:
        sys.exit("both price sources failed")


if __name__ == "__main__":
    main()