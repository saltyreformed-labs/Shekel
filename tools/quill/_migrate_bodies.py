"""X-cx's migration, its CARDS' TEXT: each item's title, body and comment, and the census.

What each card says (the L7 design's draft 4 s.3):

- a **step**: its ``steps.md`` sentence; ``## Spec``; its entry in its arc document,
  with every paragraph the input file calls a ``note`` taken out FIRST, then DEDENTED by
  its checkbox line's indentation (a line still left of it is refused, V3-2) and the
  checkbox's box dropped, so ``- [ ] **X-h** ...`` opens as ``**X-h** ...``; then each
  steps-section paragraph the input file gives this card (R-BAL245's ``card-body``);
- a **finding**: its ``ledger.md`` cell;
- a **question**: ONLY the developer's question -- the row's note word for word, or the
  question the input file wrote for it (R-BAL241) -- so ``quill file ruling
  --from-question`` takes exactly that; the row's other cells go in ONE comment opening
  :data:`AS_FILED`;
- a **ruling**: its ``rulings.md`` cell.

Every card but a question ends with an **As filed block** (:meth:`RowText.block`), and a
question's As filed comment holds one: a fenced quotation, word for word, of the row's
cells the card carries nowhere else (its lead opens the body: a step's sentence, a
finding's or a ruling's cell; a question's question, its row's note, is quoted inside its
``owner`` cell too), and first the registry and commit it was filed from.  A step's
``starts`` cell is quoted by its STORED part alone, as ``blocked by``: its blockers and
their prose, every aside and annotation whole (``StepRow.starts_stored``), never its head,
which the ranks derive (the coordinator's rulings on review 10, LOW-1, and review 11,
MEDIUM-1).  A fence renders nothing and links no ``#N``; the links and labels govern, and
the block is a quotation.  The TEXT a card's row writes
-- its lead (a question's where the input file writes none) and the block's cells --
comes from ONE producer, :func:`row_text`, which :func:`build` composes each card from.
Besides its structure (its kind, links and milestone, which the plan grades, and its board
place: the plan grades that it is on the board, ``migrate verify`` its order), two parts
of a card are made from its row OUTSIDE it, and graded apart, against this checkout only:
its title's alias (``Item.alias``, the row's id), which is its key, so a
card is found by it (``_migrate_plan.card_key``, ``_migrate_plan.py:179``) and its title
is compared exactly (:func:`differences`); and its labels (``Item.labels``: the arc's, and
for a step ``moves-money`` from its sentence, ``_migrate_source.step_labels``,
``_migrate_source.py:480``), which the plan holds equal to the item's
(``_migrate_plan._Grading._unlike``, ``_migrate_plan.py:655``).

Each card is held to quill's one check (:func:`check.violations`) with ``changed`` =
{title} (draft 4 s.3): its kind, its one arc label and its short title.

**The census is computed from the BUILT bodies** (draft 4 s.11 V3-2): every non-blank
line of each arc's steps section is put in exactly one place -- on a card, a note that
moves nowhere, a shipped step's pointer, the card already filed for X-cx
(:data:`_migrate_source.MAPPED`, whose text is not rewritten), a heading, or a paragraph
kept for L8 by its disposition -- and each step card's body is read back
(:func:`step_regions`): its sentence and its As filed block must be exactly what its row
makes (:func:`row_text`), and the NON-BLANK lines of its spec and of each given paragraph
must be the lines put on it, region by region and in order, none missing, none doubled and
nothing else
(:func:`census`).  A non-blank line the assembly lost, misplaced or added is a refusal,
whatever the spans say.

**What the census does not grade** (review of PR #546, LOW 2): BLANK lines, so a
paragraph break the assembly dropped or added (two paragraphs merged, one split) passes
it; and the DEDENT, which the census reads through the same :func:`dedent` the builder
writes with, so a wrong one is wrong on both sides alike.  The dedent is graded by its own
tests; the stored text, every blank line and indent of it, by ``migrate verify``, which
holds each fetched body EQUAL to its built one (draft 4 s.5).  Nor the bare ``#N`` each
card's text holds, which :func:`_migrate_numbers.link` writes as the input file decides
AFTER the census (R-BAL277), so the census reads what this module builds: a marker or a
link it writes changes no line's place, and ``migrate verify`` holds the stored text to
what it wrote.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tools.ci.arc_steps import CHECKBOX_RX, Entry
from tools.quill._migrate_input import Decisions
from tools.quill._migrate_source import (
    MAPPED,
    Item,
    Layout,
    Paragraph,
    Registries,
    Source,
    Spec,
    indentation,
    question_note,
)
from tools.quill.check import TITLE, Draft, card_title, normalized, violations

#: The line a question's As filed comment opens with, by which a run finds it (draft 4
#: s.4).
AS_FILED = "As filed in ledger.md"
SPEC_HEADING = "## Spec"
#: The heading over a steps-section paragraph given to one card (R-BAL245, ``card-body``).
BOUND_HEADING = "## From the arc document's steps section"
#: Each kind's registry, named in its As filed block.
REGISTRY = {"step": "steps.md", "finding": "ledger.md", "question": "ledger.md",
            "ruling": "rulings.md"}
_FENCE = "```"
#: How an As filed block's first line opens; ``migrate verify`` (X-cx's B2) reads it.
FILED_FROM = "filed from: "
#: How every As filed block opens, the fence and its first line's start.
AS_FILED_OPENING = f"{_FENCE}text\n{FILED_FROM}"
#: An As filed block's first line after :data:`FILED_FROM`: its registry, then its commit
#: (:func:`_filed_line`).
_FILED_LINE = re.compile(r"(\S+ at )(\S+)")

#: The census's places for a line (:func:`census`).
ON_A_CARD = "on a card"
A_NOTE = "a note, moving nowhere"
SHIPPED = "a shipped step's pointer, moving nowhere"
HEADING = "a heading"
NO_STEP = "a checkbox for no step"
FILED_ALREADY = "a step already filed as its card, whose text is not rewritten"


@dataclass(frozen=True)
class Built:
    """One card's text: its title, its body, and its comment (None for none)."""

    title: str
    body: str
    comment: str | None


