"""Latitude and longitude as people write them.

Workflow parameters store positions in decimal degrees, south and west
negative. People copy them from a bridge display, a station log or a GPS
receiver, so a coordinate parameter also accepts

    37.33663        -37,33663        37.33663 S
    37° 20.198      37°20.198'N      N 37 20.198      37 20.198 S
    37° 20' 11.9"   37d 20m 11.9s    37:20:11.9       021° 00.0 W / O
    3720.198 N      (NMEA ddmm.mmm, only with a hemisphere letter)

Primes may be ' ’ ′ ″ " or two apostrophes; the degree sign ° º ˚ or d;
the decimal separator a point or a comma. The hemisphere letter may lead
or trail (O for German Ost). A minus sign and S/W together is refused as
ambiguous, as are minutes or seconds of 60 and more.
"""

import re

AXES = {"lat": 90.0, "lon": 180.0}
_KEYS = {"lat": "lat", "latitude": "lat", "lon": "lon", "lng": "lon", "longitude": "lon"}
_HEMI = {"N": ("lat", 1), "S": ("lat", -1), "E": ("lon", 1), "O": ("lon", 1), "W": ("lon", -1)}
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
# What may surround the numbers: unit marks, separators, space.
_ALLOWED_REST = re.compile(r"^[\s°dms'\":,]*$")


def axis_of(key, prop):
    """'lat' or 'lon' when a parameter holds a coordinate, else None: by
    `format: latitude|longitude` in its schema, or by its name."""
    fmt = (prop or {}).get("format")
    if fmt in ("latitude", "longitude"):
        return fmt[:3]
    if (prop or {}).get("type", "number") != "number":
        return None
    return _KEYS.get(str(key).lower())


def parse(text, axis):
    """Decimal degrees from a written coordinate; ValueError says what is
    wrong with it in words a form can show."""
    if isinstance(text, (int, float)):
        value = float(text)
        _check_range(value, axis)
        return value
    original = str(text)
    s = original.strip()
    if not s:
        raise ValueError("empty")
    s = (s.replace("''", '"').replace("’", "'").replace("‘", "'").replace("′", "'")
          .replace("″", '"').replace("“", '"').replace("”", '"')
          .replace("º", "°").replace("˚", "°"))
    # d/m/s written as units (37d 20m 11.9s): then an 's' right after the
    # last number is seconds, not South.
    dms_units = re.search(r"\d\s*[dm](?![a-zA-Z])", s) is not None

    # hemisphere: one letter at either end
    hemi = None
    lead = re.match(r"^([NSEWOnsewo])\s*(?=[\d+\-−])", s)
    trail = re.search(r"(?:(?<=[\d°'\"\s:])|(?<=\d[dms]))\s*([NSEWOnsewo])$", s)
    if trail and dms_units and trail.group(1) == "s" and re.search(r"\d\s*s$", s) \
            and not re.search(r"\d\s*s\s*[NSEWOnsewo]$", s):
        trail = None
    if lead and trail:
        raise ValueError("two hemisphere letters")
    if lead or trail:
        letter = (lead or trail).group(1).upper()
        s = s[lead.end():] if lead else s[:trail.start()]
        s = s.strip()
        hemi_axis, hemi_sign = _HEMI[letter]
        if hemi_axis != axis:
            raise ValueError(f"'{letter}' is a {'latitude' if hemi_axis == 'lat' else 'longitude'} "
                             f"hemisphere, this field is a {'latitude' if axis == 'lat' else 'longitude'}")
        hemi = hemi_sign

    sign = 1
    if s[:1] in "+-−":
        sign = -1 if s[0] in "-−" else 1
        s = s[1:].strip()
        if hemi is not None and sign == -1:
            raise ValueError("both a minus sign and a hemisphere letter; use one of them")

    numbers = _NUMBER.findall(s)
    leftover = _NUMBER.sub(" ", s).replace("°", " ")
    if not numbers or not _ALLOWED_REST.match(leftover):
        raise ValueError(f"cannot read '{original.strip()}' as a coordinate")
    values = [float(n.replace(",", ".")) for n in numbers]
    has_fraction = ["," in n or "." in n for n in numbers]

    limit = AXES[axis]
    if len(values) == 1:
        value = values[0]
        if hemi is not None and value > limit and value < limit * 100 + 60:
            # NMEA ddmm.mmmm, as a receiver prints it
            degrees = int(value // 100)
            minutes = value - degrees * 100
            if minutes >= 60:
                raise ValueError("minutes must be below 60")
            value = degrees + minutes / 60
    elif len(values) == 2:
        degrees, minutes = values
        if has_fraction[0]:
            raise ValueError("degrees with a fraction cannot be followed by minutes")
        if minutes >= 60:
            raise ValueError("minutes must be below 60")
        value = degrees + minutes / 60
    elif len(values) == 3:
        degrees, minutes, seconds = values
        if has_fraction[0] or has_fraction[1]:
            raise ValueError("only the last part (seconds) may have a fraction")
        if minutes >= 60 or seconds >= 60:
            raise ValueError("minutes and seconds must be below 60")
        value = degrees + minutes / 60 + seconds / 3600
    else:
        raise ValueError(f"cannot read '{original.strip()}': more than degrees, minutes and seconds")

    value *= sign * (hemi or 1)
    _check_range(value, axis)
    return round(value, 7)


def _check_range(value, axis):
    limit = AXES[axis]
    if not -limit <= value <= limit:
        raise ValueError(f"{'latitude' if axis == 'lat' else 'longitude'} must lie "
                         f"between -{limit:g} and {limit:g}")
