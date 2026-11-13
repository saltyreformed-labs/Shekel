"""The weekly image scan's report (plan#2444; rulings plan#3710, plan#3720): the scanner's JSON,
read as data and judged here, and each CVE it finds told in an issue of this repository's own,
which ``quill sync`` files a card from for each CVE no card holds yet.

**The one judge.**  A match counts when Grype knows its fix (its ``fix.state`` is ``fixed``) and
the highest rating any source gives it is HIGH or CRITICAL (ruling plan#3720): the match's own,
which for a Debian package is Debian's, or that of any record related to it, NVD's among them.
On dev's image 2026-11-13, of 422 matches 7 had a fix, each in the interpreter (CPython 3.14.8,
which Grype finds by its binary), and one of them was rated CRITICAL; 20 of Debian's packages'
matches, of 10 CVEs, had no fix and were negligible to Debian but HIGH or CRITICAL to NVD.  Each
match counted is listed once for each package it was found in (:class:`Fixed`), its CVE as
Grype names it.

**Read as data.**  The JSON comes from the scan job, whose build ran dev's package code, to the
report's job, which holds the workflow's token widened to issues; and what a package's name and
version say is written by what the image holds.  So :func:`judged` refuses the scan whole
(:class:`NotAScan`) where it is not the shape Grype writes, where a fix state or a rating
deciding a match is one Grype does not spell (read at its pinned v0.121.2, :data:`FIX_STATES`,
:data:`RATINGS`), and where a value the issue would list holds a character outside
:data:`_LISTABLE`: none can then close its code span, mention anyone, or spell a marker that
``quill sync`` reads.  A refusal fails the report's job, which the alert reports, as it reports
a scan that cannot run, whose own job fails (ruling plan#3710).

**State is read, never stored.**  Each run lists the repository's open issues and finds its own
(:func:`tools.update.alert.the_workflows`): one the workflow's own token opened whose title
begins :data:`FOUND`.  The repository is public, so anyone may open an issue so titled, and a
stranger's is neither read nor closed.  Then:

- a red scan opens an issue of its own (:func:`issue`), and only once it is open closes each of
  its own opened before, so a refused write leaves the earlier list standing;
- a clean scan closes each of its own and opens none.

Its issue mentions no one: the card ``quill sync`` files for each CVE it lists is what reaches
the developer, at the top of the board (rulings plan#3619, plan#3620), as for the update's other
waits, and a mention each week a CVE stays unfixed would be the noise plan#3552 removed.  Each
line it lists begins naming the CVE and the package in one form, which ``quill sync`` reads
back (:func:`lines_of`) as what its card is for, and ends with a marker
(:func:`tools.update.app.marker`) naming them (:attr:`Fixed.marker`), so a CVE still unfixed
the next week, listed in a new issue, is the same thing told again and files no second card.

**It imports the standard library alone.**  Its job installs the lock's interpreter and no
package (``install: python``, as every job's Python comes from the lock, ruling
balance:R-BAL294), and the runner's own ``gh`` reads and writes the issues
(:func:`tools.update.alert.listed`, :func:`tools.update.alert.sent`).

Usage, from the root of a checkout of ``main``, where ``<scan.json>`` is Grype's JSON and
``<run-url>`` the scan's run::

    GH_TOKEN=<token> GITHUB_REPOSITORY=<owner>/<name> \\
        python -m tools.update.scan <scan.json> <run-url>

It prints a line for each write and one saying how many it listed.  Exit status: 0 the issues
are as the scan says, red or clean; 1 the scan was refused or could not be read, a read or a
write of an issue failed, each other write still tried but the closes after a refused open, or a
variable is unset, saying which.  A write GitHub refuses, an issue too long for it among them,
so fails the job, which the alert reports, and leaves the earlier issue open.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from tools.update.alert import CLOSED, ISSUE_TOKEN_VARIABLE, listed, sent, the_workflows
from tools.update.app import REPOSITORY_VARIABLE, marker, unset

#: How the title of each issue this module opens begins, the scan's day after it: what it knows
#: its own by, beside the bot that opened it, and ``quill sync`` too.
FOUND = "The weekly image scan found fixed CVEs "
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
#: What each value the issue lists may hold: letters and digits, then those and the marks the
#: names and versions of Debian's packages, of PEP 440 and of Go's modules use.  No space,
#: backtick, ``<``, ``>`` or ``@``, so nothing it holds can end the code span it is listed in,
#: mention anyone or spell a marker; and no comma, which parts the fixed versions.
_LISTABLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~:!/-]*")
#: How each line the issue lists begins (:func:`_line`): the match's CVE, then its package's
#: name and its type, each in a code span; and the expression reading it back at a line's
#: start, made from it, each value as :data:`_LISTABLE` holds one (:func:`lines_of`).
_NAMED = "- `{}` in `{}` (`{}`) "
_NAMED_READ = re.compile(re.escape(_NAMED).replace(r"\{\}", f"({_LISTABLE.pattern})"))


class NotAScan(ValueError):
    """The JSON is no scan as Grype writes one, or holds what the issue cannot list; the message
    says what and where."""


@dataclass(frozen=True)
class Fixed:
    """One match the scan counts (the module's one judge), as its issue lists it:
    ``vulnerability``, the CVE as Grype names it; the package it was found in, by its
    ``kind`` (Grype's type: ``deb``, ``python``, ``binary``), its ``package`` name and its
    ``installed`` version; ``fixes``, each version Grype names the fix in, in its order; and
    ``rating``, the highest any source gives it, an index of :data:`RATINGS`."""

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
        is the same thing told again."""
        return marker(f"scan {self.vulnerability} {self.kind} {self.package}")


def _value(node: object, key: str, kind: type, where: str):
    """``node``'s ``key``, which must be a ``kind``; :class:`NotAScan` saying ``where`` when
    ``node`` is no object or holds no such value."""
    if not isinstance(node, dict) or not isinstance(value := node.get(key), kind):
        raise NotAScan(f"{where} holds no {key} that is a {kind.__name__}")
    return value


def _listable(value: object, where: str) -> str:
    """``value``, a string the issue lists; :class:`NotAScan` when it is none, or holds a
    character outside :data:`_LISTABLE`."""
    if not isinstance(value, str) or not _LISTABLE.fullmatch(value):
        raise NotAScan(f"{where} is {value!r}, which the issue cannot list")
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
    """``match``, one of the scan's, as the issue lists it, or None when it does not count (the
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
    judge), each listed once, the highest rated first, then by its CVE's id as text, the
    package's type and name, and the version installed.

    Raises:
        NotAScan: ``scan`` is no scan as Grype writes one, or holds what the issue cannot
            list; the message says where.
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


def _line(item: Fixed) -> str:
    """``item`` as its issue lists it, each value in a code span, its marker last."""
    fixes = ", ".join(f"`{version}`" for version in item.fixes) or "no version Grype names"
    return (_NAMED.format(item.vulnerability, item.package, item.kind) + f"`{item.installed}`, "
            f"rated {RATINGS[item.rating].upper()}, fixed in {fixes} {item.marker}")


def lines_of(body: str) -> list[tuple[str, str, str, str]]:
    """Each line of ``body``, the text of an issue this module opened (:func:`issue`), that
    lists a match, in its order: the CVE, the package's name and its type, as the line begins
    with them (:data:`_NAMED`), and the line whole, its marker last.  ``quill sync``'s read of
    the issue (``tools/quill/_waits.py``); a line beginning otherwise lists nothing."""
    return [(*named.groups(), line) for line in body.splitlines()
            if (named := _NAMED_READ.match(line)) is not None]


def issue(day: date, link: str, found: Sequence[Fixed]) -> dict[str, str]:
    """The issue telling each of ``found`` that the scan of ``day``, its run at ``link``,
    counted: its title and body, which mentions no one (the module says why)."""
    lines = "\n".join(_line(item) for item in found)
    return {"title": f"{FOUND}{day:%Y-%m-%d}",
            "body": "The weekly image scan of `dev`'s image found these CVEs, each with a fix "
                    "and rated HIGH or CRITICAL by some source (ruling plan#3720), in the run "
                    f"{link}:\n\n{lines}\n\n"
                    "`quill sync` files a card for each one no card holds yet (ruling "
                    "plan#3710). The next red scan closes this issue as it opens its own, and "
                    "a clean one closes it."}


def run(repository: str, opening: dict[str, str] | None,
        listing: Callable[[str], subprocess.CompletedProcess] = listed,
        send: Callable[..., subprocess.CompletedProcess] = sent) -> int:
    """Bring ``repository``'s issues to what the scan says (the module's two cases): open
    ``opening``, the issue a red scan opens (:func:`issue`), None for a clean scan, then close
    each of its own open before; printing each write.  Exit as the module says."""
    read_back = listing(repository)
    if read_back.returncode:
        print(f"update scan: no issue was read: {read_back.stderr.strip()}", file=sys.stderr)
        return 1
    ours = [issue_read["number"] for issue_read in the_workflows(read_back.stdout)
            if issue_read["title"].startswith(FOUND)]
    if opening is not None:
        done = send(repository, opening)
        if done.returncode:
            print(f"update scan: no issue was opened, so none was closed: "
                  f"{done.stderr.strip()}", file=sys.stderr)
            return 1
        print(f"update scan: opened {done.stdout.strip()}")
    failed = 0
    for number in ours:
        done = send(repository, CLOSED, number)
        if done.returncode:
            print(f"update scan: issue {number} was not closed: {done.stderr.strip()}",
                  file=sys.stderr)
            failed = 1
        else:
            print(f"update scan: closed {done.stdout.strip()}")
    return failed


def main(argv: Sequence[str] | None = None,
         today: Callable[[], date] = lambda: datetime.now(UTC).date()) -> int:
    """Judge the scan the command line names and bring the issues of the repository the
    environment names to it, ``gh`` reading the token from it."""
    parser = argparse.ArgumentParser(prog="python -m tools.update.scan",
                                     description="The weekly image scan's report (rulings "
                                                 "plan#3710, plan#3720).")
    parser.add_argument("scan", type=Path, help="the scanner's JSON")
    parser.add_argument("run", help="the scan's run's page")
    args = parser.parse_args(argv)
    if missing := unset(ISSUE_TOKEN_VARIABLE, REPOSITORY_VARIABLE):
        print(f"update scan: {', '.join(missing)} unset, so nothing was read", file=sys.stderr)
        return 1
    try:
        found = judged(read(args.scan))
    except (OSError, NotAScan) as error:
        print(f"update scan: {args.scan} was refused: {error}", file=sys.stderr)
        return 1
    status = run(os.environ[REPOSITORY_VARIABLE],
                 issue(today(), args.run, found) if found else None)
    print(f"update scan: CVEs with a fix rated HIGH or CRITICAL: {len(found)}")
    return status


if __name__ == "__main__":
    sys.exit(main())