@dataclass(frozen=True)
class RowText:
    """The text an item's ROW writes on its card (:func:`row_text`): its LEAD -- a step's
    sentence, a finding's ``finding`` cell, a ruling's ``rule``, a question's question as
    its row's note asks it -- and its As filed block's registry and cells, by column.  The
    input file's work is none of it (a name, the notes a step's spec leaves out, a
    paragraph given to it, a written question), nor is the commit a block names, which is
    the run's read, nor anything the ranks derive: a step's rank, which places its card,
    and its ``starts`` cell's head, of which the block quotes the stored part alone as
    ``blocked by`` (``StepRow.starts_stored``: the blockers and their prose)."""

    lead: str
    registry: str
    cells: tuple[tuple[str, str], ...]

    def block(self, commit: str) -> str:
        """The As filed block naming ``commit``: the registry and commit, then each cell by
        its column, fenced.  The one maker of a block."""
        lines = [f"{FILED_FROM}{self.registry} at {commit}",
                 *(f"{column}: {value}" for column, value in self.cells)]
        return "\n".join([_FENCE + "text", *lines, _FENCE])

    def fenced(self) -> list[str]:
        """Each part of it holding ```: its opening text (the lead), then each cell by its
        column.  No part may: in the lead, which opens a line of its card, they would open
        a fenced block swallowing the As filed block where the card renders; a cell, which
        follows its column's name inside that block, is held to the same rule, one for
        every part.  The one check of every part it makes; :func:`build` refuses a row
        holding any."""
        parts = [("its opening text", self.lead),
                 *((f"its {column} cell", value) for column, value in self.cells)]
        return [name for name, text in parts if _FENCE in text]


