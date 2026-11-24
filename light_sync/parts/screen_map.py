"""Where the TV is in the camera picture, and turning the screen into colours for each light."""

import numpy as np
import time
from collections import deque

from parts.config import (
    HA_TOKEN, HA_TV_BOX, HA_URL, SCREEN_SEGMENTS, SCREEN_SEGMENTS_FROM_RIGHT, SNAP_USER, TV_ACTION, TV_ACTION_HI,
    TV_ACTION_LO, TV_STEADY_S,
)
from parts import colours, config, eyes, games, ha_link, web


# The screen is the area whose detail keeps changing while it shows something moving; walls, furniture and the lights'
# soft glow barely do. The same steps as tools/tests/find_screen.py, without OpenCV (the Pi has none). Used by TV mode's
# check (screen_color_loop: is TV_BOX still on the screen?) and the cameras' Auto-tune (pi_addon/light_sync/cameras.py).
TV_RECHECK_S = 120  # TV mode: this often, from the last ~20 s of pictures

def grey_small(img, w=160, h=90):
    """A picture -> h x w grey numbers (block averages)."""
    g = img.mean(axis=2) if img.ndim == 3 else img.astype(float)
    hh, ww = g.shape
    ys, xs = np.arange(h + 1) * hh // h, np.arange(w + 1) * ww // w
    c = np.zeros((hh + 1, ww + 1))
    c[1:, 1:] = g.cumsum(0).cumsum(1)
    blocks = c[ys[1:]][:, xs[1:]] - c[ys[:-1]][:, xs[1:]] - c[ys[1:]][:, xs[:-1]] + c[ys[:-1]][:, xs[:-1]]
    return blocks / np.outer(np.diff(ys), np.diff(xs))

def _box_blur(a, r=2):
    p = np.pad(a, r, mode="edge")
    c = np.zeros((p.shape[0] + 1, p.shape[1] + 1))
    c[1:, 1:] = p.cumsum(0).cumsum(1)
    k, (h, w) = 2 * r + 1, a.shape
    return (c[k:k + h, k:k + w] - c[:h, k:k + w] - c[k:k + h, :w] + c[:h, :w]) / (k * k)

def _grow(m, r=2):
    p, out = np.pad(m, r), np.zeros_like(m)
    for dy in range(2 * r + 1):
        for dx in range(2 * r + 1):
            out |= p[dy:dy + m.shape[0], dx:dx + m.shape[1]]
    return out

