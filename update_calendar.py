"""Build a subscribable calendar of Hapoel Jerusalem basketball games with TV channels.

Source: the schedule on the club's official site. Saved game data lives in
data/games.json and the calendar is written to docs/hapoel-jerusalem.ics.
Nothing is written if the fetch or the safety checks fail.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
STORE_PATH = ROOT / "data" / "games.json"
ICS_PATH = ROOT / "docs" / "hapoel-jerusalem.ics"
MAPPINGS_PATH = ROOT / "mappings.yaml"

SCHEDULE_URL = "https://www.hapoel.co.il/games.asp?cYear={season}&Board=0&cMode=2&lang="
USER_AGENT = "Mozilla/5.0 (compatible; hapoel-jerusalem-calendar)"
SELF_NAME = "הפועל י-ם"
ISRAEL = ZoneInfo("Asia/Jerusalem")

HEBREW_MONTHS = {
    "בינואר": 1, "בפברואר": 2, "במרץ": 3, "באפריל": 4, "במאי": 5, "ביוני": 6,
    "ביולי": 7, "באוגוסט": 8, "בספטמבר": 9, "באוקטובר": 10, "בנובמבר": 11, "בדצמבר": 12,
}
# Same numbering as date.weekday(): Monday is 0.
HEBREW_WEEKDAYS = {"שני": 0, "שלישי": 1, "רביעי": 2, "חמישי": 3, "שישי": 4, "שבת": 5, "ראשון": 6}
NO_CHANNEL_TEXT = "שידור טרם נקבע"
HOME_TEXT = "משחק בית"
AWAY_TEXT = "משחק חוץ"
EUROCUP_TEXT = "יורוקאפ"
BELGRADE = "בלגרד"
HOME_ARENA = "פיס ארנה, ירושלים"
GAME_MINUTES = 120
MAX_WEEKDAY_MISMATCHES = 3

DATE_RE = re.compile(r"^(\d{1,2})\s+(\S+),\s*(\S+)\s*(\d{1,2}:\d{2})?$")
# The link's "&gt=" parameter is decoded as ">=" when the HTML is parsed.
LINK_RE = re.compile(r"GameId=(\d+)(?:&gt=|>=)(\d+)")
SCORE_RE = re.compile(r"^\d+:\d+$")

VTIMEZONE = [
    "BEGIN:VTIMEZONE",
    "TZID:Asia/Jerusalem",
    "BEGIN:DAYLIGHT",
    "TZOFFSETFROM:+0200",
    "TZOFFSETTO:+0300",
    "TZNAME:IDT",
    "DTSTART:19700327T020000",
    "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=FR;BYMONTHDAY=23,24,25,26,27,28,29",
    "END:DAYLIGHT",
    "BEGIN:STANDARD",
    "TZOFFSETFROM:+0300",
    "TZOFFSETTO:+0200",
    "TZNAME:IST",
    "DTSTART:19701025T020000",
    "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU",
    "END:STANDARD",
    "END:VTIMEZONE",
]


class ScheduleError(Exception):
    """The source data can't be trusted, so the last good calendar should stay in place."""


@dataclass
class Game:
    uid: str
    date: str  # YYYY-MM-DD
    time: str | None  # HH:MM in Israel time, None when not final
    competition: str
    is_home: bool
    opponent: str
    site_venue: str
    channel: str | None
    score: str | None  # "home:away" as shown by the site, None before the game


def current_season(today: date) -> int:
    """The site names a season after the year it ends in; seasons start in August."""
    return today.year + 1 if today.month >= 8 else today.year


