"""Refuse a change set whose commits break the card-trailer rules (step X-cx's L4, card plan#4).

Git only, no tracker access.  Three rules, each read with :mod:`tools.ci.trailers`,
the one reader and the one ``shipped`` rule the tracker tool also asks:

1. **Citation shape.**  Every ``Ships:`` and ``Reopens:`` trailer on the change
   set's commits names a card as ``plan#<number>``, or, until step X-cx's
   cutover (L8), the old ``<arc>:<id>`` form the plan gate reads.  Anything
   else is a citation git would silently read as naming nothing.

2. **A revert carries what keeps git's answer true to the code it changes.**
   One revert commit undoes everything ANY of its revert records names (a
   squash of several reverts is one undo, judged once), and every commit it
   undoes is in the history it LANDS on, its first parent's.  Three answers
   are asked of :func:`tools.ci.trailers.shipped`, the tracker tool's own
   rule, and nothing else here decides what shipped: NOW, over that history;
   WITHOUT, over it with the undone commits' trailers taken out; and REMOVED,
   over the undone commits' trailers alone, which names each card whose
   shipped code the revert takes away.  The revert owes ``Reopens: plan#N``
   for each card REMOVED names, unless WITHOUT ships it through a
   ``Ships:`` git cancels NOW (a ship an undone ``Reopens:`` cancelled), and
   ``Ships: plan#N`` for each card shipped WITHOUT and not NOW; nothing else.
   So undoing a ship owes ``Reopens:``, even one of several ships of a card,
   since they may be its parts (``recurrence:R16-c-2`` shipped in three),
   and even when another ``Reopens:`` already cancelled it, so that
   undoing that other one later cannot bring the ship back (both decided by
   the session under ruling ``balance:R-BAL207``).  Undoing a revert's
   ``Reopens:`` owes ``Ships:``, since the code comes back.  Undoing a ship
   with the ``Reopens:`` that cancelled it, or a ``Reopens:`` that cancelled
   nothing (what one cancels is ruling ``balance:R-BAL181``'s), owes nothing.

   **Where it errs, and which way.**  Toward OPEN: one ``Reopens:`` cancels
   every ``Ships:`` of the card before it (R-BAL181), so undoing one of
   several ships reopens the card even when what remains is all of it, and
   so does undoing a later ship after a revert brought an earlier ship's code
   back (the card was shipped, so that revert owed nothing); the card stays
   open until a commit ships it again.  Toward SHIPPED, only where parallel
   lines of history meet in a merge: a ``Reopens:`` cancels no ``Ships:`` it
   was not built on, so a ``Ships:`` on one line outlives the undo of the
   card's code on another, and a revert that also restores a cancelled ship owes nothing,
   leaving the ``Ships:`` whose code it removes standing for such a merge to
   expose.  Each revert is judged where it lands, so no rule here sees the
   merge.  A trailer a revert does not owe is not refused: a ``Ships:`` it
   carries is a claim, as on any commit.

3. **A ``Reopens:`` undoes a ``Ships:`` in its own history** (ruling
   ``balance:R-BAL309``: CI refuses "an undo naming a card its own history
   never shipped (a typo)").  A ``Reopens: plan#N`` that cancels no ``Ships:
   plan#N`` is a STRAY, :func:`tools.ci.trailers.shipped`'s own answer and no
   second spelling of it, and most likely a mistyped number.  Landed, it is
   permanent: a release counts the card it names as a change it carries, and,
   when that card moves money, beside another money change in one pull request
   it blocks every release (the money wedge, step X-cx's L9d leaf c).  One walk
   answers every commit, ``shipped`` over ``head``'s history: whether a
   ``Reopens:`` cancels a ``Ships:`` reads only whether that ``Ships:`` is in
   the ``Reopens:`` commit's own history (R-BAL181), so a ``Ships:`` anywhere
   else in ``head``'s history cancels nothing, wherever it sits (a branch
   beside it, or a later commit on its own line); and every ``Ships:`` in that
   commit's own history is in ``head``'s, since the commit is.  So the answer
   is the one each commit's own history gives.  **Each ``Reopens:`` is judged
   once, when it first lands** (the coordinator, under ``R-BAL207``,
   2026-10-17; ``main`` beside ``dev`` since the L9d delta review's
   MEDIUM-1): only on the commits of the change set that have not landed,
   which neither ``dev`` nor ``main`` holds yet (:func:`not_landed`).  Of a
   pull request or a merge-queue group into ``dev``, that is every commit but
   those it carries from ``dev`` or ``main`` (so the pull request bringing
   ``main`` back into ``dev`` judges none of ``main``'s); of a pull request
   into ``main``, a release branch's own; of a push to ``main``, which
   ``main`` then holds, none.  A commit ``dev`` or ``main`` holds was judged
   at the pull request or merge group that first brought it to either, or
   got there past this check, as two kinds of commit do: a commit pushed
   straight to ``dev`` (which its ruleset lets an administrator do), and the
   merge commit GitHub writes when it merges a pull request into ``dev`` or
   ``main`` outside a merge queue (a queued group's merge commits are in its
   change set, and judged).  Each of those lands without a pull
   request judging it, and once landed it is never judged.  Either way no
   commit can change it (``R-BAL184``), so judging it again could only
   refuse it for good, never stop it landing.  The merge commit carries no
   trailer unless whoever merges writes it a body: its message is by default
   one line made from the pull request's title (``R-BAL319``, 2026-10-18),
   and git reads no trailer from a message's first line.  The same ruling
   made ``main``'s protection bind administrators, so no commit is pushed
   straight to ``main``, a third kind until then.  So this check never
   refuses a later pull request for a stray that reached ``dev`` or
   ``main`` without it (none had: neither held a ``plan#`` trailer on
   2026-10-18), or past a check no merge requires, and undoing one owes
   nothing (rule 2).  The release tool's reader before the cutover refuses
   such a stray (``tools/release/_carried.py``), and its card reader after
   it reads none as a record, since step X-cx's M3 reads the ship markers
   (ruling ``R-BAL359``).  ``dev`` and ``main`` are read only when the change set carries
   a ``Reopens:``, and a checkout that cannot read either refuses to judge.
   The old ``<arc>:<id>`` form is not read here: it ends at the cutover
   (``R-BAL287``), and no ``Reopens:`` in it exists (none on any ref,
   2026-10-17).

**What a revert is: git's own record, at the start of a line.**  ``git
revert`` writes ``This reverts commit <id>.``; with ``--reference`` the id is
shortened and followed by `` (<subject>, <date>)``; reverting a merge adds
``, reversing`` and a next line ``changes made to <parent>.``, naming the
parent it kept.  The GitHub "Revert" button writes the same.  The id must be
followed by what git writes after it (``.``, ``,``, `` (`` or the line's end),
so a sentence that merely starts with those words is not a record.  A change
undone by hand, with no such line, cannot be told from new work and is not
read.

**What a revert undoes.**  The commit it names, returning to its first parent;
for a MERGE, every commit the merge brought in beside the parent it kept
(``<parent>..<merge>``), returning to that parent, since the trailers sit on
those commits and not on the merge.  A revert naming a commit this repository
does not hold or that is not in the history the revert lands on (a rebased or
cherry-picked copy is what that history holds), a merge without the parent it
kept, or a parent that is not one of the merge's, is refused: what it undid
cannot be read, and a check that cannot read its subject has not checked it.
A commit with such a record is asked no debt either, since what it undoes is
not all known.

**The change set** is ``<base>..<head>``: the commits ``head`` holds and
``base`` does not.  ``ci.yml``'s ``scope`` job hands over the pair for a pull
request, a merge-queue group and a push to ``main``.  Rules 1 and 2 judge
every commit of it, rule 3 only those that have not landed.  A change set
holding no commit is refused too: none of those events has one, so it means
the two ends are wrong.

Usage, at the repository root::

    python -m tools.ci.commit_trailers <base-sha> <head-sha>

Exit status: 0 nothing refused; 1 refused, each reason printed; 2 the command
line or git failed, ``dev`` or ``main`` cannot be read when rule 3 needs them,
or the change set holds no commit, so nothing was checked.  What a command line
must name is :func:`ends`, which of its commits have not landed is
:func:`not_landed`, how a verdict is printed and exits is :func:`report`, and
how a check that judged nothing says why is :func:`unjudged`.
"""
from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path

