"""X-cx's migration, its input file: every map graded both ways against the registries.

The corpus is :mod:`_migrate_fake`'s; :func:`_migrate_fake.complete_input` writes the
file a session would, and each test breaks one entry of it.
"""
from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest

from tools.quill import _migrate_source
from tools.quill._migrate_fake import BALANCE, FINDINGS, STEPS, complete_input
from tools.quill._migrate_input import InputError, Numbered, Preamble, grade, load
from tools.quill._migrate_source import read_source, source_hash
from tools.quill.check import card_title


def _graded(corpus, edit=None):
    """The decisions and problems of the complete input, after ``edit`` (a function of
    the input file) changes it."""
    data = copy.deepcopy(complete_input(corpus))
    if edit is not None:
        edit(data)
    return grade(data, read_source(corpus), corpus)


def test_the_complete_input_grades_clean_and_decides_every_entry(corpus):
    """Every card named, Q-2's question written, each paragraph disposed of, the preamble
    given to A-3's card, the outcome's milestone titled."""
    decisions, problems = _graded(corpus)
    assert problems == []
    assert len(decisions.names) == 13 and decisions.names["balance:A-2a"] == "First half"
    assert decisions.questions == {
        "balance:Q-2": "Which step makes the settle-day door refuse an early day?"}
    assert sorted(decisions.notes.items()) == [
        (("balance", "A-2a", "*A flush-left note about archived steps, with no blank line "
                             "before it."), "note"),
        (("balance", "A-2b", "A flush-left note about another arc."), "note"),
        (("balance", "A-3", "A-3's own flush-left spec paragraph."), "own")]
    assert decisions.preambles == {("balance", "Every leaf here obeys one rule."): Preamble(
        "card-body", {"step": "balance:A-3"})}
    assert decisions.milestones == {"O1 the first": ("The first", "The first outcome.")}


def test_a_title_is_the_alias_in_brackets_then_the_name():
    """``[alias] name``, the name's own spaces dropped (draft 4 s.3)."""
    assert card_title("N-391 question", "  Re-read the stub ") == (
        "[N-391 question] Re-read the stub")


def _set(path, value):
    """An edit setting ``data[path[0]][path[1]]...`` to ``value``."""
    def edit(data):
        target = data
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
    return edit


def _drop(path):
    """An edit deleting ``data[path[0]]...[path[-1]]``."""
    def edit(data):
        target = data
        for part in path[:-1]:
            target = target[part]
        del target[path[-1]]
    return edit


@pytest.mark.parametrize("edit, problem", [
    (_drop(("names", "balance:A-3")), "names: balance:A-3: no name"),
    (_set(("names", "balance:A-9"), {"name": "x", "source": "0"}),
     "names: balance:A-9 names no card the registries move"),
    (_set(("names", "balance:A-3", "name"), "two\nlines"),
     "names: balance:A-3: its title '[A-3] two\\nlines' is not one line"),
    (_set(("names", "balance:A-3", "name"), " "), "names: balance:A-3: ['name'] must be text"),
    (_set(("names", "balance:A-3", "extra"), "x"),
     "names: balance:A-3: fields ['extra'] are not this entry's"),
    (_set(("names", "balance:A-3"), "Last part"),
     "names: balance:A-3: an entry is an object, not str"),
    (_set(("names", "balance:A-3", "source"), "0" * 16),
     "names: balance:A-3: its source changed since it was written"),
    (_set(("unknown",), {}), "input: 'unknown' is no map of the input file"),
    (_drop(("milestones",)), "input: 'milestones' is missing or not a JSON object"),
    (_drop(("numbers",)), "input: 'numbers' is missing or not a JSON object"),
    (_set(("notes",), {}), "input: 'notes' is missing or not a JSON array"),
])
def test_names_and_the_files_own_shape(corpus, edit, problem):
    """Every card has a name and only cards have one; a title is one line (its length is
    ``check.violations``' to grade on the built card); an entry is an object of exactly
    its fields; a stale hash is refused; the seven maps, and only they."""
    _, problems = _graded(corpus, edit)
    assert any(line.startswith(problem) for line in problems), problems


