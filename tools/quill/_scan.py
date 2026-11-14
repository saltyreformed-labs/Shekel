"""``quill scan``: the daily scan of the image production is running, from this host (rulings
plan#3785, plan#3786, plan#3820, plan#3822, plan#3825), which ``shekel-image-scan.timer`` runs
from ``quill sync``'s own clone at dev's tip (its units' sources in ``/opt/docker/scripts``,
ruling plan#3963).  :func:`tools.update.scan.scanned`
reads production's image, scans it and judges it; this door says what it found, and files it
into the private tracker by the pass ``quill sync`` files the weekly update's waits by
(:func:`_waits.file_told`), so a card holds what the scan found as one holds what the update
told, a line for each thing (:func:`_waits.told_line`), and nothing is filed twice.

**One open card for each component** (ruling plan#3787): a Debian package, a Python package,
or the interpreter, each by Grype's type and name (:func:`where`), which the card's lines name
as the place each CVE was found at, the same each day whatever version is installed; one
marker for each CVE in it (:attr:`tools.update.scan.Counted.marker`).  A CVE in a component
whose card is open is put on that card in a comment of quill's (ruling plan#3722's
mechanism); one in a component no card is open for is filed a card, the High step at the top
of the board (ruling plan#3620); and a CVE a card holds, open or closed, files nothing again,
so a dropped card keeps its CVEs quiet (ruling plan#3790), but for the closed cards the next
paragraph names, which hold nothing.  Only the CVEs no card holds are
filed or put on a card (:func:`found_items`), so a card filed after a component's card was
dropped lists the new CVEs alone.  The component with the most severe CVE is filed last, so it
is the board's top.

**A CVE still found once its fix is in the image is filed again** (ruling plan#3765): a closed
card whose fix the scanned image holds holds nothing (:func:`fixed`, the pass's
:meth:`_waits.Holdings.left_out`), so a CVE only such cards hold is filed as a new one is, on
a card of its own or the component's open card, which cites them (:func:`_again`).  A card's
fix is in the image when git
says the card shipped, its ``plan/ships/N`` in the merge of ``dev`` and ``main`` as a fetch
finds them now, and the commit the image was built from (its revision label, ruling plan#3786)
holds that file too.  So a card shipped into ``dev`` since the image was built holds its CVEs
until a release puts its fix in what production runs, and a dropped card, closed and never
shipped, holds them for good; so does a card shipped into the image and then reverted, which
git no longer says shipped.  A card git says shipped but the tracker shows open holds them too:
it is open.

**A scan refused or unable to run files a card too** (ruling plan#3788), once for each failure:
the card for the scan's failure (:data:`FAILURE`) holds one marker whatever the failure said
(:data:`FAILED_MARK`), and holds it only while it is open (:meth:`_waits.Holdings.open_alone`).
So the same failure the next day, or another, files nothing while that card is open; once it
is closed, shipped or dropped, the next failure files a card of its own.  A marker made of
what the failure said would differ from day to day wherever docker's or Grype's words name a
file of the run's own, a match's place in the scan or the database's age, and so file the same
failure each day; and a closed card holding the failure would keep a scan that fails again
after its fix quiet for good.  Applied under R-BAL207: the spec says "once per failure, not
each day it repeats", and these are the two mechanisms that would break it.

**What a card quotes.**  What the scan found, each value of it checked listable
(``scan._LISTABLE``), and what a failure said, which can quote a value the image wrote that no
check passed (``scan._listable``'s refusal), each in a code block fenced past every run of
backticks in it (:func:`_waits.block`): there it mentions no one, and no line of it holds a
thing told, as only the lines ending a text do (:func:`_waits.holds`).

A scan whose answer is refused (:class:`tools.update.scan.NotAScan`) is refused, exit 1; one
that could not run (:class:`tools.update.scan.ScanFailed`, or git or the system failing it) is
a failed call, exit 2 (the command module's exit statuses); each once its card is filed, and
neither is ever read as a clean scan.  Git failing the read of which cards' fixes the image
holds (:func:`fixed`) fails the scan so too, as no CVE it found can then be filed.  The door
connects to the tracker, and fetches ``dev`` and ``main``, only when it has something to file,
so a clean scan reads nothing of either.
"""
from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import requests

