"""Portable date formatting.

`%-d` (day of month without the leading zero) is a glibc/BSD strftime
extension: Windows' C runtime rejects it with "ValueError: Invalid format
string", which crashed /dashboard on native Windows. strftime() below keeps
the familiar spelling and substitutes the value itself before formatting.
Also registered as the `strftime` Jinja filter (see state.py).
"""

import re

# %-d, %-m, %-H, %-M, %-S, %-I, %-j: the unpadded variants in use or likely
_UNPADDED = {
    "d": lambda v: v.day,
    "m": lambda v: v.month,
    "H": lambda v: v.hour,
    "M": lambda v: v.minute,
    "S": lambda v: v.second,
    "I": lambda v: (v.hour % 12) or 12,
    "j": lambda v: v.timetuple().tm_yday,
}
_UNPADDED_RE = re.compile(r"%-([" + "".join(_UNPADDED) + "])")


def strftime(value, fmt: str) -> str:
    """value.strftime(fmt), accepting `%-d`-style directives on every OS."""
    portable = _UNPADDED_RE.sub(lambda m: str(_UNPADDED[m.group(1)](value)), fmt)
    return value.strftime(portable)
