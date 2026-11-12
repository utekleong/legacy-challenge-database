#!/usr/bin/env python3
"""Rebuild index.html from the Merge tab of the Combined Metagame Sheet.

Usage:
    python build.py              download the sheet and rebuild index.html
    python build.py --csv FILE   rebuild from a CSV you already have (for testing)

Needs only the Python standard library. If the download looks wrong (a login
page, a changed header, far fewer rows than expected) the script stops with an
error and leaves the existing index.html untouched.
"""
import csv, io, json, re, sys, urllib.request
from pathlib import Path

SHEET_ID = "1SpqXHRdLz7qkdBmzB4bm0jTrn0LjZRF5N4R7gMJmV_g"
GID = "1683586903"  # the Merge tab
URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID}"
HEADER = ["Rank", "Player", "Wins", "Losses", "Archetype", "Deck",
          "Non Mirror Wins", "Non Mirror Losses", "Date", "Event"]
MIN_ROWS = 20000  # the sheet had about 25,600 rows; far fewer means something is wrong

HERE = Path(__file__).resolve().parent


def fetch_csv():
    req = urllib.request.Request(URL, headers={"User-Agent": "legacy-db-build/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode("utf-8-sig")


def num(s):
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return 0


def iso_date(s):
    """11/8/2026 -> 2026-11-08 (ISO dates are passed through)."""
    s = (s or "").strip()
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return s[:10]


def parse(text):
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or [c.strip() for c in rows[0][:10]] != HEADER:
        raise SystemExit("Unexpected header row. Got: " + repr(rows[0][:10] if rows else None)
                         + "\nThe sheet may have changed, or Google returned a login page.")
    body = [r + [""] * (10 - len(r)) for r in rows[1:] if len(r) > 1 and r[1].strip()]
    if len(body) < MIN_ROWS:
        raise SystemExit(f"Only {len(body)} rows found (expected at least {MIN_ROWS}); not overwriting index.html.")
    return body


def encode(body):
    """Turn rows into the compact lookup-table format the page reads."""
    dates = sorted({iso_date(r[8]) for r in body})
    tables = {k: {} for k in "pakd"}   # player, archetype, deck(k) handled below
    players, archs, decks, events = {}, {}, {}, {}

    def ix(m, v):
        if v not in m:
            m[v] = len(m)
        return m[v]

    dmap = {d: i for i, d in enumerate(dates)}
    out = []
    for r in body:
        out.append([ix(players, r[1]), num(r[0]), num(r[2]), num(r[3]),
                    ix(archs, r[4]), ix(decks, r[5]), num(r[6]), num(r[7]),
                    dmap[iso_date(r[8])], ix(events, r[9])])
    return {"p": list(players), "a": list(archs), "k": list(decks),
            "e": list(events), "d": dates, "r": out}


def main():
    if "--csv" in sys.argv:
        text = Path(sys.argv[sys.argv.index("--csv") + 1]).read_text(encoding="utf-8-sig")
    else:
        print("Downloading the Merge tab...")
        text = fetch_csv()
    data = encode(parse(text))
    template = (HERE / "template.html").read_text(encoding="utf-8")
    if template.count("__DATA__") != 1:
        raise SystemExit("template.html must contain __DATA__ exactly once.")
    blob = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    (HERE / "index.html").write_text(template.replace("__DATA__", blob), encoding="utf-8")
    print(f"Wrote index.html: {len(data['r']):,} results, {len(data['p']):,} players, "
          f"{len(data['e'])} events, {data['d'][0]} to {data['d'][-1]}")


if __name__ == "__main__":
    main()
