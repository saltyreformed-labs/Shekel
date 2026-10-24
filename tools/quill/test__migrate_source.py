"""X-cx's migration, its source: the registries read as the cards they become.

Every test reads the small plan in :mod:`_migrate_fake` (the ``corpus`` fixture), which
holds one of each shape; a refusal is staged by replacing one part of it.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from tools.plan_gate.registries import LedgerRow, StepRow
from tools.quill import _migrate_source
from tools.quill._migrate_fake import BALANCE, FINDINGS, STEPS, registries
from tools.quill._migrate_source import Links, read_source, source_hash, source_text


def _items(source):
    """The source's items by key."""
    return {item.key: item for item in source.items}


def test_each_registry_row_becomes_the_card_draft_4_names(corpus):
    """Open steps under their nearest container, OPEN blockers only, the arc and marker
    labels, the outcome on its scope step; findings blocked by their FIRST step owner and
    under no card (R-BAL395); a question for each row naming no step and for N-391 too;
    every ruling, closed later and parentless now; the shipped step nowhere."""
    source = read_source(corpus)
    assert (source.refusals, source.reports) == ((), ())
    items = _items(source)
    assert {key: (item.kind, item.links, item.labels, item.outcome)
            for key, item in items.items()} == {
        "balance:A-2": ("step", Links(), ("balance", "deploy-together"), None),
        "balance:A-2a": ("step", Links("balance:A-2", (), (0, 1)), ("balance",), None),
        "balance:A-2b": ("step", Links("balance:A-2", ("balance:A-2a",), (0, 2)),
                         ("balance", "moves-money"), None),
        "balance:A-3": ("step", Links(None, ("balance:A-2a",), (0, 3)), ("balance",),
                        "O1 the first"),
        "salary:S-1": ("step", Links(None, (), (0, 4)), ("salary",), None),
        "balance:F-1": ("finding", Links(blockers=("balance:A-2a",)), ("balance",), None),
        "balance:F-2": ("finding", Links(blockers=("balance:A-3",)), ("balance",), None),
        "balance:Q-1": ("question", Links(place=(1, 2)), ("balance",), None),
        "balance:Q-2": ("question", Links(place=(1, 3)), ("balance",), None),
        "salary:N-391": ("finding", Links(blockers=("salary:S-1",)), ("salary",), None),
        "salary:N-391 question": ("question", Links(place=(1, 4)), ("salary",), None),
        "balance:R-A": ("ruling", Links(), ("balance",), None),
        "salary:R-SAL1": ("ruling", Links(), ("salary",), None),
    }


def test_a_container_carries_no_moves_money_label_its_leaves_do(corpus):
    """A container is never released (its leaves are), so the marker labels no container."""
    marked = replace(STEPS[1], title=STEPS[1].title + " **MOVES MONEY.**")
    source = read_source(replace(corpus, steps=(STEPS[0], marked, *STEPS[2:])))
    assert _items(source)["balance:A-2"].labels == ("balance", "deploy-together")


def test_the_nearest_container_is_the_one_every_other_splits():
    """X-1a is a leaf of X and of X-1, and X-1 is a leaf of X: X-1a hangs under X-1."""
    steps = (StepRow("balance", "X", "--", "Split (decomposed parent).", "container", "--", "--"),
             StepRow("balance", "X-1", "--", "Split again (decomposed parent).", "container",
                     "--", "--"),
             StepRow("balance", "X-1a", "--", "Do it.", "#1", "--", "NOW"))
    text = "## 5. The steps\n\n- [ ] **X** x\n  - [ ] **X-1** x\n    - [ ] **X-1a** x\n"
    source = read_source(registries(steps=steps, findings=(), rulings=(), outcomes=(),
                                    documents={"balance": text, "salary": "## 4. The steps\n"}))
    assert {item.key: item.links.parent for item in source.items} == {
        "balance:X": None, "balance:X-1": "balance:X", "balance:X-1a": "balance:X-1"}


