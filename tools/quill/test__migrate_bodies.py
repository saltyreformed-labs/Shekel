"""X-cx's migration, its cards' text and the census computed from it.

The corpus is :mod:`_migrate_fake`'s, with the input file a session would write.
"""
from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from tools.ci.arcs import STEPS_HEADINGS
from tools.quill import _migrate_source
from tools.quill._migrate_bodies import (
    A_NOTE,
    FILED_ALREADY,
    HEADING,
    ON_A_CARD,
    SHIPPED,
    Built,
    Inputs,
    build,
    census,
    differences,
    filed_commit,
    masked,
    row_text,
)
from tools.quill._migrate_fake import BALANCE, COMMIT, FINDINGS, complete_input
from tools.quill._migrate_input import grade
from tools.quill._migrate_source import read_source, source_hash


def _inputs(corpus, edit=None):
    """The corpus's :class:`Inputs`, its input file changed by ``edit`` first."""
    source = read_source(corpus)
    data = copy.deepcopy(complete_input(corpus, source))
    if edit is not None:
        edit(data)
    return Inputs(source, corpus, grade(data, source, corpus)[0])


def _block(registry, *cells):
    """An As filed block, as the cards carry it."""
    return "\n".join(["```text", f"filed from: {registry} at {COMMIT}", *cells, "```"])


def test_a_steps_card_is_its_sentence_then_its_dedented_spec_with_notes_out(corpus):
    """A-2a's entry sits two spaces in: dedented by two, its box dropped, its fenced sample
    whole (blank line and all), its flush-left note gone; then its As filed block."""
    built, refusals = build(_inputs(corpus))
    assert not refusals
    assert built["balance:A-2a"] == Built("[A-2a] First half", "\n".join([
        "Do the first half.", "", "## Spec", "",
        "**A-2a** the first leaf.",
        "  its continuation",
        "  ```text",
        "  - [ ] **Z-9** a sample, not a step",
        "",
        "  after a blank inside the sample",
        "  ```", "",
        _block("steps.md", "id: A-2a", "also: --", "blocked by: --")]), None)


def test_a_paragraph_given_to_a_card_follows_its_spec_under_its_own_heading(corpus):
    """A-3 keeps its OWN flush-left paragraph, then takes the steps section's preamble the
    input file gives it (R-BAL245's ``card-body``)."""
    body = build(_inputs(corpus))[0]["balance:A-3"].body
    assert body == "\n".join([
        "Do the last part.", "", "## Spec", "",
        "**A-3** a plain step.", "", "A-3's own flush-left spec paragraph.", "",
        "## From the arc document's steps section", "", "Every leaf here obeys one rule.", "",
        _block("steps.md", "id: A-3", "also: --",
               "blocked by: balance:A-2a (its rows) / balance:A-1 (shipped)")])


def test_a_finding_a_ruling_and_a_question_say_what_draft_4_says(corpus):
    """A finding: its cell, then the row's other cells quoted.  A ruling: its cell, then
    id, also and date.  A question: ONLY the question (Q-1's note word for word; Q-2's
    written one), its row in one comment opening "As filed in ledger.md"."""
    built = build(_inputs(corpus))[0]
    assert built["balance:F-1"].body == "The first half reads a stale row.\n\n" + _block(
        "ledger.md", "id: F-1 (A-2a's trace)", "also: --", "worst measured: $0.04",
        "status: OPEN", "owner: A-2a")
    assert built["salary:R-SAL1"] == Built("[R-SAL1] Now", "Question: when? Answer: now.\n\n"
                                           + _block("rulings.md", "id: R-SAL1 (S-1's)",
                                                    "also: R-SAL0", "date: 2026-10-02"), None)
    assert built["balance:Q-1"].body == "is the stub's gross right to the cent?"
    assert built["balance:Q-2"] == Built(
        "[Q-2] Unwritten fork", "Which step makes the settle-day door refuse an early day?",
        "As filed in ledger.md\n\n" + _block(
            "ledger.md", "id: Q-2", "also: --", "finding: A fork nobody wrote down.",
            "worst measured: --", "status: OPEN", "owner: developer-decision (2026-09-06)"))
    assert built["salary:N-391 question"].title == "[N-391 question] Re-read the stub"
    assert built["salary:N-391 question"].body == "re-read one stub: is the gross right?"


