"""X-cx's migration, its INPUT FILE: the text a session wrote and the developer approved.

No registry row has what some cards need, so a session writes it into one JSON file
and a fresh reviewer grades it before anything is filed (rulings ``balance:R-BAL237``,
``R-BAL241``, ``R-BAL243``, ``R-BAL245``; the L7 design's draft 4 s.2 and s.11).  The
file lives OUTSIDE the repository: its text may quote a real figure, which the private
tracker allows (``R-BAL172``) and this public repository does not (``R-BAL132``).

Seven maps, each GRADED BOTH WAYS against what the registries hold (:func:`grade`): an
entry the registries need and the file lacks is refused, and so is one the file holds
that names nothing the registries need.  Every entry records the hash of the text it
was written from (:func:`_migrate_source.source_hash`, draft 4 s.11 V3-3), so an entry
whose source changed since is refused until it is written and reviewed again.  One map is
graded here for its form alone: ``numbers``, whose text is a card's as built, which
:mod:`_migrate_numbers` grades it against both ways.

- ``names``: ``{"arc:alias": {"name", "source"}}``, every card's short name (R-BAL237);
  its title is ``[alias] name`` (:func:`check.card_title`), and its length is
  :func:`check.violations`' to grade, on the built card.  A card already filed
  (:data:`_migrate_source.MAPPED`) keeps its title and takes no name.
- ``questions``: ``{"arc:alias": {"question", "source"}}``, a question written for a
  question card whose note holds none (R-BAL241: PC-501, REC-531 and N-345 then);
- ``notes_kept``: ``{"arc:alias": {"source"}}``, a note with no ``?`` judged to be the
  question as it stands (V3-9).  Every note with no ``?`` is in exactly one of the two;
  a note that holds one moves word for word and is in neither.
- ``notes``: ``[{"arc", "step", "first_line", "disposition", "source"}]``, each paragraph
  of an open step's entry starting at or left of its checkbox (V3-2): the step's
  ``own`` text, kept on its card, or a ``note`` about other steps, which moves nowhere.
- ``preambles``: ``[{"arc", "first_line", "disposition", "source", ...}]``, each prose
  paragraph of a steps section outside every entry (R-BAL245), by where it goes after
  the cutover: ``dropped`` (its other ``home`` named), a ``rule`` (its ``home``,
  ``CLAUDE.md`` or a ``.claude/rules`` file, written by L8's PR), ``blocked-by`` (an
  edge ``steps.md`` already holds: the coordinator's registry edit, never the
  migration's, draft 4 s.12), ``card-body`` (appended to one open ``step``'s card, the
  only disposition the migration writes), or ``reasoning`` (L8's keep-or-drop list).
- ``milestones``: ``{"outcome": {"title", "description", "source"}}``, each OUTCOMES row's
  milestone (R-BAL243), titled with no number: the order among outcomes is the board's.
- ``numbers``: ``{"arc:alias": {"each": [["#N", treatment], ...], "source"}}``, each bare
  ``#N`` a card's text holds, in reading order, and its treatment, one of
  :data:`TREATMENTS` (R-BAL277): ``card`` stays a link to the tracker's card N, ``pr``
  becomes a link to the app's pull request N, ``plain`` links nothing.  Its ``source`` is
  the hash of the card's text as built (:func:`_migrate_numbers.decided_from`).
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from tools.plan_gate.registries import StepRow
from tools.quill._migrate_source import (
    Item,
    Paragraph,
    Registries,
    Source,
    MAPPED,
    no_milestone,
    question_source,
    source_hash,
    source_text,
)
from tools.quill.check import card_title

#: The file's seven maps.
MAPS = ("names", "questions", "notes_kept", "notes", "preambles", "milestones", "numbers")
NOTE_DISPOSITIONS = ("own", "note")
#: How a bare ``#N`` is written on its card (R-BAL277; :mod:`_migrate_numbers` writes each).
TREATMENTS = ("card", "pr", "plain")
#: A number as its card writes it, ``#`` and its digits (group 1): what a ``numbers`` entry
#: names, and the one spelling :data:`_migrate_numbers.CANDIDATE` finds in a card's text.
NUMBER = re.compile(r"#(\d+)")
#: Each preamble disposition and the fields it carries beside ``arc``, ``first_line``,
#: ``disposition`` and ``source``.
PREAMBLE_FIELDS = {
    "dropped": ("home",),
    "rule": ("home",),
    "blocked-by": ("step", "blocked_by"),
    "card-body": ("step",),
    "reasoning": (),
}
#: Where a ``rule`` goes: CLAUDE.md, or a path-scoped rules file.
_RULE_HOME = re.compile(r"^(CLAUDE\.md|\.claude/rules/[\w.-]+\.md)$")
#: A milestone title carries no outcome number (R-BAL243: "the names carry no
#: O-numbers").
_NUMBERED = re.compile(r"^O\d")


class InputError(ValueError):
    """The input file is not a JSON object."""


@dataclass(frozen=True)
class Preamble:
    """Where one steps-section paragraph goes: its disposition and that disposition's
    fields (:data:`PREAMBLE_FIELDS`)."""

    disposition: str
    fields: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Numbered:
    """One card's bare ``#N`` as the input file decides them: each number and its treatment,
    in reading order, and the hash of the card's text they were decided for."""

    each: tuple[tuple[str, str], ...]
    source: str