@pytest.mark.parametrize("edit, problem", [
    (_drop(("questions", "balance:Q-2")),
     "balance:Q-2: its note holds no question mark, so it is in exactly one of questions "
     "(rewritten) or notes_kept (V3-9); it is in 0"),
    (lambda data: data["notes_kept"].update({"balance:Q-2": {"source": "x"}}),
     "balance:Q-2: its note holds no question mark, so it is in exactly one of questions "
     "(rewritten) or notes_kept (V3-9); it is in 2"),
    (_set(("notes_kept", "balance:Q-1"), {"source": "x"}),
     "balance:Q-1: its note holds a question, so it moves word for word (R-BAL241) and is in "
     "neither questions nor notes_kept"),
    (_set(("questions", "balance:F-1"), {"question": "Why?", "source": "x"}),
     "questions: balance:F-1 is no question card"),
    (_set(("questions", "balance:Q-2", "question"), "Write it down."),
     "questions: balance:Q-2: a written question asks something; it holds no '?'"),
    (_set(("questions", "balance:Q-2", "source"), "0" * 16),
     "questions: balance:Q-2: its source changed since it was written"),
])
def test_a_note_without_a_question_mark_is_rewritten_or_kept_never_both(corpus, edit, problem):
    """R-BAL241 and V3-9: a note holding a ``?`` moves word for word; one holding none is
    rewritten (questions) or kept as the question it is (notes_kept), exactly one."""
    _, problems = _graded(corpus, edit)
    assert any(line.startswith(problem) for line in problems), problems


def test_a_kept_note_is_graded_and_decides_no_rewrite(corpus):
    """Kept, Q-2's note moves as it stands: no rewritten question is decided for it."""
    def keep(data):
        source = data["questions"].pop("balance:Q-2")["source"]
        data["notes_kept"]["balance:Q-2"] = {"source": source}
    decisions, problems = _graded(corpus, keep)
    assert (problems, decisions.questions) == ([], {})


@pytest.mark.parametrize("edit, problem", [
    (lambda data: data["notes"].pop(0),
     "notes: balance:A-2a's paragraph '*A flush-left note about archived steps, with no blank "
     "line ' has no disposition (own or note)"),
    (lambda data: data["notes"].append(dict(data["notes"][0])),
     "notes[3]: balance:A-2a's paragraph '*A flush-left note about archived steps, with no blank "
     "line ' is listed twice"),
    (_set(("notes", 0, "first_line"), "no such line"),
     "notes[0]: balance:A-2a has no paragraph at or left of its checkbox starting 'no such "
     "line'"),
    (_set(("notes", 0, "disposition"), "keep"),
     "notes[0]: disposition 'keep' is not one of ('own', 'note')"),
    (_set(("notes", 0, "source"), "0" * 16), "notes[0]: its source changed since it was written"),
])
def test_every_entry_paragraph_at_or_left_of_its_checkbox_is_own_or_a_note(
        corpus, edit, problem):
    """V3-2: each such paragraph has exactly one disposition, and nothing else does (a
    paragraph is named by its first 60 characters)."""
    _, problems = _graded(corpus, edit)
    assert any(line.startswith(problem) for line in problems), problems


def test_a_malformed_entry_is_reported_once_not_also_as_missing(corpus):
    """An entry naming its paragraph with a bad disposition is that one problem; the
    paragraph is not ALSO reported as having none."""
    _, problems = _graded(corpus, _set(("notes", 0, "disposition"), "keep"))
    assert problems == ["notes[0]: disposition 'keep' is not one of ('own', 'note')"]


def _preamble(**fields):
    """An edit replacing the one preamble entry's disposition and target fields."""
    def edit(data):
        entry = data["preambles"][0]
        for name in ("step", "home", "blocked_by"):
            entry.pop(name, None)
        entry.update(fields)
    return edit


