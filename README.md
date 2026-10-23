# Ed's Race Weekend Dashboard

## Quick start (no computer skills needed)

1. Double-click **1 Set up**. It installs everything the dashboard needs. If it installs
   Python for you, close the window when it says so and double-click **1 Set up** again.
2. Double-click **Try the demo** to see a made-up race play out in your web browser.
3. On race weekends, double-click **2 Start dashboard** a few minutes before the session.
   Leave the black window open while you watch; close it when you've finished.
4. To let family outside the house watch, double-click **Share with family** instead, and
   send them the link and six-number code it shows.

To make it yours, open **config.py** in Notepad (it appears after the first run) and change
the names, teams and drivers under PEOPLE. Your config.py is private and never shared.

---

A live race screen and phone alerts for James and Ed, using F1's own free live timing.

## For the technically minded: setting it up by hand

1. Unzip the `race-weekend-dashboard` folder somewhere handy, for example `C:\race-weekend-dashboard`.
2. Open PowerShell in that folder and install the two extras it needs:

   ```
   py -m pip install -r requirements.txt
   ```

3. Phone alerts (optional, free): install the **ntfy** app on each phone, tap "+" and
   subscribe to a topic name. Pick something long and hard to guess, as anyone who knows
   the name can read it, e.g. `james-raceweekend-7f3k9`. Put each name into `config.py`
   next to `ntfy_topic` for James and Ed.

## Trying it out

```
py dashboard.py --demo
```

Your browser opens the dashboard at http://localhost:8080 and a made-up race plays through
in under two minutes, with a pit stop, a safety car, a retirement, a penalty and a lead change,
so you can see the screen and alerts working.

F1 locked its archive of past sessions in mid October 2026 (it now needs an F1 TV login),
so replaying real past races from F1 no longer works. Instead, the dashboard records every
live session to the `recordings` folder as it runs. Replay any of those with:

```
py dashboard.py --folder recordings\2026-10-25_1955 --speed 20
```

Add `--from-lap 30` to jump ahead.

## On race weekends

Start it a few minutes before the session:

```
py dashboard.py
```

It connects to F1's live timing and fills in once the session is running. Each session
is saved in the `recordings` folder so you can replay it later (add `--no-record` to skip that). Leave it going
for the whole session; it reconnects by itself if the connection drops. Ctrl+C stops it.

Other screens in the house can watch too: open `http://<laptop's IP address>:8080` on
the same Wi-Fi.

## Sharing with someone outside the house

Install Cloudflare's free tunnel program once:

```
winget install --id Cloudflare.cloudflared
```

Close PowerShell and open it again, then start the dashboard with:

```
py dashboard.py --share
```

After a few seconds it prints a link (four random words ending in trycloudflare.com) and a
six-number passcode, and shows both at the top of your screen with a "Copy for Dad" button.
Send him both. He opens the link on his phone, tablet or computer, types the code once, and
he's in. Both change every time you start with --share, so each race gets a fresh pair. To
choose the code yourself: `py dashboard.py --share 271024`.

Nothing is opened on your router: the tunnel makes an outgoing, encrypted connection to
Cloudflare. Anyone at home on your Wi-Fi still gets straight in without a code. Five wrong
codes from the same place locks it out for ten minutes. People watching from outside can't
switch the AI comments off, and they don't see the link or code themselves. Cloudflare says
quick tunnels are meant for testing and come with no uptime guarantee; if the tunnel drops,
the dashboard starts a new one, which means a new link to send.

### Your own web address (optional)

If you have a domain on Cloudflare, the link can be the same every race (for example `race.example.co.uk`) instead of a new trycloudflare.com one. Set it up once in a command window:

```
cloudflared tunnel login
cloudflared tunnel create race-dashboard
cloudflared tunnel route dns race-dashboard race.example.co.uk
```

The first command opens a browser: log in to Cloudflare and pick your domain. Then put the address in config.py:

```
SHARE_HOSTNAME = "race.example.co.uk"
SHARE_TUNNEL = "race-dashboard"
```

From then on `py dashboard.py --share` uses that address. The passcode still changes each time, unless you give one, e.g. `py dashboard.py --share 271024`.

## Qualifying

In qualifying the timing tower shows each driver's best time in the current part, the gap to
P1, and how far they are from the cut. A red dashed line marks the drop zone, the cars below
it are tinted red, and drivers already knocked out are greyed with "out in Q1" or "out in Q2".
With 22 cars, six drop out in Q1 and six more in Q2, then the top ten fight for pole.