def row_text(item: Item) -> RowText:
    """What ``item``'s row writes on its card (:class:`RowText`), from the row alone: the one
    producer of it.  :func:`build` composes each card from it, :func:`census` reads a step
    card back against it, and ``migrate verify`` holds the row a card's As filed block names
    to it (:func:`row_texts`).  A question's lead is the body :func:`build` gives it where
    the input file writes no question; the note it is, word for word, sits in the
    ``owner`` cell too."""
    row = item.row
    if item.kind == "step":
        return RowText(row.title, REGISTRY[item.kind],
                       (("id", row.ident), ("also", row.aliases),
                        ("blocked by", row.starts_stored or "--")))
    if item.kind == "ruling":
        return RowText(row.rule, REGISTRY[item.kind],
                       (("id", row.ident), ("also", row.also), ("date", row.date)))
    if item.kind == "question":
        lead, finding = question_note(row) or "", (("finding", row.finding),)
    else:
        lead, finding = row.finding, ()
    return RowText(lead, REGISTRY[item.kind],
                   (("id", row.ident), ("also", row.also), *finding,
                    ("worst measured", row.worst), ("status", row.status), ("owner", row.owner)))


def row_texts(items: Sequence[Item]) -> dict[str, frozenset[RowText]]:
    """What each item's row writes on its card (:func:`row_text`), by key, ``items`` being
    one commit's: a set, as a key two rows share there (which the source refuses) makes
    two, and a card saying what either writes says what a row of that commit writes."""
    found = defaultdict(set)
    for item in items:
        found[item.key].add(row_text(item))
    return {key: frozenset(made) for key, made in found.items()}


def masked(text: str) -> str:
    """``text`` as two runs compare a text whose built form holds an As filed block
    (:func:`differences`): :func:`check.normalized`, that block's commit left out
    (:func:`_filed_line`) and nothing else -- its registry is compared.  The commit is the
    one the card was written at, which a later run reading the same text at a later commit
    names otherwise (draft 4 s.5: the commit is not compared here; ``migrate verify`` reads
    it, :func:`filed_commit`, holds it to resolve, and holds the row it names to make what
    this checkout's row makes, :func:`row_text`).  A line elsewhere that reads like the
    block's first is compared whole."""
    text = normalized(text)
    split = _filed_line(text)
    return text if split is None else f"{split[0]}{split[1]}<commit>{split[3]}"


def filed_commit(text: str) -> str | None:
    """The commit ``text``'s As filed block names (:func:`_filed_line`), None for a text
    holding no such block: what ``migrate verify`` holds to resolve."""
    split = _filed_line(normalized(text))
    return None if split is None else split[2]


def _filed_line(text: str) -> tuple[str, str, str, str] | None:
    """``text`` split at its As filed block -- what precedes the block; the block up to its
    commit; the commit; what follows -- the block being the LAST opening with
    :data:`AS_FILED_OPENING`, as :func:`build` puts it last and :func:`step_regions` reads
    it; None when there is none, or its first line is not a registry and a commit.
    :func:`masked` and :func:`filed_commit` read the block here alone."""
    head, opened, tail = text.rpartition(AS_FILED_OPENING)
    line, newline, rest = tail.partition("\n")
    found = _FILED_LINE.fullmatch(line) if opened else None
    if found is None:
        return None
    return head, f"{opened}{found.group(1)}", found.group(2), f"{newline}{rest}"


def differences(built: Built, title: str, body: str, comment: str | None) -> list[str]:
    """The parts of a card that differ from ``built``, its text as the registries make it:
    ``title`` exactly; ``body`` and its As filed ``comment``, the one of them that holds the
    As filed block :func:`masked` -- a question's comment (the only text :func:`build` gives
    a comment), any other card's body -- and a question's body :func:`check.normalized`
    whole, so a written question quoting a block's first line is compared whole.  A
    ``comment`` of None is not compared: whether one is due is the caller's question (the
    plan writes a missing one, and refuses one more).  A comment given where ``built`` has
    none is a difference."""
    found = [] if title == built.title else ["title"]
    found += [] if _same(body, built.body, built.comment is None) else ["body"]
    if comment is not None and not _same(comment, built.comment or "", True):
        found.append("comment")
    return found


