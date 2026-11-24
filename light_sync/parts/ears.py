"""Hearing: beats and loudness (Ear), and the sound arriving from the Pi or the phone."""

import numpy as np
import os
import threading
import time
from collections import deque

from parts.config import (
    AC_CONF, AC_PRIOR, AC_WINDOW, BIG_HIT, BLOCK, BROAD_K, LOCAL_HOLD, LOCAL_PLAYING, MIC_GATE_X, MIC_KEEP_SHARE, MIC_MIN_GATE, MIC_MUSIC_SHARE, PHONE_HOLD, RATE,
    ROLE, ROOM_HOLD,
)
from parts import config, pi_backup, soundtest, web


class Ear:
    def __init__(self):
        freqs = np.fft.rfftfreq(BLOCK, 1 / RATE)
        self.bass_bins = (freqs >= 40) & (freqs <= 160)  # kick drum / bass range
        # for the show visuals: bass / mids / highs levels (0-1) and a 32-band log spectrum
        self.band_bins = [(freqs >= 40) & (freqs < 160), (freqs >= 160) & (freqs < 2000), (freqs >= 2000) & (freqs < 8000)]
        self.band_avg = [1e-6, 1e-6, 1e-6]
        self.bands = [0.0, 0.0, 0.0]
        edges = np.geomspace(40, 8000, 33)
        self.spec_idx = [np.where((freqs >= lo) & (freqs < hi))[0] if ((freqs >= lo) & (freqs < hi)).any()
                         else np.array([int(np.argmin(np.abs(freqs - (lo + hi) / 2)))]) for lo, hi in zip(edges[:-1], edges[1:])]
        self.spectrum = np.zeros(32)
        self.spec_peak = 1e-6
        self.window = np.hanning(BLOCK)
        self.bass_avg = 1e-6
        self.vol_avg = 1e-6
        self.last_beat = 0.0
        self._beat = False
        self.energy = 0.0
        self.beat_times = []
        self._big = False
        self.beat_strength = []
        self.lock = threading.Lock()
        self.phone_seen = 0.0  # last sound from a phone (Light Sync app "Listen")
        self.room_mode = None  # "auto" (laptop sound, else the room) / "only": room sound from the Pi's camera mic
        self.room_seen = 0.0   # last room sound from the Pi
        self.local_at = 0.0    # Auto: last time the laptop played something
        self.gate = 0.003      # below this level = silence (a mic's room noise); laptop sound has no noise: lower
        self.anchor = 0.0      # latest beat on the tempo grid (see period())
        self.grid_r = 0.0      # how well the recent beats sit on one grid (0-1): beat steadiness
        self.onset = False     # beat = a sudden rise of the bass (adaptive), instead of bass louder than its average
        self.prev_bass = 0.0
        self.flux_avg = 1e-6
        self.flux_var = 1e-12
        # "broad" beats for microphones (Oct 19, owner: music from the phone while the laptop is off). A phone
        # speaker can't play the kick's bass, and across the room the bass detector + beat grid locked 2-53 % of the
        # time. Instead: onset curves (sudden rises of the sound); the tempo from the 150-5000 Hz curve's
        # autocorrelation over 8 s; the beat position from that curve + the bass curve together (hi-hats alone sit
        # between the kicks). Tests: scratch groove / room-mic simulations, see docs/light-sync.md.
        self.detector = "bass"  # "broad": every block is mic sound; room / phone sound is always broad
        self.broad_bins = (freqs >= 150) & (freqs <= 5000)
        self.low_bins = self.bass_bins  # the second curve: the bass (40-160 Hz), where a speaker plays it
        self.broad_prev = self.low_prev = None
        self.broad_peak = self.low_peak = 1e-6
        n = int(AC_WINDOW * RATE / BLOCK)
        self.odf_b, self.odf_l, self.odf_t = deque(maxlen=n), deque(maxlen=n), deque(maxlen=n)
        self.loud_hist = deque(maxlen=n)  # which recent blocks were above the silence level
        self.loud_2s = deque(maxlen=int(2 * RATE / BLOCK))  # the same, last 2 s: is this music, or a noise in a quiet room?
        self.use_broad = False
        self.loud_at = 0.0     # last block above the silence gate
        self.rms = self.room_rms = 0.0  # sound levels (~1 s average): what's listened to, the room mic's
        self.noise = 0.002     # a mic's background noise (the quietest recent blocks): its silence level
        self._ac, self._ac_at = None, 0.0  # broad: (period, latest beat time) or None, and when worked out

    def phone_active(self):
        return time.time() - self.phone_seen < PHONE_HOLD

    def room_active(self):
        return time.time() - self.room_seen < ROOM_HOLD

    def local_playing(self):
        return time.time() - self.local_at < LOCAL_HOLD

    def using_room(self):
        """The room's sound (camera mic, sent by the Pi) is the ears: "only", or Auto while the laptop is quiet."""
        return bool(self.room_mode) and self.room_active() and (self.room_mode == "only" or not self.local_playing())

    def process(self, samples, now=None, phone=False, room=False):
        if phone:
            self.phone_seen = time.time()
        elif self.phone_active():
            return  # a phone is listening: it is the ears until it stops
        elif room:
            self.room_seen = time.time()
            self.room_rms += (float(np.sqrt(np.mean(np.square(samples)))) - self.room_rms) * 0.05
            if not self.using_room():
                return  # Auto and the laptop is playing: its own sound is cleaner than a mic across the room
        elif self.room_mode:
            if float(np.sqrt(np.mean(np.square(samples)))) > LOCAL_PLAYING:
                self.local_at = time.time()
            if self.using_room():
                return  # the laptop is quiet: the room is the ears (music from the phone, the TV...)
        now = time.time() if now is None else now
        x = samples.astype(np.float64)
        spec = np.abs(np.fft.rfft(x * self.window))
        bass = spec[self.bass_bins].mean()
        vol = float(np.sqrt(np.mean(x ** 2)))
        self.rms += (vol - self.rms) * 0.05  # ~1 s average, for the analysis
        if room or phone or self.detector == "broad":
            # a mic: silence = a little above the room's own noise (the quietest blocks; rises ~2 %/s so it follows
            # a noisier room). The fixed 0.003 cut a phone 30 cm from the MeetUp (0.002-0.005) half the time (Oct 20)
            self.noise = vol if vol < self.noise else self.noise * 1.0005
            gate = max(MIC_MIN_GATE, MIC_GATE_X * self.noise)  # no upper cap: a humming room stays silence (Oct 20 review)
        else:
            gate = self.gate
        loud_enough = vol > gate  # ignore silence
        if self.onset:
            # a kick = the bass jumps up much more than it usually moves: flux above its mean + k standard
            # deviations (k from the dashboard sensitivity: 1.1 -> 0.2, 1.4 -> 0.8, 2.0 -> 2.0)
            flux = max(0.0, bass - self.prev_bass)
            k = max(0.0, (config.SENSITIVITY - 1.0) * 2.0)
            is_beat = (loud_enough and flux > self.flux_avg + k * float(np.sqrt(self.flux_var))
                       and bass > 0.7 * self.bass_avg and now - self.last_beat > 0.25)
            self.flux_avg = 0.95 * self.flux_avg + 0.05 * flux
            self.flux_var = 0.95 * self.flux_var + 0.05 * (flux - self.flux_avg) ** 2
        else:
            is_beat = (loud_enough and bass > config.SENSITIVITY * self.bass_avg
                       and now - self.last_beat > 0.25)
        # onset curves: log spectral flux (how much the sound suddenly rose) over 150-5000 Hz and 40-160 Hz
        b, lm = spec[self.broad_bins], spec[self.low_bins]
        self.broad_peak = max(0.9995 * self.broad_peak, float(b.max()), 1e-9)
        self.low_peak = max(0.9995 * self.low_peak, float(lm.max()), 1e-9)
        lb, ll = np.log1p(100 * b / self.broad_peak), np.log1p(100 * lm / self.low_peak)
        odf = float(np.maximum(0.0, lb - self.broad_prev).mean()) if self.broad_prev is not None else 0.0
        odf_l = float(np.maximum(0.0, ll - self.low_prev).mean()) if self.low_prev is not None else 0.0
        self.broad_prev, self.low_prev = lb, ll
        hist = list(self.odf_b)[-64:]  # ~1.5 s
        self.odf_b.append(odf)
        self.odf_l.append(odf_l)
        self.odf_t.append(now)
        self.loud_hist.append(loud_enough)
        if loud_enough:
            self.loud_at = now
        self.use_broad = room or phone or self.detector == "broad"
        if self.use_broad:
            thr = float(np.mean(hist) + BROAD_K * np.std(hist)) if len(hist) >= 16 else 1e9
            is_beat = loud_enough and odf > thr and now - self.last_beat > 0.25
            if is_beat and self._ac and now - self._ac_at < 1.0:  # tempo known: only onsets on the beat grid
                per, anchor = self._ac                              # (not the hi-hats between the kicks)
                is_beat = abs(((now - anchor) / per + 0.5) % 1.0 - 0.5) < 0.2
        # a mic in a quiet room: a door, a cough, the fridge is not music. With no sound lasting and no tempo locked it
        # moves nothing: energy is measured against the last 10 s, so in silence a tiny noise read as 70 % (Oct 30, owner's
        # video: no music, BPM jumping 37-79, Corner 41-75 %). A locked tempo needs it too, or noise moves the lights after the music ends.
        # The checks above (the lock) still see the real levels.
        self.loud_2s.append(loud_enough)
        if self.use_broad and sum(self.loud_2s) < (MIC_MUSIC_SHARE if self._ac is None else MIC_KEEP_SHARE) * self.loud_2s.maxlen:
            loud_enough = is_beat = False
        self.prev_bass = bass
        self.bass_avg = 0.96 * self.bass_avg + 0.04 * bass   # ~0.5 s memory
        self.vol_avg = 0.9975 * self.vol_avg + 0.0025 * vol   # ~10 s memory
        # show visuals: each band relative to its own recent level (fast rise, slow fall), auto-gained spectrum
        for i, bins in enumerate(self.band_bins):
            lvl = spec[bins].mean()
            self.band_avg[i] = 0.995 * self.band_avg[i] + 0.005 * lvl
            v = min(1.0, lvl / (2.5 * self.band_avg[i] + 1e-9)) if loud_enough else 0.0
            self.bands[i] += (v - self.bands[i]) * (0.6 if v > self.bands[i] else 0.12)
        s = np.array([spec[ix].mean() for ix in self.spec_idx])
        self.spec_peak = max(0.999 * self.spec_peak, float(s.max()), 1e-6)
        self.spectrum += ((np.sqrt(s / self.spec_peak) - self.spectrum) * 0.5) if loud_enough else (-self.spectrum * 0.2)
        with self.lock:
            if is_beat:
                self.last_beat = now
                self._beat = True
                # big hit = much harder than the recent kicks (drops, accents)
                recent = self.beat_strength[-8:]
                strength = odf if self.use_broad else bass  # broad: no bass to compare
                if len(recent) >= 4 and strength > BIG_HIT * float(np.median(recent)):
                    self._big = True
                self.beat_strength = (self.beat_strength + [strength])[-16:]
                self.beat_times = (self.beat_times + [now])[-12:]
            # a mic with the tempo locked: a quiet moment in the music (under the silence level) still counts as music
            hold = (room or phone or self.detector == "broad") and self._ac is not None and now - self.loud_at < 0.5
            self.energy = min(1.0, vol / (2 * self.vol_avg + 1e-9)) if loud_enough or hold else 0.0

    def callback(self, indata, frames, t, status):
        self.process(indata[:, 0])

    def bpm(self, now=None):
        now = time.time() if now is None else now
        per = self.period(now)
        if per:
            return int(round(60 / per))
        with self.lock:
            t = self.beat_times
        if len(t) < 4 or now - t[-1] > 3:
            return 0
        gaps = np.diff(t)
        return int(round(60 / float(np.median(gaps))))

    CANDIDATES = np.arange(0.4, 1.0, 0.004)  # beat periods tried: 60-150 BPM (faster music locks at half tempo)
    GRID_MIN = 0.45  # how well the recent beats must sit on the grid to call the tempo steady (tools/tests/beat_synth.py)

    def period(self, now=None):
        """Beat period in seconds once the tempo is steady, else None. Finds the beat grid most recent beats sit on
        (phase alignment), so one extra off-beat or a missed kick doesn't break the lock. self.anchor = the latest
        beat on that grid, for predicting the next beats."""
        now = time.time() if now is None else now
        if self.use_broad:
            return self.broad_period(now)
        with self.lock:
            t = list(self.beat_times)
        if len(t) < 5 or now - t[-1] > 2.0:
            return None
        key = (len(t), t[-1])
        if getattr(self, "_grid_key", None) != key:
            self._grid_key = key
            self._grid = self._fit_grid(np.array(t[-12:]))
        if not self._grid:
            return None
        self.anchor = self._grid[1]
        return self._grid[0]

    def broad_period(self, now):
        """Mic sound: the tempo from the onset curve's autocorrelation over the last AC_WINDOW s (60-180 BPM, a
        gentle preference for ~120 so it doesn't jump between half and double), the latest beat from the low-mid
        curve's comb (every period back). Worked out at most 4 times a second."""
        if now - self._ac_at < 0.25:
            return self._ac[0] if self._ac else None
        self._ac_at, self._ac = now, None
        x = np.array(self.odf_b)
        if len(x) < self.odf_b.maxlen // 2 or now - self.loud_at > 2.0 or sum(self.loud_hist) < 0.3 * len(self.loud_hist):
            self.grid_r = 0.0
            return None  # under half the window heard yet, silent, or only room noise (a fan, a clock): no tempo
        fps = RATE / BLOCK
        n = len(x)
        frames, conf = self._ac_tempo(x)
        if conf < AC_CONF:  # no clear rhythm in 150-5000 Hz (a bass-only song): the bass curve (Oct 20 self-test)
            frames_b, conf_b = self._ac_tempo(np.array(self.odf_l))
            if conf_b > conf:
                frames, conf = frames_b, conf_b
        self.grid_r = min(1.0, max(0.0, conf * 2))  # beat steadiness, for the analysis
        if conf < AC_CONF or frames is None or not 1 <= frames < n:  # (and never a comb longer than the window)
            return None
        z = lambda v: (v - v.mean()) / (v.std() + 1e-12)  # noqa: E731
        lm, ts = z(np.array(self.odf_b)) + z(np.array(self.odf_l)), list(self.odf_t)
        best, back = -1e9, 0
        for o in range(int(round(frames))):  # the latest beat: the offset whose comb catches the most onsets
            idx = np.round(n - 1 - o - np.arange(int((n - 1 - o) / frames) + 1) * frames).astype(int)
            s = float(lm[idx[idx >= 0]].mean())
            if s > best:
                best, back = s, o
        self._ac = (frames / fps, ts[n - 1 - back])
        self.anchor = self._ac[1]
        return self._ac[0]

    @staticmethod
    def _ac_tempo(x):
        """(beat period in blocks, how clear the rhythm is 0-1) from an onset curve's autocorrelation, 60-180 BPM
        with a gentle preference for ~120; (None, clarity) when there's no usable peak."""
        fps = RATE / BLOCK
        x = x - x.mean()
        n = len(x)
        f = np.fft.rfft(x, 2 * n)
        ac = np.fft.irfft(f * np.conj(f))[:n]
        if ac[0] <= 0:
            return None, 0.0
        bpm = 60 * fps / np.maximum(np.arange(n), 1)
        score = np.where((bpm >= 60) & (bpm <= 180), ac * np.exp(-0.5 * (np.log2(bpm / 120) / AC_PRIOR) ** 2), -np.inf)
        L = int(np.argmax(score))
        conf = float(ac[L] / ac[0])
        if not 1 <= L < n - 1:
            return None, conf
        a, b, c = ac[L - 1], ac[L], ac[L + 1]
        # between two lags, only on a real peak and at most half a lag away: the prior can pick a lag where the curve
        # is nearly flat, and dividing by that flatness gave a period of billions of blocks (Oct 20, 15:19: the main
        # loop then spun in broad_period's comb, one core at 100 %, lights frozen ~13 min)
        curve = a - 2 * b + c
        return L + (float(np.clip(0.5 * (a - c) / curve, -0.5, 0.5)) if curve < 0 else 0.0), conf

    def _fit_grid(self, t):
        rel = t - t[-1]
        z = np.exp(2j * np.pi * rel[None, :] / self.CANDIDATES[:, None])  # each beat's phase on each grid
        R = np.abs(z.mean(axis=1))                                         # 1 = every beat on that grid
        best = float(R.max())
        self.grid_r = best                                                 # beat steadiness, for the analysis
        if best < self.GRID_MIN:
            return None
        # beats on a grid also sit on its halves and thirds: of the peaks that fit almost as well, take the longest
        # period (the top of its peak, not the edge, so the tempo isn't read slow)
        peaks = [j for j in range(1, len(R) - 1) if R[j] >= R[j - 1] and R[j] >= R[j + 1] and R[j] >= 0.85 * best]
        i = max(peaks) if peaks else int(np.argmax(R))
        p = float(self.CANDIDATES[i])
        mean_phase = np.angle(z[i].mean()) / (2 * np.pi)                   # grid offset, in periods
        diff = (rel / p - mean_phase + 0.5) % 1.0 - 0.5                    # each beat's distance from the grid
        on = np.nonzero(np.abs(diff) < 0.15)[0]
        if len(on) < self.GRID_MIN * len(t):
            return None
        k = on[-1]
        return p, float(t[k] - diff[k] * p)                                # latest on-grid beat, snapped to the grid

    def pop_big(self):
        with self.lock:
            b, self._big = self._big, False
            return b

    def pop_beat(self):
        with self.lock:
            b, self._beat = self._beat, False
            return b

