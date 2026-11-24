"""Colour maths: hue, warmth, soft fades, the colour of a patch of the screen."""

import colorsys
import numpy as np
import time

from parts import config


def kelvin_rgb(k):
    """An RGB colour close to white light of this colour temperature (for cross-fades and the live view)."""
    t = k / 100.0
    r = 255.0 if t <= 66 else 329.7 * (t - 60) ** -0.1332
    g = 99.47 * np.log(t) - 161.12 if t <= 66 else 288.12 * (t - 60) ** -0.0755
    b = 255.0 if t >= 66 else (0.0 if t <= 19 else 138.52 * np.log(t - 10) - 305.04)
    return tuple(float(np.clip(v, 0, 255)) for v in (r, g, b))

def is_night():
    h = time.localtime().tm_hour
    return config.LATE_NIGHT or h >= 21 or h < 7

def soft_color(rgb, night):
    """Comfort color: less saturated; at night every hue folded into warm rose-red-amber (no blue)."""
    h, s, v = colorsys.rgb_to_hsv(*(max(0.0, min(255.0, float(c))) / 255 for c in rgb))
    if v < 0.02:
        return (0.0, 0.0, 0.0)
    if night:
        h = (0.97 + 0.12 * ((h + 0.03) % 1.0)) % 1.0
    s = min(s, 0.6 if night else 0.55)
    r, g, b = colorsys.hsv_to_rgb(h, s, 0.75 + 0.25 * v)
    return (r * 255, g * 255, b * 255)

LUM = np.array([0.2126, 0.7152, 0.0722])  # how bright a colour looks (Rec. 709)

def _lum(c):
    return float(np.dot(np.asarray(c, float), LUM))

def vivid(rgb):
    """Push a color to full saturation/brightness so bulbs look vibrant."""
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    r, g, b = colorsys.hsv_to_rgb(h, max(s, 0.85), 1.0)
    return (r * 255, g * 255, b * 255)

# TvD's colour correction (Nov 6, owner's video: a green outdoor scene, the strip purple-pink; the TV camera: sent hue
# 34 deg, the wall lit 11 deg - the strip's green is weak, pale colours turn pink / purple). Applied only where frames
# go to the strip (bulbs.py), so the rest of Light Sync keeps the true colours.
# measured with the TV camera (scratchpad strip_calib.py, Nov 6): the strip's "white" lit the wall (11, 9, 27) -
# blue 3x red / green; per channel the wall gained red 32, green 17, blue 63; a neutral white needs ~(0.54, 1, 0.25)
# A second run in daylight didn't repeat it (the camera's own white balance moved): a middle value until a measurement
# in the dark; the owner's eyes and video agree the strip's white is purple.
# Measured in the dark (Nov 6 evening, strip_calib2.py, two runs, Corner / Top off): white lit the wall (21, 19, 42);
# per channel red 58, green 52, blue 130 -> a neutral white needs (0.78, 1, 0.30)
STRIP_GAIN = (0.78, 1.0, 0.30)  # red, green, blue
STRIP_MIN_SAT = 0.45           # paler colours get this much colour, so they keep their hue on the strip


def strip_correct(rgb):
    r, g, b = (max(0.0, float(c)) for c in rgb)
    top = max(r, g, b)
    if top <= 0:
        return (0.0, 0.0, 0.0)
    h, s, v = colorsys.rgb_to_hsv(r / top, g / top, b / top)
    if 0.02 < s < STRIP_MIN_SAT:  # (true greys / whites stay white)
        s = STRIP_MIN_SAT - (STRIP_MIN_SAT - s) * 0.5
    r, g, b = colorsys.hsv_to_rgb(h, s, 1.0)
    out = np.array([r * STRIP_GAIN[0], g * STRIP_GAIN[1], b * STRIP_GAIN[2]])
    return tuple(out / out.max() * top)


