"""X-cx's migration, its SOURCE: the plan's registries read as the cards they become.

X-cx's piece L7 moves the plan in ``docs/plans/`` into the tracker: every open step,
every finding, every question and every ruling becomes one card (ruling
``balance:R-BAL170``; the design of record is the L7 design's draft 4).  This module
reads the registries and says what each card is -- its kind, its key, its parent and
blockers, its labels, its outcome, its place on the board -- and where each step's
spec and each steps-section paragraph lies.  It decides nothing the developer's input
file decides (:mod:`_migrate_input`: names, rewritten questions, what each paragraph
is), and it writes no card text (:mod:`_migrate_bodies`).  It is pure: it reads
nothing but the :class:`Registries` it is handed, which :func:`read_registries` fills
from git, as one commit holds them (rulings ``balance:R-BAL257``, ``R-BAL259``).

**One producer per fact (CLAUDE.md rule 14).**  The rows are the plan gate's own
readers, through its read-only door (``tools/plan_gate/registries.py``, ruling
``R-BAL244``); a step's entry, the checkbox grammar and the fence rule are
:mod:`tools.ci.arc_steps`'; the arcs and their steps headings are :mod:`tools.ci.arcs`'.
Nothing here parses a registry table or an arc document's grammar a second time.

**What moves** (draft 4 s.1): every step not SHIPPED (R-BAL236: a shipped step moves
nowhere, so neither does an edge to one); every ``ledger.md`` row, as a FINDING blocked
by the first step its owner cell names, its owner, and no sub-issue of it (R-BAL239,
R-BAL395), or, naming none, as a QUESTION; and every ``rulings.md`` row as a closed
ruling with no parent (R-BAL236).  A card's KEY is its arc and its ALIAS, the old id its
title carries in brackets (``[X-bk-2] ...``).
One open step is a card already (:data:`MAPPED`), so nothing is filed for it.

**What it refuses, never chooses** (each a line of :attr:`Source.refusals`): an arc
document whose steps heading starts no line or more than one; an open step in an
identity class of more than one row; one whose steps section holds a checkbox count
other than one; one whose order cell and sentence disagree on whether it is a
container; one open with no rank that is no container; a checkbox outside every steps
section, or for no step of its arc; a blocker naming no step, or itself; an outcome's
scope naming no step, a step in two scopes, and two outcomes sharing a name; an owner
cell outside the owner grammar; a finding whose FIRST step owner does not move; a
question whose owner states no note; a key two items share; a :data:`MAPPED` key naming
no open step; a step under two containers neither of which splits the other; an open
container with no open step under it; two paragraphs the input file would name by one
first line; and GitHub's own caps, which it would refuse mid-run (:data:`SUB_ISSUE_CAP`,
:data:`NESTING_CAP`).
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from tools.ci.arc_steps import (
    Entry,
    HeadingCountError,
    checkboxes,
    entries,
    fenced_lines,
    section_span,
)
from tools.ci.arcs import ARC_DOCS, ARCS, REPO, STEPS_HEADINGS
from tools.ci.gitcmd import git
from tools.quill.setup_tracker import DEPLOY_TOGETHER_LABEL, MOVES_MONEY_LABEL
from tools.plan_gate.registries import (
    LEDGER,
    NON_STEP_OWNERS,
    OWNER_RX,
    RULINGS,
    STEPS,
    LedgerRow,
    RulingRow,
    StepRow,
    decomposition_leaf_keys,
    identity_class,
    ledger_rows,
    outcome_scopes,
    ruling_rows,
    split_owners,
    step_rows,
)

#: The marker ``steps.md`` writes in a step's sentence when its release moves money;
#: such a step's card carries :data:`setup_tracker.MOVES_MONEY_LABEL`.  A container's
#: does not: no release ships a container, its leaves are what move money (balance:X-bk
#: today).
MOVES_MONEY = "MOVES MONEY"
#: The parent cards whose leaves ``/release`` ships together, never split
#: (:data:`setup_tracker.DEPLOY_TOGETHER_LABEL`): ruling R-BAL240, the one open case the
#: registries' text states.  No registry column holds it, so the ruling's key is spelled
#: here, and :func:`read_source` reports it moot once the step no longer moves.
DEPLOY_TOGETHER = frozenset({"balance:X-bi-6-4d"})
#: The registry items already filed as cards, by the card's number: the tracker holds
#: #1-#10, #26 and #27, and only #1 is a registry item, steps.md's X-cx row (draft 4
#: s.1).  Nothing is filed over it and its text is not rewritten; X-cx's piece B2 grades
#: its key, kind, labels and links.  A key here that names no open step is refused.
MAPPED = {"balance:X-cx": 1}
#: Findings that ALSO get a question card (R-BAL239: N-391 names a step and the
#: developer, so it is a finding its step owns and his question too).  Its question
#: card's alias is the row's id and :data:`QUESTION_SUFFIX`, so ``quill show N-391``
#: still names the finding.
ALSO_A_QUESTION = frozenset({"salary:N-391"})
QUESTION_SUFFIX = " question"
#: GitHub's caps on sub-issues: "You can add up to 100 sub-issues per parent issue and
#: create up to eight levels of nested sub-issues" (docs.github.com, read 2026-10-08).
#: Eight levels is read as eight cards deep counting the top one, the stricter of the
#: sentence's two readings.
SUB_ISSUE_CAP = 100
NESTING_CAP = 8
#: A Markdown list item's first line: a ``-`` or ``*`` bullet and a space.
_LIST_ITEM = re.compile(r"^\s*[-*]\s")
#: The board's two tiers (R-BAL238): the ranked steps by rank, then the questions in
#: the ledger's order.
STEPS_TIER, QUESTIONS_TIER = 0, 1


@dataclass(frozen=True)
class Registries:
    """What the migration reads: the three registries' rows, the OUTCOMES table, each
    arc document's text, and the commit they were read at."""

    steps: tuple[StepRow, ...]
    findings: tuple[LedgerRow, ...]
    rulings: tuple[RulingRow, ...]
    outcomes: tuple[tuple[str, tuple[str, ...]], ...]
    documents: Mapping[str, str]
    commit: str