from tools.ci import trailers
from tools.ci.arcs import REPO
from tools.ci.gitcmd import FULL_ID, GitError, git, run

#: Git's revert record, at the start of a line; the id may be shortened, and is
#: followed by what git writes after it.
_REVERTS = re.compile(
    r"^This reverts commit (?P<named>[0-9a-f]{4,64})(?=[.,]| \(|[ \t]*$)", re.MULTILINE,
)

#: A merge revert's second half, read on the record's own line and the next.
_KEPT_PARENT = re.compile(r"reversing\s+changes made to (?P<parent>[0-9a-f]{4,64})\b")

_FIELD, _RECORD = "\x01", "\x03"


def reverts(message: str) -> list[tuple[str, str | None]]:
    """Each ``(commit named, parent kept or None)`` the message's revert records name, in order.

    A squashed commit can hold several records.  The kept parent is read only
    from the record's own line and the line after it, where git writes it.
    """
    found = []
    for record in _REVERTS.finditer(message):
        window = "\n".join(message[record.end():].split("\n", 2)[:2])
        kept = _KEPT_PARENT.search(window)
        found.append((record.group("named"), kept.group("parent") if kept else None))
    return found


def _commits(root: Path, base: str, head: str) -> Iterator[tuple[str, str, str]]:
    """``(sha, subject, message)`` for every commit of the change set, newest first."""
    out = git(root, "log", f"--format=%H{_FIELD}%s{_FIELD}%B{_RECORD}", f"{base}..{head}")
    for record in filter(str.strip, out.split(_RECORD)):
        sha, subject, message = record.lstrip("\n").split(_FIELD, 2)
        yield sha, subject, message