def _same(text: str, built: str, blocked: bool) -> bool:
    """Whether ``text`` says what ``built`` does (:func:`differences`): :func:`masked` where
    it is ``blocked``, the text holding the As filed block, else whole."""
    if blocked:
        return masked(text) == masked(built)
    return normalized(text) == normalized(built)


def dedent(line: str, indent: int, opener: bool) -> str:
    """One line of a step's entry as its card holds it: ``indent`` spaces taken off, and on
    the ``opener`` (the checkbox line) the box too; a blank line is empty.  The spec's
    builder and the census both read it, so the two cannot disagree on what a line
    becomes."""
    if not line.strip():
        return ""
    line = line[indent:]
    if opener:
        found = CHECKBOX_RX.match(line)
        line = line[found.start("step") - len("**"):]
    return line


def _note_lines(spec: Spec, decisions: Decisions) -> set[int]:
    """The lines of ``spec``'s entry the input file calls a note: they move nowhere."""
    return {index for paragraph in spec.paragraphs
            if decisions.notes.get((paragraph.arc, paragraph.step, paragraph.first_line))
            == "note" for index in range(paragraph.start, paragraph.stop)}


def _spec_text(item: Item, spec: Spec, lines: Sequence[str], decisions: Decisions,
               refusals: list[str]) -> str:
    """A step's spec as its card holds it: notes out first, then dedented, box dropped."""
    notes = _note_lines(spec, decisions)
    kept = [index for index in range(spec.entry.start, spec.entry.stop) if index not in notes]
    left = [index + 1 for index in kept
            if lines[index].strip() and indentation(lines[index]) < spec.indent]
    if left:
        refusals.append(f"{item.key}: lines {left} of its arc document sit left of its "
                        "checkbox once its notes are taken out (V3-2): call each one's "
                        "paragraph a note, or indent it")
    return "\n".join(dedent(lines[index], spec.indent, index == spec.entry.start)
                     for index in kept).strip("\n")


@dataclass(frozen=True)
class Inputs:
    """What the cards' text is built from: the registries, what they hold
    (:func:`_migrate_source.read_source`) and what the input file decides."""

    source: Source
    registries: Registries
    decisions: Decisions


def build(inputs: Inputs) -> tuple[dict[str, Built], list[str]]:
    """Every item's card text, by key, and what is refused in it."""
    refusals = []
    bound = defaultdict(list)
    for paragraph in inputs.source.preambles:
        given = inputs.decisions.preambles.get((paragraph.arc, paragraph.first_line))
        if given is not None and given.disposition == "card-body":
            bound[given.fields["step"]].append(paragraph)
    built = {item.key: _built(item, inputs, bound[item.key], refusals)
             for item in inputs.source.items if item.key not in MAPPED}
    return built, refusals


def _built(item: Item, inputs: Inputs, bound: Sequence[Paragraph],
           refusals: list[str]) -> Built:
    """One item's card text (the module docstring), composed from what its row writes
    (:func:`row_text`) and the input file's decisions, held to quill's check with only its
    title written; ``bound``: the steps-section paragraphs given to its card."""
    made = row_text(item)
    fenced = made.fenced()
    if fenced:
        refusals.append(f"{item.key}: its row puts {_FENCE} in {', '.join(fenced)}; no text a "
                        "row writes may hold them, as they can open a fenced block on its card "
                        "that swallows its As filed block")
    block = made.block(inputs.registries.commit)
    comment = None
    if item.kind == "step":
        spec = inputs.source.specs.get(item.key)
        text = "" if spec is None else _spec_text(
            item, spec, inputs.registries.documents[item.arc].splitlines(), inputs.decisions,
            refusals)
        parts = [made.lead, SPEC_HEADING, text]
        for paragraph in bound:
            parts += [BOUND_HEADING, paragraph.text]
        body = "\n\n".join([*parts, block])
    elif item.kind == "question":
        body = inputs.decisions.questions.get(item.key, made.lead)
        comment = f"{AS_FILED}\n\n{block}"
    else:
        body = f"{made.lead}\n\n{block}"
    title = card_title(item.alias, inputs.decisions.names.get(item.key, ""))
    refusals += [f"{item.key}: {problem}" for problem in violations(
        Draft(item.kind, title, body, item.labels), {TITLE})]
    return Built(title, body, comment)