def read_registries(root: Path, commit: str) -> Registries:
    """The registries and each arc document as ``commit`` holds them in the repository at
    ``root``, read from git and parsed by the plan gate's own readers (its door): every
    card's As filed block names ``commit``, so its text is that commit's whatever the
    checkout holds (rulings ``balance:R-BAL257``, ``R-BAL259``).

    Raises:
        tools.ci.gitcmd.GitError: When git cannot read a file at ``commit``.
        UnicodeDecodeError: When a file is not text in the locale's encoding, which the
            plan gate's own read of a checkout decodes it in too (UTF-8 on this host,
            measured 2026-10-09).
    """
    def at_commit(path: Path) -> str:
        return git(root, "cat-file", "blob", f"{commit}:{path.relative_to(REPO).as_posix()}")

    steps = at_commit(STEPS)
    return Registries(
        tuple(step_rows(text=steps)), tuple(ledger_rows(text=at_commit(LEDGER))),
        tuple(ruling_rows(text=at_commit(RULINGS))),
        tuple((name, tuple(scope)) for name, scope in outcome_scopes(text=steps)),
        {arc: at_commit(ARC_DOCS[arc]) for arc in ARCS}, commit)


def source_hash(text: str) -> str:
    """The hash an input-file entry records of the text it was written from (draft 4
    s.11 V3-3): the first 16 hex digits of its SHA-256."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Links:
    """Where a card sits: its parent's key, its OPEN blockers' keys -- a finding's one
    blocker is its owner step (R-BAL395) -- and its place on the board (``(tier,
    position)``, :data:`STEPS_TIER` or :data:`QUESTIONS_TIER`), None off it."""

    parent: str | None = None
    blockers: tuple[str, ...] = ()
    place: tuple[int, int] | None = None


@dataclass(frozen=True)
class Item:
    """One card the migration files (or, :data:`MAPPED`, finds filed): its arc, its alias
    (the old id), its kind, the registry row it is made from, its labels, its
    :class:`Links` and its outcome -- the OUTCOMES table's name for the milestone its card is
    filed in, which the input file maps to that milestone's title; a split step's is carried
    to its leaves (:func:`_carried_outcomes`) -- None for none."""

    arc: str
    alias: str
    kind: str
    row: StepRow | LedgerRow | RulingRow
    labels: tuple[str, ...]
    links: Links
    outcome: str | None = None

    @property
    def key(self) -> str:
        """``arc:alias``: the card's key, in the tracker as in the registries."""
        return f"{self.arc}:{self.alias}"