def test_an_entry_paragraph_is_split_where_a_line_steps_back_to_the_checkbox(corpus):
    """The paragraphs the input file must call OWN or NOTE: each that starts at or left of
    its checkbox -- after a blank line, or with none where a line steps back from deeper
    in (A-2a's flush-left note right after its fenced sample, X-bk-2's shape) -- and a
    fenced sample's blank line splits nothing."""
    source = read_source(corpus)
    assert {key: [(paragraph.start + 1, paragraph.first_line) for paragraph in spec.paragraphs]
            for key, spec in source.specs.items() if spec.paragraphs} == {
        "balance:A-2a": [(18, "*A flush-left note about archived steps, with no blank line "
                              "before it.")],
        "balance:A-2b": [(21, "A flush-left note about another arc.")],
        "balance:A-3": [(25, "A-3's own flush-left spec paragraph.")],
    }
    assert [spec.indent for spec in source.specs.values()] == [0, 2, 2, 0, 0]


def test_a_preamble_is_prose_outside_every_entry_and_a_bare_heading_is_none(corpus):
    """The section's prose paragraph is a preamble; its ``### Phase A`` heading and its own
    ``##`` heading are not."""
    assert [(paragraph.start + 1, paragraph.text) for paragraph in read_source(
        corpus).preambles] == [(5, "Every leaf here obeys one rule.")]


def test_a_source_hash_is_sha256s_first_sixteen_hex_digits():
    """The hash every input-file entry records (V3-3)."""
    assert source_hash("") == "e3b0c44298fc1c14"


def _refusals(corpus, **changes):
    """The source's refusals once ``changes`` replace parts of the corpus (``documents``:
    the arcs named, the rest kept)."""
    if "documents" in changes:
        changes["documents"] = {**corpus.documents, **changes["documents"]}
    return read_source(replace(corpus, **changes)).refusals


@pytest.mark.parametrize("change, refusal", [
    ({"steps": (replace(STEPS[2], aliases="balance:A-2b"), *STEPS[3:], *STEPS[:2])},
     "balance:A-2a is one step under 2 names (an identity class); a card has one key"),
    ({"steps": (*STEPS[:4], replace(STEPS[4], blocked="balance:A-9"), STEPS[5])},
     "balance:A-3 is blocked by balance:A-9, which is no step in steps.md"),
    ({"steps": (*STEPS[:4], replace(STEPS[4], blocked="balance:A-3"), STEPS[5])},
     "balance:A-3 is blocked by balance:A-3, which is itself"),
    ({"steps": (*STEPS[:5], replace(STEPS[5], state="--"))},
     "salary:S-1 is open with no rank and is no container, so it has no place on the board"),
    ({"steps": (STEPS[0], replace(STEPS[1], title="Split the work."), *STEPS[2:])},
     "balance:A-2: its order cell and its sentence disagree on whether it is a container "
     "(one says so, the other does not)"),
    ({"outcomes": (("O1 the first", ("balance:A-9",)),)},
     "outcome 'O1 the first' scopes balance:A-9, which is no step in steps.md"),
    ({"outcomes": (("O1 the first", ("balance:A-3",)), ("O2 the next", ("balance:A-3",)))},
     "balance:A-3 is in two outcomes' scopes, 'O1 the first' and 'O2 the next'; a card holds "
     "one milestone"),
    ({"findings": (replace(FINDINGS[0], owner="A-1"), *FINDINGS[1:])},
     "balance:F-1's first owner balance:A-1 does not move (shipped, or no step): re-point the "
     "row before the migration"),
    ({"findings": (replace(FINDINGS[0], owner="A-2a (unclosed"), *FINDINGS[1:])},
     "balance:F-1: its owner cell 'A-2a (unclosed' is not the owner grammar"),
    ({"findings": (*FINDINGS, LedgerRow("balance", "A-3", "--", "Same key.", "--", "OPEN",
                                        "A-2a"))},
     "balance:A-3 is the key of 2 items"),
    ({"findings": (replace(FINDINGS[2], owner="operator"), *FINDINGS[3:])},
     "balance:Q-1: a question card states the developer's question, and its row's operator "
     "or developer-decision owner has no note"),
    ({"documents": {"balance": BALANCE + "- [ ] **A-3** again\n"}},
     "balance: line 28 is a checkbox for A-3 outside the steps section"),
    ({"documents": {"balance": BALANCE.replace(
        "- [ ] **A-3** a plain step.", "- [ ] **A-3** a plain step.\n- [ ] **A-3** twice")}},
     "balance: 2 checkboxes in the steps section name A-3"),
    ({"documents": {"balance": BALANCE.replace("- [ ] **A-3** a plain step.",
                                                "- [ ] **A-4** a step of no row")}},
     "balance: line 23 is a checkbox for A-4, which is no step of balance in steps.md"),
    ({"documents": {"balance": BALANCE.replace("## 5. The steps", "## 5b. Not the heading")}},
     "balance: expected exactly one heading starting '## 5.' in its document; found 0"),
])
def test_what_the_source_refuses_it_names(corpus, change, refusal):
    """Each refusal draft 4 names, staged by replacing one part of the corpus."""
    assert refusal in _refusals(corpus, **change)