from tools.ci.gitcmd import GitError
from tools.quill import _git, _waits
from tools.quill._draft import Draft
from tools.quill._exits import Refused, described
from tools.quill._github import CredentialsError, GitHubError
from tools.quill._tracker import Tracker, TrackerError
from tools.update import scan
from tools.update.app import marker

#: The scan, as the pass's lines name it (:class:`_waits.Teller`): what it finds in each
#: component filed, and put on the component's open card, under ruling plan#3787.
FOUND = _waits.Teller("the scan's", "found", "plan#3787", "plan#3787")
#: The scan, as the pass's lines name its failure: filed under ruling plan#3788.
FAILED = _waits.Teller("the scan's", "found", "plan#3788", "plan#3788")
#: Where the scan's failure is told, as the line holding it names it: no component, each of
#: which names a package (:func:`where`), and no pull request or issue of the update's.
FAILURE = "failure"
#: The one marker each card for the scan's failure holds (the module says why).
FAILED_MARK = marker("scan failed")
#: What the refusal of a scan says before why.
REFUSED = "the scan of production's image was refused: "
#: What filing the card for the scan's failure can fail with: the connection to the tracker,
#: its App's credentials among it, and the tracker's reads and writes.
_FILING = (GitHubError, TrackerError, requests.RequestException, CredentialsError, OSError)


def where(item: scan.Counted) -> str:
    """The component ``item`` was found in, as the lines of its card name it: Grype's type and
    the package's name (``deb package libssl3``), never its version."""
    return f"{item.kind} package {item.package}"


def _image(done: scan.Scanned) -> str:
    """The image ``done`` scanned, as a card names it: its digest and the commit it was built
    from, each in a code span, which links nothing."""
    return f"the image `{done.image}`, built from `{done.revision}`"


def _again(fresh: Sequence[scan.Counted], cited: Mapping[str, tuple[int, ...]]) -> list[str]:
    """The paragraph naming each CVE of ``fresh`` that closed cards whose fix the image holds
    held (``cited``, by marker: those cards; ruling plan#3765), grouped by those cards, each
    CVE once, and the blank line after it; none where ``cited`` names none of them."""
    groups: dict[tuple[int, ...], dict[str, None]] = {}
    for item in fresh:
        if cards := cited.get(item.marker):
            groups.setdefault(cards, {})[f"`{item.vulnerability}`"] = None
    if not groups:
        return []
    return [
        "The scan still finds these though every card that held each is closed and shipped, "
        "and the image was built from a commit holding each one's ship marker, so its fix did "
        "not remove them (ruling plan#3765): " + "; ".join(
            f"{', '.join(f'plan#{number}' for number in cards)} held {', '.join(named)}"
            for cards, named in groups.items()) + ".", ""]


def _listed(fresh: Sequence[scan.Counted], cited: Mapping[str, tuple[int, ...]]) -> list[str]:
    """``fresh`` as the card or comment filing them lists them: each one's line in a code
    block, then the paragraph naming each found again after its fix shipped into the image
    (:func:`_again`)."""
    return [*_waits.block("\n".join(item.line for item in fresh)), "", *_again(fresh, cited)]


def _card(place: str, listed: Sequence[str], marks: Sequence[str], done: scan.Scanned,
          when: str) -> Draft:
    """The card filed for the CVEs found in the component ``place`` names that no card holds,
    as ``listed`` lists them (:func:`_listed`), its lines holding ``marks``."""
    return _waits.step(_waits.capped("Production image CVEs: ", place), "\n".join([
        f"The daily scan of the image production is running found these CVEs in its {place}, "
        f"at {when}, in {_image(done)}: each has a fix rated HIGH or CRITICAL by some source, or "
        "is known to be exploited (rulings plan#3720, plan#3819).", "", *listed,
        "`quill scan` filed this card for that package (ruling plan#3787), holding Priority "
        "High at the top of the board (ruling plan#3620). A CVE the scan finds in it later is "
        "put on this card while it is open, and one the card holds is filed again only if the "
        "scan still finds it once the card has shipped and production's image was built from a "
        "commit holding its ship marker (ruling plan#3765). The session that claims this card "
        "takes the fix, and sets the card's Effort.", "",
        *_waits.told_lines(place, marks)]))