@dataclass(frozen=True)
class Paragraph:
    """A run of a document's lines with no blank line between them except inside a
    fenced sample: its arc, the open step whose entry holds it (None outside every
    entry), its first line's index and its lines as written."""

    arc: str
    step: str | None
    start: int
    lines: tuple[str, ...]

    @property
    def first_line(self) -> str:
        """Its first line, stripped: how the input file names it."""
        return self.lines[0].strip()

    @property
    def text(self) -> str:
        """Its lines, joined."""
        return "\n".join(self.lines)

    @property
    def stop(self) -> int:
        """The index just past its last line."""
        return self.start + len(self.lines)


@dataclass(frozen=True)
class Spec:
    """An open step's entry in its arc document (:func:`tools.ci.arc_steps.entries`): the
    entry, its opener's indentation, and each later paragraph that starts at or left of
    the opener (draft 4 s.11 V3-2), which the input file must say is the step's OWN or
    a NOTE about other steps."""

    entry: Entry
    indent: int
    paragraphs: tuple[Paragraph, ...]


@dataclass(frozen=True)
class Layout:
    """One arc document's steps section, as the census reads it: its span, every entry
    in it (open and shipped), and which of the document's lines are fenced."""

    span: tuple[int, int]
    entries: tuple[Entry, ...]
    fenced: tuple[bool, ...]


@dataclass(frozen=True)
class Source:
    """What the registries say, before the input file's decisions: the items, each open
    step's :class:`Spec`, each steps-section paragraph outside every entry that is prose
    (a bare ``###`` heading is not), each arc's :class:`Layout`, what is refused, and
    what is reported without being refused."""

    items: tuple[Item, ...]
    specs: Mapping[str, Spec]
    preambles: tuple[Paragraph, ...]
    layouts: Mapping[str, Layout]
    refusals: tuple[str, ...]
    reports: tuple[str, ...]


def indentation(line: str) -> int:
    """How many spaces open ``line``."""
    return len(line) - len(line.lstrip(" "))


def paragraphs(lines: Sequence[str], fenced: Sequence[bool], lo: int, hi: int) -> list[range]:
    """The paragraphs among ``lines[lo:hi]``: each maximal run of lines that are not blank
    or are fenced (a fenced sample's blank line does not end its paragraph)."""
    found, start = [], None
    for index in range(lo, hi):
        filled = fenced[index] or bool(lines[index].strip())
        if filled and start is None:
            start = index
        elif not filled and start is not None:
            found.append(range(start, index))
            start = None
    if start is not None:
        found.append(range(start, hi))
    return found


def question_note(row: LedgerRow) -> str | None:
    """The question a row asks the developer: its first ``operator`` or
    ``developer-decision`` owner's note, word for word; None when no such owner states
    one."""
    for part in split_owners(row.owner.strip()):
        match = OWNER_RX.match(part.strip())
        if match and match.group("owner") in NON_STEP_OWNERS:
            return match.group("note")
    return None


def source_text(item: Item, specs: Mapping[str, Spec], documents: Mapping[str, str]) -> str:
    """The text an item's card is written from, which the hash of its name (and of a
    question's written or kept question) records: a step's sentence and its entry as
    written, the blank lines that end it aside (its sentence alone while it has no single
    entry, which :func:`read_source` refuses); a finding's cell; a question's finding cell
    and note, both of which its question is written from; a ruling's cell.  A step's
    entry includes the paragraphs the input file calls notes: a note edited makes the
    step's name stale too, which costs a re-read and never a stale name."""
    row = item.row
    if item.kind == "step":
        spec = specs.get(item.key)
        if spec is None:
            return row.title
        lines = documents[item.arc].splitlines()[spec.entry.start:spec.entry.stop]
        return "\n".join([row.title, "", *lines]).rstrip()
    if item.kind == "finding":
        return row.finding
    if item.kind == "question":
        return "\n\n".join(question_source(row))
    return row.rule


def question_source(row: LedgerRow) -> tuple[str, str]:
    """What a question card's question is written from: its row's finding cell and its
    note (:func:`question_note`; empty when it has none)."""
    return row.finding, question_note(row) or ""