@pytest.mark.parametrize("edit, problem", [
    (_preamble(disposition="dropped", home="CLAUDE.md rule 14"), None),
    (_preamble(disposition="rule", home=".claude/rules/coding.md"), None),
    (_preamble(disposition="rule", home="CLAUDE.md"), None),
    (_preamble(disposition="reasoning"), None),
    (_preamble(disposition="blocked-by", step="balance:A-3", blocked_by="balance:A-2a"), None),
    (_preamble(disposition="rule", home="docs/rules.md"),
     "preambles[0]: a rule's home is CLAUDE.md or .claude/rules/<file>.md, not 'docs/rules.md'"),
    (_preamble(disposition="card-body", step="balance:A-1"),
     "preambles[0]: balance:A-1 is no open step that moves"),
    (_preamble(disposition="blocked-by", step="balance:A-3", blocked_by="balance:A-2b"),
     "preambles[0]: steps.md does not record balance:A-3 as blocked by balance:A-2b: the "
     "coordinator adds that edge to its `blocked by` cell first (R-BAL245; the migration "
     "copies edges, it writes none of its own)"),
    (_preamble(disposition="dropped"), "preambles[0]: missing ['home']"),
    (_preamble(disposition="reasoning", step="balance:A-3"),
     "preambles[0]: fields ['step'] are not this entry's"),
    (_preamble(disposition="moved"),
     "preambles[0]: disposition 'moved' is not one of ('dropped', 'rule', 'blocked-by', "
     "'card-body', 'reasoning')"),
    (lambda data: data["preambles"].clear(),
     "preambles: balance's paragraph 'Every leaf here obeys one rule.' has no disposition "
     "(R-BAL245)"),
    (lambda data: data["preambles"].append(dict(data["preambles"][0])),
     "preambles[1]: balance's paragraph 'Every leaf here obeys one rule.' is listed twice"),
    (_set(("preambles", 0, "first_line"), "Other prose."),
     "preambles[0]: balance has no prose paragraph outside every entry starting "
     "'Other prose.'"),
    (_set(("preambles", 0, "source"), "0" * 16),
     "preambles[0]: its source changed since it was written"),
])
def test_every_preamble_goes_one_place_and_an_edge_is_steps_mds_alone(corpus, edit, problem):
    """R-BAL245's five dispositions, each with its target graded: a rule's home is
    CLAUDE.md or a rules file; a card that moves; and an edge ``steps.md`` already holds
    (draft 4 s.12) -- A-3 is recorded as blocked by A-2a, and by no A-2b."""
    _, problems = _graded(corpus, edit)
    if problem is None:
        assert problems == []
    else:
        assert any(line.startswith(problem) for line in problems), problems


def test_an_edge_once_steps_md_records_it_passes(corpus):
    """The coordinator's registry edit is what makes a ``blocked-by`` disposition pass."""
    blocked = replace(STEPS[4], blocked=STEPS[4].blocked + " / balance:A-2b")
    edited = replace(corpus, steps=(*STEPS[:4], blocked, STEPS[5]))
    data = complete_input(edited)
    data["preambles"][0].update(disposition="blocked-by", blocked_by="balance:A-2b")
    assert grade(data, read_source(edited), edited)[1] == []


@pytest.mark.parametrize("edit, problem", [
    (_drop(("milestones", "O1 the first")), "milestones: 'O1 the first': no milestone"),
    (_set(("milestones", "O9 none"), {"title": "x", "description": "y", "source": "z"}),
     "milestones: 'O9 none' is no outcome in steps.md's OUTCOMES table"),
    (_set(("milestones", "O1 the first", "title"), "O1 The first"),
     "milestones: 'O1 the first': its title 'O1 The first' carries an outcome number; the "
     "order among outcomes is the board's (R-BAL243)"),
    (_set(("milestones", "O1 the first", "source"), "0" * 16),
     "milestones: 'O1 the first': its source changed since it was written"),
])
def test_each_outcome_has_one_milestone_titled_with_no_number(corpus, edit, problem):
    """R-BAL243: a milestone each, its title free of the O-number."""
    _, problems = _graded(corpus, edit)
    assert any(line.startswith(problem) for line in problems), problems


def test_two_milestones_may_not_share_a_title(corpus):
    """Milestones are found by title (V3-5), so two with one title are refused."""
    edited = replace(corpus, outcomes=(*corpus.outcomes, ("O2 the next", ("salary:S-1",))))
    data = complete_input(edited)
    data["milestones"]["O2 the next"] = {**data["milestones"]["O1 the first"],
                                         "source": source_hash("O2 the next\nsalary:S-1")}
    assert grade(data, read_source(edited), edited)[1] == [
        "milestones: the title 'The first' is given twice"]


_FORM = "its numbers [1] are not [\"#N\", treatment], the treatment one of ('card', 'pr', 'plain')"