def deglare(rgb, lum, own=None):
    """Nov 6, the owner's night video: the room all red while the game was grey - the TV's glass reflected the red
    room, a dark picture's most colourful pixels were that reflection, the lights went redder: a loop. So a screen
    colour counts less the darker the picture (lum: its brightness 0-255), and much less again when it is the lights'
    own colour (own: what TvD shows now) in a picture that isn't bright. What's taken off goes to a warm white."""
    c = np.clip(np.array(rgb, float), 0, 255)
    m = 0.15 + 0.85 * float(np.clip((float(lum) - 40.0) / 110.0, 0.0, 1.0))  # (0.3 + 30-120: still red, Nov 6)
    if own is not None and lum < 130:
        o = np.clip(np.array(own, float), 0, 255)
        if o.max() > 20 and c.max() > 20:
            ho, so, _ = colorsys.rgb_to_hsv(*(o / o.max()))
            hc, sc, _ = colorsys.rgb_to_hsv(*(c / c.max()))
            if so > 0.5 and min(abs(ho - hc), 1 - abs(ho - hc)) < 30 / 360:
                m *= 0.3
    warm = np.array([255.0, 214.0, 170.0]) * (c.max() / 255.0)
    return tuple(c * m + warm * (1 - m))


def film_colour(rgb):
    """TV mode (Nov 6): the film's own colour for a bulb: its hue and saturation kept (a little lift, a pale scene
    stays pale), full value (the lights' brightness is set apart). Music's vivid() pushes everything to 85 % saturation."""
    h, s, v = colorsys.rgb_to_hsv(*(np.clip(np.array(rgb, float), 0, 255) / 255))
    r, g, b = colorsys.hsv_to_rgb(h, min(1.0, s * 1.15), 1.0)
    return (r * 255, g * 255, b * 255)

def turn_hue(rgb, amount):
    """The same colour turned on the colour wheel (amount: 1 = all the way round), its saturation and value kept."""
    h, s, v = colorsys.rgb_to_hsv(*(np.clip(np.array(rgb, float), 0, 255) / 255))
    return tuple(c * 255 for c in colorsys.hsv_to_rgb((h + amount) % 1.0, s, v))

def film_mood(rgb):
    """TV mode's Top (Nov 6, "on my eye, smooth"): only the scene's mood - its hue, at most 35 % saturation, toward a
    warm white when the scene is grey. Never a strong colour."""
    h, s, v = colorsys.rgb_to_hsv(*(np.clip(np.array(rgb, float), 0, 255) / 255))
    r, g, b = colorsys.hsv_to_rgb(h, min(s, 0.35), 1.0)
    warm = np.array([255.0, 214.0, 170.0])
    k = min(1.0, s / 0.35)  # grey scene -> warm white
    return tuple(warm + (np.array([r, g, b]) * 255 - warm) * k)

def colorful(bgr):
    """Mask of colorful pixels (saturation > 70, value > 60 on OpenCV's 0-255 HSV scale)."""
    mx = bgr.max(axis=-1).astype(np.int32)
    mn = bgr.min(axis=-1).astype(np.int32)
    sat = (mx - mn) * 255 // np.maximum(mx, 1)
    return (sat > 70) & (mx > 60)

def second_colour(px_bgr, main_rgb, prev=None):
    """The screen's second colour (Oct 28, owner: "make the camera make more than one colour"): the strongest colour
    family (30 degree hue bins) at least 60 degrees from the main colour, covering 15 %+ of the colour (one already
    showing stays down to 10 %, so Corner doesn't flick between two). (RGB, family), or (None, None): one colour."""
    rgb = px_bgr[:, ::-1].astype(np.float32)
    mx, mn = rgb.max(axis=1), rgb.min(axis=1)
    d = np.maximum(mx - mn, 1e-6)
    r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    h = np.where(mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) / 6
    fam = (h * 12).astype(int) % 12
    hist = np.bincount(fam, weights=d / 255, minlength=12)  # saturation x value = chroma
    main_f = int(colorsys.rgb_to_hsv(*(np.clip(np.array(main_rgb, float), 0, 255) / 255))[0] * 12) % 12
    far = np.minimum((np.arange(12) - main_f) % 12, (main_f - np.arange(12)) % 12) >= 2
    total = max(1e-9, hist.sum())
    if prev is not None and far[prev] and hist[prev] >= 0.10 * total:
        best = prev
    else:
        best = int(np.argmax(np.where(far, hist, 0)))
        if hist[best] < 0.15 * total or not far[best]:
            return None, None
    return rgb[fam == best].mean(axis=0), best