def _later(place: str, listed: Sequence[str], marks: Sequence[str], done: scan.Scanned,
           when: str) -> str:
    """The comment putting the CVEs ``listed`` lists (:func:`_listed`) on the card open for
    the component ``place`` names, its lines holding ``marks``."""
    return "\n".join([
        f"The daily scan found these CVEs later in production's {place}, at {when}, in "
        f"{_image(done)}:", "", *listed,
        "`quill scan` put them on this card, the one open for that package, rather than filing "
        "another (ruling plan#3787).", "", *_waits.told_lines(place, marks)])


def _cited(have: _waits.Holdings, spent: Collection[int], place: str,
           marks: Sequence[str]) -> dict[str, tuple[int, ...]]:
    """Each of ``marks``, markers of CVEs found in the component ``place`` names, that a card
    of ``spent`` held (:meth:`_waits.Holdings.holding`), with each such card, lowest first."""
    cited = {}
    for mark in marks:
        if held := tuple(sorted(number for number in spent
                                if (place, mark) in have.holding(number))):
            cited[mark] = held
    return cited


def found_items(done: scan.Scanned, when: str, have: _waits.Holdings,
                spent: Collection[int]) -> list[_waits.Item]:
    """An item for each component ``done`` found a CVE in that no card holds for it
    (:meth:`_waits.Holdings.standing`), found at ``when``, holding those CVEs alone, each that
    cards of ``spent`` held cited (:func:`_again`): ``spent``, the closed cards whose fix the
    image holds, which ``have`` leaves out (:meth:`_waits.Holdings.left_out`).  The component
    with the most severe CVE last, as ``done`` lists the most severe first."""
    found: dict[str, list[scan.Counted]] = {}
    for item in done.found:
        found.setdefault(where(item), []).append(item)
    items = []
    for place, counted in found.items():
        standing = have.standing(place)
        if fresh := [item for item in counted if (place, item.marker) not in standing]:
            marks = tuple(dict.fromkeys(item.marker for item in fresh))
            listed = _listed(fresh, _cited(have, spent, place, marks))
            items.append(_waits.Item(place, when, marks, _card(place, listed, marks, done, when),
                                     _later(place, listed, marks, done, when)))
    return items[::-1]


def fixed(root: Path, revision: str) -> frozenset[int]:
    """Each card whose fix the image built from the commit ``revision`` holds (ruling
    plan#3765; the module says why): each git says shipped, its marker in the merge of ``dev``
    and ``main`` as one fetch of both brings them now (:func:`_git.fetch`,
    :func:`_git.shipped`), whose marker the tree of ``revision`` holds too
    (:func:`_git.markers_in`), from the checkout at ``root``.

    Raises:
        GitError: the fetch failed, git cannot read ``revision``, or a marker or a task's file
            conflicts in that merge (:class:`_git.MarkerConflict`), so which cards shipped is
            unknown.
    """
    _git.fetch(root)
    return _git.shipped(root).cards & _git.markers_in(root, revision)[0]


def file_found(tracker: Tracker, done: scan.Scanned, when: str, fix: Collection[int],
               dry_run: bool) -> int:
    """What ``done`` found, at ``when``, filed (:func:`found_items`, :func:`_waits.file_told`),
    each closed card of ``fix``, the cards whose fix the image holds (:func:`fixed`), holding
    nothing; under ``dry_run`` printed and not written.  The number of REPORT lines.

    Raises:
        GitHubError, requests.RequestException, TrackerError: as the reads and the writes
            raise them.
    """
    have = _waits.holdings(tracker, {where(item) for item in done.found})
    spent = {number for number, card in have.cards.items()
             if not card.is_open and number in fix}
    kept = have.left_out(spent)
    return _waits.file_told(tracker, found_items(done, when, kept, spent), kept, FOUND, dry_run)


