"""pose.py - a person's body points from a picture (Nov 9, owner: "I only have the Pi and the camera, I'm down for a
better solution" -> a body-pose model instead of movement). Google's MoveNet Lightning (Apache 2.0, the ONNX copy from
huggingface.co/Xenova/movenet-singlepose-lightning), on the CPU. The picture becomes 17 points (numbers) and is dropped.

Two passes like MoveNet's own cropping: the whole picture first; once a person is found, the next pictures are cut
around them (a person far from a wide camera is small in 192 x 192), which sharpens the points a lot."""
import numpy as np
import onnxruntime as ort

NAMES = ["nose", "l_eye", "r_eye", "l_ear", "r_ear", "l_shoulder", "r_shoulder", "l_elbow", "r_elbow", "l_wrist", "r_wrist",
         "l_hip", "r_hip", "l_knee", "r_knee", "l_ankle", "r_ankle"]
SIZE = 192
MIN_SCORE = 0.3      # a point counts as seen
PERSON_POINTS = 5    # this many points seen (with a shoulder or a hip): a person


class Pose:
    def __init__(self, model="movenet.onnx", threads=2):
        o = ort.SessionOptions()
        o.intra_op_num_threads = threads
        self.s = ort.InferenceSession(model, sess_options=o, providers=["CPUExecutionProvider"])
        self.inp = self.s.get_inputs()[0].name
        self.crop = None  # (y0, x0, y1, x1) in the picture, 0-1, from the last person found

    @staticmethod
    def _square(img, box):
        """The part of the picture in box (0-1, may reach outside: padded black), as SIZE x SIZE RGB int32."""
        h, w = img.shape[:2]
        y0, x0, y1, x1 = box
        ys = np.clip(((np.arange(SIZE) + 0.5) / SIZE * (y1 - y0) + y0) * h, -1, h).astype(int)
        xs = np.clip(((np.arange(SIZE) + 0.5) / SIZE * (x1 - x0) + x0) * w, -1, w).astype(int)
        oky, okx = (ys >= 0) & (ys < h), (xs >= 0) & (xs < w)
        out = np.zeros((SIZE, SIZE, 3), np.int32)
        out[np.ix_(oky, okx)] = img[np.ix_(ys[oky], xs[okx])]
        return out

    def _run(self, img, box):
        k = self.s.run(None, {self.inp: self._square(img, box)[None]})[0][0, 0]  # 17 x (y, x, score) in the square
        y0, x0, y1, x1 = box
        pts = np.empty_like(k)
        pts[:, 0] = y0 + k[:, 0] * (y1 - y0)
        pts[:, 1] = x0 + k[:, 1] * (x1 - x0)
        pts[:, 2] = k[:, 2]
        return pts

    @staticmethod
    def is_person(pts):
        seen = pts[:, 2] >= MIN_SCORE
        return int(seen.sum()) >= PERSON_POINTS and bool(seen[[5, 6, 11, 12]].any())

    def find(self, img):
        """img: H x W x 3 RGB. -> (person?, points 17 x (y, x, score) with y, x in 0-1 of the picture)."""
        h, w = img.shape[:2]
        full = (0.5 - 0.5 * max(w / h, 1), 0.5 - 0.5 * max(h / w, 1), 0.5 + 0.5 * max(w / h, 1), 0.5 + 0.5 * max(h / w, 1))
        pts = self._run(img, self.crop or full)
        if not self.is_person(pts):
            # not found (or lost in the crop): search - the whole picture, then closer sections side by side (in a wide
            # picture a person across the room is ~30 of 192 pixels: too small for one pass, Nov 9 room test)
            self.scan = getattr(self, "scan", 0) + 1
            boxes = [full] if self.crop else []
            if w > h:
                side = h / w
                n = 4
                boxes += [(0.0, x0, 1.0, x0 + side) for x0 in np.linspace(0, 1 - side, n)]
                half = side / 2  # and closer still: two rows of smaller squares, one section a call (taken in turns)
                small = [(y0, x0, y0 + 0.5, x0 + half) for y0 in (0.0, 0.25, 0.5) for x0 in np.linspace(0, 1 - half, 6)]
                boxes.append(small[self.scan % len(small)])
            best = pts
            for b in boxes:
                cand = self._run(img, b)
                if self.is_person(cand) and (not self.is_person(best) or cand[:, 2].sum() > best[:, 2].sum()):
                    best = cand
            pts = best
        if not self.is_person(pts):
            self.crop = None
            return False, pts
        seen = pts[pts[:, 2] >= MIN_SCORE]
        cy, cx = (seen[:, 0].min() + seen[:, 0].max()) / 2, (seen[:, 1].min() + seen[:, 1].max()) / 2
        # a square around the person, 1.5 x their size (in pixels, so it's square in the picture), at least 0.4 of it
        side = max((seen[:, 0].max() - seen[:, 0].min()) * h, (seen[:, 1].max() - seen[:, 1].min()) * w) * 1.5
        side = max(side, 0.4 * min(h, w))
        self.crop = (cy - side / 2 / h, cx - side / 2 / w, cy + side / 2 / h, cx + side / 2 / w)
        return True, pts
