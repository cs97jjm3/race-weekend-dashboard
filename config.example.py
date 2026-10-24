# Ed's Race Weekend Dashboard settings.
# This is the template. Your own copy is config.py, which the dashboard makes for you the
# first time it runs. Change config.py, not this file: config.py stays private (it's kept
# out of git), so your API key and codes never get shared.
# Change these to suit, then restart.

# Who gets told about what. Teams are matched loosely ("Ferrari" matches
# "Scuderia Ferrari"), drivers by their three-letter code.
# Everyone also hears about the big stuff: safety cars, red flags,
# a new race leader, and the start and finish.
PEOPLE = {
    "James": {
        "ntfy_topic": "",          # e.g. "james-raceweekend-7f3k9" (leave empty for no phone alerts)
        "teams": ["Ferrari"],
        "drivers": ["ANT", "VER"],
        "colour": "#E8002D",
    },
    "Ed": {
        "ntfy_topic": "",          # e.g. "ed-raceweekend-q8m2x"
        "teams": ["Audi"],
        "drivers": ["ANT", "VER"],
        "colour": "#00B140",
    },
}

# Web page port on the laptop: open http://localhost:8080
WEB_PORT = 8080

# Sharing with your own web address (optional). Leave blank to get a free
# throwaway trycloudflare.com link each time instead.
# Set up once with cloudflared (see "Your own web address" in the README), then e.g.:
#   SHARE_HOSTNAME = "race.example.co.uk"
SHARE_HOSTNAME = ""
SHARE_TUNNEL = "race-dashboard"

# Roughly how many seconds a pit stop costs at each track, used to guess
# where a driver will rejoin. These are approximate; tweak them if the
# guesses come out consistently early or late.
PIT_LOSS_SECONDS = {
    "Austin": 20,
    "Mexico City": 22,
    "São Paulo": 21,
    "Sao Paulo": 21,
    "Las Vegas": 21,
    "Lusail": 26,
    "Yas Island": 22,
    "Yas Marina": 22,
}
DEFAULT_PIT_LOSS = 22

# A watched driver within this many seconds of the car ahead counts as a battle
BATTLE_GAP = 1.0

# Don't send the same kind of alert about the same driver more often than this
ALERT_COOLDOWN_SECONDS = 90

# Optional AI comments, "What's happening?" reads and race debriefs.
# Leave the keys empty to switch it off (everything still works, with plain written alerts).
# Pick which AI with AI_PROVIDER: "claude" (default), "gemini", or "openai" for anything
# that speaks the OpenAI style (for example a FreeLLMAPI server on your own laptop).
AI_PROVIDER = "claude"
# A back-up to try if the first one fails or is busy (it needs its own key below), e.g. "claude".
AI_FALLBACK = ""

# Claude: a key from console.anthropic.com. About a tenth of a penny per comment.
ANTHROPIC_API_KEY = ""
AI_MODEL = "claude-haiku-5-5"

# Gemini: a key from aistudio.google.com/apikey (starts AQ. or AIza). Google offers a free tier in
# some countries; on the free tier Google may use what's sent to improve its products.
GEMINI_API_KEY = ""
GEMINI_MODEL = "gemini-3.8-flash"

# Anything OpenAI-style: the address, a key if it needs one, and the model name.
OPENAI_BASE_URL = ""          # e.g. "http://localhost:3001/v1"
OPENAI_API_KEY = ""
OPENAI_MODEL = "auto"

# Size of the circle drawn round the circuit on the weather radar, in miles
WEATHER_RADIUS_MILES = 5

# Where each circuit is (latitude, longitude), for the weather radar and forecast.
# Matched loosely against the meeting's location name.
CIRCUIT_COORDS = {
    "Melbourne": (-37.8497, 144.9680), "Shanghai": (31.3389, 121.2197), "Suzuka": (34.8431, 136.5407),
    "Sakhir": (26.0325, 50.5106), "Jeddah": (21.6319, 39.1044), "Miami": (25.9581, -80.2389),
    "Montr": (45.5000, -73.5228), "Monaco": (43.7347, 7.4206), "Monte Carlo": (43.7347, 7.4206),
    "Barcelona": (41.5700, 2.2611), "Spielberg": (47.2197, 14.7647), "Silverstone": (52.0786, -1.0169),
    "Spa": (50.4372, 5.9714), "Budapest": (47.5789, 19.2486), "Hungaroring": (47.5789, 19.2486),
    "Zandvoort": (52.3888, 4.5409), "Monza": (45.6156, 9.2811), "Madrid": (40.4637, -3.6157),
    "Baku": (40.3725, 49.8533), "Singapore": (1.2914, 103.8640), "Austin": (30.1328, -97.6411),
    "Mexico City": (19.4042, -99.0907), "Sao Paulo": (-23.7036, -46.6997), "São Paulo": (-23.7036, -46.6997),
    "Las Vegas": (36.1147, -115.1728), "Lusail": (25.4900, 51.4542), "Yas": (24.4672, 54.6031),
}

# Turn team radio into text, if the clip can be had and the speech model is installed
# (py -m pip install -r requirements-radio.txt). "base.en" is a good balance of speed
# and accuracy on a laptop; "small.en" is more accurate but slower.
RADIO_TRANSCRIPTS = True
WHISPER_MODEL = "base.en"

# For later: an F1 TV login token, which unlocks the locked parts of F1's timing
# (team radio clips, the archive). Leave empty for now.
F1TV_TOKEN = ""