def fetch_schedule(season: int) -> str:
    request = urllib.request.Request(
        SCHEDULE_URL.format(season=season), headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8")
    except OSError as error:
        raise ScheduleError(f"Could not fetch the schedule: {error}") from error


def parse_schedule(page: str, season: int) -> list[Game]:
    soup = BeautifulSoup(page, "html.parser")
    blocks = soup.select("div.game")
    games = []
    mismatches = []
    for block in blocks:
        game, weekday_ok = _parse_game(block, season)
        games.append(game)
        if not weekday_ok:
            mismatches.append(game.date)
    if mismatches:
        print(f"WARNING: weekday doesn't match the date for: {', '.join(mismatches)}")
    if len(mismatches) > MAX_WEEKDAY_MISMATCHES:
        raise ScheduleError(f"Too many date mismatches ({len(mismatches)}); the page may have changed")
    return games


def _clean(text: str) -> str:
    """Collapse whitespace; the site mixes in non-breaking spaces, so names can differ otherwise."""
    return " ".join(text.split())


def _parse_game(block, season: int) -> tuple[Game, bool]:
    date_link = block.select_one("div.date-time a")
    if date_link is None:
        raise ScheduleError("A game has no date; the page layout may have changed")
    link = LINK_RE.search(date_link.get("href", ""))
    match = DATE_RE.match(date_link.get_text(" ", strip=True))
    if link is None or match is None:
        raise ScheduleError(f"Could not read game date: {date_link}")

    day, month_name, weekday_name, time = match.groups()
    month = HEBREW_MONTHS.get(month_name)
    if month is None:
        raise ScheduleError(f"Unknown month: {month_name}")
    year = season - 1 if month >= 8 else season
    game_date = date(year, month, int(day))
    weekday_ok = HEBREW_WEEKDAYS.get(weekday_name) == game_date.weekday()

    teams_link = block.select_one("div.game-data div.text a")
    if teams_link is None or teams_link.span is None:
        raise ScheduleError(f"Could not read the teams for game {link.group(1)}")
    teams = [_clean(part) for part in teams_link.get_text("|").split("|") if _clean(part)]
    if len(teams) != 3 or SELF_NAME not in (teams[0], teams[2]):
        raise ScheduleError(f"Unexpected teams for game {link.group(1)}: {teams}")
    listed_first = teams[0] == SELF_NAME
    opponent = teams[2] if listed_first else teams[0]

    # Fields are identified by content: their positions shift when a value is missing.
    texts = [_clean(el.get_text()) for el in block.select("div.game-type div.text")]
    labels = [_clean(el.get_text()) for el in block.select("div.game-type div.stadium")]
    site_venue = texts[0] if texts else ""
    raw_channel = texts[1] if len(texts) > 1 else ""
    channel = None if raw_channel in ("", NO_CHANNEL_TEXT) else raw_channel

    if HOME_TEXT in labels:
        is_home = True
    elif AWAY_TEXT in labels:
        is_home = False
    else:
        is_home = listed_first  # Cup and preseason games carry no label.

    cycle = block.select_one("div.cycle")
    raw_score = block.select_one("div.score")
    score_text = raw_score.get_text(strip=True) if raw_score else ""
    score = score_text if SCORE_RE.match(score_text) and score_text != "0:0" else None

    game = Game(
        uid=f"{link.group(1)}-{link.group(2)}",
        date=game_date.isoformat(),
        time=time,
        competition=_clean(cycle.get_text()) if cycle else "",
        is_home=is_home,
        opponent=opponent,
        site_venue=site_venue,
        channel=channel,
        score=score,
    )
    return game, weekday_ok


def resolve_venue(game: Game | dict, mappings: dict) -> tuple[str | None, str]:
    """Return (short location for the title, full location for the event)."""
    g = _as_dict(game)
    opponent = mappings.get("opponents", {}).get(g["opponent"]) or {}
    site_venue = g["site_venue"]
    # The site lists Belgrade on some away games, where it can't be right.
    usable = bool(site_venue) and not (site_venue == BELGRADE and not g["is_home"])

    if usable:
        short = mappings.get("venues", {}).get(site_venue)
        return short, site_venue
    if g["is_home"]:
        full = BELGRADE if g["competition"].startswith(EUROCUP_TEXT) else HOME_ARENA
        return mappings.get("venues", {}).get(full), full
    return opponent.get("venue"), opponent.get("venue_full", "")


def format_title(game: Game | dict, mappings: dict) -> str:
    g = _as_dict(game)
    opponent = mappings.get("opponents", {}).get(g["opponent"]) or {}
    title = opponent.get("name", g["opponent"])
    short_venue, _ = resolve_venue(g, mappings)
    if short_venue:
        title = f"{title} {short_venue}"
    if g["channel"]:
        title = f"{title} | {mappings.get('channels', {}).get(g['channel'], g['channel'])}"
    return title


def _as_dict(game: Game | dict) -> dict:
    return asdict(game) if isinstance(game, Game) else game


def merge_games(store: dict, parsed: list[Game], now: datetime, today: date) -> dict:
    """Apply fresh data to the saved games, tracking what changed and when."""
    previous = store.get("games", {})
    merged = {}
    for game in parsed:
        fields = asdict(game)
        old = previous.get(game.uid)
        if old is None:
            merged[game.uid] = {**fields, "sequence": 0, "updated": now.isoformat()}
            continue
        # A finished game keeps its channel if the site later drops it.
        if not fields["channel"] and date.fromisoformat(fields["date"]) < today:
            fields["channel"] = old.get("channel")
        changed = any(old.get(key) != value for key, value in fields.items())
        merged[game.uid] = {
            **fields,
            "sequence": old["sequence"] + 1 if changed else old["sequence"],
            "updated": now.isoformat() if changed else old["updated"],
        }
    # Past games stay; future games missing from the page were cancelled or re-listed.
    for uid, old in previous.items():
        if uid not in merged and date.fromisoformat(old["date"]) < today:
            merged[uid] = old
    return {"games": dict(sorted(merged.items(), key=lambda item: (item[1]["date"], item[0])))}


def check_safe(parsed: list[Game], store: dict, today: date, season: int) -> None:
    """Refuse results that look wrong instead of wiping games from people's calendars."""
    if not parsed:
        raise ScheduleError("No games found on the page")
    # Compare the same season only: a new season naturally starts with a short list.
    season_start = date(season - 1, 8, 1)
    season_end = date(season, 7, 31)
    known_upcoming = {
        uid
        for uid, game in store.get("games", {}).items()
        if today <= date.fromisoformat(game["date"]) <= season_end
        and date.fromisoformat(game["date"]) >= season_start
    }
    found = {game.uid for game in parsed}
    missing = known_upcoming - found
    if known_upcoming and len(missing) > len(known_upcoming) / 2:
        raise ScheduleError(
            f"{len(missing)} of {len(known_upcoming)} upcoming games disappeared from the page"
        )


def report_unmapped(games: dict, mappings: dict) -> None:
    for game in games.values():
        short_venue, _ = resolve_venue(game, mappings)
        if not short_venue:
            print(
                f"WARNING: no location for {game['date']} {game['opponent']} "
                f"(site venue: '{game['site_venue']}'). Add it to mappings.yaml."
            )


def _escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    )