@dataclass(frozen=True)
class Decisions:
    """What the input file decides, entry by entry, for the entries it states well: each
    card's name, each rewritten question, each entry paragraph's and each preamble's
    disposition, each outcome's milestone (title, description), and each card's numbers."""

    names: Mapping[str, str]
    questions: Mapping[str, str]
    notes: Mapping[tuple[str, str, str], str]
    preambles: Mapping[tuple[str, str], Preamble]
    milestones: Mapping[str, tuple[str, str]]
    numbers: Mapping[str, Numbered]


def load(path: Path) -> dict:
    """The input file as JSON.

    Raises:
        InputError: When it is not UTF-8 JSON, is JSON but not an object, or names a key
            twice in one object, where JSON keeps the last and the entry a reviewer read
            first would be dropped unseen.
        OSError: When it cannot be read.
    """
    def unique(pairs: list[tuple[str, object]]) -> dict:
        """One object's pairs, refused when a key comes twice."""
        keys = [key for key, _ in pairs]
        if twice := sorted({key for key in keys if keys.count(key) > 1}):
            raise InputError(f"{path} names {twice} twice in one object")
        return dict(pairs)

    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise InputError(f"{path} is not UTF-8 JSON: {error}") from error
    if not isinstance(data, dict):
        raise InputError(f"{path} holds {type(data).__name__}, not an object of {MAPS}")
    return data


def _strings(entry, wanted: tuple[str, ...], where: str, problems: list[str]) -> bool:
    """Whether ``entry`` is an object holding exactly the fields ``wanted``, each a
    non-empty string; each way it is not is a problem naming ``where``."""
    if not isinstance(entry, dict):
        problems.append(f"{where}: an entry is an object, not {type(entry).__name__}")
        return False
    missing = [name for name in wanted if name not in entry]
    extra = sorted(set(entry) - set(wanted))
    empty = [name for name in wanted if name in entry
             and not (isinstance(entry[name], str) and entry[name].strip())]
    problems += ([f"{where}: missing {missing}"] if missing else []) + (
        [f"{where}: fields {extra} are not this entry's"] if extra else []) + (
        [f"{where}: {empty} must be text"] if empty else [])
    return not (missing or extra or empty)


def _stale(entry: dict, text: str, where: str, problems: list[str]) -> bool:
    """Whether ``entry`` was written from text other than ``text``: a problem, refused
    until it is written and reviewed again (V3-3)."""
    if entry["source"] != source_hash(text):
        problems.append(f"{where}: its source changed since it was written (hash "
                        f"{entry['source']}, now {source_hash(text)}): write and review it "
                        "again")
        return True
    return False


def _map(data: dict, name: str, kind: type, problems: list[str]):
    """The map ``name`` of the file, ``kind`` (dict or list); empty, with a problem, when it
    is missing or another kind."""
    value = data.get(name)
    if not isinstance(value, kind):
        problems.append(f"input: {name!r} is missing or not a JSON "
                        f"{'object' if kind is dict else 'array'}")
        return kind()
    return value


def grade(data: dict, source: Source, registries: Registries) -> tuple[Decisions, list[str]]:
    """Grade the input file both ways against the registries: every problem, and what
    the file decides for the entries it states well.

    Args:
        data: The file as :func:`load` read it.
        source: What the registries hold (:func:`_migrate_source.read_source`).
        registries: The registries themselves.

    Returns:
        The decisions, and one problem a line (each a refusal).
    """
    problems = [f"input: {name!r} is no map of the input file ({', '.join(MAPS)})"
                for name in sorted(set(data) - set(MAPS))]
    items = {item.key: item for item in source.items}
    names = _names(_map(data, "names", dict, problems), items, source, registries, problems)
    questions = _questions(_map(data, "questions", dict, problems),
                           _map(data, "notes_kept", dict, problems), items, problems)
    notes = _notes(_map(data, "notes", list, problems), source, problems)
    preambles = _preambles(_map(data, "preambles", list, problems), source, registries,
                           problems)
    milestones = _milestones(_map(data, "milestones", dict, problems), registries, source,
                             problems)
    numbers = _numbers(_map(data, "numbers", dict, problems), items, problems)
    return Decisions(names, questions, notes, preambles, milestones, numbers), problems