def _largest_blob(m):
    h, w = m.shape
    seen, best = np.zeros_like(m), []
    for y, x in zip(*np.nonzero(m)):
        if seen[y, x]:
            continue
        stack, cells = [(y, x)], []
        seen[y, x] = True
        while stack:
            cy, cx = stack.pop()
            cells.append((cy, cx))
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < h and 0 <= nx < w and m[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        if len(cells) > len(best):
            best = cells
    return best

def find_screen_box(greys):
    """greys: ~20-40 grey_small pictures over 10-20 s. -> {"box": the part to read (x1, y1, x2, y2 as fractions, a
    little inside the edges), "outer": the whole moving area, "strength": how much more it moves than the rest,
    "share": its part of the picture, "mask": the cells} or None."""
    if len(greys) < 8:
        return None
    change = _box_blur(np.stack(greys).astype(np.float32).std(axis=0), 2)
    med = float(np.median(change))
    m = change > max(float(np.percentile(change, 90)), 3 * med + 1e-6)
    m = ~_grow(~_grow(m, 2), 2)  # closing: small gaps inside the screen filled
    blob = _largest_blob(m)
    if not blob:
        return None
    ys, xs = np.array(blob).T
    h, w = change.shape
    x1, x2, y1, y2 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    ix, iy = (x2 - x1) * 0.1, (y2 - y1) * 0.1  # the bezel and the light spilling around the screen aren't the picture
    mask = np.zeros((h, w), bool)
    mask[ys, xs] = True
    return {"box": ((x1 + ix) / w, (y1 + iy) / h, (x2 - ix) / w, (y2 - iy) / h), "outer": (x1 / w, y1 / h, x2 / w, y2 / h),
            "strength": float(change[ys, xs].mean() / (med + 1e-6)), "share": len(blob) / (h * w), "mask": mask}

def box_on_screen(box, mask):
    """How much of box (fractions) lies on the moving area."""
    h, w = mask.shape
    x1, y1, x2, y2 = int(box[0] * w), int(box[1] * h), int(np.ceil(box[2] * w)), int(np.ceil(box[3] * h))
    part = mask[y1:y2, x1:x2]
    return float(part.mean()) if part.size else 0.0

def quad_box(quad):
    """The box around four corners."""
    xs, ys = [p[0] for p in quad], [p[1] for p in quad]
    return min(xs), min(ys), max(xs), max(ys)

def quad_cells(quad, w, h):
    """Which cells of a w x h grid lie inside the four corners (a convex shape, corners in order)."""
    yy, xx = np.mgrid[0:h, 0:w]
    px, py = (xx + 0.5) / w, (yy + 0.5) / h
    area = sum(quad[i][0] * quad[(i + 1) % 4][1] - quad[(i + 1) % 4][0] * quad[i][1] for i in range(4))
    side = 1 if area > 0 else -1  # the inside is on the same side of every edge
    inside = np.ones((h, w), bool)
    for i in range(4):
        (x1, y1), (x2, y2) = quad[i], quad[(i + 1) % 4]
        inside &= ((x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)) * side >= 0
    return inside

def fit_quad(mask, shrink=0.06):
    """The screen's four corners from its cells (find_screen_box's mask): the corner cells furthest out along the
    diagonals (top-left, top-right, bottom-right, bottom-left), pulled in by shrink towards the middle so the
    bezel and the wall stay out. Fractions of the picture."""
    h, w = mask.shape
    ys, xs = np.nonzero(mask)
    if len(xs) < 8:
        return None
    x, y = xs + 0.5, ys + 0.5  # cells are about square (160 x 90 for 16:9)
    pts = [(x[i], y[i]) for i in (np.argmin(x + y), np.argmax(x - y), np.argmax(x + y), np.argmin(x - y))]
    cx, cy = x.mean(), y.mean()
    return tuple(((px + (cx - px) * shrink) / w, (py + (cy - py) * shrink) / h) for px, py in pts)

def warp_quad(img, quad, out_w=240, out_h=135):
    """The screen inside four corners (fractions), straightened into an out_w x out_h picture: left to right and top
    to bottom as on the screen itself, whatever the camera's angle. OpenCV on the laptop, numpy on the Pi."""
    h, w = img.shape[:2]
    src = np.array([(px * w, py * h) for px, py in quad], np.float32)
    dst = np.array([(0, 0), (out_w - 1, 0), (out_w - 1, out_h - 1), (0, out_h - 1)], np.float32)
    try:
        import cv2
        return cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (out_w, out_h), flags=cv2.INTER_AREA)
    except ImportError:
        pass
    # the same with numpy: the homography from the flat picture to the camera's, then each pixel looked up
    a, b = [], []
    for (u, v), (xx, yy) in zip(dst, src):
        a += [[u, v, 1, 0, 0, 0, -u * xx, -v * xx], [0, 0, 0, u, v, 1, -u * yy, -v * yy]]
        b += [xx, yy]
    hm = np.append(np.linalg.solve(np.array(a, float), np.array(b, float)), 1.0).reshape(3, 3)
    vv, uu = np.mgrid[0:out_h, 0:out_w]
    pts = hm @ np.stack([uu.ravel(), vv.ravel(), np.ones(uu.size)])
    sx = np.clip((pts[0] / pts[2]).round().astype(int), 0, w - 1)
    sy = np.clip((pts[1] / pts[2]).round().astype(int), 0, h - 1)
    return img[sy, sx].reshape(out_h, out_w, -1)

def quad_point(quad, u, v):
    """A point inside four corners: u across (0 left, 1 right), v down (0 top, 1 bottom), as fractions of the picture
    (blended between the corners: close enough to draw with)."""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = quad
    top, bottom = (x0 + (x1 - x0) * u, y0 + (y1 - y0) * u), (x3 + (x2 - x3) * u, y3 + (y2 - y3) * u)
    return top[0] + (bottom[0] - top[0]) * v, top[1] + (bottom[1] - top[1]) * v

def box_overlap(a, b):
    """How much two boxes (fractions) are the same place: shared area / combined area (0-1)."""
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    both = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - w * h
    return w * h / both if both > 0 else 0.0

