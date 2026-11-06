"""Message identifiers.

IDs sort chronologically and double as archive filenames, e.g.
``2026-11-06_193210_k3f9``. The random suffix avoids collisions if two
messages ever start in the same second (or the clock jumps backwards).
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime

# No 0/o, 1/l/i: easy to read aloud and type.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}_[a-z0-9]{4}$")


def new_message_id(when: datetime) -> str:
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(4))
    return f"{when.strftime('%Y-%m-%d_%H%M%S')}_{suffix}"


def is_message_id(value: str) -> bool:
    return bool(ID_RE.match(value))
