"""The camera's field of view: the bright disc of the flash on a dark frame.

What a profile sampled is that disc times the depth of field, frame by
frame, so concentrations need it. Nothing in the pipeline records it — the
C++ segmenter does not mask — and it differs per instrument: PISCO3's disc
lies inside its 4504 px frame, PISCO2's is larger than its 2560 px frame
and only the corners are dark. So the circle is fitted to the edge of the
bright area wherever that edge is visible, and the sampled area is the
part of the circle inside the frame, not π·r².

Used by the new-workflow page's depth preview (the user confirms what is
found, the run keeps it as parameters) and by the Data Explorer for runs
made before that, from their profile_dataset.h5.
"""
import math

import numpy as np

# Workflow parameters that describe a run's optics.
DIAMETER = "fov_diameter_px"
CENTER_X = "fov_center_x_px"
CENTER_Y = "fov_center_y_px"
DEPTH = "depth_of_field_mm"
PIXEL = "pixel_size_um"

# What a run that does not say gets, by its instrument. The depth of field
# is the old pipeline's 500 mm; nobody has measured either camera's.
INSTRUMENT_DEFAULTS = {
    "PISCO2": {PIXEL: 22.5, "image_size": 2560},
    "PISCO3": {PIXEL: 17.13, "image_size": 4504},
}
DEFAULT_DEPTH_MM = 500.0
DEFAULT_PIXEL_UM = 22.5

# Frames are reduced to this many pixels on the long side before fitting:
# the edge still has hundreds of points, and a PISCO3 frame is 20 MB.
WORK_SIZE = 1200


class FovError(Exception):
    pass


def _reduce(frame):
    import cv2
    frame = np.asarray(frame)
    if frame.ndim == 3:
        frame = frame[..., 0]
    if frame.dtype != np.uint8:
        top = float(np.percentile(frame, 99.9)) or 1.0
        frame = np.clip(frame.astype(np.float32) * (255.0 / top), 0, 255).astype(np.uint8)
    height, width = frame.shape
    scale = min(1.0, WORK_SIZE / max(height, width))
    if scale < 1:
        frame = cv2.resize(frame, (round(width * scale), round(height * scale)),
                           interpolation=cv2.INTER_AREA)
    return frame, scale


def _fit_circle(x, y):
    """Least-squares circle through points (Kåsa): (cx, cy, r)."""
    a = np.column_stack([x, y, np.ones_like(x)])
    b = x * x + y * y
    (p, q, c), *_ = np.linalg.lstsq(a, b, rcond=None)
    cx, cy = p / 2, q / 2
    return cx, cy, math.sqrt(max(c + cx * cx + cy * cy, 0.0))