def _names(given: dict, items: Mapping[str, Item], source: Source, registries: Registries,
           problems: list[str]) -> dict[str, str]:
    """Every card's name, on one line; a card already filed takes none."""
    names = {}
    problems += [f"names: {key} names no card the registries move" for key in sorted(
        set(given) - set(items))]
    problems += [f"names: {key} is plan#{MAPPED[key]} already, whose title is not rewritten"
                 for key in sorted(set(given) & set(MAPPED))]
    for key, item in items.items():
        where = f"names: {key}"
        entry = given.get(key)
        if key in MAPPED:
            continue
        if entry is None:
            problems.append(f"{where}: no name")
            continue
        if not _strings(entry, ("name", "source"), where, problems):
            continue
        if "\n" in entry["name"]:
            problems.append(f"{where}: its title {card_title(item.alias, entry['name'])!r} is "
                            "not one line")
            continue
        if not _stale(entry, source_text(item, source.specs, registries.documents), where,
                      problems):
            names[key] = entry["name"].strip()
    return names


def _questions(rewritten: dict, kept: dict, items: Mapping[str, Item],
               problems: list[str]) -> dict[str, str]:
    """Each question card whose note holds no question mark: rewritten, or kept as the
    question it is (R-BAL241, V3-9); a note holding one moves word for word."""
    asked = {key: item for key, item in items.items() if item.kind == "question"}
    for name, given in (("questions", rewritten), ("notes_kept", kept)):
        problems += [f"{name}: {key} is no question card" for key in sorted(set(given) - set(
            asked))]
    questions = {}
    for key, item in asked.items():
        note = question_source(item.row)[1]
        where = (key in rewritten, key in kept)
        if "?" in note:
            if any(where):
                problems.append(f"{key}: its note holds a question, so it moves word for word "
                                "(R-BAL241) and is in neither questions nor notes_kept")
            continue
        if where.count(True) != 1:
            problems.append(f"{key}: its note holds no question mark, so it is in exactly one "
                            f"of questions (rewritten) or notes_kept (V3-9); it is in "
                            f"{where.count(True)}")
            continue
        entry = rewritten.get(key, kept.get(key))
        wanted = ("question", "source") if key in rewritten else ("source",)
        label = f"{'questions' if key in rewritten else 'notes_kept'}: {key}"
        if not _strings(entry, wanted, label, problems) or _stale(
                entry, source_text(item, {}, {}), label, problems):
            continue
        if key in rewritten:
            if "?" not in entry["question"]:
                problems.append(f"{label}: a written question asks something; it holds no '?'")
                continue
            questions[key] = entry["question"].strip()
    return questions


def _notes(given: list, source: Source, problems: list[str]) -> dict[tuple[str, str, str], str]:
    """Each at-or-left paragraph of an open step's entry: ``own`` or ``note``."""
    wanted, ambiguous = _unique(paragraph for spec in source.specs.values()
                                for paragraph in spec.paragraphs)
    notes, named = {}, set()
    for index, entry in enumerate(given):
        where = f"notes[{index}]"
        named.add(_named(entry, ("arc", "step", "first_line")))
        if not _strings(entry, ("arc", "step", "first_line", "disposition", "source"), where,
                        problems):
            continue
        key = (entry["arc"], entry["step"], entry["first_line"].strip())
        paragraph = wanted.get(key)
        if key in ambiguous:
            problems.append(f"{where}: {key[0]}:{key[1]} has {ambiguous[key]} paragraphs "
                            f"starting {key[2][:60]!r}, so this names none of them")
        elif paragraph is None:
            problems.append(f"{where}: {key[0]}:{key[1]} has no paragraph at or left of its "
                            f"checkbox starting {key[2][:60]!r}")
        elif key in notes:
            problems.append(f"{where}: {key[0]}:{key[1]}'s paragraph {key[2][:60]!r} is listed "
                            "twice")
        elif entry["disposition"] not in NOTE_DISPOSITIONS:
            problems.append(f"{where}: disposition {entry['disposition']!r} is not one of "
                            f"{NOTE_DISPOSITIONS}")
        elif not _stale(entry, paragraph.text, where, problems):
            notes[key] = entry["disposition"]
    problems += [f"notes: {arc}:{step}'s paragraph {first[:60]!r} has no disposition (own or "
                 "note)" for arc, step, first in sorted(set(wanted) - named)]
    return notes