def test_a_step_with_no_single_checkbox_is_refused_once_its_arc_is_read(corpus):
    """Two checkboxes for one step leave it no entry, which its row reports too."""
    doubled = BALANCE.replace("- [ ] **A-3** a plain step.",
                              "- [ ] **A-3** a plain step.\n- [ ] **A-3** twice")
    assert ("balance:A-3: its arc document's steps section holds no single checkbox for it"
            in _refusals(corpus, documents={"balance": doubled}))


def test_githubs_sub_issue_caps_are_refusals_at_one_past_the_cap(corpus, monkeypatch):
    """GitHub holds 100 sub-issues a parent and nests eight deep; the corpus's A-2 holds
    two and its leaf A-2a sits two deep, so caps of one refuse them, and caps of exactly
    two refuse nothing.  A finding is blocked by its owner and is no sub-issue of it
    (R-BAL395), so A-2a's finding F-1 sits one deep and is in no step's count: no cap
    refuses it."""
    monkeypatch.setattr(_migrate_source, "SUB_ISSUE_CAP", 1)
    monkeypatch.setattr(_migrate_source, "NESTING_CAP", 1)
    refusals = read_source(corpus).refusals
    assert "balance:A-2 would hold 2 sub-issues; GitHub holds 1" in refusals
    assert "balance:A-2a would sit more than 1 cards deep; GitHub nests 1" in refusals
    assert not [line for line in refusals
                if line.startswith(("balance:A-2 would sit", "balance:A-2a would hold",
                                    "balance:F-1"))]
    monkeypatch.setattr(_migrate_source, "SUB_ISSUE_CAP", 2)
    monkeypatch.setattr(_migrate_source, "NESTING_CAP", 2)
    assert not read_source(corpus).refusals


def test_a_ruled_key_that_no_longer_moves_is_reported_and_one_naming_nothing_refused(
        corpus, monkeypatch):
    """R-BAL240's step once shipped, or R-BAL239's row once closed, is moot and reported;
    a deploy-together key naming no step is a typo, refused."""
    monkeypatch.setattr(_migrate_source, "DEPLOY_TOGETHER", frozenset({"balance:A-1"}))
    monkeypatch.setattr(_migrate_source, "ALSO_A_QUESTION", frozenset({"salary:N-999"}))
    source = read_source(corpus)
    assert source.reports == (
        "R-BAL240's balance:A-1 has shipped, so no card carries 'deploy-together'",
        "R-BAL239's salary:N-999 is not a ledger row with a step owner, so it gets no question "
        "card")
    monkeypatch.setattr(_migrate_source, "DEPLOY_TOGETHER", frozenset({"balance:A-9"}))
    assert ("R-BAL240 labels balance:A-9 deploy-together, which is no step in steps.md"
            in read_source(corpus).refusals)


def test_a_fenced_samples_blank_line_never_starts_a_paragraph(corpus):
    """A flush-left fence holding a blank line, right under a flush-left checkbox: the
    sample's second half starts no paragraph of its own, so the input file has nothing to
    call a note (``arc_steps.fenced_lines``, the reason it is the fence rule's spelling)."""
    text = BALANCE.replace("A-3's own flush-left spec paragraph.\n",
                           "A-3's own flush-left spec paragraph.\n```text\nfirst\n\nsecond\n```\n")
    spec = read_source(replace(corpus, documents={**corpus.documents, "balance": text})).specs[
        "balance:A-3"]
    assert [paragraph.lines for paragraph in spec.paragraphs] == [(
        "A-3's own flush-left spec paragraph.", "```text", "first", "", "second", "```")]


