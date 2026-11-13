#!/usr/bin/env python3
"""Build videre.html from the Videre Project's public API (MTGO Legacy Challenges).

Usage:
    python build_videre.py                 fetch from api.videreproject.com, write videre.html

Environment variables (all optional):
    VIDERE_API   API base URL            (default https://api.videreproject.com)
    FORMAT       format path segment     (default legacy)
    KINDS        comma list of MTGO event kinds to keep (default Challenge)
                 e.g. "Challenge,Showcase" to include Showcase Challenges
    START        first date to fetch     (default 2025-01-01)
    OUT          output file             (default videre.html next to this script)
    MIN_ROWS     refuse to write if fewer result rows than this (default 10000)

Needs only the Python standard library. The existing videre.html is left untouched if
anything looks wrong (no data, too few rows, API errors).

How the numbers are built (matches the layout the page already reads):
  * one row per player per event, from /standings (final rank + match record)
  * Archetype / Deck = the Videre archetype and deck labels joined to that player's
    decklist; players with no published decklist get "(no decklist)"
  * Non-mirror wins/losses are counted from /matches, skipping byes and pairings where
    both players' archetype labels are the same
"""
import json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import date, timedelta
from pathlib import Path

API = os.environ.get("VIDERE_API", "https://api.videreproject.com").rstrip("/")
FORMAT = os.environ.get("FORMAT", "legacy")
KINDS = {k.strip().lower() for k in os.environ.get("KINDS", "Challenge").split(",") if k.strip()}
START = os.environ.get("START", "2025-01-01")
MIN_ROWS = int(os.environ.get("MIN_ROWS", "10000"))
HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("OUT", HERE / "videre.html"))
PAGE = 500            # API maximum page size
NO_DECK = "(no decklist)"


def get(path, params):
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(6):
        req = urllib.request.Request(url, headers={"User-Agent": "legacy-db-videre-build/1.0",
                                                   "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 400:                  # the API answers 400 when a window has no rows
                return {"data": [], "meta": {"has_more": False}}
            if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(2 ** attempt)
                continue
            raise SystemExit(f"API error {e.code} for {url}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 5:
                time.sleep(2 ** attempt)
                continue
            raise SystemExit(f"Could not reach the API ({e}) for {url}")


def fetch_all(route, lo, hi):
    """Page through one route for one date window."""
    out, offset = [], 0
    while True:
        res = get(f"/{route}/{FORMAT}", {"min_date": lo, "max_date": hi, "limit": PAGE, "offset": offset})
        out.extend(res.get("data", []))
        meta = res.get("meta", {})
        if not meta.get("has_more") or meta.get("next_offset") is None:
            return out
        offset = meta["next_offset"]


def windows():
    """Calendar-month windows from START to today (keeps each query small and cacheable)."""
    d = date.fromisoformat(START)
    today = date.today()
    while d <= today:
        nxt = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield d.isoformat(), min(nxt - timedelta(days=1), today).isoformat()
        d = nxt


def keep(row):
    return str(row.get("event_type") or row.get("kind") or "").lower() in KINDS


def record(rec):
    """'9-1-0' -> (9, 1)  (wins, losses; draws ignored)"""
    try:
        p = [int(x) for x in str(rec).split("-")]
        return p[0], p[1]
    except (ValueError, IndexError):
        return 0, 0


def build():
    standings, matches = {}, {}
    for lo, hi in windows():
        s = [r for r in fetch_all("standings", lo, hi) if keep(r)]
        m = [r for r in fetch_all("matches", lo, hi) if keep(r)]
        for r in s:
            standings[(r["event_id"], r["player"])] = r
        for r in m:
            matches[r["id"]] = r
        print(f"{lo} .. {hi}: {len(s)} standings, {len(m)} matches", flush=True)
    if not standings:
        raise SystemExit("No standings returned; not writing anything.")

    # non-mirror tallies per (event, player), from the player's side of each pairing
    nm = {}
    for r in matches.values():
        if r.get("isbye") or r.get("result") not in ("win", "loss"):
            continue
        key = (r["event_id"], r["player"])
        t = nm.setdefault(key, [0, 0, 0])          # nm wins, nm losses, rows seen
        t[2] += 1
        pa, oa = r.get("player_archetype"), r.get("opponent_archetype")
        if pa and oa and pa == oa:
            continue                                 # mirror
        t[0 if r["result"] == "win" else 1] += 1

    dates = sorted({str(r["date"])[:10] for r in standings.values()})
    dmap = {d: i for i, d in enumerate(dates)}
    players, archs, decks, events = {}, {}, {}, {}

    def ix(m, v):
        if v not in m:
            m[v] = len(m)
        return m[v]

    rows = []
    for (eid, player), r in sorted(standings.items(), key=lambda kv: (str(kv[1]["date"]), kv[0][0], kv[1].get("rank") or 0)):
        w, l = record(r.get("record"))
        seen = nm.get((eid, player))
        nw, nl = (seen[0], seen[1]) if seen else (w, l)   # no match rows: fall back to the totals
        deck = r.get("deck_name") or r.get("archetype") or NO_DECK
        arch = r.get("archetype") or deck
        rows.append([ix(players, player), int(r.get("rank") or 0), w, l,
                     ix(archs, arch), ix(decks, deck), nw, nl,
                     dmap[str(r["date"])[:10]], ix(events, eid)])
    ev_names = {}
    for (eid, _), r in standings.items():
        ev_names.setdefault(eid, r.get("event_name") or f"Event {eid}")
    data = {"p": list(players), "a": list(archs), "k": list(decks),
            "e": [ev_names[e] for e in events], "d": dates, "r": rows}
    return data


def main():
    data = build()
    if len(data["r"]) < MIN_ROWS:
        raise SystemExit(f"Only {len(data['r'])} rows (expected at least {MIN_ROWS}); not writing {OUT.name}.")
    template = (HERE / "template.html").read_text(encoding="utf-8")
    if template.count("__DATA__") != 1:
        raise SystemExit("template.html must contain __DATA__ exactly once.")
    # credit Videre instead of the Legacy Data Collection Project
    old = ('Data from the <a href="https://patreon.com/legacydatacollection" target="_blank" '
           'rel="noopener">Legacy Data Collection Project</a>')
    new = ('Data from the <a href="https://videreproject.com" target="_blank" rel="noopener">'
           'Videre Project</a>')
    if template.count(old) != 1:
        raise SystemExit("Could not find the footer credit in template.html.")
    template = template.replace(old, new)
    blob = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    OUT.write_text(template.replace("__DATA__", blob), encoding="utf-8")
    print(f"Wrote {OUT.name}: {len(data['r']):,} results, {len(data['p']):,} players, "
          f"{len(data['e'])} events, {data['d'][0]} to {data['d'][-1]}")


if __name__ == "__main__":
    main()
