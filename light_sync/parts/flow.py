"""How Party's colours flow along the lights (Movement: flow, flow back, bounce, together).
Nov 6: Effects mode was removed (owner: "delete anything we are not using"); this is what Party still needs from it."""

import numpy as np

from parts import config


FLOW_OFFSET = {"screen": 1.0, "beat": 0.5, "wash": 0.0}  # flow style: TvD leads, Corner follows, then Top

def flow_positions(x):
    """Each light's place in the flowing palette at flow position x (one step per "Change colour"), and when the
    beat's ripple reaches it (s). bounce = a new color travels TvD -> Corner -> Top, the next one back
    Top -> Corner -> TvD, and so on (each light takes the new color in turn: no jump when it turns around)."""
    if config.STYLE == "bounce":
        k = int(np.floor(x))
        f = x - k
        order = {"screen": 0, "beat": 1, "wash": 2} if k % 2 == 0 else {"wash": 0, "beat": 1, "screen": 2}
        return ({key: k + min(1.0, max(0.0, (f - i / 3) * 3)) for key, i in order.items()},
                {key: 0.09 * i for key, i in order.items()})
    offs, delays = flow_offsets()
    return {key: x + o for key, o in offs.items()}, delays

def flow_offsets():
    """Movement: where each light sits in the flowing palette, and when the beat's ripple reaches it (s).
    flow = colors travel TvD -> Corner -> Top, flow back = Top -> Corner -> TvD, together = all on one color."""
    if config.STYLE == "flow back":
        return {"screen": 0.0, "beat": 0.5, "wash": 1.0}, {"wash": 0.0, "beat": 0.09, "screen": 0.18}
    if config.STYLE == "together":
        return {"screen": 0.0, "beat": 0.0, "wash": 0.0}, {"screen": 0.0, "beat": 0.0, "wash": 0.0}
    return FLOW_OFFSET, {"screen": 0.0, "beat": 0.09, "wash": 0.18}
