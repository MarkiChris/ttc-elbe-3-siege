"""Sammelt alle Punktspiele des TTC Elbe Dresden der laufenden Saison und erzeugt index.html.

Quellen:
  - TischtennisLive (STTV, Bezirk Dresden, Stadt Dresden)
  - myTischtennis / click-TT (Regional-/Oberligen, z. B. Damen-Oberliga)

Aufruf:  python update.py          (Mannschaftsliste aus teams.json, falls juenger als 7 Tage)
         python update.py --neu    (Mannschaften neu suchen)
Bereits bestaetigte Spielberichte werden in cache.json zwischengespeichert.
"""
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CLUB = "TTC Elbe Dresden"
TTLIVE_HOSTS = ["sttv", "dresden", "dresden-stadt"]
HERE = Path(__file__).resolve().parent
CACHE_FILE = HERE / "cache.json"
TEAMS_FILE = HERE / "teams.json"
TEMPLATE = HERE / "template.html"
OUT = HERE / "index.html"
UA = {"User-Agent": "Mozilla/5.0 (3Siege-Auswertung TTC Elbe Dresden)"}
# In GitHub Actions gesetzt (owner/repo); damit verlinkt der Aktualisieren-Button den Workflow
REPO = os.environ.get("GITHUB_REPOSITORY", "")


def now_berlin():
    """Ortszeit Dresden, auch auf GitHub-Servern (die laufen in UTC)."""
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("Europe/Berlin")).replace(tzinfo=None)
    except Exception:  # noqa: BLE001  (Windows ohne tzdata)
        return dt.datetime.now()

sys.stdout.reconfigure(encoding="utf-8")


def fetch(url, encoding=None):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = r.read()
                cs = encoding or r.headers.get_content_charset() or "utf-8"
            time.sleep(0.25)
            return decode_mixed(raw) if encoding == "mixed" else raw.decode(cs, "replace")
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                raise
            print(f"  Wiederhole {url}: {e}")
            time.sleep(2)


def decode_mixed(raw):
    """TTLive mischt UTF-8 und cp1252 in einer Seite: gueltige UTF-8-Folgen als UTF-8, sonst cp1252."""
    out, i, n = [], 0, len(raw)
    while i < n:
        b = raw[i]
        size = 2 if 0xC2 <= b <= 0xDF else 3 if 0xE0 <= b <= 0xEF else 4 if 0xF0 <= b <= 0xF4 else 1
        if size > 1:
            try:
                out.append(raw[i:i + size].decode("utf-8"))
                i += size
                continue
            except UnicodeDecodeError:
                pass
        out.append(raw[i:i + 1].decode("cp1252", "replace") if b >= 0x80 else chr(b))
        i += 1
    return "".join(out)


def cells(row):
    """Zellen einer Tabellenzeile; robust gegen nicht geschlossene <td>-Tags."""
    return [strip(c) for c in re.split(r"<td[^>]*>", row)[1:]]


def strip(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def flip_name(n):
    """'Goldbach, Florian' -> 'Florian Goldbach'"""
    n = n.strip()
    if "," in n:
        last, first = n.split(",", 1)
        return f"{first.strip()} {last.strip()}"
    return n


def pmap(fn, items, workers=8):
    """fn parallel auf items anwenden, Reihenfolge bleibt erhalten."""
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))


def is_club(team):
    return team.strip().startswith(CLUB)


# --------------------------------------------------------------------------- TTLive