def save_tv_box(box, why, quad=None):
    """A new TV area (a box, or the screen's four corners): here at once, and in Home Assistant
    (input_text.light_sync_tv_box) for the other machine."""
    if quad is not None:
        config.TV_QUAD = tuple(tuple(round(float(v), 2) for v in pt) for pt in quad)
        config.TV_BOX = quad_box(config.TV_QUAD)
        value = ",".join(f"{v:.2f}" for pt in config.TV_QUAD for v in pt)
    else:
        config.TV_BOX, config.TV_QUAD = tuple(round(v, 2) for v in box), None
        value = ",".join(f"{v:.2f}" for v in config.TV_BOX)
    try:
        ha_link.session.post(f"{HA_URL}/api/services/input_text/set_value", timeout=5, json={"entity_id": HA_TV_BOX, "value": value})
    except Exception as e:
        print(f"TV area: couldn't save it in Home Assistant ({e})", flush=True)
    print(f"TV area: {value} ({why})", flush=True)

def strip_colors(bgr, smap, raw=False):
    """Each of TvD's parts its colour from the screen next to it (STRIP_MAP): a patch reaching 30 % into the picture
    from the part's place on the edge. Returned in zone_colors' order (left to right: reversed for a strip wired from
    the right), so the rest works the same. None when the whole picture is dark or grey."""
    h, w = bgr.shape[:2]
    cols = []
    # Nov 6: the strip ends on the desk under the screen's right corner (owner, Oct 28), so ~6 parts read the same
    # bottom-right patch and the strip looked one colour. Those desk parts now read along the bottom edge, right to
    # left, so the strip shows the top, the right side and the bottom of the picture.
    desk = [i for i, (u, v) in enumerate(smap) if v >= 0.85]
    if len(desk) >= 3:
        order = sorted(desk, key=lambda i: smap[i][1])  # nearest the corner first
        smap = list(smap)
        for k, i in enumerate(order):
            smap[i] = (1.0 - 0.75 * k / (len(order) - 1), 1.0)
    for u, v in smap:
        x1, y1 = int(max(0.0, min(0.82, u - 0.09)) * w), int(max(0.0, min(0.82, v - 0.09)) * h)  # was 30 %: 18 % (Nov 6)
        cols.append(colours._patch_colour(bgr[y1:y1 + max(1, int(0.18 * h)), x1:x1 + max(1, int(0.18 * w))], raw))
    known = [i for i, c in enumerate(cols) if c is not None]
    if not known:
        return None
    cols = [cols[i] if cols[i] is not None else cols[min(known, key=lambda k: abs(k - i))] for i in range(len(cols))]
    return cols[::-1] if SCREEN_SEGMENTS_FROM_RIGHT else cols

# 45 colour points (Nov 6, owner: "each of the 9 boxes 5 colours, 4 for the sides and one for the middle, more
# precise, and make a perfect algorithm for the lights"): in each box its middle and a point near each side
SAMPLE_UV = []
for _gy in range(3):
    for _gx in range(3):
        _x0, _y0, _cx, _cy = _gx / 3, _gy / 3, (_gx + 0.5) / 3, (_gy + 0.5) / 3
        SAMPLE_UV += [(_cx, _cy), (_cx, _y0 + 0.06), (_cx, _y0 + 1 / 3 - 0.06), (_x0 + 0.06, _cy), (_x0 + 1 / 3 - 0.06, _cy)]
# how far a point still counts for a strip part (screen widths). Nov 6 test, 60 pictures each, against one patch per part:
# 0.16 halved the jumps between neighbours but washed out the variety (0.040-0.049 vs the screen's 0.069-0.071);
# 0.10 keeps the same accuracy (0.999), ~15 % smaller jumps and more of the variety
SAMPLE_SIGMA = 0.10


def sample_colors(bgr):
    """The 45 points' colours (RGB, all their pixels counting, the colourful ones a little more), None where dark."""
    h, w = bgr.shape[:2]
    rx, ry = max(1, int(0.045 * w)), max(1, int(0.045 * h))
    out = []
    for u, v in SAMPLE_UV:
        x, y = int(u * (w - 1)), int(v * (h - 1))
        out.append(colours._patch_colour(bgr[max(0, y - ry):y + ry + 1, max(0, x - rx):x + rx + 1], raw=True))
    return out