def test_a_paragraph_left_of_its_checkbox_kept_as_own_is_refused(corpus):
    """V3-2: notes come out first; a line still left of the checkbox (A-2b's paragraph,
    called its own) cannot be dedented, so it is refused with every such line named."""
    def own(data):
        for entry in data["notes"]:
            entry["disposition"] = "own"
    assert build(_inputs(corpus, own))[1] == [
        "balance:A-2a: lines [18] of its arc document sit left of its checkbox once its notes "
        "are taken out (V3-2): call each one's paragraph a note, or indent it",
        "balance:A-2b: lines [21] of its arc document sit left of its checkbox once its notes "
        "are taken out (V3-2): call each one's paragraph a note, or indent it"]


def test_a_cell_holding_a_fence_and_a_title_quills_check_refuses_are_refused(corpus):
    """A ``` in a cell is refused, as in every part a row writes (``RowText.fenced``); a
    card is held to quill's own check on its title (here: a name that leaves only the
    alias, still a title, passes; one over the cap does not)."""
    fenced = replace(corpus, findings=(replace(FINDINGS[0], status="OPEN ```x```"),
                                       *FINDINGS[1:]))
    assert ("balance:F-1: its row puts ``` in its status cell; no text a row writes may hold "
            "them, as they can open a fenced block on its card that swallows its As filed block"
            in build(_inputs(fenced))[1])

    def too_long(data):
        data["names"]["balance:F-1"]["name"] = "x" * 95
    inputs = _inputs(corpus, too_long)
    inputs = replace(inputs, decisions=replace(inputs.decisions, names={
        **inputs.decisions.names, "balance:F-1": "x" * 95}))
    assert ("balance:F-1: its title is a short name of at most 100 characters; it is 101"
            in build(inputs)[1])


@pytest.mark.parametrize("rows, ident, cells", [
    ("steps", "A-3", {"title": "```sh Do the last part."}),
    ("findings", "F-1", {"finding": "```The first half reads a stale row."}),
    ("rulings", "R-A", {"rule": "```text Question: where? Answer: here."}),
], ids=["a-steps-sentence", "a-findings-cell", "a-rulings-rule"])
def test_a_row_whose_opening_text_holds_a_fence_is_refused(corpus, rows, ident, cells):
    """The text a card opens with, its row's lead, holding a fence, which would open a
    fenced block swallowing the card's As filed block where it renders: refused by the
    one check of every part its row writes (``RowText.fenced``), as a cell is.  The
    coordinator, 2026-10-12: the check once read the cells alone."""
    changed = replace(corpus, **{rows: tuple(
        replace(row, **cells) if row.ident.split()[0] == ident else row
        for row in getattr(corpus, rows))})
    assert (f"balance:{ident}: its row puts ``` in its opening text; no text a row writes may "
            "hold them, as they can open a fenced block on its card that swallows its As filed "
            "block" in build(_inputs(changed))[1])


def test_the_census_puts_every_line_in_one_place_from_the_built_bodies(corpus):
    """balance's 16 non-blank lines: 11 on cards (A-2's opener, A-2a's six, A-2b's opener,
    A-3's two, the preamble given to A-3), two notes, two headings, one shipped pointer;
    salary's one on a card; every other arc its heading alone."""
    inputs = _inputs(corpus)
    places, refusals = census(inputs, build(inputs)[0])
    assert not refusals
    assert dict(places["balance"]) == {ON_A_CARD: 11, A_NOTE: 2, HEADING: 2, SHIPPED: 1}
    assert dict(places["salary"]) == {ON_A_CARD: 1, HEADING: 1}
    assert {arc: dict(counted) for arc, counted in places.items()
            if arc not in ("balance", "salary")} == {
        arc: {HEADING: 1} for arc in ("recurrence", "pay_calendar", "credit_card",
                                      "bank_import")}


@pytest.mark.parametrize("card, lost, refusal", [
    ("balance:A-2a", "  its continuation",
     "census: line 12 of balance's document belongs on balance:A-2a's card, and its built "
     "body does not hold it in its place"),
    ("balance:A-3", "Every leaf here obeys one rule.",
     "census: balance:A-3's card holds 0 steps-section paragraphs given to it; 1 are"),
])
def test_a_line_the_built_body_lost_is_a_refusal(corpus, card, lost, refusal):
    """The census reads the BUILT body, not the spans: a body missing one line of its entry
    (or the paragraph given to it) is refused."""
    inputs = _inputs(corpus)
    built = build(inputs)[0]
    built[card] = replace(built[card], body=built[card].body.replace(lost + "\n", ""))
    assert census(inputs, built)[1] == [refusal]