def _resolve(root: Path, named: str) -> str | None:
    """The full id of the commit ``named`` (a full or shortened id), or None when this
    repository holds no such commit or the shortened id is ambiguous."""
    done = run(root, "rev-parse", "--verify", "--quiet", f"{named}^{{commit}}")
    return done.stdout.strip() if done.returncode == 0 else None


def undone(root: Path, named: str, kept: str | None, onto: str) -> trailers.History | str:
    """The card trailers on what a revert of ``named`` undid, or one sentence saying why
    that cannot be read.

    Args:
        root: The repository.
        named: The commit the revert record names.
        kept: The parent a merge revert says it kept, or None.
        onto: The commit the revert lands on, its first parent.
    """
    full = _resolve(root, named)
    if full is None:
        return (f"it says it reverts commit {named}, which this repository does not hold "
                f"(if that commit was rebased, cite its new id)")
    if not trailers.is_ancestor(root, full, onto):
        return (f"it says it reverts commit {full[:12]}, which is not in the history it "
                f"lands on (if that commit was rebased or cherry-picked, cite the copy's id)")
    parents = git(root, "rev-list", "--parents", "-n", "1", full).split()[1:]
    if len(parents) < 2:
        return trailers.history(root, f"{full}^!")
    if kept is None:
        return (f"it reverts merge {full[:12]} without the line naming the parent it kept "
                f"(\"reversing changes made to <parent>\"), so what it undid cannot be read")
    kept_full = _resolve(root, kept)
    if kept_full not in parents:
        return (f"it says it kept {kept}, which is not a parent of merge {full[:12]}, so "
                f"what it undid cannot be read")
    return trailers.history(root, f"{kept_full}..{full}")


