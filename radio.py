"""Team radio: fetch clips for the drivers you follow and turn them into text.

Two things have to go right for a transcript:
  1. F1 has to hand over the clip. Since October 2026 that usually needs an F1 TV
     login (F1TV_TOKEN in config.py), so without one this mostly says "locked".
  2. The free speech model has to be installed:  py -m pip install -r requirements-radio.txt
     It runs on the laptop itself, so nothing is sent anywhere and it costs nothing.
     The first transcript downloads the model (about 150 MB), after that it's quick.
"""
import logging
import os
import queue
import threading
import urllib.error
import urllib.request

import config

log = logging.getLogger("dashboard")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
for noisy in ("httpx", "huggingface_hub", "faster_whisper"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
STATIC = "https://livetiming.formula1.com/static/"

try:
    from faster_whisper import WhisperModel
    HAVE_WHISPER = True
except Exception:
    HAVE_WHISPER = False


def decode(audio):
    """Turn an audio file (mp3, flac, wav...) into the 16 kHz mono samples the speech model
    wants. Done here rather than by faster-whisper itself, because newer versions of the
    PyAV library dropped an option that faster-whisper still passes."""
    import io
    import av
    import numpy as np
    chunks = []
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    with av.open(io.BytesIO(audio), mode="r") as container:
        stream = container.streams.audio[0]
        for frame in container.decode(stream):
            frame.pts = None
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


class Radio:
    def __init__(self, race):
        self.race = race
        self.clips = {}           # clip path -> audio bytes, so Play doesn't fetch twice
        self.model = None
        self.q = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    def available(self):
        return HAVE_WHISPER and getattr(config, "RADIO_TRANSCRIPTS", True)

    def fetch(self, clip):
        """Returns (audio bytes, None) or (None, reason)."""
        if clip in self.clips:
            return self.clips[clip], None
        base = self.race.session().get("Path") or ""
        if not base or ".." in clip or clip.startswith("/"):
            return None, "no clip address"
        headers = {"User-Agent": "race-weekend-dashboard/1.0"}
        token = getattr(config, "F1TV_TOKEN", "").strip()
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            with urllib.request.urlopen(urllib.request.Request(STATIC + base + clip, headers=headers), timeout=15) as r:
                audio = r.read()
        except urllib.error.HTTPError as e:
            return None, "locked" if e.code in (401, 403) else "HTTP %s" % e.code
        except Exception as e:
            return None, str(e)
        self.clips[clip] = audio
        return audio, None

    def __call__(self, event):
        """Called when one of your drivers gets a new radio clip."""
        self.q.put(event)

    def _transcribe(self, audio):
        if self.model is None:
            name = getattr(config, "WHISPER_MODEL", "base.en")
            log.info("Loading the speech model (%s). The first time, this downloads it.", name)
            self.model = WhisperModel(name, device="cpu", compute_type="int8")
        segments, _ = self.model.transcribe(decode(audio), language="en", vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()

    def _worker(self):
        while True:
            event = self.q.get()
            audio, why = self.fetch(event.get("audio", ""))
            if audio is None:
                event["radioNote"] = "Locked, needs F1 TV" if why == "locked" else "Clip not available"
                log.info("Team radio clip not available: %s", why)
                continue
            if not self.available():
                continue
            try:
                text = self._transcribe(audio)
                event["transcript"] = text or "(nothing clear on the clip)"
            except Exception as e:
                log.warning("Couldn't transcribe radio clip: %s", e)