def test_an_undecided_paragraph_is_counted_as_such(corpus):
    """With no input entry, the preamble is kept for L8 'undecided' and A-2a's note goes
    on its card (and is refused there, being left of the checkbox)."""
    def bare(data):
        data["notes"].clear()
        data["preambles"].clear()
    inputs = _inputs(corpus, bare)
    places = census(inputs, build(inputs)[0])[0]
    assert dict(places["balance"]) == {
        ON_A_CARD: 12, "a steps-section paragraph kept for L8: undecided": 1, HEADING: 2,
        SHIPPED: 1}


def test_the_corpus_is_read_under_the_live_arc_tables_heading():
    """The corpus's balance section opens with the heading ``tools.ci.arcs`` names for
    balance, so moving that heading fails here as it fails the live gate."""
    assert BALANCE.splitlines()[2].startswith(STEPS_HEADINGS["balance"])


def test_a_title_of_exactly_one_hundred_characters_passes_and_one_more_does_not(corpus):
    """R-BAL180's cap, held by quill's own check on the built card: ``[F-1] `` and 94
    characters make 100."""
    for length, refused in ((94, False), (95, True)):
        inputs = _inputs(corpus)
        inputs = replace(inputs, decisions=replace(inputs.decisions, names={
            **inputs.decisions.names, "balance:F-1": "x" * length}))
        assert any("its title is a short name" in line
                   for line in build(inputs)[1]) is refused


@pytest.mark.parametrize("card, old, new, refusal", [
    ("balance:A-2a", "  ```\n\n", "  ```\n*A flush-left note about archived steps.\n\n",
     "census: balance:A-2a's card holds a line no source line puts there: '*A flush-left note "
     "about archived steps.'"),
    ("salary:S-1", "\n\n```text\nfiled", "\n\n## From the arc document's steps section\n\n"
     "Every leaf here obeys one rule.\n\n```text\nfiled",
     "census: salary:S-1's card holds 1 steps-section paragraphs given to it; 0 are"),
    ("balance:A-3", "A-3's own flush-left spec paragraph.",
     "A-3's own flush-left spec paragraph.\nA-3's own flush-left spec paragraph.",
     "census: balance:A-3's card holds a line no source line puts there: \"A-3's own "
     "flush-left spec paragraph.\""),
    ("balance:A-3", "**A-3** a plain step.\n\nA-3's own flush-left spec paragraph.",
     "A-3's own flush-left spec paragraph.\n\n**A-3** a plain step.",
     "census: line 23 of balance's document belongs on balance:A-3's card, and its built "
     "body does not hold it in its place"),
], ids=["a-note-leaked", "a-paragraph-on-the-wrong-card", "a-line-doubled", "two-lines-swapped"])
def test_the_census_reads_each_line_back_nothing_extra_moved_or_doubled(corpus, card, old,
                                                                       new, refusal):
    """H2 of B1's review: a note leaked onto a card, a paragraph given to the wrong card, a
    doubled line and two lines swapped each passed the one-way, find-in-order census;
    each card's non-blank lines read back region by region, each is refused."""
    inputs = _inputs(corpus)
    built = build(inputs)[0]
    assert old in built[card].body
    built[card] = replace(built[card], body=built[card].body.replace(old, new, 1))
    assert census(inputs, built)[1] == [refusal]


def test_a_card_filed_already_gets_no_text_and_its_entry_moves_nowhere(corpus, monkeypatch):
    """S-1 mapped (as X-cx is card #1): no title or body is built over it, and its entry's
    line is counted as filed already, not as on a card."""
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 1)
    inputs = _inputs(corpus)
    built, refusals = build(inputs)
    assert "salary:S-1" not in built and not refusals
    places, refusals = census(inputs, built)
    assert dict(places["salary"]) == {FILED_ALREADY: 1, HEADING: 1} and not refusals