def _unique(found) -> tuple[dict[tuple, Paragraph], dict[tuple, int]]:
    """The paragraphs ``found`` by the key the input file names them by (arc, its step for
    an entry's paragraph, first line): those one key names alone, and each key naming
    more than one with its count, which no entry can name (``_migrate_source`` refuses
    it)."""
    by_key = defaultdict(list)
    for paragraph in found:
        key = ((paragraph.arc, paragraph.first_line) if paragraph.step is None
               else (paragraph.arc, paragraph.step, paragraph.first_line))
        by_key[key].append(paragraph)
    return ({key: shared[0] for key, shared in by_key.items() if len(shared) == 1},
            {key: len(shared) for key, shared in by_key.items() if len(shared) > 1})


def _named(entry, fields: tuple[str, ...]) -> tuple | None:
    """The paragraph an input entry names by ``fields`` (its first line stripped), well
    formed otherwise or not, so a paragraph it names is not reported missing besides
    whatever is wrong with the entry; None when it names none."""
    if not isinstance(entry, dict) or not all(isinstance(entry.get(name), str)
                                              for name in fields):
        return None
    return (*(entry[name] for name in fields[:-1]), entry[fields[-1]].strip())


@dataclass(frozen=True)
class _Targets:
    """What a preamble's entry may name: the prose paragraphs one key names alone, the keys
    naming more than one (:func:`_unique`), the steps.md rows by key, and the keys of the
    steps that move."""

    paragraphs: Mapping[tuple[str, str], Paragraph]
    ambiguous: Mapping[tuple[str, str], int]
    steps: Mapping[str, StepRow]
    moving: frozenset[str]


def _preambles(given: list, source: Source, registries: Registries,
               problems: list[str]) -> dict[tuple[str, str], Preamble]:
    """Each prose paragraph outside every entry: where it goes after the cutover."""
    targets = _Targets(
        *_unique(source.preambles), {row.key: row for row in registries.steps},
        frozenset(item.key for item in source.items if item.kind == "step"))
    preambles, named = {}, set()
    for index, entry in enumerate(given):
        named.add(_named(entry, ("arc", "first_line")))
        graded = _preamble(entry, f"preambles[{index}]", targets, problems)
        if graded is not None and graded[0] in preambles:
            problems.append(f"preambles[{index}]: {graded[0][0]}'s paragraph "
                            f"{graded[0][1][:60]!r} is listed twice")
        elif graded is not None:
            preambles[graded[0]] = graded[1]
    problems += [f"preambles: {arc}'s paragraph {first[:60]!r} has no disposition (R-BAL245)"
                 for arc, first in sorted(set(targets.paragraphs) - named)]
    return preambles


def _preamble(entry, where: str, targets: _Targets,
              problems: list[str]) -> tuple[tuple[str, str], Preamble] | None:
    """One preamble entry graded: its paragraph's key and disposition, or None with each
    problem added."""
    disposition = entry.get("disposition") if isinstance(entry, dict) else None
    if disposition not in PREAMBLE_FIELDS:
        problems.append(f"{where}: disposition {disposition!r} is not one of "
                        f"{tuple(PREAMBLE_FIELDS)}")
        return None
    extra = PREAMBLE_FIELDS[disposition]
    if not _strings(entry, ("arc", "first_line", "disposition", "source", *extra), where,
                    problems):
        return None
    key = (entry["arc"], entry["first_line"].strip())
    paragraph = targets.paragraphs.get(key)
    fields = {name: entry[name] for name in extra}
    problem = (f"{key[0]} has {targets.ambiguous[key]} paragraphs outside every entry "
               f"starting {key[1][:60]!r}, so this names none of them"
               if key in targets.ambiguous else
               f"{key[0]} has no prose paragraph outside every entry starting {key[1][:60]!r}"
               if paragraph is None else
               _target_problem(disposition, fields, targets.steps, targets.moving))
    if problem:
        problems.append(f"{where}: {problem}")
        return None
    if _stale(entry, paragraph.text, where, problems):
        return None
    return key, Preamble(disposition, fields)