def stream_audio_loop(ear, url):
    """Read a network audio stream (e.g. the MeetUp mic via go2rtc) through ffmpeg."""
    import subprocess
    cmd = ["ffmpeg", "-loglevel", "error", "-fflags", "nobuffer", "-flags", "low_delay",
           "-rtsp_transport", "tcp", "-i", url, "-vn", "-ac", "1", "-ar", str(RATE),
           "-f", "s16le", "-"]
    while True:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        nbytes = BLOCK * 2
        while True:
            chunk = proc.stdout.read(nbytes)
            if len(chunk) < nbytes:
                break
            ear.process(np.frombuffer(chunk, np.int16) / 32768.0)
        proc.kill()
        print("audio stream dropped, reconnecting...", flush=True)
        time.sleep(2)

class PulseMic:
    """Pi add-on: the MeetUp mic through Home Assistant's audio system (PulseAudio), read with parec.
    Only opened while the music matters (Music sync on, or Gaming with Music in games), never recorded."""

    def __init__(self, ear):
        self.ear, self.running, self.proc = ear, True, None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        if self.ear:
            self.ear.detector = "broad"  # the MeetUp mic across the room: mic beats (phone speaker, TV, speaker)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.proc:
            self.proc.kill()

    def close(self):
        self.thread.join(timeout=2)
        if self.ear:
            self.ear.energy = 0.0

    def run(self):
        import subprocess
        cmd = ["parec", "--format=s16le", f"--rate={RATE}", "--channels=1", "--latency-msec=20"]
        if os.getenv("PULSE_SOURCE"):
            cmd.append(f"--device={os.getenv('PULSE_SOURCE')}")
        nbytes = BLOCK * 2
        while self.running:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            while self.running:
                chunk = self.proc.stdout.read(nbytes)
                if len(chunk) < nbytes:
                    break
                self.feed(chunk)
            self.proc.kill()
            if self.running:
                print("Pi mic dropped, reopening...", flush=True)
                time.sleep(2)

    def feed(self, chunk):
        self.ear.process(np.frombuffer(chunk, np.int16) / 32768.0)