def test_a_card_whose_sentence_or_as_filed_block_was_tampered_with_is_refused(corpus):
    """The census reads back the whole step card: text added before the spec or after the
    As filed block's start is refused too (review of B1's fixes, LOW-1)."""
    inputs = _inputs(corpus)
    blocker = "blocked by: balance:A-2a"
    for old, new in (("Do the last part.", "Do the last part.\n\nEvery leaf here obeys one rule."),
                     (blocker, f"{blocker}\nA leaked note.\n{blocker}")):
        built = build(inputs)[0]
        built["balance:A-3"] = replace(built["balance:A-3"],
                                       body=built["balance:A-3"].body.replace(old, new))
        assert census(inputs, built)[1] == [
            "census: balance:A-3's card holds text before its spec or after its As filed "
            "block's start that its sentence and its cells do not"]


def test_paragraphs_from_two_arcs_given_to_one_card_are_read_in_source_order(corpus):
    """A salary paragraph given to a balance card, beside balance's own: each block is keyed
    by its arc and line, so neither merges into the spec, and the two are read in the
    order the source holds them (review of B1's fixes, LOW-2; H2d: swapped, refused)."""
    salary = "# Salary\n\n## 4. The steps\n\nSalary's rule.\n\n- [ ] **S-1** the salary step.\n"
    edited = replace(corpus, documents={**corpus.documents, "salary": salary})
    source = read_source(edited)
    data = copy.deepcopy(complete_input(edited, source))
    data["preambles"].append({"arc": "salary", "first_line": "Salary's rule.",
                              "disposition": "card-body", "step": "balance:A-3",
                              "source": source_hash("Salary's rule.")})
    decisions, problems = grade(data, source, edited)
    assert not problems and len(decisions.preambles) == 2
    inputs = Inputs(source, edited, decisions)
    built = build(inputs)[0]
    assert not census(inputs, built)[1]
    assert built["balance:A-3"].body.index("Every leaf") < built["balance:A-3"].body.index(
        "Salary's rule.")
    ours, theirs = "Every leaf here obeys one rule.", "Salary's rule."
    swapped = built["balance:A-3"].body.replace(ours, "@").replace(theirs, ours).replace(
        "@", theirs)
    built["balance:A-3"] = replace(built["balance:A-3"], body=swapped)
    assert census(inputs, built)[1] == [
        "census: line 5 of balance's document belongs on balance:A-3's card, and its built body "
        "does not hold it in its place"]


def test_a_line_bound_to_a_card_nothing_builds_is_refused(corpus):
    """Built text missing for a card a line is bound to is refused, never counted as on a
    card (review of B1's fixes, MEDIUM-1)."""
    inputs = _inputs(corpus)
    built = build(inputs)[0]
    del built["balance:A-3"]
    assert ("census: line 5 of balance's document belongs on balance:A-3's card, which "
            "nothing builds") in census(inputs, built)[1]


def test_a_cards_text_is_compared_with_its_commit_left_out_and_nothing_else(corpus):
    """:func:`masked` leaves out only the As filed block's commit (draft 4 s.5), so the same
    text at another commit is no difference, and every other change is one: the title
    exactly, the body and a question's comment (review of B2a, M4), the registry its
    block's first line names included (review 9, LOW-1)."""
    built = build(_inputs(corpus))[0]
    step, question = built["balance:A-3"], built["balance:Q-1"]
    later = step.body.replace(COMMIT, "f" * 40)
    assert later != step.body and masked(later) == masked(step.body)
    assert masked(step.body).count("<commit>") == 1 and COMMIT not in masked(step.body)
    assert not differences(step, step.title, later, None)
    assert differences(step, step.title + " ", step.body.replace("the", "a", 1), None) == [
        "title", "body"]
    assert differences(question, question.title, question.body,
                       question.comment.replace("OPEN", "CLOSED")) == ["comment"]
    assert not differences(question, question.title, question.body, question.comment)
    edited = step.body.replace("blocked by: ", "blocked by: after ", 1)
    assert differences(step, step.title, edited, None) == ["body"]
    assert differences(step, step.title, step.body.replace(
        "filed from: steps.md at", "filed from: ledger.md at"), None) == ["body"]
    assert not differences(question, question.title, question.body, None)
    assert differences(step, step.title, step.body, question.comment) == ["comment"]