def ttlive_teams():
    teams = {}
    for host in TTLIVE_HOSTS:
        base = f"https://{host}.tischtennislive.de/default.aspx"
        start = fetch(base + "?L1=Public", "mixed")
        groups = sorted(set(re.findall(r"L2=TTStaffeln&(?:amp;)?L2P=(\d+)", start)))
        pages = pmap(lambda g: fetch(f"{base}?L1=Ergebnisse&L2=TTStaffeln&L2P={g}", "mixed"), groups)
        for g, page in zip(groups, pages):
            title = strip(re.search(r"<title>(.*?)</title>", page, re.S).group(1))
            league = " - ".join(title.split(" - ")[2:]) or title
            if "pokal" in league.lower():
                continue
            for href, name in re.findall(r'<a[^>]*href="([^"]*L3=Mannschaften&L3P=\d+)"[^>]*>\s*(?:<span>)?([^<]*)', page):
                name = strip(name)
                if is_club(name):
                    tid = re.search(r"L3P=(\d+)", href).group(1)
                    teams[(host, tid)] = {
                        "source": "ttlive", "host": host, "group": g, "id": tid,
                        "team": name, "league": league,
                        "url": f"https://{host}.tischtennislive.de/?L1=Ergebnisse&L2=TTStaffeln&L2P={g}&L3=Mannschaften&L3P={tid}",
                    }
    return list(teams.values())


def ttlive_meetings(team):
    page = fetch(team["url"], "mixed")
    out = []
    for row in re.findall(r"<tr[^>]*ID=\"Spiel\d+\".*?</tr>", page, re.S):
        m = re.search(r"L3=Spielbericht&L3P=(\d+)", row)
        d = re.search(r"(\d\d)\.(\d\d)\.(\d\d)", row)
        if not m or not d:
            continue
        res = re.search(r"L3=Spielbericht&L3P=\d+\"[^>]*>\s*(\d+:\d+)\s*</a>", row)
        if not res:
            continue
        res = res.group(1)
        date = dt.date(2000 + int(d.group(3)), int(d.group(2)), int(d.group(1)))
        out.append({
            "date": date.isoformat(),
            "result": res,
            "report": f"https://{team['host']}.tischtennislive.de/?L1=Ergebnisse&L2=TTStaffeln&L2P={team['group']}&L3=Spielbericht&L3P={m.group(1)}",
        })
    return out


def ttlive_report(url):
    page = fetch(url, "mixed")
    body = page[page.find("PUNKTMANNSCHAFTSSPIEL"):]
    body = re.sub(r'<input[^>]*value="([^"]*)"[^>]*>', r"\1", body)
    rows = [cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)]

    teams = None
    for r in rows:
        if len(r) >= 4 and r[0] == "A" and r[2] == "B":
            teams = (r[1], r[3])
            break
    # Doppel-Aufstellung: D1 | Name / Name | D1 | Name / Name
    pairs = ({}, {})
    for r in rows:
        if len(r) == 4 and re.fullmatch(r"D\d", r[0]) and re.fullmatch(r"D\d", r[2]) and "," in r[1] + r[3]:
            pairs[0][r[0]] = [flip_name(x) for x in r[1].split(" / ")] if "," in r[1] else []
            pairs[1][r[2]] = [flip_name(x) for x in r[3].split(" / ")] if "," in r[3] else []

    games = []
    for r in rows:
        if len(r) < 8 or not re.fullmatch(r"[DE]\d+", r[0]) or not re.fullmatch(r"\d+:\d+", r[-1]):
            continue
        sets = [s for s in r[4:-2] if re.fullmatch(r"\d+:\d+", s)]
        pa, pb = map(int, r[-1].split(":"))
        if r[0].startswith("D"):
            a = pairs[0].get(r[0]) or [flip_name(x) for x in r[1].split("/")]
            b = pairs[1].get(r[2]) or [flip_name(x) for x in r[3].split("/")]
            kind = "Doppel"
        else:
            a, b, kind = [flip_name(r[1])], [flip_name(r[3])], "Einzel"
        games.append({"kind": kind, "home": a, "guest": b, "homeWin": pa > pb,
                      "played": bool(sets), "sets": r[-2],
                      "label": f"{r[0]}-{r[2]}"})
    confirmed = "vom Spielleiter bestätigt" in strip(body)
    return {"home": teams[0] if teams else "", "guest": teams[1] if teams else "",
            "result": None, "games": games, "confirmed": confirmed}


# --------------------------------------------------------------------------- myTischtennis

def season_code(today):
    y = today.year if today.month >= 7 else today.year - 1
    return f"{y % 100:02d}--{(y + 1) % 100:02d}"


