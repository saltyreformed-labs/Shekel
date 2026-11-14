"""The daily image scan's inputs read as data (:mod:`tools.update.scan`, its paragraph
read as data): the refusal of one not as Grype, the image's python or uv writes it
(:class:`NotAScan`), and the readers each part of the scan shares.  It imports the standard
library alone (ruling plan#3825)."""
from __future__ import annotations

import json
import re

#: What each value a counted match names may hold: letters and digits, then those and the marks
#: the names and versions of Debian's packages, of PEP 440 and of Go's modules use.  No space,
#: backtick, ``<``, ``>`` or ``@``, so nothing it holds can end a code span it is quoted in,
#: mention anyone or spell a marker; and no comma, which would part a list of versions.
_LISTABLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~:!/-]*")


class NotAScan(ValueError):
    """The scanner's answer is no scan as Grype writes one, read from a database missing a
    source the scan relies on or holding one captured too long before it, holds what a counted
    match cannot name, or names no commit the scanned image was built from; its catalog, the
    image's python's report, ``uv tree``'s answer or the Dockerfile's ``uv sync`` is not as
    written, or names a platform uv cannot be told; or the scan did not see what the image runs
    (:func:`tools.update.scan_seen.seen`, :func:`tools.update.scan.held`); the message says what
    and where."""


def _value(node: object, key: str, kind: type, where: str):
    """``node``'s ``key``, which must be a ``kind``; :class:`NotAScan` saying ``where`` when
    ``node`` is no object or holds no such value."""
    if not isinstance(node, dict) or not isinstance(value := node.get(key), kind):
        raise NotAScan(f"{where} holds no {key} that is a {kind.__name__}")
    return value


def _listable(value: object, where: str) -> str:
    """``value``, a string a counted match names; :class:`NotAScan` when it is none, or holds a
    character outside :data:`_LISTABLE`."""
    if not isinstance(value, str) or not _LISTABLE.fullmatch(value):
        raise NotAScan(f"{where} is {value!r}, which cannot be listed")
    return value


def parsed(answer: bytes) -> object:
    """The JSON the scanner's ``answer``, its standard output, holds.

    Raises:
        NotAScan: the answer holds no UTF-8 JSON.
    """
    try:
        return json.loads(answer.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NotAScan(f"it is no JSON: {error}") from error
