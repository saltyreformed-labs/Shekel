"""The weekly image scan's judge (plan#2444; ruling plan#3720): the scanner's JSON, read as data
and judged here.  Its findings stay off GitHub: no issue, log or artifact there holds them, as
an open security finding is never public (ruling plan#3785).

**The one judge.**  A match counts when Grype knows its fix (its ``fix.state`` is ``fixed``) and
the highest rating any source gives it is HIGH or CRITICAL (ruling plan#3720): the match's own,
which for a Debian package is Debian's, or that of any record related to it, NVD's among them.
On dev's image 2026-11-13, of 422 matches 7 had a fix, each in the interpreter (CPython 3.14.8,
which Grype finds by its binary), and one of them was rated CRITICAL; 20 of Debian's packages'
matches, of 10 CVEs, had no fix and were negligible to Debian but HIGH or CRITICAL to NVD.  Each
match counted is one :class:`Fixed` for each package it was found in, its CVE as Grype names it.

**Read as data.**  What a package's name and version say is written by what the image holds.
So :func:`judged` refuses the scan whole (:class:`NotAScan`) where it is not the shape Grype
writes, where a fix state or a rating deciding a match is one Grype does not spell (read at its
pinned v0.121.2, :data:`FIX_STATES`, :data:`RATINGS`), and where a value a counted match names
holds a character outside :data:`_LISTABLE`: none can then close a code span it is quoted in,
mention anyone, or spell a marker that ``quill sync`` reads.  A scan refused is never read as a
clean one.

**It imports the standard library alone**, and :mod:`tools.update.app`, which does too, so it
runs on an interpreter given no installed package.

Usage, from the root of a checkout, where ``<scan.json>`` is Grype's JSON::

    python -m tools.update.scan <scan.json>

It prints how many matches count.  Exit status: 0 the scan was judged, red or clean; 1 it was
refused or could not be read, saying why.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from tools.update.app import marker

#: Grype's ratings, lowest first, as it reads each, ignoring case: ``ParseSeverity``, which reads
#: any other spelling as unknown (``grype/vulnerability/severity.go``, read 2026-11-13 at
#: v0.121.2).  A rating may be missing (its JSON field is ``omitempty``,
#: ``grype/presenter/models/vulnerability_metadata.go``), which Grype reads as unknown too; any
#: other spelling deciding a match is refused, as a rating this scale does not hold, ``Important``
#: say, could be a HIGH one read as nothing.
RATINGS = ("unknown", "negligible", "low", "medium", "high", "critical")
#: The lowest rating that turns the scan red (ruling plan#3720).
RED = RATINGS.index("high")
#: The fix state of a match whose fix Grype knows.
FIXED = "fixed"
#: Every fix state Grype writes: the four of ``AllFixStates`` (``grype/vulnerability/fix.go``,
#: read 2026-11-13 at v0.121.2), and none, which it writes for a match whose record holds no
#: metadata (``grype/presenter/models/vulnerability.go``'s ``NewVulnerability``).
FIX_STATES = frozenset({FIXED, "not-fixed", "wont-fix", "unknown", ""})
#: What each value a counted match names may hold: letters and digits, then those and the marks
#: the names and versions of Debian's packages, of PEP 440 and of Go's modules use.  No space,
#: backtick, ``<``, ``>`` or ``@``, so nothing it holds can end a code span it is quoted in,
#: mention anyone or spell a marker; and no comma, which would part a list of versions.
_LISTABLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~:!/-]*")


class NotAScan(ValueError):
    """The JSON is no scan as Grype writes one, or holds what a counted match cannot name; the
    message says what and where."""


@dataclass(frozen=True)
class Fixed:
    """One match the scan counts (the module's one judge): ``vulnerability``, the CVE as Grype
    names it; the package it was found in, by its ``kind`` (Grype's type: ``deb``, ``python``,
    ``binary``), its ``package`` name and its ``installed`` version; ``fixes``, each version
    Grype names the fix in, in its order; and ``rating``, the highest any source gives it, an
    index of :data:`RATINGS`."""

    vulnerability: str
    kind: str
    package: str
    installed: str
    fixes: tuple[str, ...]
    rating: int

    @property
    def marker(self) -> str:
        """The marker naming the CVE and the package (:func:`tools.update.app.marker`), the
        same whatever version is installed or fixes it, so a CVE still unfixed the next week
        is the same thing found again."""
        return marker(f"scan {self.vulnerability} {self.kind} {self.package}")


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


def _rating(record: object, where: str) -> int:
    """The rating ``record``, a vulnerability's, gives it, as an index of :data:`RATINGS`: a
    missing one as unknown, as Grype reads it; :class:`NotAScan` for any spelling Grype's scale
    does not hold."""
    spelled = record.get("severity", "") if isinstance(record, dict) else None
    if not isinstance(spelled, str) or spelled.lower() not in ("", *RATINGS):
        raise NotAScan(f"{where} is rated {spelled!r}, which is no rating Grype gives")
    return RATINGS.index(spelled.lower() or "unknown")


def _counted(match: object, where: str) -> Fixed | None:
    """``match``, one of the scan's, as a :class:`Fixed`, or None when it does not count (the
    module's one judge)."""
    vulnerability = _value(match, "vulnerability", dict, where)
    fix = _value(vulnerability, "fix", dict, f"{where}'s vulnerability")
    if (state := _value(fix, "state", str, f"{where}'s fix")) not in FIX_STATES:
        raise NotAScan(f"{where}'s fix state is {state!r}, which Grype does not write")
    if state != FIXED:
        return None
    related = _value(match, "relatedVulnerabilities", list, where)
    rating = max(_rating(record, f"{where}'s {name}") for name, record in (
        ("vulnerability", vulnerability),
        *((f"related vulnerability {index}", record) for index, record in enumerate(related))))
    if rating < RED:
        return None
    artifact = _value(match, "artifact", dict, where)
    fixes = _value(fix, "versions", list, f"{where}'s fix")
    return Fixed(_listable(vulnerability.get("id"), f"{where}'s vulnerability's id"),
                 *(_listable(artifact.get(key), f"{where}'s artifact's {key}")
                   for key in ("type", "name", "version")),
                 tuple(_listable(version, f"{where}'s fix's version {index}")
                       for index, version in enumerate(fixes)),
                 rating)


def judged(scan: object) -> list[Fixed]:
    """Each match of ``scan``, Grype's JSON as read, that the scan counts (the module's one
    judge), each once, the highest rated first, then by its CVE's id as text, the package's
    type and name, and the version installed.

    Raises:
        NotAScan: ``scan`` is no scan as Grype writes one, or holds what a counted match cannot
            name; the message says where.
    """
    found = {item for index, match in enumerate(_value(scan, "matches", list, "the scan"))
             if (item := _counted(match, f"match {index}")) is not None}
    return sorted(found, key=lambda item: (-item.rating, item.vulnerability, item.kind,
                                           item.package, item.installed))


def read(path: Path) -> object:
    """The JSON the file at ``path`` holds.

    Raises:
        NotAScan: the file holds no UTF-8 JSON.
        OSError: as reading the file raises it.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NotAScan(f"it is no JSON: {error}") from error


def main(argv: Sequence[str] | None = None) -> int:
    """Judge the scan the command line names, saying how many of its matches count."""
    parser = argparse.ArgumentParser(prog="python -m tools.update.scan",
                                     description="The weekly image scan's judge (ruling "
                                                 "plan#3720).")
    parser.add_argument("scan", type=Path, help="the scanner's JSON")
    args = parser.parse_args(argv)
    try:
        found = judged(read(args.scan))
    except (OSError, NotAScan) as error:
        print(f"update scan: {args.scan} was refused: {error}", file=sys.stderr)
        return 1
    print(f"update scan: CVEs with a fix rated HIGH or CRITICAL: {len(found)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