@pytest.mark.parametrize("entry, problem", [
    ({"each": [["#1", "plain"]], "source": "s"}, None),
    ({"each": [["#1", "card"], ["#22", "pr"]], "source": "s"}, None),
    ({"each": [], "source": "s"}, "its 'each' is a non-empty list of [\"#N\", treatment]"),
    ({"each": "#1 plain", "source": "s"}, "its 'each' is a non-empty list"),
    ({"source": "s"}, "its 'each' is a non-empty list"),
    ("plain", "its 'each' is a non-empty list"),
    ({"each": [["1", "plain"]], "source": "s"}, _FORM),
    ({"each": [["#1x", "plain"]], "source": "s"}, _FORM),
    ({"each": [["#1", "link"]], "source": "s"}, _FORM),
    ({"each": [["#1"]], "source": "s"}, _FORM),
    ({"each": [["#1", "plain", "x"]], "source": "s"}, _FORM),
    ({"each": [{"#1": "plain"}], "source": "s"}, _FORM),
    ({"each": [["#1", "plain"]]}, "missing ['source']"),
    ({"each": [["#1", "plain"]], "source": " "}, "['source'] must be text"),
    ({"each": [["#1", "plain"]], "source": "s", "note": "x"},
     "fields ['note'] are not this entry's"),
])
def test_a_cards_numbers_are_a_list_of_number_and_treatment_with_a_hash(corpus, entry, problem):
    """R-BAL277: each card's numbers are decided in order, ``card``, ``pr`` or ``plain``,
    with the hash of the text decided; the text itself is ``_migrate_numbers``' to grade
    them against, so here only their form and the card are."""
    decisions, problems = _graded(corpus, _set(("numbers", "balance:R-A"), entry))
    if problem is None:
        assert problems == [] and decisions.numbers == {"balance:R-A": Numbered(
            tuple(tuple(pair) for pair in entry["each"]), "s")}
    else:
        assert len(problems) == 1 and problems[0].startswith(
            f"numbers: balance:R-A: {problem}"), problems
        assert decisions.numbers == {}


def test_numbers_for_no_card_or_a_card_filed_already_are_refused(corpus, monkeypatch):
    """A key naming no card the migration writes decides nothing; nor one filed already."""
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 1)
    data = complete_input(corpus)
    data["numbers"] = {key: {"each": [["#1", "plain"]], "source": "s"}
                       for key in ("balance:A-9", "salary:S-1")}
    decisions, problems = grade(data, read_source(corpus), corpus)
    assert problems == ["numbers: balance:A-9 names no card the registries move",
                        "numbers: salary:S-1 is plan#1 already, whose text is not rewritten"]
    assert decisions.numbers == {}


def test_load_reads_an_object_and_refuses_anything_else(tmp_path):
    """The file is a JSON object of the seven maps."""
    good, listed, broken = (tmp_path / name for name in ("good.json", "list.json", "bad.json"))
    good.write_text(json.dumps({"names": {}}))
    listed.write_text("[1]")
    broken.write_text("{")
    latin = tmp_path / "latin.json"
    latin.write_bytes('{"names": {"x": "\u00ff"}}'.encode("latin-1"))
    assert load(good) == {"names": {}}
    with pytest.raises(InputError, match="holds list, not an object"):
        load(listed)
    for path in (broken, latin):
        with pytest.raises(InputError, match="is not UTF-8 JSON"):
            load(path)


def test_load_refuses_a_key_named_twice_in_one_object(tmp_path):
    """JSON keeps a key's last value, so a card's numbers decided twice would file the
    second where a reviewer read the first: refused, at any depth (review of S2, L6)."""
    twice, nested = tmp_path / "twice.json", tmp_path / "nested.json"
    twice.write_text('{"names": {}, "names": {}}')
    nested.write_text('{"numbers": {"balance:R-A": {"each": [], "source": "a", '
                      '"source": "b"}}}')
    with pytest.raises(InputError, match=r"names \['names'\] twice in one object"):
        load(twice)
    with pytest.raises(InputError, match=r"names \['source'\] twice in one object"):
        load(nested)