def test_only_the_last_as_filed_blocks_commit_is_left_out(corpus):
    """An ordinary body line reading like the block's first, ``filed from: X at Y``, is
    compared whole: a fetched body differing there is a difference, while one differing in
    the block's own commit is not (B2b-2; the pattern once masked every such line)."""
    step = build(_inputs(corpus))[0]["balance:A-3"]
    line = f"filed from: steps.md at {COMMIT}"
    body = step.body.replace("## Spec\n\n", f"## Spec\n\n{line}\n\n", 1)
    quoting = replace(step, body=body)
    assert masked(body).count("<commit>") == 1 and line in masked(body)
    assert differences(quoting, step.title, body.replace(COMMIT, "f" * 40, 1), None) == ["body"]
    head, opened, tail = body.rpartition(line)
    assert not differences(quoting, step.title, f"{head}{opened.replace(COMMIT, 'f' * 40)}{tail}",
                           None)


def test_a_text_built_with_no_block_is_compared_whole(corpus):
    """A question's body holds no As filed block, so a written question quoting a block's
    first line is compared whole: a fetched body differing in that line's last token is a
    difference (review 7, LOW-2)."""
    question = build(_inputs(corpus))[0]["balance:Q-1"]
    quoting = replace(question, body=f"{question.body}\n\n{_block('steps.md', 'id: Z-9')}")
    assert differences(quoting, question.title, quoting.body.replace(COMMIT, "f" * 40),
                       question.comment) == ["body"]


def test_a_line_inside_the_block_that_reads_like_its_first_is_compared_whole(corpus):
    """A cell inside the As filed block whose value reads ``filed from: X at Y``: only the
    block's FIRST line has its commit left out, so a fetched body differing in that cell is
    a difference (review 7, LOW-2: masking from the last ``filed from: `` passed it)."""
    finding = build(_inputs(corpus))[0]["balance:F-1"]
    cell = "status: filed from: a at b"
    body = f"{finding.body.removesuffix(chr(10) + '```')}\n{cell}\n```"
    assert body.count(cell) == 1 and body.endswith(f"{cell}\n```")
    assert differences(replace(finding, body=body), finding.title,
                       body.replace(cell, "status: filed from: a at c"), None) == ["body"]


def test_the_commit_read_is_the_last_as_filed_blocks():
    """:func:`filed_commit` reads the commit of the LAST block opening as one does, the one
    a card's text ends with, in a text read as :func:`masked` reads it (a web form's CRLF
    as LF); a text holding none, or whose block's first line names no commit, gives
    None."""
    block = _block("steps.md", "id: A-3")
    earlier = block.replace(COMMIT, "f" * 40)
    assert filed_commit(f"text\n\n{earlier}\n\nmore\n\n{block}") == COMMIT
    assert filed_commit(f"As filed in ledger.md\n\n{block}\r\n") == COMMIT
    assert filed_commit(f"As filed in ledger.md\n\n{block}".replace("\n", "\r\n")) == COMMIT
    assert filed_commit("a question, and no block") is None
    assert filed_commit(block.replace(f" at {COMMIT}", " at")) is None
    assert filed_commit(block.replace(f" at {COMMIT}", f" at {COMMIT} and more")) is None


def test_each_card_is_composed_from_what_its_row_writes(corpus):
    """What each item's row writes (:func:`row_text`) is where :func:`build` puts it: a
    step's sentence opens its body and its block ends it; a finding's or a ruling's body is
    its lead and its block and nothing else; a question's As filed comment is its block
    under the opening line, and its body its lead (its row's note) where the input file
    writes no question, Q-1, and the written one where it does, Q-2 (review 9,
    MEDIUM-1: one producer, so no second spelling of a row's text)."""
    inputs = _inputs(corpus)
    built = build(inputs)[0]
    for item in inputs.source.items:
        made, card = row_text(item), built[item.key]
        block = made.block(COMMIT)
        if item.kind == "step":
            assert card.body.startswith(f"{made.lead}\n\n## Spec\n\n")
            assert card.body.endswith(f"\n\n{block}") and card.comment is None
        elif item.kind == "question":
            assert card.comment == f"As filed in ledger.md\n\n{block}"
        else:
            assert (card.body, card.comment) == (f"{made.lead}\n\n{block}", None)
    leads = {item.key: row_text(item).lead for item in inputs.source.items}
    assert built["balance:Q-1"].body == leads["balance:Q-1"] == (
        "is the stub's gross right to the cent?")
    assert built["balance:Q-2"].body not in (leads["balance:Q-2"], "")
    assert [leads[key] for key in ("balance:A-3", "balance:F-1", "balance:R-A")] == [
        "Do the last part.", "The first half reads a stale row.",
        "Question: where? Answer: here."]