def test_two_paragraphs_one_first_line_names_are_refused(corpus):
    """The input file names a paragraph by its first line (H1 of B1's review): two that
    share one, in one entry or among one section's preambles, are refused, since one
    disposition would land on both."""
    text = BALANCE.replace("A-3's own flush-left spec paragraph.\n",
                           "Note.\nA-3's own text.\n\nNote.\nBookkeeping.\n").replace(
        "Every leaf here obeys one rule.\n", "Every leaf here obeys one rule.\n\n"
        "Every leaf here obeys one rule.\nAnd a second sentence.\n")
    assert _refusals(corpus, documents={"balance": text})[-2:] == (
        "balance:A-3: 2 paragraphs at or left of its checkbox start 'Note.'; the input file "
        "names a paragraph by its first line: reword one",
        "balance: 2 paragraphs outside every entry start 'Every leaf here obeys one rule.'; "
        "the input file names a paragraph by its first line: reword one")


def test_an_open_container_with_no_open_step_under_it_is_refused(corpus):
    """Its leaves shipped, an open A-2 would be a card under no step and on no board, shown
    nowhere (M3 of B1's review)."""
    shipped = tuple(replace(row, state="SHIPPED", commit="`abc1234`")
                    if row.ident in ("A-2a", "A-2b") else row for row in STEPS)
    assert ("balance:A-2 is an open container with no open step under it: it splits nothing, "
            "and a container is no work itself, so no command would ever offer it: ship it, or "
            "file its leaf" in _refusals(corpus, steps=shipped))


def test_outcomes_named_twice_are_refused_and_one_wholly_shipped_is_reported(corpus):
    """A milestone is found by its outcome's name; an outcome with nothing open gets none
    (L7 of B1's review)."""
    assert ("2 outcomes are named 'O1 the first'; a milestone is found by its outcome"
            in _refusals(corpus, outcomes=(*corpus.outcomes,
                                           ("O1 the first", ("salary:S-1",)))))
    done = (*corpus.outcomes, ("O2 done", ("balance:A-1",)))
    source = read_source(replace(corpus, outcomes=done))
    assert source.reports == ("outcome 'O2 done''s whole scope has shipped, so it gets no "
                              "milestone",)


def test_a_card_filed_already_is_an_item_and_one_naming_no_open_step_is_refused(
        corpus, monkeypatch):
    """``MAPPED``: X-cx is card #1 (draft 4 s.1).  Mapped, S-1 still moves as an item (B2
    grades its links); a mapped key naming no open step is refused."""
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 1)
    assert "salary:S-1" in _items(read_source(corpus))
    assert not read_source(corpus).refusals
    monkeypatch.setitem(_migrate_source.MAPPED, "balance:A-1", 2)
    assert read_source(corpus).refusals == (
        "card plan#2 is balance:A-1's, which is no open step",)


def test_a_first_line_two_steps_share_is_no_ambiguity(corpus):
    """The input file names an entry's paragraph by its step AND first line, so two steps'
    paragraphs may share one (review of B1's fixes, H1e)."""
    text = BALANCE.replace("A flush-left note about another arc.",
                           "A-3's own flush-left spec paragraph.")
    assert not _refusals(corpus, documents={"balance": text})


def test_a_sibling_bullet_right_under_a_checkbox_is_a_paragraph_of_its_own(corpus):
    """A list item at the checkbox's level, with no blank line before it, is the
    checkbox's sibling, not its text: shipped X-au-e's shape, measured 2026-10-09."""
    text = BALANCE.replace("- [ ] **A-3** a plain step.\n",
                           "- [ ] **A-3** a plain step.\n* **A-9 is DISSOLVED** into A-3.\n")
    spec = read_source(replace(corpus, documents={**corpus.documents, "balance": text})).specs[
        "balance:A-3"]
    assert [paragraph.first_line for paragraph in spec.paragraphs] == [
        "* **A-9 is DISSOLVED** into A-3.", "A-3's own flush-left spec paragraph."]


def test_a_steps_hash_ignores_the_blank_lines_that_end_its_entry(corpus):
    """A blank line added under a step's entry changes no word of it, so its name is not
    made stale (review of B1, L4)."""
    item = _items(read_source(corpus))["balance:A-3"]
    widened = replace(corpus, documents={**corpus.documents, "balance": BALANCE.replace(
        "A-3's own flush-left spec paragraph.\n", "A-3's own flush-left spec paragraph.\n\n\n")})
    assert source_text(item, read_source(corpus).specs, corpus.documents) == source_text(
        item, read_source(widened).specs, widened.documents)