def _fold(line: str) -> list[str]:
    """Split a line into pieces of at most 75 bytes without breaking a character."""
    pieces, current, size = [], "", 0
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > 75:
            pieces.append(current)
            current, size = " ", 1
        current += char
        size += width
    pieces.append(current)
    return pieces


def _describe(game: dict, full_venue: str) -> str:
    lines = [
        game["competition"],
        "משחק בית" if game["is_home"] else "משחק חוץ",
    ]
    if full_venue:
        lines.append(f"מיקום: {full_venue}")
    if game["channel"] or not game["score"]:
        lines.append(f"שידור: {game['channel'] or 'טרם נקבע'}")
    if not game["time"]:
        lines.append("שעת המשחק טרם נקבעה")
    if game["score"]:
        lines.append(f"תוצאה: {game['score']}")
    updated = datetime.fromisoformat(game["updated"]).astimezone(ISRAEL)
    lines.append(f"עודכן: {updated:%d/%m/%Y %H:%M}")
    return "\n".join(lines)


def _utc_stamp(moment: str) -> str:
    return datetime.fromisoformat(moment).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def render_calendar(games: dict, mappings: dict) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//hapoel-jerusalem-calendar//HE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:הפועל ירושלים כדורסל",
        "X-WR-TIMEZONE:Asia/Jerusalem",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
        *VTIMEZONE,
    ]
    for uid, game in games.items():
        day = date.fromisoformat(game["date"])
        _, full_venue = resolve_venue(game, mappings)
        lines += [
            "BEGIN:VEVENT",
            f"UID:game-{uid}@hapoel-jerusalem-calendar",
            f"DTSTAMP:{_utc_stamp(game['updated'])}",
            f"LAST-MODIFIED:{_utc_stamp(game['updated'])}",
            f"SEQUENCE:{game['sequence']}",
        ]
        if game["time"]:
            hour, minute = map(int, game["time"].split(":"))
            start = datetime(day.year, day.month, day.day, hour, minute)
            end = start + timedelta(minutes=GAME_MINUTES)
            lines += [
                f"DTSTART;TZID=Asia/Jerusalem:{start:%Y%m%dT%H%M%S}",
                f"DTEND;TZID=Asia/Jerusalem:{end:%Y%m%dT%H%M%S}",
            ]
        else:
            lines += [
                f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
                f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
            ]
        lines += [
            f"SUMMARY:{_escape(format_title(game, mappings))}",
            f"DESCRIPTION:{_escape(_describe(game, full_venue))}",
        ]
        if full_venue:
            lines.append(f"LOCATION:{_escape(full_venue)}")
        lines += ["TRANSP:TRANSPARENT", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(piece for line in lines for piece in _fold(line)) + "\r\n"


def load_store(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"games": {}}


def write_if_changed(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == content.encode("utf-8"):
        return False
    path.write_bytes(content.encode("utf-8"))
    return True


def run(page: str | None, now: datetime, store_path: Path, ics_path: Path, mappings_path: Path) -> bool:
    today = now.astimezone(ISRAEL).date()
    season = current_season(today)
    mappings = yaml.safe_load(mappings_path.read_text(encoding="utf-8"))
    store = load_store(store_path)

    parsed = parse_schedule(page if page is not None else fetch_schedule(season), season)
    check_safe(parsed, store, today, season)
    merged = merge_games(store, parsed, now, today)
    report_unmapped(merged["games"], mappings)

    store_text = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
    store_changed = write_if_changed(store_path, store_text)
    ics_changed = write_if_changed(ics_path, render_calendar(merged["games"], mappings))
    with_channel = sum(1 for game in merged["games"].values() if game["channel"])
    print(f"{len(merged['games'])} games, {with_channel} with a channel.")
    print("Calendar updated." if store_changed or ics_changed else "No changes.")
    return store_changed or ics_changed


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-file", type=Path, help="read a saved schedule page instead of fetching")
    args = parser.parse_args()
    page = args.from_file.read_text(encoding="utf-8") if args.from_file else None
    try:
        run(page, datetime.now(timezone.utc).replace(microsecond=0), STORE_PATH, ICS_PATH, MAPPINGS_PATH)
    except ScheduleError as error:
        print(f"ERROR: {error}\nThe existing calendar was left unchanged.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