def parts_from_samples(samples, smap):
    """Each of TvD's parts its colour from the 45 points: a weighted mix, the points nearest its place on the screen's
    edge counting most (a Gaussian of the distance), so neighbouring parts blend smoothly. Returned in zone_colors'
    order like strip_colors (reversed for a strip wired from the right)."""
    pts = [(u, v, np.array(c, float)) for (u, v), c in zip(SAMPLE_UV, samples) if c is not None]
    if not pts:
        return None
    uv = np.array([(u, v) for u, v, _ in pts])
    cols = np.array([c for _, _, c in pts])
    out = []
    for pu, pv in smap:
        d2 = (uv[:, 0] - pu) ** 2 + (uv[:, 1] - pv) ** 2
        wts = np.exp(-d2 / (2 * SAMPLE_SIGMA ** 2))
        out.append(tuple((cols * wts[:, None]).sum(axis=0) / max(1e-9, wts.sum())))
    return out[::-1] if SCREEN_SEGMENTS_FROM_RIGHT else out


def own_rgb():
    """What TvD shows now (its average colour), for colours.deglare; None if not known."""
    try:
        return web.STATE.get("lights", {}).get("screen", {}).get("rgb")
    except Exception:
        return None


def zone_colors(bgr, n, raw=False):
    """The picture's colors in n vertical slices, left to right (colorful pixels of each slice). Dark or gray
    slices borrow from their neighbours; None when the whole picture is dark or gray. raw: the slices' own average
    colors, not made vivid (TV mode smooths them first)."""
    if n <= 0:
        return None
    w = bgr.shape[1]
    cols = []
    for i in range(n):
        part = bgr[:, int(i * w / n):max(int((i + 1) * w / n), int(i * w / n) + 1)]
        cols.append(colours._patch_colour(part, raw))  # the vivid pixels count more: a slice shows its colour, not the mix
    known = [i for i, c in enumerate(cols) if c is not None]
    if not known:
        return None
    return [cols[i] if cols[i] is not None else cols[min(known, key=lambda k: abs(k - i))] for i in range(n)]