def read_source(registries: Registries) -> Source:
    """Every item, spec, preamble and layout the registries hold, and what is refused."""
    refusals, reports = [], []
    layouts, specs, preambles = _documents(registries, refusals)
    steps = _steps(registries, layouts, specs, refusals, reports)
    moving = {item.key for item in steps}
    findings = _findings(registries, moving, refusals, reports)
    rulings = [Item(row.arc, row.bare_ident, "ruling", row, (row.arc,), Links())
               for row in registries.rulings]
    items = (*steps, *findings, *rulings)
    refusals += _shape_refusals(items)
    refusals += [f"card plan#{number} is {key}'s, which is no open step"
                 for key, number in sorted(MAPPED.items()) if key not in moving]
    specs = {key: spec for key, spec in specs.items() if key in moving and key not in MAPPED}
    refusals += _ambiguous(specs, preambles)
    return Source(items, specs, tuple(preambles), layouts, tuple(refusals), tuple(reports))


def _ambiguous(specs: Mapping[str, Spec], preambles: Sequence[Paragraph]) -> list[str]:
    """Each first line two paragraphs share where the input file names a paragraph by
    it -- within one step's entry, or among one steps section's preambles -- so it could
    name neither: one disposition would land on both, unread."""
    within = Counter((paragraph.arc, paragraph.step, paragraph.first_line)
                     for spec in specs.values() for paragraph in spec.paragraphs)
    among = Counter((paragraph.arc, paragraph.first_line) for paragraph in preambles)
    return ([f"{arc}:{step}: {count} paragraphs at or left of its checkbox start {first!r}; "
             "the input file names a paragraph by its first line: reword one"
             for (arc, step, first), count in within.items() if count > 1]
            + [f"{arc}: {count} paragraphs outside every entry start {first!r}; the input "
               "file names a paragraph by its first line: reword one"
               for (arc, first), count in among.items() if count > 1])


def _documents(registries: Registries, refusals: list[str]) -> tuple[
        dict[str, Layout], dict[str, Spec], list[Paragraph]]:
    """Each arc document's steps section: its layout, a spec for every entry (a step's
    openness is decided against the rows, by :func:`_steps`), and its preambles."""
    layouts, specs, preambles = {}, {}, []
    for arc in ARCS:
        text = registries.documents[arc]
        try:
            span = section_span(text, STEPS_HEADINGS[arc])
            found = entries(text, STEPS_HEADINGS[arc])
        except HeadingCountError as error:
            refusals.append(f"{arc}: {error.message('its document')}")
            continue
        lines = text.splitlines()
        layouts[arc] = Layout(span, tuple(found), tuple(fenced_lines(lines)))
        refusals += [f"{arc}: line {box.line + 1} is a checkbox for {box.step} outside the "
                     "steps section" for box in checkboxes(text)
                     if not span[0] <= box.line < span[1]]
        counts = Counter(entry.step for entry in found)
        refusals += [f"{arc}: {count} checkboxes in the steps section name {step}"
                     for step, count in counts.items() if count > 1]
        specs.update({f"{arc}:{entry.step}": _spec(arc, lines, layouts[arc], entry)
                      for entry in found if counts[entry.step] == 1})
        preambles += _preambles(arc, lines, layouts[arc])
    return layouts, specs, preambles


def _spec(arc: str, lines: Sequence[str], layout: Layout, entry: Entry) -> Spec:
    """One entry's :class:`Spec`: each paragraph after the opener's that starts at or left
    of the opener (:func:`entry_paragraphs`)."""
    indent = indentation(lines[entry.start])
    return Spec(entry, indent, tuple(
        Paragraph(arc, entry.step, run.start, tuple(lines[run.start:run.stop]))
        for run in entry_paragraphs(lines, layout.fenced, entry, indent)[1:]
        if indentation(lines[run.start]) <= indent))


def entry_paragraphs(lines: Sequence[str], fenced: Sequence[bool], entry: Entry,
                     indent: int) -> list[range]:
    """The paragraphs of an entry whose checkbox line is indented ``indent``: its
    :func:`paragraphs`, each split again where a line outside a fence steps back to the
    opener's indentation or left of it from a line indented further.

    A step's own text is indented under its checkbox, so a line that drops back from
    deeper to the checkbox's level or left of it has left the step's list item even with
    no blank line before it.  Measured 2026-10-09: balance X-bk-2's checkbox, at four
    spaces, is followed at once by a flush-left note about archived steps, which a split
    on blank lines alone read as part of the step's own first paragraph, where no input
    could call it a note.  A list item at or left of the checkbox's level starts one
    too, however deep the line before it: it is the checkbox's sibling, not its text
    (measured 2026-10-09: shipped X-au-e's flush-left checkbox is followed at once by the
    bullet ``* **X-au-m is DISSOLVED** ...``, a note about another step)."""
    found = []
    for run in paragraphs(lines, fenced, entry.start, entry.stop):
        start = run.start
        for index in range(run.start + 1, run.stop):
            here = indentation(lines[index])
            if not fenced[index] and here <= indent and (
                    here < indentation(lines[index - 1]) or _LIST_ITEM.match(lines[index])):
                found.append(range(start, index))
                start = index
        found.append(range(start, run.stop))
    return found