def remix(url):
    t = fetch(url, "utf-8")
    i = t.find("window.__remixContext = ")
    s = t[i + len("window.__remixContext = "):]
    return json.loads(s[: s.find(";</script>")])["state"]["loaderData"]


def mytt_teams(today):
    season = season_code(today)
    base = "https://www.mytischtennis.de"
    overview = fetch(f"{base}/click-tt/DTTB/{season}/ligen/DTTB_{season}", "utf-8")
    groups = sorted(set(re.findall(rf'(/click-tt/DTTB/{season}/ligen/[^"/\\]+/gruppe/\d+)/tabelle/gesamt', overview)))
    teams = {}
    pages = pmap(lambda g: fetch(base + g + "/tabelle/gesamt", "utf-8"), groups)
    for g, page in zip(groups, pages):
        for path, tid, slug in re.findall(rf'({re.escape(g)}/mannschaft/(\d+)/(TTC_Elbe_Dresden[^/"\\]*))/', page):
            league = urllib.parse.unquote(g.split("/ligen/")[1].split("/")[0]).replace("_", " ")
            teams[tid] = {"source": "mytt", "id": tid, "team": slug.replace("_", " "),
                          "league": league, "url": base + path + "/spielplan/gesamt"}
    return list(teams.values())


def mytt_meetings(team):
    ld = remix(team["url"])
    data = next(v for k, v in ld.items() if "spielplan" in k)["data"]
    head = data.get("head_infos") or {}
    if head.get("league_name"):
        team["league"] = head["league_name"]
    if head.get("team_name"):
        team["team"] = f"{CLUB} {head['team_name']}"
    out = []
    for day in data["meetings_excerpt"]["meetings"]:
        for date, ms in day.items():
            for m in ms:
                if m.get("state") != "done":
                    continue
                slug = (m["team_home"] + "-vs-" + m["team_away"]).replace(" ", "_")
                grp = team["url"].split("/mannschaft/")[0]
                out.append({"date": date,
                            "report": f"{grp}/spielbericht/{m['meeting_id']}/{urllib.parse.quote(slug)}"})
    return out


def mytt_report(url):
    ld = remix(url)
    mr = next(v for k, v in ld.items() if "spielbericht" in k)["meetingReport"]

    def nm(p):
        return f"{p['firstname']} {p['lastname']}" if p else None

    games = []
    for g in mr["match"]:
        home = [n for n in (nm(g.get("mm_player11")), nm(g.get("mm_player12"))) if n]
        guest = [n for n in (nm(g.get("mm_player21")), nm(g.get("mm_player22"))) if n]
        sets = f"{g.get('sets_home')}:{g.get('sets_guest')}"
        games.append({"kind": "Doppel" if g["game_type"] == "double" else "Einzel",
                      "home": home, "guest": guest, "homeWin": (g.get("matches_home") or 0) > (g.get("matches_guest") or 0),
                      "played": not (g.get("home_wo") or g.get("guest_wo")) and (g.get("sets_home") or 0) + (g.get("sets_guest") or 0) > 0,
                      "sets": sets, "label": g.get("match_name", "")})
    return {"home": mr["team_home"], "guest": mr["team_guest"],
            "result": f"{mr['matches_home']}:{mr['matches_guest']}", "games": games,
            "confirmed": bool(mr.get("is_confirmed"))}


# --------------------------------------------------------------------------- Auswertung