def screen_color_loop():
    try:
        import cv2
    except ImportError:
        cv2 = None  # Pi add-on: Pillow decoding, no preview (there's no live view there)
    auth = eyes.stream_auth()
    # Home Assistant camera URLs (/api/camera_proxy/...) use your HA token; its MJPEG twin is camera_proxy_stream
    headers = {"Authorization": f"Bearer {HA_TOKEN}"} if "/api/camera_proxy/" in config.SNAPSHOT_URL else {}
    stream_url = config.SNAPSHOT_URL.replace("/api/camera_proxy/", "/api/camera_proxy_stream/")
    fps = eyes.CAM_FPS or (10.0 if cv2 else 5.0)  # the Pi decodes with Pillow: fewer reads
    last_err, stream, last_at = None, None, 0.0
    picture = eyes.PictureArea()
    avg_s = None  # TV mode: the screen's left and right thirds, averaged (Corner and Top, Nov 6)
    avg, avg_z, avg_at, colour_at = None, None, 0.0, 0.0  # TV mode: the screen's colour (and slices') averaged
    avg2, sec_fam = None, None  # ... its second colour (averaged too) and that colour's family
    # TV mode's check: is TV_BOX still on the screen? (grey numbers every 0.5 s, checked every TV_RECHECK_S)
    heal_greys, heal_at, heal_checked, heal_misses = deque(maxlen=40), 0.0, time.time(), 0
    heal_found = None  # where the last miss found the screen
    act_cells = deque(maxlen=40)  # the Cinema look's action: the colour cells of the last few seconds
    while True:
        if config.STANDBY:  # backup: the laptop is driving the lights, don't read the camera
            eyes.screen_rgb = None
            if stream:
                stream.stop()
                stream = None
            time.sleep(1)
            continue
        live = (config.RUNNING and (web.STATE.get("mode") in ("ambient", "gaming") or web.STATE.get("tv_party"))  # + TV + Music (Nov 6)
                and not (config.GAMING_ON and games.GAME_ON_LAPTOP))
        if live and stream is None:
            stream = eyes.CamStream(stream_url, config.SNAPSHOT_URL, auth, headers)
        elif not live:
            # only TV mode and gaming read the screen. Music and Effects used to fetch a full picture every second
            # anyway (~15% of a core on the laptop) and "each own" then took the screen's colors over the chosen
            # Colours (Oct 19)
            if stream is not None:
                stream.stop()
                stream = None
            eyes.CAM_IDLE, eyes.screen_rgb, eyes.screen_zones, eyes.SCREEN_OFF, eyes.SCREEN_SECOND = True, None, None, False, None
            time.sleep(0.5)
            continue
        eyes.CAM_IDLE = False
        err = None
        try:
            if stream is not None:
                if stream.at <= last_at:  # no newer picture yet (or still connecting / switching to polling)
                    if time.time() - max(stream.at, last_at, stream.started) > 10:
                        raise RuntimeError(f"camera: {stream.err or f'no new picture for 10 s (HTTP {stream.status})'}")
                    time.sleep(0.03)
                    continue
                status, ctype, data, last_at = stream.status, "image/jpeg", stream.jpg, stream.at
            else:
                status, ctype, data = eyes.fetch_frame(config.SNAPSHOT_URL, auth, headers)
            if status == 401 and SNAP_USER:
                auth = eyes.stream_auth()  # login type changed (e.g. motionEye switched to Digest)
                if stream is not None:
                    stream.stop()
                    stream = None
            if not data:
                img = None
            elif cv2:
                img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            else:
                img = eyes.decode_bgr(data)
            eyes.CAM_OK = img is not None
            if img is None:
                err = (f"SNAPSHOT_URL didn't return an image (HTTP {status}, {ctype}). "
                       + ("Login needed - check SNAP_USER / SNAP_PASS. " if status in (401, 403) else "")
                       + "Open the URL in your browser: it should show just a picture.")
                eyes.screen_rgb = None
            else:
                h, w = img.shape[:2]
                x1, y1, x2, y2 = config.TV_BOX
                quad = config.TV_QUAD  # the screen's four corners: straightened into a flat picture of it (Oct 28)
                box = warp_quad(img, quad) if quad else img[int(y1 * h):int(y2 * h), int(x1 * w):int(x2 * w)]
                # the picture only: no letterbox bars (rows dark for ~10 s), and the colours without the subtitle band
                top, bottom = picture.bounds(box) if box.size else (0, 0)
                crop = box[top:bottom] if bottom > top else box
                colour = crop[:max(1, len(crop) - len(crop) // 5)]  # the bottom fifth = subtitles, never a colour
                if cv2:
                    hsv = cv2.cvtColor(colour, cv2.COLOR_BGR2HSV).reshape(-1, 3)
                    px = colour.reshape(-1, 3)[(hsv[:, 1] > 70) & (hsv[:, 2] > 60)]  # colorful pixels only
                else:
                    px = colour.reshape(-1, 3)[colours.colorful(colour).reshape(-1)]
                clipped = float((crop.max(axis=2) >= 250).mean()) if crop.size else 0.0
                # screen off? With the monitor off, its dark glass reflected the blue light around it and the lights
                # kept themselves blue (Oct 19). An off screen is much darker than the wall around it (measured:
                # 0.34 x the wall; on: 1.30 x), so below 0.6 x it doesn't count as a picture.
                mxb, myb = max(x2 - x1, 0.02), max(y2 - y1, 0.02)
                rx1, ry1 = int(max(0.0, x1 - mxb) * w), int(max(0.0, y1 - myb) * h)
                rx2, ry2 = int(min(1.0, x2 + mxb) * w), int(min(1.0, y2 + myb) * h)
                ring = img[ry1:ry2:4, rx1:rx2:4].max(axis=2).astype(np.float32)  # every 4th pixel is plenty
                ring[(int(y1 * h) - ry1) // 4:(int(y2 * h) - ry1) // 4, (int(x1 * w) - rx1) // 4:(int(x2 * w) - rx1) // 4] = np.nan
                box_v = float(np.median(crop[::4, ::4].max(axis=2))) if crop.size else 0.0
                ring_v = float(np.nanmedian(ring)) if np.isfinite(ring).any() else 0.0
                # Nov 6 (owner's picture: the TV off, its glass reflecting the lights in orange, TV mode took those): with
                # the outline out to the screen's edges the ring around it takes in the black frame too, so an off
                # screen read 42 against 67 (0.63 x); a very dark screen counts as off at 0.75 x as well
                eyes.SCREEN_OFF = (box_v < 0.6 * ring_v and box_v < 120) or (box_v < 0.75 * ring_v and box_v < 60)
                eyes.SCREEN_LUM = None if eyes.SCREEN_OFF else box_v  # TV mode's brightness follows the picture (Nov 6)
                # TV mode's check: from the pictures read anyway, is the screen still where TV_BOX reads? Moved twice in
                # a row (the camera turned, zoomed or knocked) -> the screen found again and saved
                try:  # never in the way of the colours: a problem here only skips the check
                    if web.STATE.get("mode") != "ambient":
                        heal_greys.clear()
                        heal_checked = time.time()
                    elif time.time() - heal_at >= 0.5:
                        heal_at = time.time()
                        heal_greys.append(grey_small(img))
                        if time.time() - heal_checked > TV_RECHECK_S and len(heal_greys) >= 30:
                            heal_checked = time.time()
                            found = find_screen_box(list(heal_greys))
                            heal_greys.clear()
                            if found and found["strength"] >= 3 and 0.003 <= found["share"] <= 0.6:
                                if quad:  # how much of the four corners' shape is on the moving screen
                                    inq = quad_cells(quad, found["mask"].shape[1], found["mask"].shape[0])
                                    on = float(found["mask"][inq].mean()) if inq.any() else 0.0
                                else:
                                    on = box_on_screen(config.TV_BOX, found["mask"])
                                # a miss counts twice only at the same new place (Oct 27: a box half off the screen
                                # was saved at 17:56, maybe from something passing by; the owner's movie got the
                                # wall's colours)
                                same = heal_found is not None and box_overlap(found["box"], heal_found) >= 0.6
                                heal_misses = (heal_misses + 1 if same else 1) if on < 0.5 else 0
                                heal_found = found["box"] if on < 0.5 else None
                                eyes.DIAG["tv_check"] = {"at": round(time.time()), "on_screen": round(on, 2),
                                                    "strength": round(found["strength"], 1), "misses": heal_misses}
                                if heal_misses >= 2 and quad:  # four corners are measured over ~90 s (find_screen.py
                                    # --quad): a 15 s look at a screen seen from below is too patchy to replace them
                                    eyes.DIAG["tv_check"]["note"] = "the screen may have moved: find_screen.py --quad"
                                elif heal_misses >= 2:
                                    save_tv_box(found["box"], f"TV mode's check: the old one was {100 * on:.0f} % on the screen")
                                    heal_misses, heal_found = 0, None
                except Exception as e:
                    eyes.DIAG["tv_check"] = {"at": round(time.time()), "error": str(e)}
                    heal_greys.clear()
                eyes.DIAG["ring"] = (eyes.DIAG.get("ring", []) + [(round(time.time(), 2), round(ring_v, 1), round(box_v, 1))])[-40:]
                if eyes.SCREEN_OFF:
                    px = px[:0]  # no picture: ignore reflections
                now_t = time.time()
                steady = web.STATE.get("mode") == "ambient" or bool(web.STATE.get("tv_party"))
                # the Cinema look's action: the colours' change against the picture 1 s ago (nothing on the screen: calm)
                cells = colours.colour_cells(colour) if steady and len(px) > 50 and colour.size else None
                if cells is not None:
                    act_cells.append((now_t, cells))
                    ref = next((c for t, c in reversed(act_cells) if t <= now_t - 1.0), None)
                    raw = float(np.abs(cells[:, :, 1:] - ref[:, :, 1:]).mean() * 1000) if ref is not None else TV_ACTION["raw"]
                else:
                    act_cells.clear()
                    raw = 0.0
                ka = 1 - np.exp(-min(1.0, now_t - TV_ACTION["at"]) / 1.0)  # averaged over ~1 s
                TV_ACTION["raw"] += (raw - TV_ACTION["raw"]) * ka
                TV_ACTION["level"] = float(np.clip((TV_ACTION["raw"] - TV_ACTION_LO) / max(1.0, TV_ACTION_HI - TV_ACTION_LO), 0, 1))
                TV_ACTION["at"] = now_t
                if not steady or now_t - colour_at > 2:  # gaming, or no colour for 2 s: start over (no old scene's colour)
                    avg = avg_z = avg2 = None
                wk = 1 - np.exp(-min(1.0, now_t - avg_at) / TV_STEADY_S)
                avg_at = now_t
                if len(px) > 50:
                    colour_at = now_t
                    raw = px.mean(axis=0)[::-1]  # RGB
                    if steady:
                        avg = raw if avg is None else avg + (raw - avg) * wk
                        raw = avg
                    eyes.screen_rgb = colours.vivid(raw)
                    if steady:  # TV: against the room's own reflection in the screen (colours.deglare, Nov 6)
                        eyes.screen_rgb = colours.deglare(eyes.screen_rgb, box_v, own_rgb())
                    if steady:  # the Cinema look's Corner: a second colour family on the screen, if there is one
                        sec, sec_fam = colours.second_colour(px, eyes.screen_rgb, sec_fam)
                        avg2 = None if sec is None else sec if avg2 is None else avg2 + (sec - avg2) * wk
                    eyes.SCREEN_SECOND = colours.vivid(avg2) if steady and avg2 is not None else None
                elif clipped > 0.6 and not eyes.SCREEN_OFF:
                    # the screen is brighter than the camera can see: burnt out to white, its color is lost (Oct 19:
                    # an orange screen read as nothing, so the lights kept an old blue). Soft white, not a stale color.
                    eyes.screen_rgb = (255, 214, 170)
                else:
                    eyes.screen_rgb = None  # screen dark or gray -> use music palette
                if eyes.screen_rgb is None or len(px) <= 50:
                    eyes.SCREEN_SECOND = avg2 = sec_fam = None
                # Nov 6 (owner: "I don't like how it's taking only one colour"): Corner and Top had the screen's one main
                # colour, shifted a little. Now each has its own side of the picture: the left and right thirds.
                sides = zone_colors(colour, 3, raw=True) if eyes.screen_rgb and steady and len(px) > 50 else None
                if sides and all(c is not None for c in sides):
                    sd = np.array(sides, float)  # left, middle, right: raw, the TV rules colour them (light_sync.py)
                    avg_s = sd if avg_s is None or len(avg_s) != 3 else avg_s + (sd - avg_s) * wk
                    eyes.SCREEN_SIDES = [tuple(c) for c in avg_s]
                else:
                    avg_s, eyes.SCREEN_SIDES = None, None
                if SCREEN_SEGMENTS and eyes.screen_rgb and steady:
                    smap = config.STRIP_MAP
                    if len(px) > 50 and smap:  # the 45 points, mixed for each part by distance (Nov 6)
                        samples = sample_colors(crop)
                        eyes.SAMPLES = [(u, v, c) for (u, v), c in zip(SAMPLE_UV, samples)]
                        z = parts_from_samples(samples, smap)
                    else:
                        eyes.SAMPLES = None
                        z = zone_colors(colour, SCREEN_SEGMENTS, raw=True) if len(px) > 50 else None
                    if z:  # each slice blended a little with its neighbours, then averaged over time, then vivid
                        z = np.array(z, float)
                        sp = z.copy()
                        if len(z) > 2:
                            # (a little of the neighbours: 15 % each, was 25 %, so the segments keep their own colours)
                            sp[1:-1] = 0.15 * z[:-2] + 0.7 * z[1:-1] + 0.15 * z[2:]
                            sp[0], sp[-1] = 0.85 * z[0] + 0.15 * z[1], 0.85 * z[-1] + 0.15 * z[-2]
                        avg_z = sp if avg_z is None or len(avg_z) != len(sp) else avg_z + (sp - avg_z) * wk
                        own = own_rgb()  # (and the room's reflection kept out, Nov 6)
                        eyes.screen_zones = [colours.deglare(colours.film_colour(c), box_v, own) for c in avg_z]  # TV: the film's colours (Nov 6)
                    else:
                        eyes.screen_zones = [eyes.screen_rgb] * SCREEN_SEGMENTS
                elif SCREEN_SEGMENTS and eyes.screen_rgb:
                    eyes.screen_zones = (((strip_colors(colour, config.STRIP_MAP) if config.STRIP_MAP else zone_colors(colour, SCREEN_SEGMENTS))
                                     if len(px) > 50 else None) or [eyes.screen_rgb] * SCREEN_SEGMENTS)
                else:
                    eyes.screen_zones = None
                if cv2:
                    # preview for the live view: ONLY the area around the screen (privacy: the rest of the bedroom is
                    # never shown), green box = the part being read
                    mx, my = max(x2 - x1, 0.04), max(y2 - y1, 0.04)  # one box size of margin on each side
                    cx1, cy1 = int(max(0.0, x1 - mx) * w), int(max(0.0, y1 - my) * h)
                    cx2, cy2 = int(min(1.0, x2 + mx) * w), int(min(1.0, y2 + my) * h)
                    view = img[cy1:cy2, cx1:cx2].copy()
                    web.STATE["cam_crop"] = [cx1 / w, cy1 / h, cx2 / w, cy2 / h]  # the live view's corner editor (Nov 6)
                    web.STATE["tv_quad"] = [list(p) for p in quad] if quad else None
                    p1 = (int(x1 * w) - cx1, int(y1 * h) - cy1)
                    p2 = (int(x2 * w) - cx1, int(y2 * h) - cy1)
                    if quad:
                        th = max(2, (cx2 - cx1) // 200)
                        pts = np.array([(int(px * w) - cx1, int(py * h) - cy1) for px, py in quad], np.int32)
                        cv2.polylines(view, [pts], True, (0, 255, 0), th)
                        # the screen in 3 x 3 parts, each with the colour read there (Oct 28, owner: "9 boxes, each
                        # one with its colour, to make it more clear")
                        at = lambda u, v: (int(quad_point(quad, u, v)[0] * w) - cx1, int(quad_point(quad, u, v)[1] * h) - cy1)
                        for i in (1, 2):
                            cv2.line(view, at(i / 3, 0), at(i / 3, 1), (0, 200, 0), max(1, th // 2))
                            cv2.line(view, at(0, i / 3), at(1, i / 3), (0, 200, 0), max(1, th // 2))
                        fh, fw = box.shape[:2]
                        r = max(8, (cx2 - cx1) // 45)
                        rs = max(4, r // 2)  # the 45 points (Nov 6): each in the colour it reads
                        for u, v, sc in (eyes.SAMPLES or []):
                            c = at(u, v)
                            if sc is not None and not eyes.SCREEN_OFF:
                                cv2.circle(view, c, rs, tuple(int(x) for x in np.clip(sc, 0, 255)[::-1]), -1)
                                cv2.circle(view, c, rs, (255, 255, 255), 1)
                            else:  # dark there: an empty ring
                                cv2.circle(view, c, rs, (128, 128, 128), 1)
                        smap, zones = config.STRIP_MAP, eyes.screen_zones
                        if smap and zones and len(zones) == len(smap):  # TvD's parts where they sit, in their colours
                            zs = zones[::-1] if SCREEN_SEGMENTS_FROM_RIGHT else zones
                            for (u, v), zc in zip(smap, zs):
                                c = at(u, v)
                                cv2.rectangle(view, (c[0] - r // 2, c[1] - r // 2), (c[0] + r // 2, c[1] + r // 2),
                                              tuple(int(x) for x in np.clip(zc, 0, 255)[::-1]), -1)
                                cv2.rectangle(view, (c[0] - r // 2, c[1] - r // 2), (c[0] + r // 2, c[1] + r // 2),
                                              (255, 255, 255), 1)
                    else:
                        cv2.rectangle(view, p1, p2, (0, 255, 0), max(2, (cx2 - cx1) // 200))
                    scale = 640 / max(1, cx2 - cx1)
                    view = cv2.resize(view, (640, max(1, int((cy2 - cy1) * scale))))
                    ok, buf = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    if ok:
                        eyes.CAM_JPG = buf.tobytes()
        except Exception as e:
            err = f"can't reach camera: {e}"
            eyes.screen_rgb = eyes.screen_zones = eyes.SCREEN_SECOND = None
            eyes.CAM_OK = False
        if err and err != last_err:
            print("camera:", err, "(using music colors meanwhile)", flush=True)
        elif not err and last_err:
            print("camera: OK, screen colors working", flush=True)
        last_err = err
        time.sleep(1.0 / fps if stream is not None else 1.0)  # TV mode / gaming: the newest picture, fps times a second