class RoomRelay(PulseMic):
    """Pi add-on on standby: sends the room's sound (MeetUp mic) to the laptop while the laptop asks for it (its
    heartbeat says "room_mic": Auto or Room mic picked there, and the beat matters). ~0.1 s bursts to the laptop's
    POST /room, each stamped with when it was heard on the laptop's clock (offset worked out from the laptop's
    answers, like the phone does). Home network only, never recorded."""

    BURST = 4  # blocks per post (~93 ms)

    def __init__(self, url):
        super().__init__(None)
        self.url, self.offset, self.best_rtt = url, None, 1e9
        self.buf, self.t0, self.q = [], 0.0, []
        self.cv = threading.Condition()
        threading.Thread(target=self.sender, daemon=True).start()

    def stop(self):
        super().stop()
        with self.cv:
            self.cv.notify()

    def feed(self, chunk):
        if not self.buf:
            self.t0 = time.time() - BLOCK / RATE - 0.02  # when this block's first sample was heard (parec latency)
        self.buf.append(chunk)
        if len(self.buf) >= self.BURST:
            if self.offset is not None:
                import struct
                body = struct.pack("<d", self.t0 + self.offset) + b"".join(self.buf)
                with self.cv:
                    self.q = (self.q + [body])[-10:]  # laptop slow to answer: drop the oldest, never pile up
                    self.cv.notify()
            self.buf = []

    def post(self, body):
        a = time.time()
        t = pi_backup.PLAIN.post(self.url, data=body, timeout=1, headers={"Content-Type": "application/octet-stream"}).json().get("t")
        b = time.time()
        if t:  # the laptop's clock in its answer: keep the offset from the quickest round trips
            rtt, off = b - a, t - (a + b) / 2
            if self.offset is None or rtt < self.best_rtt * 1.5:
                self.offset = off if self.offset is None else 0.8 * self.offset + 0.2 * off
                self.best_rtt = min(self.best_rtt, rtt)

    def sender(self):
        while self.running:
            try:
                if self.offset is None:
                    for _ in range(4):  # learn the laptop's clock first (an empty post just returns it)
                        self.post(b"\0" * 8)
                    print("Room mic: sending the room's sound to the laptop", flush=True)
                with self.cv:
                    while self.running and not self.q:
                        self.cv.wait(1)
                    body, self.q = (self.q[0], self.q[1:]) if self.q else (None, self.q)
                if body:
                    self.post(body)
            except Exception:
                time.sleep(1)  # laptop busy or gone: the standby check notices if it's gone

