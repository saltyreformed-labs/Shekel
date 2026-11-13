"""``quill scan``: the daily scan of the image production is running, from this host (rulings
plan#3785, plan#3786, plan#3820, plan#3822, plan#3825).  :func:`tools.update.scan.scanned`
reads production's image, scans it and judges it; this door says what it found, and writes
nothing anywhere yet, the tracker included, so it never connects to it (plan#3797).

A scan whose answer is refused (:class:`tools.update.scan.NotAScan`) is refused, exit 1; one
that could not run (:class:`tools.update.scan.ScanFailed`) is a failed call, exit 2 (the
command module's exit statuses).  Neither is ever read as a clean scan.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from tools.quill._exits import Refused
from tools.quill._tracker import Tracker
from tools.update import scan


def cmd_scan(_args, _connect: Callable[[], Tracker], root: Path) -> int:
    """Scan the image production is running from the checkout at ``root``, and print the
    image's digest, the commit it was built from, how many matches count, and each one: its
    CVE, its rating, the package it is in and each version Grype names its fix in.

    Raises:
        Refused: the scanner's answer is no scan, or names no commit.
    """
    try:
        done = scan.scanned(root)
    except scan.NotAScan as error:
        raise Refused(f"the scan of production's image was refused: {error}") from error
    print(f"scan: production's image {done.image}, built from {done.revision}")
    print(f"scan: CVEs with a fix rated HIGH or CRITICAL: {len(done.found)}")
    for item in done.found:
        print(f"  {item.vulnerability}, rated {scan.RATINGS[item.rating]}: {item.kind} "
              f"{item.package} {item.installed}, fixed in {', '.join(item.fixes)}")
    return 0