def step_regions(body: str) -> tuple[str, list[str], list[list[str]], str] | None:
    """A step card's body read back (:func:`build`'s shape): what precedes
    :data:`SPEC_HEADING` (its sentence); the non-blank lines of its spec, and of each
    steps-section paragraph given to it under :data:`BOUND_HEADING`, in order; and its
    As filed block, from the LAST block opening with :data:`FILED_FROM` to the end.
    None when it is not a step card's shape."""
    head, opened, rest = body.partition(f"\n\n{SPEC_HEADING}\n\n")
    rest, filed, tail = rest.rpartition(f"\n\n{AS_FILED_OPENING}")
    if not (opened and filed):
        return None
    spec, *bound = rest.split(f"\n\n{BOUND_HEADING}\n\n")
    return (head, _filled(spec), [_filled(paragraph) for paragraph in bound],
            f"{AS_FILED_OPENING}{tail}")


def _filled(text: str) -> list[str]:
    """``text``'s non-blank lines: what the census counts."""
    return [line for line in text.splitlines() if line.strip()]


def census(inputs: Inputs,
           built: Mapping[str, Built]) -> tuple[dict[str, Counter], list[str]]:
    """Every non-blank line of each arc's steps section in exactly one place, by arc, and
    a refusal for each step card whose BUILT body, read back (:func:`step_regions`), is
    not what its sources make it: its sentence; its entry's non-blank lines as its spec;
    each given paragraph's non-blank lines, in the order the source puts them; its As
    filed block -- nothing missing, doubled, moved or added -- and for each line put on a
    card nobody built.  Blank lines and the dedent are not graded here (the module
    docstring)."""
    places, expected = {}, defaultdict(list)
    for arc, layout in inputs.source.layouts.items():
        places[arc] = Counter()
        for index, placed, card, block in _placed(arc, layout, inputs):
            places[arc][ON_A_CARD if card is not None else placed] += 1
            if card is not None:
                expected[card, (arc, block)].append((arc, index, placed))
    refusals = [f"census: line {lines[0][1] + 1} of {lines[0][0]}'s document belongs on "
                f"{card}'s card, which nothing builds" for (card, _), lines in expected.items()
                if card not in built]
    for item in inputs.source.items:
        if item.kind == "step" and item.key in built:
            made = row_text(item)
            refusals += _census_refusal(item, _wanted(item, inputs, expected),
                                        step_regions(built[item.key].body),
                                        (made.lead, made.block(inputs.registries.commit)))
    return places, refusals


def _wanted(item: Item, inputs: Inputs, expected: Mapping) -> list[list]:
    """What a step card must hold, block by block: its entry's lines, then each given
    paragraph's in the order :attr:`_migrate_source.Source.preambles` holds them (arc by
    arc, then line); each line ``(arc, index, text)``."""
    spec = inputs.source.specs.get(item.key)
    opener = None if spec is None else (item.arc, spec.entry.start)
    order = {(paragraph.arc, paragraph.start): position
             for position, paragraph in enumerate(inputs.source.preambles)}
    blocks = sorted((block for card, block in expected if card == item.key and block != opener),
                    key=order.__getitem__)
    return [expected.get((item.key, opener), []),
            *(expected[item.key, block] for block in blocks)]