def _target_problem(disposition: str, fields: Mapping[str, str], steps: Mapping,
                    moving: set[str]) -> str | None:
    """Why a preamble's disposition names a target it cannot have, or None: a rule's home
    that is neither CLAUDE.md nor a rules file; a card that does not move; an edge
    ``steps.md`` does not hold (draft 4 s.12: the coordinator adds it to the ``blocked by``
    cell, and the migration only copies it)."""
    if disposition == "rule" and not _RULE_HOME.match(fields["home"]):
        return f"a rule's home is CLAUDE.md or .claude/rules/<file>.md, not {fields['home']!r}"
    if disposition in ("card-body", "blocked-by") and fields["step"] not in moving:
        return f"{fields['step']} is no open step that moves"
    if disposition == "card-body" and fields["step"] in MAPPED:
        return (f"{fields['step']} is plan#{MAPPED[fields['step']]} already, whose text the "
                "migration does not rewrite, so a paragraph given to it would land nowhere")
    if disposition == "blocked-by" and fields["blocked_by"] not in steps[
            fields["step"]].blocked_keys():
        return (f"steps.md does not record {fields['step']} as blocked by "
                f"{fields['blocked_by']}: the coordinator adds that edge to its `blocked by` "
                "cell first (R-BAL245; the migration copies edges, it writes none of its own)")
    return None


def _milestones(given: dict, registries: Registries, source: Source,
                problems: list[str]) -> dict[str, tuple[str, str]]:
    """Each milestone, a title with no number and a description: one for every OUTCOMES row
    some card is filed in (``Item.outcome``), and none for a row that holds no card -- its
    whole scope shipped, or its open steps all split steps whose leaves are filed in other
    outcomes' milestones (``_migrate_source`` reports either, :func:`no_milestone`)."""
    outcomes = dict(registries.outcomes)
    held = {item.outcome for item in source.items if item.outcome is not None}
    moving = {item.key for item in source.items}
    problems += [f"milestones: {name!r} is no outcome in steps.md's OUTCOMES table"
                 for name in sorted(set(given) - set(outcomes))]
    problems += [f"milestones: {no_milestone(name, shipped=moving.isdisjoint(outcomes[name]))}"
                 for name in sorted(set(given) & set(outcomes) - held)]
    milestones = {}
    for name, scope in outcomes.items():
        if name not in held:
            continue
        where = f"milestones: {name!r}"
        entry = given.get(name)
        if entry is None:
            problems.append(f"{where}: no milestone")
            continue
        if not _strings(entry, ("title", "description", "source"), where, problems):
            continue
        if _NUMBERED.match(entry["title"].strip()):
            problems.append(f"{where}: its title {entry['title']!r} carries an outcome number; "
                            "the order among outcomes is the board's (R-BAL243)")
            continue
        if not _stale(entry, f"{name}\n" + " / ".join(scope), where, problems):
            milestones[name] = (entry["title"].strip(), entry["description"].strip())
    titles = [title for title, _ in milestones.values()]
    problems += [f"milestones: the title {title!r} is given twice" for title in sorted(
        {title for title in titles if titles.count(title) > 1})]
    return milestones


def _numbers(given: dict, items: Mapping[str, Item], problems: list[str]) -> dict[str, Numbered]:
    """Each card's bare ``#N`` decisions, graded for their form and for naming a card the
    migration writes; :mod:`_migrate_numbers` grades them against the card's text, both
    ways (a card holding a number no entry decides, an entry for a card holding none)."""
    problems += [f"numbers: {key} names no card the registries move" for key in sorted(
        set(given) - set(items))]
    problems += [f"numbers: {key} is plan#{MAPPED[key]} already, whose text is not rewritten"
                 for key in sorted(set(given) & set(MAPPED))]
    numbers = {}
    for key in sorted(set(given) & set(items) - set(MAPPED)):
        where, entry = f"numbers: {key}", given[key]
        each = entry.get("each") if isinstance(entry, dict) else None
        if not (isinstance(each, list) and each):
            problems.append(f"{where}: its 'each' is a non-empty list of [\"#N\", treatment]")
            continue
        wrong = [index + 1 for index, pair in enumerate(each) if not (
            isinstance(pair, list) and len(pair) == 2 and all(isinstance(part, str)
                                                              for part in pair)
            and NUMBER.fullmatch(pair[0]) and pair[1] in TREATMENTS)]
        if wrong:
            problems.append(f"{where}: its numbers {wrong} are not [\"#N\", treatment], the "
                            f"treatment one of {TREATMENTS}")
        if _strings({name: value for name, value in entry.items() if name != "each"},
                    ("source",), where, problems) and not wrong:
            numbers[key] = Numbered(tuple((text, treatment) for text, treatment in each),
                                    entry["source"])
    return numbers
