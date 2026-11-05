# Hapoel Jerusalem basketball calendar (not official)

A calendar of all Hapoel Jerusalem basketball games (not official), with the TV channel in each title, for example `מכבי ת"א בארנה | 5PLUS`.

The data comes from the [club's schedule](https://www.hapoel.co.il/games.asp?cYear=2027). A GitHub job checks it twice a day and updates the calendar when something changes.

## Subscribe

Calendar link: `https://eliahj.github.io/hapoel-jerusalem-calendar/hapoel-jerusalem.ics`

- **Google Calendar:** on a computer, open [Add from URL](https://calendar.google.com/calendar/u/0/r/settings/addbyurl), paste the link and choose **Add calendar**. To see it on your phone, turn it on in the Google Calendar app's settings.
- **iPhone:** go to Settings > Apps > Calendar > Calendar Accounts > Add Account > Other > Add Subscribed Calendar, then paste the link.

Google refreshes subscribed calendars on its own schedule, often taking 12–24 hours or more. It also ignores reminders set inside the calendar, so set a default notification for this calendar in Google Calendar's settings.

## What the events show

- **`בחוץ` in the title:** an away EuroCup game, for example `דרטונה בחוץ`. The city and arena are in the event details.
- **No channel in the title:** the club hasn't published one yet.
- **All-day event:** the start time isn't final yet.
- **Event details:** competition, home or away, venue, channel, result and last update.

## Change names or locations

Edit [mappings.yaml](mappings.yaml). The next run applies the change. To apply it right away, start **Update calendar** from the repository's Actions tab.

If a location is missing from a title, the job log shows a warning naming the venue to add.

## Run it locally

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -r requirements.txt
```

```bash
.venv/Scripts/python -m pytest -q
```

```bash
.venv/Scripts/python update_calendar.py
```

## If an update fails

If the site can't be read, or its page looks wrong, the job fails and leaves the last good calendar in place. GitHub can email you about failed runs.