def _census_refusal(item: Item, wanted: Sequence[Sequence[tuple[str, int, str]]],
                    regions: tuple[str, list[str], list[list[str]], str] | None,
                    row: tuple[str, str]) -> list[str]:
    """The refusal for a step card whose body read back is not ``row`` -- its sentence and
    its As filed block, as its row makes them (:func:`row_text`) -- around ``wanted`` (its
    spec's lines, then each given paragraph's), naming the first line out of place."""
    if regions is None:
        return [f"census: {item.key}'s card is not in a step card's shape"]
    head, spec, bound, tail = regions
    if (head, tail) != row:
        return [f"census: {item.key}'s card holds text before its spec or after its As filed "
                "block's start that its sentence and its cells do not"]
    got = [spec, *bound]
    if [[text for _, _, text in lines] for lines in wanted] == got:
        return []
    if len(got) != len(wanted):
        return [f"census: {item.key}'s card holds {len(got) - 1} steps-section paragraphs "
                f"given to it; {len(wanted) - 1} are"]
    return [_first_misplaced(item, wanted, got)]


def _first_misplaced(item: Item, wanted: Sequence[Sequence[tuple[str, int, str]]],
                     got: Sequence[Sequence[str]]) -> str:
    """The first line of a step card out of its place, block by block, given that the
    card and its sources differ and hold as many blocks."""
    for lines, held in zip(wanted, got):
        for position, (arc, index, text) in enumerate(lines):
            if position >= len(held) or held[position] != text:
                return (f"census: line {index + 1} of {arc}'s document belongs on "
                        f"{item.key}'s card, and its built body does not hold it in its place")
        if len(held) > len(lines):
            return (f"census: {item.key}'s card holds a line no source line puts there: "
                    f"{held[len(lines)][:80]!r}")
    return f"census: {item.key}'s card differs from its sources in a way this census cannot name"


def _placed(arc: str, layout: Layout,
            inputs: Inputs) -> list[tuple[int, str, str | None, int | None]]:
    """Each non-blank line of ``arc``'s steps section: its index; either its place and no
    card, or the text its card must hold and that card's key; and the first line of the
    block it comes from (its entry, or its paragraph), within which a card holds its
    lines in order."""
    lines = inputs.registries.documents[arc].splitlines()
    entry_of = {index: entry for entry in layout.entries
                for index in range(entry.start, entry.stop)}
    preamble_of = {index: paragraph for paragraph in inputs.source.preambles
                   if paragraph.arc == arc for index in range(paragraph.start, paragraph.stop)}
    placed = []
    for index in range(*layout.span):
        if not lines[index].strip():
            continue
        if index in entry_of:
            placed.append((index, *_entry_place(arc, entry_of[index], index, lines, inputs)))
            continue
        paragraph = preamble_of.get(index)
        given = None if paragraph is None else inputs.decisions.preambles.get(
            (arc, paragraph.first_line))
        if given is not None and given.disposition == "card-body":
            placed.append((index, lines[index], given.fields["step"], paragraph.start))
        elif paragraph is not None:
            placed.append((index, "a steps-section paragraph kept for L8: "
                           + (given.disposition if given else "undecided"), None, None))
        else:
            placed.append((index, HEADING, None, None))
    return placed


def _entry_place(arc: str, entry: Entry, index: int, lines: Sequence[str],
                 inputs: Inputs) -> tuple[str, str | None, int | None]:
    """Where line ``index`` of ``entry`` goes: on its open step's card as :func:`dedent`
    makes it, unless the input file calls its paragraph a note; else it moves nowhere, as
    a note, the entry of a step already filed (:data:`_migrate_source.MAPPED`), a shipped
    step's pointer, or a checkbox for no step (refused by the source)."""
    key = f"{arc}:{entry.step}"
    spec = inputs.source.specs.get(key)
    if key in MAPPED:
        return FILED_ALREADY, None, None
    if spec is not None and index not in _note_lines(spec, inputs.decisions):
        return dedent(lines[index], spec.indent, index == entry.start), key, entry.start
    if spec is not None:
        return A_NOTE, None, None
    shipped = any(row.shipped for row in inputs.registries.steps
                  if (row.arc, row.bare_ident) == (arc, entry.step))
    return (SHIPPED if shipped else NO_STEP), None, None
