"""Each song its own look: noticing a new song and how fast to react."""

import numpy as np


def rhythm_reaction(bpm):
    """Reaction speed from the tempo: 60 BPM -> 2 (calm), 120 -> 6, 150 -> 8 (quick); no steady beat -> 3."""
    return 3.0 if not bpm else max(2.0, min(8.0, 2.0 + (bpm - 60) / 90 * 6))

class SongWatch:
    """Each song its own look (Oct 20, owner: "something like Shazam for the vibes", then: no song names, just the
    mood, following each new song clearly). A new song: music again after a pause (SONG_GAP s under the silence
    level), or the sound's make-up (the spectrum's shape, last ~4 s vs the song so far) and its tempo both changing for
    good. Only levels, the tempo and the spectrum's shape are used; nothing is kept or sent anywhere."""

    GAP = 1.5      # s of (near) silence between two songs
    MIN_LEN = 30   # s: a song lasts at least this long (no second "new song" right after one)
    DIST = 0.14    # how different the sound's make-up must become (0-1: half the summed difference of the shapes;
    #               inside the owner's songs it stayed under 0.09, made-up songs with a fuller chorus 0.12)
    HOLD = 4       # s the change must last

    def __init__(self):
        self.n, self.at, self.why = 0, 0.0, ""
        self.look_n = 0                     # the song whose look is shown (its mood is decided a few seconds in)
        self.prof_s = self.prof_l = None    # spectrum shape: last ~4 s, the song so far (~20 s memory)
        self.quiet, self.changed_at, self.bpm = 0.0, None, 0.0

    def update(self, now, dt, spectrum, silent, bpm):
        """One main-loop turn. silent: under the silence level now; bpm: the tempo when locked, else 0.
        Returns the reason when a new song starts, else None."""
        if silent:
            self.quiet += dt
            return None
        why = None
        if self.n == 0:
            why = "music started"
        elif self.quiet >= self.GAP and now - self.at > self.MIN_LEN:
            why = "after a pause"
        self.quiet = 0.0
        sp = np.asarray(spectrum, float)
        if sp.sum() > 0.05:
            shape = sp / sp.sum()
            if self.prof_s is None:
                self.prof_s, self.prof_l = shape.copy(), shape.copy()
            self.prof_s += (shape - self.prof_s) * (1 - np.exp(-dt / 4.0))
            self.prof_l += (shape - self.prof_l) * (1 - np.exp(-dt / 20.0))
        dist = 0.5 * float(np.abs(self.prof_s - self.prof_l).sum()) if self.prof_s is not None else 0.0
        if not why and now - self.at > self.MIN_LEN and dist > self.DIST:
            self.changed_at = self.changed_at or now
            if now - self.changed_at > self.HOLD:
                ratio = bpm / self.bpm if bpm and self.bpm else 0.0
                same = ratio and min(abs(ratio - r) / r for r in (0.5, 1.0, 2.0)) <= 0.06  # half / double time: same
                if same:
                    self.changed_at = None  # the same tempo: a new part of the same song (a chorus, a bridge)
                else:
                    why = "the sound changed"
        else:
            self.changed_at = None
        if why:
            self.n, self.at, self.why, self.changed_at, self.bpm = self.n + 1, now, why, None, 0.0
            if self.prof_s is not None:
                self.prof_l = self.prof_s.copy()  # the new song's make-up starts from here
            return why
        if bpm and now - self.at > 10:  # the song's own tempo (settled)
            self.bpm = bpm if not self.bpm else self.bpm + (bpm - self.bpm) * (1 - np.exp(-dt / 20.0))
        return None

SONG = SongWatch()