def player_stats(team, meeting, rep):
    """Siege je Spieler auf der Seite dieser Mannschaft.

    Bei vereinsinternen Duellen wertet jede der beiden Mannschaften ihre eigene Seite.
    """
    if rep["home"] == team["team"]:
        sides = ["home"]
    elif rep["guest"] == team["team"]:
        sides = ["guest"]
    else:
        sides = [s for s in ("home", "guest") if is_club(rep[s])][:1]
    result = []
    for side in sides:
        opp = rep["guest"] if side == "home" else rep["home"]
        players = {}
        for g in rep["games"]:
            if not g["played"]:
                continue
            mine = g[side]
            other = g["guest" if side == "home" else "home"]
            won = g["homeWin"] if side == "home" else not g["homeWin"]
            sets = g["sets"] if side == "home" else ":".join(reversed(g["sets"].split(":")))
            for p in mine:
                st = players.setdefault(p, {"name": p, "wins": 0, "games": []})
                st["wins"] += won
                st["games"].append({"kind": g["kind"], "won": won, "sets": sets,
                                    "partner": [x for x in mine if x != p],
                                    "opponents": other})
        res = rep["result"] or meeting.get("result")
        if res and side == "guest":
            res = ":".join(reversed(res.split(":")))
        result.append({"date": meeting["date"], "team": team["team"], "league": team["league"],
                       "home": side == "home", "opponent": opp, "result": res,
                       "report": meeting["report"], "players": list(players.values())})
    return result


def find_teams(today, rediscover):
    """Mannschaftsliste; wird bis zu 7 Tage aus teams.json wiederverwendet."""
    if not rediscover and TEAMS_FILE.exists():
        saved = json.loads(TEAMS_FILE.read_text("utf-8"))
        age = (today - dt.date.fromisoformat(saved["date"])).days
        if saved["season"] == season_code(today) and 0 <= age < 7 and saved["teams"]:
            print(f"  {len(saved['teams'])} Mannschaften aus teams.json (Stand {saved['date']})")
            return saved["teams"]
    print("Suche Mannschaften ...")
    teams = ttlive_teams()
    try:
        teams += mytt_teams(today)
    except Exception as e:  # noqa: BLE001
        print(f"  myTischtennis nicht erreichbar: {e}")
    print(f"  {len(teams)} Mannschaften gefunden")
    TEAMS_FILE.write_text(json.dumps({"date": today.isoformat(), "season": season_code(today),
                                      "teams": teams}, ensure_ascii=False), "utf-8")
    return teams


def team_matches(t, cache):
    try:
        meetings = ttlive_meetings(t) if t["source"] == "ttlive" else mytt_meetings(t)
    except Exception as e:  # noqa: BLE001
        print(f"  {t['team']}: Fehler beim Spielplan: {e}")
        return []
    out = []
    for m in meetings:
        rep = cache.get(m["report"])
        if not rep:
            try:
                rep = ttlive_report(m["report"]) if t["source"] == "ttlive" else mytt_report(m["report"])
            except Exception as e:  # noqa: BLE001
                print(f"  Fehler im Bericht {m['report']}: {e}")
                continue
            if rep["confirmed"]:
                cache[m["report"]] = rep
        out += player_stats(t, m, rep)
    print(f"{t['team']} – {t['league']}: {len(meetings)} Spiele")
    return out


def main(rediscover=False):
    """Sammelt alle Daten, schreibt index.html/artifact.html und gibt die Artifact-Seite zurueck."""
    now = now_berlin()
    today = now.date()
    cache = json.loads(CACHE_FILE.read_text("utf-8")) if CACHE_FILE.exists() else {}
    teams = find_teams(today, rediscover)
    matches = [m for ms in pmap(lambda t: team_matches(t, cache), teams) for m in ms]

    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), "utf-8")
    matches.sort(key=lambda x: (x["date"], x["team"]), reverse=True)
    data = {"generated": now.isoformat(timespec="minutes"), "club": CLUB, "repo": REPO,
            "teams": len(teams), "matches": matches}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.read_text("utf-8").replace("/*__DATA__*/null", payload)
    OUT.write_text(
        '<!doctype html>\n<html lang="de">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        "</head>\n<body>\n" + page + "\n</body>\n</html>\n", "utf-8")
    # Gleiche Seite ohne Dokumentgeruest, zum Veroeffentlichen als Artifact
    (HERE / "artifact.html").write_text(page, "utf-8")
    print(f"Fertig: {len(matches)} Spiele -> {OUT}")
    return page, data


if __name__ == "__main__":
    main(rediscover="--neu" in sys.argv)