Your drivers get alerts when they fall into the drop zone ("Hulkenberg is in the drop zone,
P17, 0.192 off P16"), when they climb out of it, when they're knocked out, and when they make
it through to the next part. Try it with:

```
py dashboard.py --demo quali
```

## Sectors and the middle panel

Every row on the timing tower has three little sector blocks: purple for the fastest anyone
has done, green for a driver's own best, yellow for slower. The middle of the screen has tabs:

Map shows the circuit with the cars on it. Tyres shows each driver's stints as coloured bars,
one per set of tyres, so you can see who's still got to stop. Gaps is a line chart of how far
your drivers are behind the car in front, lap by lap, with the "within a second" zone shaded
so you can see who's catching. Speeds ranks everyone by their best speed trap reading.
Standings shows the championship as it would be if the race finished now, with the points
each driver is earning in green, and the team table alongside.

Standings come from the free Jolpica results service. If it can't be reached, the tab just
says so and everything else carries on.

## Predictions game

Click Predictions at the top before a race or sprint. You and Ed each pick a podium. It locks
at lights out and is scored at the chequered flag: 5 points for each driver in exactly the
right place, 2 if they're on the podium but in a different place, so 15 is a perfect score.
Season totals are saved in predictions.json in the dashboard folder. Replays and the demo can
be played but never count towards the season.

## Race debrief

If the AI comments are on, a few seconds after the chequered flag Claude writes a short debrief
of the race from your and Ed's side, using what happened in the feed and the final order. It
appears at the top of the feed with a Copy button, ready to paste into a message or a post.
After the race, the "What's happening?" button becomes "Race debrief" so you can ask for
another one. It costs well under a penny.

## Telly mode and sound

Telly mode (top right, or press T) makes everything bigger and hides the buttons, for
watching from the sofa. Press Esc or T to leave it.

Sound on makes the dashboard beep for a safety car, a virtual safety car, a red flag and the
chequered flag. Browsers only allow sound after you've clicked something on the page, so
click Sound on at the start of the session.

## The track map

The middle of the screen shows the circuit with every car on it. F1 keeps live GPS for
F1 TV subscribers, so the positions are worked out from timing: when each car last
crossed the line and how long its last lap took. It's right about who's near whom and
how spread out the field is, but a car can be drawn a corner or so out, especially
behind the safety car. Your drivers get a ring in your colour and their name.

Circuit outlines come from MultiViewer's free circuit data (the same source FastF1
uses). Each track is downloaded the first time it's needed and kept in the `tracks`
folder. If one can't be had, the map falls back to a plain loop.

## Flags on the map

When race control puts out a yellow in a marshal sector, that stretch of track lights up
yellow on the map (orange for a double yellow) until it's cleared. The whole circuit turns
amber behind a safety car or VSC, and red for a red flag.

## Team radio

When one of your drivers gets a radio message, it shows in the feed with a Play button.
Since F1 locked its archive in October 2026, the clips usually need an F1 TV login, so
expect "Locked, needs F1 TV" for now. If a clip does come through and you've installed the
free speech model, the words appear under the alert:

```
py -m pip install -r requirements-radio.txt
py dashboard.py --test-radio
```

The test transcribes a short public domain clip so you know it works. It runs on the laptop
itself, costs nothing, and downloads the model (about 150 MB) the first time.

## Hiding parts of the screen

Use the Show switches (Timings, Map, Feed) to hide any part you don't want, for example
just the map and the feed on the telly. The rest spreads out to fill the space, and it
remembers your choice next time.

## Weather

The bar along the top shows dry or rain, air and track temperature, humidity and wind,
straight from F1's timing. Click it to open the weather window: a rain radar map with a
5-mile circle round the circuit (change WEATHER_RADIUS_MILES in config.py) (radar from RainViewer, background map from Esri, playing the last two hours), the
conditions at the track now, and Open-Meteo's forecast for rain at the circuit over the
next three hours in 15-minute steps. Radar detail is coarse that close in, so treat the
forecast as the better guide. Circuit locations are in `config.py` under CIRCUIT_COORDS.

The little light in the top right shows LIVE when data is flowing, QUIET or NO DATA if
it stops, and REPLAY when you're playing something back.

## Changing who you follow

Everything is in `config.py`: the teams and drivers each person follows, the ntfy topics,
how close counts as a battle, and how long a pit stop costs at each track (used to guess
where a driver will rejoin).

## What you get alerted about

Everyone: lights out, safety car, VSC, red flag, back to green, a new race leader, and the
chequered flag with how your drivers finished.

Your own drivers: pit stops with a guess at where they'll rejoin, overtakes, battles within
a second, fastest laps, penalties and investigations, and retirements.

## Good to know

Live car telemetry and GPS are F1 TV only, so the live screen uses timing, gaps, tyres,
pit stops, flags and race control messages, which are all free. The full telemetry becomes
free in the archive about half an hour after each session.

This uses unofficial access to F1's timing feed, the same one FastF1 uses. If F1 changes
something, it may need a fix.

## AI comments (optional)

Put your Claude API key between the quotes after `ANTHROPIC_API_KEY` in `config.py`, then check it with:

```
py dashboard.py --test-ai
```

With a key, pit stops for your drivers, safety cars, red flags and the finish get a one-line
comment from Claude, shown in grey under the alert. It uses the small Haiku model and costs
a fraction of a penny per race. Once there's a key, two AI controls appear on the screen. "Comments on/off" switches the AI
comments off (for the screen and the phone alerts) without touching config.py. "What's
happening?" asks Claude for a quick two or three sentence read of the race right now; there's
a 20 second gap between presses so a double tap doesn't cost twice. Each read is a fraction
of a penny.

Keep the key private, like a password, and never put
config.py on GitHub with the key in it.