def phone_feed(body, room=False):
    """Sound from a phone's mic (Light Sync app, "Listen"), or with room=True the room's sound from the Pi's camera
    mic (RoomRelay): 8 bytes start time of the first sample (this machine's clock, seconds, little-endian double),
    then 16-bit mono samples at RATE. The capture times keep the tempo exact even though the sound arrives in
    bursts over Wi-Fi. Returns this machine's clock (the sender uses it to work out its clock offset)."""
    import struct
    ear = web.EAR
    if ear and len(body) >= 8 + BLOCK * 2:
        t0 = struct.unpack("<d", body[:8])[0]
        n = (len(body) - 8) // (BLOCK * 2)
        x = np.frombuffer(body[8:8 + n * BLOCK * 2], "<i2") / 32768.0
        test = soundtest.TEST_EARS.get("room" if room else "phone")  # a sound test running: it hears this too
        for k in range(n):
            ear.process(x[k * BLOCK:(k + 1) * BLOCK], t0 + k * BLOCK / RATE, phone=not room, room=room)
            if test:
                test.process(x[k * BLOCK:(k + 1) * BLOCK], t0 + k * BLOCK / RATE)
    return time.time()

def room_wanted():
    """Laptop: ask the Pi for the room's sound (heartbeat "room_mic") while the beat matters and the picked source
    uses it (Auto or Room mic), and during a sound test."""
    e = web.EAR
    return bool(ROLE == "main" and e and ((e.room_mode and config.RUNNING and beat_matters()) or "room" in soundtest.TEST_EARS))

def beat_matters():
    """Is anything listening to the music: Music sync, Music in games while gaming, Music in TV mode in TV mode."""
    # TV mode listens too (Nov 6: TvD rides the film's / game's sound), so the room mic opens while TV mode runs
    return config.MUSIC_ON or (config.GAMING_ON and config.GAME_MUSIC) or (config.AMBIENT_ON and not config.GAMING_ON)

def demo_audio_loop(ear):
    """Fake 124 BPM track with a build-up and drop, fed in real time."""
    rng = np.random.default_rng()
    period = 60 / 124
    start = time.time()
    t_block = BLOCK / RATE
    while True:
        now = time.time()
        el = now - start
        phase = (el % 16) / 16                  # 16 s loop: quiet -> build -> drop
        level = 0.03 + 0.25 * phase if phase < 0.75 else 0.35
        tt = np.arange(BLOCK) / RATE
        x = level * 0.2 * np.sin(2 * np.pi * 330 * (el + tt)) + 0.004 * rng.standard_normal(BLOCK)
        since = el % period
        if since < t_block:
            x += level * 2 * np.sin(2 * np.pi * 60 * tt) * np.exp(-tt * 25)
        ear.process(x, now)
        time.sleep(t_block)
