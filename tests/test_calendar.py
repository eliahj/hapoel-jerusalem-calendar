import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import update_calendar as uc  # noqa: E402

FIXTURE = (ROOT / "tests" / "fixtures" / "schedule.html").read_text(encoding="utf-8")
MAPPINGS = yaml.safe_load((ROOT / "mappings.yaml").read_text(encoding="utf-8"))
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 5)


@pytest.fixture(scope="module")
def games():
    return {game.uid: game for game in uc.parse_schedule(FIXTURE, 2027)}


def unfold(calendar: str) -> list[str]:
    return calendar.replace("\r\n ", "").split("\r\n")


def run_in(tmp_path, page=FIXTURE, now=NOW):
    store, ics = tmp_path / "games.json", tmp_path / "cal.ics"
    changed = uc.run(page, now, store, ics, ROOT / "mappings.yaml")
    return changed, store, ics


def test_reads_every_game(games):
    assert len(games) == 48


def test_known_game_with_channel(games):
    game = games["26676-5"]
    assert (game.date, game.time, game.channel) == ("2026-11-08", "20:15", "5PLUS")
    assert game.is_home and game.opponent == "הפועל העמק"


def test_undetermined_channel_is_empty(games):
    assert games["26682-5"].channel is None


def test_blank_channel_is_empty_and_time_not_final(games):
    game = games["26704-5"]
    assert game.channel is None and game.time is None


def test_year_rolls_over_in_january(games):
    assert games["26726-5"].date == "2027-01-03"


def test_finished_game_has_score_and_upcoming_does_not(games):
    assert games["9972-1012"].score == "73:84"
    assert games["26676-5"].score is None


@pytest.mark.parametrize(
    "uid, title",
    [
        ("26811-5", 'מכבי ת"א בארנה'),  # home league, no venue on the site
        ("26662-5", 'הפועל ת"א בדרייב אין | 5SPORT'),  # the example from the request
        ("9972-1012", "אריס סלוניקי בבלגרד | ספורט 1"),  # home EuroCup
        ("9981-1012", "רוסטוק ברוסטוק"),  # away, site wrongly says Belgrade; sponsor dropped
        ("10001-1012", "ריגה זלי בריגה"),  # same site error
        ("9991-1012", "צדביטה אולימפיה בבלגרד"),
        ("26763-5", "הפועל העמק בגן נר"),  # non-breaking space in the site's name
        ("26844-10", 'מכבי ת"א בחולון | 5SPORT'),  # neutral cup venue beats the opponent's home
    ],
)
def test_titles(games, uid, title):
    assert uc.format_title(games[uid], MAPPINGS) == title


def test_unknown_venue_falls_back_to_opponent_only():
    game = uc.Game("1-5", "2026-12-01", None, "ליגה", False, "קבוצה חדשה", "", None, None)
    assert uc.format_title(game, MAPPINGS) == "קבוצה חדשה"


def test_timed_game_uses_israel_time(tmp_path):
    _, _, ics = run_in(tmp_path)
    lines = unfold(ics.read_bytes().decode("utf-8"))
    start = lines.index("UID:game-26676-5@hapoel-jerusalem-calendar")
    event = lines[start:start + 10]
    assert "DTSTART;TZID=Asia/Jerusalem:20261108T201500" in event
    assert "DTEND;TZID=Asia/Jerusalem:20261108T221500" in event


def test_game_without_final_time_is_all_day(tmp_path):
    _, _, ics = run_in(tmp_path)
    lines = unfold(ics.read_bytes().decode("utf-8"))
    start = lines.index("UID:game-26704-5@hapoel-jerusalem-calendar")
    event = lines[start:start + 10]
    assert "DTSTART;VALUE=DATE:20261213" in event
    assert "DTEND;VALUE=DATE:20261214" in event


def test_lines_are_folded_to_75_bytes(tmp_path):
    _, _, ics = run_in(tmp_path)
    for line in ics.read_bytes().split(b"\r\n"):
        assert len(line) <= 75
        line.decode("utf-8")  # a fold must not split a Hebrew character


def test_second_run_changes_nothing(tmp_path):
    assert run_in(tmp_path)[0] is True
    changed, _, _ = run_in(tmp_path, now=datetime(2026, 11, 6, 5, 0, tzinfo=timezone.utc))
    assert changed is False


def test_new_channel_updates_the_same_event(tmp_path):
    _, store, ics = run_in(tmp_path)
    before = ics.read_bytes().decode("utf-8").count("BEGIN:VEVENT")
    # The next game to get a channel, on a later run.
    page = FIXTURE.replace(uc.NO_CHANNEL_TEXT, "5SPORT", 1)
    changed, _, _ = run_in(tmp_path, page, datetime(2026, 11, 6, 5, 0, tzinfo=timezone.utc))
    calendar = unfold(ics.read_bytes().decode("utf-8"))
    assert changed is True
    assert sum(line == "BEGIN:VEVENT" for line in calendar) == before
    assert 'SUMMARY:מכבי ת"א ביד אליהו | 5SPORT' in calendar
    saved = json.loads(store.read_text(encoding="utf-8"))["games"]["26682-5"]
    assert saved["channel"] == "5SPORT" and saved["sequence"] == 1


def test_finished_game_keeps_its_channel_if_site_drops_it(tmp_path):
    _, store, _ = run_in(tmp_path)
    page = FIXTURE.replace('<div class="text ">ספורט3</div>', "", 1)
    run_in(tmp_path, page)
    saved = json.loads(store.read_text(encoding="utf-8"))["games"]["9944-1012"]
    assert saved["channel"] == "ספורט3"


def test_empty_page_keeps_last_good_calendar(tmp_path):
    _, _, ics = run_in(tmp_path)
    before = ics.read_bytes()
    with pytest.raises(uc.ScheduleError):
        run_in(tmp_path, "<html></html>")
    assert ics.read_bytes() == before


def test_page_missing_most_upcoming_games_is_rejected(tmp_path):
    _, _, ics = run_in(tmp_path)
    before = ics.read_bytes()
    parts = FIXTURE.split('<div class="game">')
    with pytest.raises(uc.ScheduleError):
        run_in(tmp_path, '<div class="game">'.join(parts[:20]))
    assert ics.read_bytes() == before


def test_changed_layout_is_rejected():
    page = FIXTURE.replace('class="date-time "', 'class="when"')
    with pytest.raises(uc.ScheduleError):
        uc.parse_schedule(page, 2027)


def test_season_changes_in_august():
    assert uc.current_season(date(2026, 11, 5)) == 2027
    assert uc.current_season(date(2027, 5, 1)) == 2027
    assert uc.current_season(date(2027, 8, 1)) == 2028