def _preambles(arc: str, lines: Sequence[str], layout: Layout) -> list[Paragraph]:
    """The prose paragraphs of the steps section outside every entry: each paragraph of
    the lines no entry holds, but a bare ``###`` heading (and the section's own ``##``
    heading, which is no paragraph of it)."""
    inside = {index for entry in layout.entries for index in range(entry.start, entry.stop)}
    start, stop = layout.span
    found = []
    for run in _runs([index for index in range(start + 1, stop) if index not in inside]):
        found += [Paragraph(arc, None, run_.start, tuple(lines[run_.start:run_.stop]))
                  for run_ in paragraphs(lines, layout.fenced, run.start, run.stop)
                  if not (len(run_) == 1 and lines[run_.start].startswith("###"))]
    return found


def _runs(indexes: Sequence[int]) -> list[range]:
    """``indexes`` (ascending) as maximal runs of consecutive numbers."""
    runs = []
    for index in indexes:
        if runs and runs[-1].stop == index:
            runs[-1] = range(runs[-1].start, index + 1)
        else:
            runs.append(range(index, index + 1))
    return runs


def _steps(registries: Registries, layouts: Mapping[str, Layout], specs: Mapping[str, Spec],
           refusals: list[str], reports: list[str]) -> list[Item]:
    """An item for every step not SHIPPED, under its nearest container."""
    rows = list(registries.steps)
    by_key = {row.key: row for row in rows}
    open_rows = [row for row in rows if not row.shipped]
    containers = {row.key for row in open_rows if row.is_decomposed_parent}
    refusals += [f"{row.key}: its order cell and its sentence disagree on whether it is a "
                 "container (one says so, the other does not)"
                 for row in open_rows if row.is_container != row.is_decomposed_parent]
    leaves = {key: set(decomposition_leaf_keys(by_key[key], rows)) for key in containers}
    outcome = _outcomes(registries, by_key, refusals, reports)
    refusals += _together_refusals(by_key, reports)
    items = []
    for row in open_rows:
        if len(identity_class(row, rows)) > 1:
            refusals.append(f"{row.key} is one step under {len(identity_class(row, rows))} "
                            "names (an identity class); a card has one key")
        if row.arc in layouts and row.key not in specs:
            refusals.append(f"{row.key}: its arc document's steps section holds no single "
                            "checkbox for it")
        if row.rank is None and not row.is_decomposed_parent:
            refusals.append(f"{row.key} is open with no rank and is no container, so it has no "
                            "place on the board")
        links = Links(_nearest(row.key, containers, leaves, refusals),
                      _open_blockers(row, by_key, refusals),
                      None if row.rank is None else (STEPS_TIER, row.rank))
        items.append(Item(row.arc, row.bare_ident, "step", row, step_labels(row), links,
                          outcome.get(row.key)))
    items = _carried_outcomes(items, containers, outcome, reports)
    refusals += _shown_nowhere(containers, items)
    known = {(row.arc, row.bare_ident) for row in rows}
    refusals += [f"{arc}: line {entry.start + 1} is a checkbox for {entry.step}, which is no "
                 f"step of {arc} in steps.md" for arc, layout in layouts.items()
                 for entry in layout.entries if (arc, entry.step) not in known]
    return items


def _shown_nowhere(containers: set[str], items: Sequence[Item]) -> list[str]:
    """Each open container no open step hangs under: a card on no board and under no
    step (review of B1, M3)."""
    split = {item.links.parent for item in items}
    return [f"{key} is an open container with no open step under it: it splits nothing, "
            "and a container is no work itself, so no command would ever offer it: ship it, "
            "or file its leaf" for key in sorted(containers - split)]