def owed_at_landing(root: Path, revert: str,
                    found: list[trailers.History]) -> dict[tuple[str, int], trailers.Trailer]:
    """``{(trailer owed, card): the undone trailer that owes it}`` for the commit ``revert``,
    whose revert records undo ``found`` (rule 2).

    NOW, WITHOUT and REMOVED are :func:`trailers.shipped`'s answers over the
    history ``revert`` lands on, over it without the undone commits' trailers,
    and over those trailers alone.
    """
    undone_trailers = tuple(trailer for each in found for trailer in each.trailers)
    gone = {trailer.sha for trailer in undone_trailers}
    landing = trailers.history(root, f"{revert}^1")
    now = trailers.shipped(root, landing)[0]
    without = trailers.shipped(root, trailers.History(
        tuple(trailer for trailer in landing.trailers if trailer.sha not in gone),
        landing.malformed))[0]
    removed = trailers.shipped(root, trailers.History(undone_trailers, ()))[0]
    debts: dict[tuple[str, int], trailers.Trailer] = {}
    for card, claims in removed.items():
        if not any(claim not in now.get(card, ()) for claim in without.get(card, ())):
            debts[(trailers.REOPENS, card)] = claims[0]
    for card in without.keys() - now.keys():
        # One always exists: a Ships standing WITHOUT and not NOW is cancelled NOW
        # only by Reopens trailers the revert undoes.
        debts[(trailers.SHIPS, card)] = next(
            undo for undo in undone_trailers if undo.key == trailers.REOPENS
            and any(trailers.cancels(root, undo, claim) for claim in without[card]))
    return debts


def _revert_reasons(root: Path, commit: tuple[str, str, str],
                    carries: set[tuple[str, int]]) -> list[str]:
    """Why one commit of the change set breaks rule 2, one sentence per debt; empty when
    it reverts nothing or pays every debt.  ``commit`` is ``(sha, subject, message)``.

    Every record the commit holds is read before any debt is asked, since a
    debt is owed by the whole undo: a record that cannot be read is refused,
    and then no debt is asked.
    """
    sha, subject, message = commit
    found = [undone(root, named, kept, f"{sha}^1") for named, kept in reverts(message)]
    unread = [f"{sha[:12]} ({subject}): {what}" for what in found if isinstance(what, str)]
    if unread or not found:
        return unread
    read = [what for what in found if isinstance(what, trailers.History)]
    return [
        f"{sha[:12]} ({subject}) undoes {cause.sha[:12]} ({cause.subject}), which carries "
        f"`{cause.key}: plan#{card}`; add the trailer `{key}: plan#{card}` to the last "
        f"paragraph of {sha[:12]}'s message"
        for (key, card), cause in sorted(owed_at_landing(root, sha, read).items())
        if (key, card) not in carries
    ]


class LandedUnknown(GitError):
    """Which commits have landed is unknown: :data:`tools.ci.trailers.DEV` or
    :data:`tools.ci.trailers.MAIN` cannot be read in this checkout."""


def not_landed(root: Path, base: str, head: str) -> set[str]:
    """The commits of ``<base>..<head>`` that have not landed: neither
    :data:`tools.ci.trailers.DEV` nor :data:`tools.ci.trailers.MAIN` holds them yet.  They
    are the commits a rule judged once, when it first lands, judges (rule 3).  Each branch
    is handed to git by its full name
    (:func:`tools.ci.trailers.tracking`), so no tag or local branch named like it stands in,
    and whether it exists is asked of that exact reference (``show-ref --verify``): git
    would resolve even a full name that does not exist to a tag named like it.

    Raises:
        LandedUnknown: when ``dev`` or ``main`` cannot be read in this checkout, naming it.
    """
    for branch in (trailers.DEV, trailers.MAIN):
        if run(root, "show-ref", "--verify", "--quiet", trailers.tracking(branch)).returncode:
            raise LandedUnknown(f"{branch} cannot be read in this checkout, so which commits "
                                "have landed already is unknown (in CI, check out with "
                                "fetch-depth: 0)")
    return set(git(root, "rev-list", head, f"^{base}", f"^{trailers.tracking(trailers.DEV)}",
                   f"^{trailers.tracking(trailers.MAIN)}").split())