def detect(frames):
    """Fit the field of view on a few frames of one profile.

    The median of the frames removes the particles and any one odd frame
    (an empty flash, the window out of the water); a frame's own objects at
    the edge would otherwise bend the fit. Returns the circle in full-frame
    pixels, how well it fits, and how much of it lies inside the frame.
    """
    import cv2
    reduced = [_reduce(f) for f in frames]
    if not reduced:
        raise FovError("no frames")
    shape = reduced[0][0].shape
    scale = reduced[0][1]
    stack = [r for r, _ in reduced if r.shape == shape]
    image = np.median(np.stack(stack), axis=0).astype(np.uint8) if len(stack) > 1 else stack[0]
    image = cv2.GaussianBlur(image, (0, 0), 2)

    # Dark background against the lit disc: two populations, Otsu splits them.
    level, mask = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if count < 2:
        raise FovError("no bright area in the frames")
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    disc = np.where(labels == biggest, 255, 0).astype(np.uint8)
    disc = cv2.morphologyEx(disc, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    lit = float(np.count_nonzero(disc)) / disc.size
    if lit > 0.995:
        raise FovError("the whole frame is lit: no edge of the field of view to fit")
    if lit < 0.05:
        raise FovError("too little of the frame is lit to be the field of view")

    contours, _ = cv2.findContours(disc, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    points = max(contours, key=len)[:, 0, :].astype(float)
    h, w = disc.shape
    margin = 3
    inside = ((points[:, 0] > margin) & (points[:, 0] < w - 1 - margin) &
              (points[:, 1] > margin) & (points[:, 1] < h - 1 - margin))
    points = points[inside]
    if len(points) < 30:
        raise FovError("too little of the field of view's edge is visible")

    # Refit without the points far off the circle: a bright object or a
    # reflection touching the edge.
    x, y = points[:, 0], points[:, 1]
    keep = np.ones(len(x), bool)
    for _ in range(4):
        cx, cy, r = _fit_circle(x[keep], y[keep])
        residual = np.abs(np.hypot(x - cx, y - cy) - r)
        mad = float(np.median(residual[keep])) or 0.5
        new = residual < max(3.0 * 1.4826 * mad, 1.5)
        if new.sum() < 30 or (new == keep).all():
            break
        keep = new
    cx, cy, r = _fit_circle(x[keep], y[keep])
    rms = float(np.sqrt(np.mean((np.hypot(x[keep] - cx, y[keep] - cy) - r) ** 2)))

    # How much of the circumference was seen: a short arc fits poorly.
    angles = np.sort(np.arctan2(y[keep] - cy, x[keep] - cx))
    gaps = np.diff(np.concatenate([angles, angles[:1] + 2 * math.pi]))
    seen = 1.0 - float(gaps[gaps > 0.05].sum()) / (2 * math.pi)

    full_h, full_w = round(h / scale), round(w / scale)
    cx, cy, r = float(cx) / scale, float(cy) / scale, float(r) / scale
    area = area_in_frame(cx, cy, r, full_w, full_h)
    warning = None
    if rms > 0.025 * r:
        warning = ("the edge is ragged for a circle (fit off by "
                   f"{rms / scale:.0f} px): check it against the frame")
    elif seen < 0.15:
        warning = "only a short arc of the edge is visible, so the diameter is uncertain"
    return {
        "warning": warning,
        DIAMETER: round(2 * r, 1), CENTER_X: round(cx, 1), CENTER_Y: round(cy, 1),
        "width": full_w, "height": full_h,
        "area_px": round(area),
        "coverage": round(area / (full_w * full_h), 4),
        "inside_frame": round(area / (math.pi * r * r), 4) if r else 0.0,
        "fit_rms_px": round(rms / scale, 2),
        "edge_seen": round(seen, 3),
        "frames_used": len(stack),
        "threshold": float(level),
    }


def area_in_frame(cx, cy, r, width, height):
    """Pixels of the circle that lie inside a width × height frame:
    the vertical chord through every column, clipped to the frame."""
    if r <= 0:
        return 0.0
    x = np.arange(width) + 0.5 - cx
    half = np.sqrt(np.clip(r * r - x * x, 0, None))
    top = np.clip(cy - half, 0, height)
    bottom = np.clip(cy + half, 0, height)
    return float(np.sum(bottom - top))


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def optics(params, width=None, height=None, instrument=None):
    """A run's optics from its workflow parameters: diameter, centre,
    depth of field, pixel size and the litres one frame samples. None
    while there is no diameter. The centre defaults to the frame's;
    pixel size and frame size to the instrument's."""
    params = params or {}
    diameter = _number(params.get(DIAMETER))
    if diameter is None:
        return None
    defaults = INSTRUMENT_DEFAULTS.get(str(params.get("instrument") or instrument or ""), {})
    size = _number(params.get("image_size")) or defaults.get("image_size")
    width = width or size
    height = height or size
    if not width or not height:
        return None
    pixel = _number(params.get(PIXEL)) or defaults.get(PIXEL) or DEFAULT_PIXEL_UM
    depth = _number(params.get(DEPTH)) or DEFAULT_DEPTH_MM
    cx = params.get(CENTER_X)
    cy = params.get(CENTER_Y)
    cx = float(cx) if cx not in (None, "") else width / 2
    cy = float(cy) if cy not in (None, "") else height / 2
    area_px = area_in_frame(cx, cy, diameter / 2, int(width), int(height))
    area_mm2 = area_px * (pixel / 1000.0) ** 2
    return {
        DIAMETER: diameter, CENTER_X: cx, CENTER_Y: cy, DEPTH: depth, PIXEL: pixel,
        "width": int(width), "height": int(height),
        "area_px": area_px, "area_mm2": area_mm2,
        "litres_per_frame": area_mm2 * depth / 1e6,
    }