def test_a_questions_hash_covers_its_finding_cell_as_well_as_its_note(corpus):
    """PC-501's note is only a date, so its question is written from its finding cell too:
    an edit of that cell makes the written question, and its card's name, stale (M2 of
    B1's review)."""
    edited = replace(corpus, findings=(*FINDINGS[:3], replace(FINDINGS[3], finding="Changed."),
                                       FINDINGS[4]))
    problems = grade(complete_input(corpus), read_source(edited), edited)[1]
    assert [line.split(":", 2)[:2] for line in problems] == [
        ["names", " balance"], ["questions", " balance"]]


def test_an_entry_naming_a_first_line_two_paragraphs_share_decides_nothing(corpus):
    """H1 of B1's review: the source refuses the pair, and an entry naming them decides
    for neither -- in an entry, and among the preambles."""
    text = BALANCE.replace("A-3's own flush-left spec paragraph.\n",
                           "Note.\nA-3's own text.\n\nNote.\nBookkeeping.\n").replace(
        "Every leaf here obeys one rule.\n", "Every leaf here obeys one rule.\n\n"
        "Every leaf here obeys one rule.\nAnd a second sentence.\n")
    edited = replace(corpus, documents={**corpus.documents, "balance": text})
    data = complete_input(corpus)
    data["notes"][2].update(first_line="Note.")
    decisions, problems = grade(data, read_source(edited), edited)
    assert ("notes[2]: balance:A-3 has 2 paragraphs starting 'Note.', so this names none of "
            "them") in problems
    assert ("preambles[0]: balance has 2 paragraphs outside every entry starting 'Every leaf "
            "here obeys one rule.', so this names none of them") in problems
    assert ("balance", "A-3", "Note.") not in decisions.notes and not decisions.preambles


def test_a_card_filed_already_takes_no_name(corpus, monkeypatch):
    """S-1 mapped (as X-cx is card #1): no name is asked of it, and one given is refused."""
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 1)
    data = complete_input(corpus)
    assert "salary:S-1" not in data["names"]
    assert grade(data, read_source(corpus), corpus)[1] == []
    data["names"]["salary:S-1"] = {"name": "x", "source": "y"}
    assert grade(data, read_source(corpus), corpus)[1] == [
        "names: salary:S-1 is plan#1 already, whose title is not rewritten"]


def test_a_wholly_shipped_outcome_takes_no_milestone(corpus):
    """L7 of B1's review: nothing is required of it, and a milestone given is refused."""
    edited = replace(corpus, outcomes=(*corpus.outcomes, ("O2 done", ("balance:A-1",))))
    data = complete_input(edited)
    assert grade(data, read_source(edited), edited)[1] == []
    data["milestones"]["O2 done"] = {"title": "Done", "description": "d", "source": "s"}
    assert grade(data, read_source(edited), edited)[1] == [
        "milestones: 'O2 done''s whole scope has shipped, so it gets no milestone"]


def test_a_paragraph_given_to_the_card_filed_already_is_refused(corpus, monkeypatch):
    """Its text is not rewritten, so the paragraph would land nowhere (review of B1's
    fixes, MEDIUM-1)."""
    monkeypatch.setitem(_migrate_source.MAPPED, "balance:A-3", 1)
    data = complete_input(corpus)
    assert ("preambles[0]: balance:A-3 is plan#1 already, whose text the migration does not "
            "rewrite, so a paragraph given to it would land nowhere"
            in grade(data, read_source(corpus), corpus)[1])


def test_an_outcome_holding_no_card_for_its_split_steps_leaves_takes_no_milestone(corpus):
    """recurrence R5's shape: its scope's one open step is split, and every leaf carries an
    outcome of its own, so it holds no card; nothing is required of it, and a milestone given
    is refused for that reason, not as a shipped scope."""
    split = (("O1 the first", ("balance:A-3", "balance:A-2a", "balance:A-2b")),
             ("O2 the split", ("balance:A-2",)))
    edited = replace(corpus, outcomes=split)
    data = complete_input(edited)
    assert "O2 the split" not in data["milestones"]
    assert grade(data, read_source(edited), edited)[1] == []
    data["milestones"]["O2 the split"] = {"title": "Split", "description": "d", "source": "s"}
    assert grade(data, read_source(edited), edited)[1] == [
        "milestones: 'O2 the split' holds no card: its open steps are all split steps, whose "
        "leaves are filed in other outcomes' milestones, so it gets no milestone"]