def _stray_reasons(root: Path, base: str, head: str, found: trailers.History) -> list[str]:
    """Why the change set ``<base>..<head>``, whose card trailers ``found`` holds, breaks
    rule 3: one sentence per stray ``Reopens:`` on a commit that has not landed, a trailer
    a commit carries twice named once; empty when it carries no ``Reopens:``, and ``dev``
    and ``main`` are then not read.

    Raises:
        GitError: when ``dev`` or ``main`` cannot be read (:class:`LandedUnknown`), or git
            cannot read ``head``'s history.
    """
    if not any(trailer.key == trailers.REOPENS for trailer in found.trailers):
        return []
    fresh = not_landed(root, base, head)
    strays = trailers.shipped(root, trailers.history(root, head))[1]
    return [f"{stray.sha[:12]} ({stray.subject}) carries `Reopens: plan#{stray.card}`, but no "
            f"commit in its history ships plan#{stray.card}, so it undoes nothing (a mistyped "
            "number?): name the card whose `Ships:` the commit undoes; if it does undo "
            f"plan#{stray.card}, rebuild it on top of the commit that ships plan#{stray.card}; "
            "otherwise remove the trailer"
            for stray in dict.fromkeys(strays) if stray.sha in fresh]


def problems(root: Path, base: str, head: str) -> tuple[int, list[str]]:
    """How many commits the change set holds, and one sentence per broken rule.

    Raises:
        GitError: when git cannot read the change set (a shallow clone among them), or
            ``dev`` or ``main`` when the change set carries a ``Reopens:``.
    """
    found = trailers.history(root, f"{base}..{head}")
    reasons = [f"{line} (nor the old <arc>:<id> form, read until the cutover)"
               for line in found.malformed]
    carried: dict[str, set[tuple[str, int]]] = {}
    for trailer in found.trailers:
        carried.setdefault(trailer.sha, set()).add((trailer.key, trailer.card))
    commits = list(_commits(root, base, head))
    for commit in commits:
        reasons.extend(_revert_reasons(root, commit, carried.get(commit[0], set())))
    reasons.extend(_stray_reasons(root, base, head, found))
    return len(commits), reasons


def ends(args: list[str]) -> tuple[str, str] | None:
    """A change set's two ends, ``(base, head)``, from a command line naming them: two full
    commit ids, neither all zeros; None for any other command line."""
    if (len(args) != 2 or not all(FULL_ID.fullmatch(a) for a in args)
            or any(set(a) == {"0"} for a in args)):
        return None
    return args[0], args[1]


def report(check: str, base: str, head: str, count: int, reasons: list[str]) -> int:
    """Print the verdict of the change-set check named ``check`` on ``<base>..<head>``, which
    holds ``count`` commits and broke a rule for each of ``reasons``; its exit status as the
    module says.  A change set holding no commit is refused: no pull request, merge group
    or push has one, so it means the two ends are wrong."""
    if not count:
        print(f"{check}: {base[:12]}..{head[:12]} holds no commit, so nothing was "
              "checked; no pull request, merge group or push has an empty change set",
              file=sys.stderr)
        return 2
    for reason in reasons:
        print(f"REFUSED -- {reason}")
    print(f"{check}: {count} commit(s) in {base[:12]}..{head[:12]}, {len(reasons)} refused")
    return 1 if reasons else 0


def unjudged(check: str, base: str, head: str, error: GitError) -> int:
    """Print why the change-set check named ``check`` judged nothing of ``<base>..<head>``:
    git's ``error``, which names the ref when ``dev`` or ``main`` cannot be read
    (:class:`LandedUnknown`) and the change set otherwise; exit status 2."""
    said = (str(error) if isinstance(error, LandedUnknown)
            else f"git could not read {base[:12]}..{head[:12]}: {error}")
    print(f"{check}: {said}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None, root: Path = REPO) -> int:
    """Check the change set ``<base>..<head>``; print each refusal; exit as the module says."""
    args = sys.argv[1:] if argv is None else argv
    if (pair := ends(args)) is None:
        print("usage: python -m tools.ci.commit_trailers <base-sha> <head-sha> (two full "
              f"commit ids, neither all zeros); given {args!r}", file=sys.stderr)
        return 2
    base, head = pair
    try:
        count, reasons = problems(root, base, head)
    except GitError as exc:
        return unjudged("commit trailers", base, head, exc)
    return report("commit trailers", base, head, count, reasons)


if __name__ == "__main__":
    sys.exit(main())