def failure_item(said: str, when: str) -> _waits.Item:
    """The scan's failure at ``when``, which said ``said``, as the pass files it: its text the
    card's and, worded to read as either, the comment's that would put it on an open card for
    the scan's failure not holding its line, which none of quill's is."""
    text = "\n".join([
        f"The daily scan of the image production is running failed at {when}, so no CVE in the "
        "image reaches the board until it runs again. What `quill scan` said:", "",
        *_waits.block(said), "",
        "The card open for the scan's failure holds it (ruling plan#3788), at Priority High at "
        "the top of the board (ruling plan#3620): while that card is open, a later failure "
        "files nothing, and once it is closed, the next failure files a card of its own. The "
        "session that claims it makes the scan run again, and sets its Effort.", "",
        _waits.told_line(FAILURE, FAILED_MARK)])
    return _waits.Item(FAILURE, when, (FAILED_MARK,),
                       _waits.step("The daily scan of production's image failed", text), text)


def file_failure(tracker: Tracker, said: str, when: str, dry_run: bool) -> int:
    """The scan's failure at ``when``, which said ``said``, filed unless an open card holds it
    (:func:`failure_item`, the module says why); under ``dry_run`` printed and not written.
    The number of REPORT lines.

    Raises:
        GitHubError, requests.RequestException, TrackerError: as the reads and the writes
            raise them.
    """
    return _waits.file_told(tracker, [failure_item(said, when)],
                            _waits.holdings(tracker, {FAILURE}).open_alone(), FAILED, dry_run)


def _failed(connect: Callable[[], Tracker], said: str, when: str, dry_run: bool) -> None:
    """The scan's failure, which said ``said``, filed (:func:`file_failure`) once the tracker
    is reached.  A failure to file it is raised with what the scan said as its note, so the one
    line the failure is printed on (``_exits.described``) says both.

    Raises:
        GitHubError, requests.RequestException, TrackerError, CredentialsError, OSError: as
            reaching the tracker, reading it and writing it raise them.
    """
    try:
        file_failure(connect(), said, when, dry_run)
    except _FILING as error:
        error.add_note(f"while filing the card for the scan's failure, which said: {said}")
        raise


def cmd_scan(args, connect: Callable[[], Tracker], root: Path,
             clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> int:
    """Scan the image production is running from the checkout at ``root``, and print the
    image's digest, the commit it was built from, how many matches count, and each one
    (:attr:`tools.update.scan.Counted.line`); then file each component one counts in that no
    card holds it for (:func:`file_found`), a closed card whose fix the image holds holding
    nothing (:func:`fixed`, read only when one counts), or a scan refused or unable to run as
    the scan's failure (:func:`file_failure`), each at the time ``clock`` reads at the start,
    and under ``args.dry_run`` printed and not written.  Exit 0, or 1 on a REPORT.

    Raises:
        Refused: the scanner's answer is no scan, names no commit, or saw not what the image
            runs, once its failure is filed.
        ScanFailed, GitError, OSError: the scan could not run, or git could not say which
            cards' fixes the image holds, once its failure is filed.
    """
    when = _git.instant(clock())
    try:
        done = scan.scanned(root)
        fix = fixed(root, done.revision) if done.found else frozenset()
    except scan.NotAScan as error:
        refused = Refused(f"{REFUSED}{error}")
        _failed(connect, str(refused), when, args.dry_run)
        raise refused from error
    except (scan.ScanFailed, GitError, OSError) as error:
        _failed(connect, described(error), when, args.dry_run)
        raise
    print(f"scan: production's image {done.image}, built from {done.revision}")
    print("scan: CVEs with a fix rated HIGH or CRITICAL, or known exploited: "
          f"{len(done.found)}")
    for item in done.found:
        print(f"  {item.line}")
    if not done.found:
        return 0
    return 1 if file_found(connect(), done, when, fix, args.dry_run) else 0