def step_labels(row: StepRow) -> tuple[str, ...]:
    """A step card's labels: its arc; :data:`MOVES_MONEY_LABEL` when its sentence carries
    :data:`MOVES_MONEY` and it is no container; :data:`DEPLOY_TOGETHER_LABEL` when
    R-BAL240 names it.  The release tool reads a step's money flag and group through it
    too, until the cutover (:mod:`tools.quill.door`), so a row it reads gets the
    label the migration would give it."""
    labels = [row.arc]
    if MOVES_MONEY in row.title and not row.is_decomposed_parent:
        labels.append(MOVES_MONEY_LABEL)
    if row.key in DEPLOY_TOGETHER:
        labels.append(DEPLOY_TOGETHER_LABEL)
    return tuple(labels)


def _nearest(key: str, containers: set[str], leaves: Mapping[str, set[str]],
             refusals: list[str]) -> str | None:
    """The NEAREST container ``key`` is a leaf of: the one that is itself a leaf of every
    other container ``key`` is under; None at the top level."""
    under = [container for container in containers if key in leaves[container]]
    nearest = [container for container in under
               if all(container in leaves[other] for other in under if other != container)]
    if under and len(nearest) != 1:
        refusals.append(f"{key} is a leaf of {sorted(under)}, and no one of them is the "
                        "nearest (a leaf of all the others)")
        return None
    return nearest[0] if nearest else None


def _open_blockers(row: StepRow, by_key: Mapping[str, StepRow],
                   refusals: list[str]) -> tuple[str, ...]:
    """The keys of the OPEN steps ``row`` is blocked by; one already shipped is satisfied
    and has no card (R-BAL236)."""
    blockers = []
    for key in row.blocked_keys():
        blocker = by_key.get(key)
        if blocker is None or key == row.key:
            refusals.append(f"{row.key} is blocked by {key}, which is "
                            + ("itself" if key == row.key else "no step in steps.md"))
        elif not blocker.shipped:
            blockers.append(key)
    return tuple(dict.fromkeys(blockers))


def _outcomes(registries: Registries, by_key: Mapping[str, StepRow], refusals: list[str],
              reports: list[str]) -> dict[str, str]:
    """Each open scope step's outcome (R-BAL243: membership only; the order among the
    outcomes is the board's).  A scope key naming no step, a step in two scopes (a card
    holds one milestone) and two outcomes of one name are refused; an outcome whose whole
    scope has shipped is reported, and gets no milestone (one would hold no card)."""
    names = Counter(name for name, _ in registries.outcomes)
    refusals += [f"{count} outcomes are named {name!r}; a milestone is found by its outcome"
                 for name, count in names.items() if count > 1]
    member = {}
    for name, scope in registries.outcomes:
        if all(key in by_key and by_key[key].shipped for key in scope):
            reports.append(f"outcome {no_milestone(name, shipped=True)}")
        for key in scope:
            row = by_key.get(key)
            if row is None:
                refusals.append(f"outcome {name!r} scopes {key}, which is no step in steps.md")
            elif key in member:
                refusals.append(f"{key} is in two outcomes' scopes, {member[key]!r} and "
                                f"{name!r}; a card holds one milestone")
            elif not row.shipped:
                member[key] = name
    return member


def _carried_outcomes(items: Sequence[Item], containers: set[str], own: Mapping[str, str],
                      reports: list[str]) -> list[Item]:
    """Each step item with the outcome its card is filed in: none for a split step, else
    its own, else that of the nearest split step above it holding one (the coordinator's
    code call, 14:4x 10-21, under R-BAL274's one milestone per card).  A split step leaves
    the board, so its milestone would count it beside its leaves while they grouped under
    none.  After the cutover ``quill file step --parent`` files a leaf in its split step's
    milestone only while that step holds one, which no split step this files does
    (``_filing._leaf_milestone``).  An outcome left holding no card -- its open scope steps
    all split steps whose leaves are filed in other outcomes' milestones -- is reported
    (:func:`no_milestone`), and gets no milestone."""
    parents = {item.key: item.links.parent for item in items}

    def carried(key: str | None) -> str | None:
        while key is not None and key not in own:
            key = parents[key]
        return None if key is None else own[key]

    found = [replace(item, outcome=None if item.key in containers else carried(item.key))
             for item in items]
    held = {item.outcome for item in found}
    reports += [f"outcome {no_milestone(name, shipped=False)}"
                for name in dict.fromkeys(own.values()) if name not in held]
    return found