# -- a split step's outcome (the coordinator's code call, 14:4x 10-21; R-BAL274) -------------

def _outcomes(source):
    """Each step item's outcome, by key."""
    return {item.key: item.outcome for item in source.items if item.kind == "step"}


def test_a_split_steps_outcome_is_carried_to_its_leaves_and_it_keeps_none(corpus):
    """A-2 split into A-2a and A-2b: an outcome scoping A-2 files both leaves in its
    milestone and A-2 in none, so the leaves group under it and its progress counts no
    split step; nothing is reported."""
    split = (("O1 the first", ("balance:A-3",)), ("O2 the split", ("balance:A-2",)))
    source = read_source(replace(corpus, outcomes=split))
    assert (source.refusals, source.reports) == ((), ())
    assert _outcomes(source) == {
        "balance:A-2": None, "balance:A-2a": "O2 the split", "balance:A-2b": "O2 the split",
        "balance:A-3": "O1 the first", "salary:S-1": None}


def test_a_leaf_keeps_an_outcome_of_its_own(corpus):
    """A-2a scoped by its own outcome keeps it; A-2b, scoped by none, takes A-2's."""
    split = (("O1 the first", ("balance:A-3", "balance:A-2a")), ("O2 the split", ("balance:A-2",)))
    source = read_source(replace(corpus, outcomes=split))
    assert (source.refusals, source.reports) == ((), ())
    assert _outcomes(source) == {
        "balance:A-2": None, "balance:A-2a": "O1 the first", "balance:A-2b": "O2 the split",
        "balance:A-3": "O1 the first", "salary:S-1": None}


@pytest.mark.parametrize("outcomes, carried, unheld", [
    ((("O1 the top", ("balance:X",)),), "O1 the top", []),
    ((("O1 the top", ("balance:X",)), ("O2 the middle", ("balance:X-1",))), "O2 the middle",
     ["O1 the top"]),
    ((("O2 the middle", ("balance:X-1",)),), "O2 the middle", []),
])
def test_a_leaf_split_twice_takes_the_nearest_split_steps_outcome(outcomes, carried, unheld):
    """X splits into X-1, which splits into X-1a: neither split step keeps an outcome, and
    X-1a takes the nearest one above it that holds one (bank_import X-f6b and salary X-av-3's
    shape, measured at dev e9ab6e0ab).  With both scoped, X's outcome holds no card, which is
    reported."""
    steps = (StepRow("balance", "X", "--", "Split (decomposed parent).", "container", "--", "--"),
             StepRow("balance", "X-1", "--", "Split again (decomposed parent).", "container",
                     "--", "--"),
             StepRow("balance", "X-1a", "--", "Do it.", "#1", "--", "NOW"))
    text = "## 5. The steps\n\n- [ ] **X** x\n  - [ ] **X-1** x\n    - [ ] **X-1a** x\n"
    source = read_source(registries(steps=steps, findings=(), rulings=(), outcomes=outcomes,
                                    documents={"balance": text, "salary": "## 4. The steps\n"}))
    assert [line for line in (*source.refusals, *source.reports) if "outcome" in line] == [
        f"outcome {name!r} holds no card: its open steps are all split steps, whose leaves are "
        "filed in other outcomes' milestones, so it gets no milestone" for name in unheld]
    assert _outcomes(source) == {"balance:X": None, "balance:X-1": None, "balance:X-1a": carried}


def test_an_outcome_whose_split_steps_leaves_all_carry_their_own_is_reported(corpus):
    """recurrence R5's shape (measured at dev e9ab6e0ab): its one leaf carries an outcome of
    its own, so R5's holds no card and gets no milestone; it is reported, not refused."""
    split = (("O1 the first", ("balance:A-3", "balance:A-2a", "balance:A-2b")),
             ("O2 the split", ("balance:A-2",)))
    source = read_source(replace(corpus, outcomes=split))
    assert not source.refusals
    assert source.reports == ("outcome 'O2 the split' holds no card: its open steps are all "
                              "split steps, whose leaves are filed in other outcomes' "
                              "milestones, so it gets no milestone",)
    assert "O2 the split" not in _outcomes(source).values()