def glide_colour(cur, tgt, k):
    """cur moved k of the way to tgt. Between two clear colours it goes round the colour wheel: a straight RGB line
    between opposite colours went through white (Oct 28: blue to orange showed a white, then pale green Corner)."""
    hc, sc, vc = colorsys.rgb_to_hsv(*(np.clip(np.array(cur, float), 0, 255) / 255))
    ht, st, vt = colorsys.rgb_to_hsv(*(np.clip(np.array(tgt, float), 0, 255) / 255))
    if sc < 0.25 or st < 0.25 or vc < 0.05:  # white-ish or dark: no hue to go round
        return np.array(cur, float) + (np.array(tgt, float) - np.array(cur, float)) * k
    dh = (ht - hc + 0.5) % 1.0 - 0.5
    return np.array(colorsys.hsv_to_rgb((hc + dh * k) % 1.0, sc + (st - sc) * k, vc + (vt - vc) * k)) * 255

def soft_tint(rgb, sat=0.25):
    """A room light with a hint of the colour: the same hue, little saturation, full value."""
    hh, s, v = colorsys.rgb_to_hsv(*(np.clip(np.array(rgb, float), 0, 255) / 255))
    return tuple(c * 255 for c in colorsys.hsv_to_rgb(hh, min(s, sat), 1.0))

def colour_cells(bgr, nx=8, ny=3):
    """The picture as nx x ny cells, each as its colour's shares (blue, green, red adding up to 1): brightness taken
    out, so the TV's backlight shimmer doesn't count as action. None for a picture smaller than the cells."""
    h, w = bgr.shape[:2]
    if h < ny or w < nx:
        return None
    c = bgr[:h // ny * ny, :w // nx * nx].astype(np.float32).reshape(ny, h // ny, nx, w // nx, 3).mean(axis=(1, 3))
    return c / (c.sum(axis=2, keepdims=True) + 1e-3)

def _patch_colour(part, raw=False):
    """A part of the picture's colour: its colourful pixels, the vivid ones counting more (saturation squared), or
    None (dark or grey)."""
    if raw:
        # TV mode (Nov 6, an ad of beige walls and a small red shirt lit the room red): every visible pixel counts,
        # the colourful ones only a little more - only colourful pixels, squared, let a small red patch win
        pf = part.reshape(-1, 3).astype(np.float32)
        pf = pf[pf.max(axis=1) > 40]  # not the black bars / dark corners
        if len(pf) <= 8:
            return None
        sat = (pf.max(axis=1) - pf.min(axis=1)) / np.maximum(pf.max(axis=1), 1.0)
        wts = 0.3 + sat
        return ((pf * wts[:, None]).sum(axis=0) / float(wts.sum()))[::-1]
    px = part.reshape(-1, 3)[colorful(part).reshape(-1)] if part.size else part.reshape(-1, 3)
    if len(px) <= 8:
        return None
    pf = px.astype(np.float32)
    sat = (pf.max(axis=1) - pf.min(axis=1)) / np.maximum(pf.max(axis=1), 1.0)
    c = (pf * (sat ** 2)[:, None]).sum(axis=0) / max(1e-6, float((sat ** 2).sum()))
    return vivid(c[::-1])

def hue_rgb(h):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, 1.0, 1.0)
    return (r * 255, g * 255, b * 255)

def pal_at(x):
    """The palette color at a fractional position (smooth blend between neighbours, wraps around)."""
    n = len(config.PALETTE)
    i = int(np.floor(x))
    f = x - i
    f = f * f * (3 - 2 * f)
    a, b = np.array(hue_rgb(config.PALETTE[i % n])), np.array(hue_rgb(config.PALETTE[(i + 1) % n]))
    return a + (b - a) * f

def shift_hue(rgb, amount):
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    return hue_rgb(h + amount)