def no_milestone(name: str, shipped: bool) -> str:
    """Why the outcome ``name`` gets no milestone, which would hold no card: its whole scope
    ``shipped``, or else its open steps are all split steps, whose leaves are filed in
    other outcomes' milestones (:func:`_carried_outcomes`).  The one spelling of both,
    which this module's reports and ``_migrate_input``'s refusal of a milestone given for
    such an outcome each name."""
    return (f"{name!r}'s whole scope has shipped, so it gets no milestone" if shipped else
            f"{name!r} holds no card: its open steps are all split steps, whose leaves are "
            "filed in other outcomes' milestones, so it gets no milestone")


def _together_refusals(by_key: Mapping[str, StepRow], reports: list[str]) -> list[str]:
    """Each :data:`DEPLOY_TOGETHER` key naming no step (refused), or a step that no longer
    moves (reported: its leaves have shipped, so no release is left to hold together)."""
    refusals = []
    for key in sorted(DEPLOY_TOGETHER):
        row = by_key.get(key)
        if row is None:
            refusals.append(f"R-BAL240 labels {key} {DEPLOY_TOGETHER_LABEL}, which is no step "
                            "in steps.md")
        elif row.shipped:
            reports.append(f"R-BAL240's {key} has shipped, so no card carries "
                           f"{DEPLOY_TOGETHER_LABEL!r}")
    return refusals


def _findings(registries: Registries, moving: set[str], refusals: list[str],
              reports: list[str]) -> list[Item]:
    """A finding for each ledger row naming a step owner, blocked by the first, its owner,
    and no sub-issue of it, which its progress would count (R-BAL395); a question for each
    naming none, and for each of :data:`ALSO_A_QUESTION` too (R-BAL239)."""
    items, asked = [], set()
    for position, row in enumerate(registries.findings):
        owners = [OWNER_RX.match(part.strip()) for part in split_owners(row.owner.strip())]
        if not all(owners):
            refusals.append(f"{row.key}: its owner cell {row.owner!r} is not the owner grammar")
            continue
        steps = [found.group("owner") for found in owners
                 if found.group("owner") not in NON_STEP_OWNERS]
        question = Item(row.arc, row.bare_ident, "question", row, (row.arc,),
                        Links(place=(QUESTIONS_TIER, position)))
        if not steps:
            items.append(question)
            continue
        owner = f"{row.arc}:{steps[0]}"
        if owner not in moving:
            refusals.append(f"{row.key}'s first owner {owner} does not move (shipped, or no "
                            "step): re-point the row before the migration")
        items.append(Item(row.arc, row.bare_ident, "finding", row, (row.arc,),
                          Links(blockers=(owner,))))
        if row.key in ALSO_A_QUESTION:
            asked.add(row.key)
            items.append(Item(row.arc, row.bare_ident + QUESTION_SUFFIX, "question", row,
                              (row.arc,), Links(place=(QUESTIONS_TIER, position))))
    reports += [f"R-BAL239's {key} is not a ledger row with a step owner, so it gets no "
                "question card" for key in sorted(ALSO_A_QUESTION - asked)]
    refusals += [f"{item.key}: a question card states the developer's question, and its row's "
                 "operator or developer-decision owner has no note"
                 for item in items if item.kind == "question" and not question_note(item.row)]
    return items


def _shape_refusals(items: Sequence[Item]) -> list[str]:
    """A key two items share; a parent with more sub-issues than GitHub holds; a card
    nested deeper than GitHub nests."""
    counts = Counter(item.key for item in items)
    refusals = [f"{key} is the key of {count} items" for key, count in counts.items()
                if count > 1]
    children = defaultdict(int)
    parent_of = {}
    for item in items:
        if item.links.parent is not None:
            children[item.links.parent] += 1
            parent_of[item.key] = item.links.parent
    refusals += [f"{key} would hold {count} sub-issues; GitHub holds {SUB_ISSUE_CAP}"
                 for key, count in children.items() if count > SUB_ISSUE_CAP]
    for item in items:
        depth, key = 1, item.key
        while key in parent_of and depth <= NESTING_CAP:
            key, depth = parent_of[key], depth + 1
        if depth > NESTING_CAP:
            refusals.append(f"{item.key} would sit more than {NESTING_CAP} cards deep; GitHub "
                            f"nests {NESTING_CAP}")
    return refusals
