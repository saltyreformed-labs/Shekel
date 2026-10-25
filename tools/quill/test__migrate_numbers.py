"""X-cx's migration, its bare ``#N`` (R-BAL277): which numbers are decided, each one written
as the input file decides, what is refused rather than written, and GitHub's renderer
grading each card before it is filed (R-BAL355).

The forms GitHub links and does not were measured 2026-10-21 through its Markdown API,
rendering in the plan tracker's context (the X-cx L8 lane's ``plan-tracker-2026-10-21/``
records).  The grade is fed GitHub's own HTML for every text it renders here, captured once
the same way into ``markdown_renders.json`` (card 1 exists on the tracker, as do 2 and 7);
the cards are made up.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.quill._migrate_bodies import Built
from tools.quill._migrate_input import Numbered
from tools.quill._migrate_numbers import (
    APP_PULLS,
    CARD,
    PLAIN,
    PR,
    SWAPPED_TO,
    UNDECIDED,
    _named,
    decided_from,
    grade_rendered,
    link,
    numbers,
)
from tools.quill._migrate_source import MAPPED
from tools.quill.setup_tracker import PLAN

#: Each form GitHub linked, and how many links it made: after a quote, a bracket, a dash,
#: a slash, another ``#``, a backslash and emphasis among them.
LINKED = [
    ("see #2 here", 1), ("'#2' quoted", 1), ("(#2) paren", 1), ("#1-#2 range", 2),
    ("a/#2 slash", 1), ("#2's possessive", 1), ("**#2** bold", 1), ("_#2_ italic", 1),
    ("[ #2 (03/26 bracket", 1), ('"#2 dquote', 1), ("-#2 dash", 1), ("##2 double", 1),
    ("#2.5 decimal", 1), ("*#2* star", 1), ("~#2 tilde", 1), (":#2 colon", 1),
    ("=#2 equals", 1), ("#02 leading-zero", 1), ("\\#2 backslash", 1), ("@#2 at", 1),
    (".#2 dot", 1), ("#2/3 slash-after", 1),
]
#: Each form GitHub did not link that no candidate is taken from.
UNLINKED = ["x#2 word", "`#2` code", "PR#2 glued", "#2a suffix", "1#2 digit",
            "#<!-- -->2 marker", "```\n#2\n```"]


def _card(body: str, comment: str | None = None, title: str = "[R-X] A ruling") -> Built:
    """A card's text as built."""
    return Built(title, body, comment)


def _decided(card: Built, *treatments: str) -> Numbered:
    """``card``'s numbers decided, in reading order, as ``treatments``, for its text now."""
    return Numbered(tuple((number.text, treatment)
                          for number, treatment in zip(numbers(card), treatments)),
                    decided_from(card))


def _linked(card: Built, decision: Numbered | None):
    """:func:`link` of the one card ``card``, keyed ``balance:R-X``, decided ``decision``."""
    return link({"balance:R-X": card}, {} if decision is None else {"balance:R-X": decision})


@pytest.mark.parametrize("text, links", LINKED)
def test_every_number_github_links_is_a_candidate(text, links):
    """What GitHub linked is taken, every one: a number taken by no decision would link the
    tracker's card N unasked (the scanner before this took none after ``/``, ``#``, ``_``)."""
    assert len(numbers(_card(text))) == links


@pytest.mark.parametrize("text", UNLINKED)
def test_a_number_github_does_not_link_after_a_letter_or_in_code_is_no_candidate(text):
    """After a letter or a digit, before a letter, a digit or ``_``, in a code span, in a
    fence, or behind the marker: GitHub links none, and none is taken."""
    assert not numbers(_card(text))


@pytest.mark.parametrize("text", ["a_#2 b", "#2_x c"])
def test_more_than_github_links_may_be_taken_and_is_written(text):
    """``a_#2`` and ``#2_x``, which GitHub did not link, are taken: an ``_`` may be
    emphasis, which GitHub links inside (``_#2_``).  Each is written as decided; the grade
    holds its marker to reading the same
    (:func:`test_a_marker_where_github_links_nothing_reads_the_same`)."""
    card = _card(text)
    assert [number.text for number in numbers(card)] == ["#2"]
    assert _linked(card, _decided(card, PLAIN)).built["balance:R-X"].body == text.replace(
        "#2", "#<!-- -->2")


def test_a_code_span_starts_at_the_start_of_its_backtick_run():
    """#5 after a doubled backtick that no doubled run closes, and #6 after an unmatched
    pair, are links GitHub makes; neither run is opened in its middle (review of B1's
    fixes, LOW-6).  On one line, the doubled runs would pair and hide #5 as code."""
    assert [len(numbers(_card(text))) for text in ("``#5`", "x `` #6 `")] == [1, 1]
    assert [number.text for number in numbers(_card("``#5` and x `` #6 `"))] == ["#6"]


def test_each_treatment_writes_what_was_measured_to_read_as_written():
    """A tracker card stays bare, a pull request becomes a link to the app's, a rank takes
    the invisible marker; nothing else on the card moves."""
    card = _card("Card #8 cites PR #47 at rank #149.\n\nAgain #1-#34.")
    found = _linked(card, _decided(card, CARD, PR, PLAIN, PLAIN, PLAIN))
    assert not found.refusals
    assert found.built["balance:R-X"] == _card(
        f"Card #8 cites PR [#47]({APP_PULLS}47) at rank #<!-- -->149.\n\n"
        "Again #<!-- -->1-#<!-- -->34.")
    assert APP_PULLS == "https://github.com/saltyreformed-labs/Shekel/pull/"
    assert found.counts == {CARD: 1, PR: 1, PLAIN: 3}


def test_a_pull_request_links_its_number_with_no_leading_zero():
    """``PR #02`` reads as written and links pull request 2, whose URL GitHub writes with no
    leading zero (review of S2, L2)."""
    card = _card("PR #02 again.")
    found = _linked(card, _decided(card, PR))
    assert found.built["balance:R-X"].body == f"PR [#02]({APP_PULLS}2) again."


def test_a_number_in_the_as_filed_block_is_none_and_moves_no_decision():
    """The block is fenced, so GitHub links nothing in it: none is taken, and a cell it
    quotes changing leaves the decisions standing."""
    block = "```text\nfiled from: rulings.md at c0ffee\nid: #9\n```"
    card = _card(f"Rank #3.\n\n{block}")
    assert [number.text for number in numbers(card)] == ["#3"]
    moved = _card(f"Rank #3.\n\n{block.replace('#9', '#10')}")
    found = _linked(moved, _decided(card, PLAIN))
    assert not found.refusals
    assert found.built["balance:R-X"].body == f"Rank #<!-- -->3.\n\n{block.replace('#9', '#10')}"


def test_a_comment_is_read_and_written_as_a_body_is():
    """A number in a card's comment outside its fence is a candidate, written there."""
    card = _card("Which?", "As filed in ledger.md, see #4\n\n```text\nid: #5\n```")
    found = _linked(card, _decided(card, PLAIN))
    assert found.built["balance:R-X"].comment == (
        "As filed in ledger.md, see #<!-- -->4\n\n```text\nid: #5\n```")


def test_a_line_is_split_at_newlines_alone_so_the_text_comes_back_whole():
    """A line separator that is not ``\\n`` stays where it was."""
    card = _card("one two #2\r\nthree")
    assert _linked(card, _decided(card, PLAIN)).built["balance:R-X"].body == (
        "one two #<!-- -->2\r\nthree")


def test_a_card_holding_numbers_nobody_decided_is_refused_naming_each_and_its_hash():
    """R-BAL277: the migration refuses to file any ``#N`` nobody decided; the refusal names
    each with its words around it and the hash an entry records, and the card is left as
    built, its numbers counted undecided."""
    card = _card("It lands at #149 of about 180 (card #8).")
    found = _linked(card, None)
    assert found.refusals == (
        "numbers: balance:R-X: its card holds 2 bare #N, none decided: "
        "#149 at 'It lands at #149 of about 180 (card '; "
        "#8 at 'It lands at #149 of about 180 (card #8).'; "
        f"its text's hash is {decided_from(card)}",)
    assert found.built["balance:R-X"] is card
    assert found.counts == {UNDECIDED: 2}


def test_decisions_for_text_that_changed_since_are_refused_until_decided_again():
    """An entry records the hash of the text it decided; the text moved, so the numbers
    may have, and nothing is written."""
    card = _card("Rank #5.")
    found = _linked(card, Numbered((("#5", PLAIN),), "0" * 16))
    assert found.refusals == (
        f"numbers: balance:R-X: its text changed since its numbers were decided (hash "
        f"{'0' * 16}, now {decided_from(card)}): decide and review them again",)
    assert found.built["balance:R-X"] is card and found.counts == {UNDECIDED: 1}


def test_decisions_naming_other_numbers_or_another_order_are_refused():
    """Each decision names its number, so a list out of step with the card is seen."""
    card = _card("PR #47, card #8.")
    swapped = Numbered((("#8", CARD), ("#47", PR)), decided_from(card))
    assert _linked(card, swapped).refusals == (
        "numbers: balance:R-X: decides #8, #47, and its card holds #47, #8, in that order",)


def test_an_entry_for_a_card_holding_no_number_is_refused():
    """A decision that matches nothing is stale, never ignored (R-BAL277's record, the
    coordinator's 2026-10-13 ruling)."""
    found = _linked(_card("No numbers."), Numbered((("#1", PLAIN),), "0" * 16))
    assert found.refusals == (
        "numbers: balance:R-X: decides 1 number(s), and its card holds no bare #N: drop its "
        "entry",)


def test_a_number_in_a_title_is_refused():
    """A title is not Markdown and takes no marker."""
    found = _linked(_card("Nothing.", title="[R-X] About #5"), None)
    assert found.refusals == (
        "numbers: balance:R-X: its title holds #5, which no marker can stop in a title: "
        "reword its name",)


@pytest.mark.parametrize("title", ["[R-X] About GH-5",
                                   "[R-X] See saltyreformed-labs/shekel-plan#5",
                                   "[R-X] About &num;5"])
def test_any_other_form_github_could_link_in_a_title_is_refused(title):
    """No candidate, but a form GitHub could link a card by (R-BAL355's review, L3): a
    title takes no marker, so it is refused as a candidate is."""
    found = _linked(_card("Nothing.", title=title), None)
    assert found.refusals == (
        "numbers: balance:R-X: its title holds a number GitHub could link a card by (GH-N, "
        "owner/name#N, a card's URL, or one written with character references), which no "
        "marker can stop in a title: reword its name",)


@pytest.mark.parametrize("text", ["&#35;2", "&#x23;2", "&num;2", "&#035;2", "&#X23;2", "#&#50;",
                                  "&#35;&#50;", "`&#35;2`", "&amp; and &#65;"])
def test_a_number_written_with_character_references_is_no_candidate(text):
    """GitHub reads each reference as its character and linked card 2 through every one of
    the first seven (measured): no ``#N`` stands for a candidate, and a reference's own
    digits are none, so the grade refuses each
    (:func:`test_every_form_github_links_a_card_in_is_refused`)."""
    assert not numbers(_card(f"see {text} here"))


def test_a_decimal_run_too_long_for_a_reference_is_read_as_written():
    """GitHub reads eight digits as a reference (``&#12345678;`` renders U+FFFD, measured
    2026-10-22, where CommonMark says seven) and nine as written, so nine digits' ``#`` and
    digits are a candidate, after ``&``, where no rewrite was measured.  Changed from
    CommonMark's bound to GitHub's on Josh's approval of 2026-10-22 (rule 5's exception)."""
    assert not numbers(_card("&#12345678;"))
    assert [number.text for number in numbers(_card("&#123456789;"))] == ["#123456789"]


def test_only_the_card_refused_is_left_as_built():
    """One card's refusal writes nothing on it and leaves another's decisions written."""
    good, bad = _card("Rank #5."), _card("Rank #6.")
    found = link({"balance:R-X": good, "balance:R-Y": bad},
                 {"balance:R-X": _decided(good, PLAIN)})
    assert found.built == {"balance:R-X": _card("Rank #<!-- -->5."), "balance:R-Y": bad}
    assert found.counts == {PLAIN: 1, UNDECIDED: 1}
    assert len(found.refusals) == 1 and found.refusals[0].startswith("numbers: balance:R-Y: ")


# -- the grade: GitHub's renderer, before anything is filed (R-BAL355) ----------------

def recording() -> dict:
    """GitHub's answers as captured (the module docstring): ``renders``, each text's HTML,
    and ``unlinked``, each text the control's test can render, rendered as plain Markdown,
    which links no card."""
    return json.loads((Path(__file__).parent / "markdown_renders.json").read_text(
        encoding="utf-8"))


def _github() -> dict[str, str]:
    """GitHub's HTML for each text the grade renders here, as captured."""
    return recording()["renders"]


class _Renderer:
    """GitHub's renderer as captured: each text's HTML, and every text asked, in order; a
    text never captured fails the test (``KeyError``) rather than be answered."""

    def __init__(self, answers: dict[str, str] | None = None):
        """Answer from ``answers`` (GitHub's, as captured, by default)."""
        self.answers = _github() if answers is None else answers
        self.asked: list[str] = []

    def __call__(self, text: str) -> str:
        """``text``'s HTML."""
        self.asked.append(text)
        return self.answers[text]


#: The grade's control as it renders it: card 1's number at each length a card's has.
CONTROL = "#1 #01 #001 #0001"


def _graded(card: Built, *treatments: str, renderer: _Renderer | None = None):
    """:func:`grade_rendered` of the one card ``card``, keyed ``balance:R-X``, its numbers
    decided ``treatments`` (none: no entry) and filed as :func:`link` writes them."""
    decided = {"balance:R-X": _decided(card, *treatments)} if treatments else {}
    filed = link({"balance:R-X": card}, decided)
    assert not filed.refusals
    return grade_rendered({"balance:R-X": card}, filed.built, decided,
                          renderer or _Renderer(), PLAN.full_name)


def _unasked(text: str, before: str, part: str = "body") -> str:
    """The grade's refusal of a link GitHub makes to card ``text`` after ``before``."""
    return (f"numbers: balance:R-X: GitHub links a card of the tracker from its {part} that no "
            f"decision names card: {text!r} after {before!r} (every number named card 1 here, "
            "so each link stands for one GitHub makes once its card is filed): reword its "
            "source, or decide it (a number decided plain still links in a table cell, an "
            "alert or a paragraph holding math)")


#: The opening of the grade's refusal of a part that renders otherwise once written.
DIFFERS = ("numbers: balance:R-X: its body renders differently once its numbers are written: "
           "GitHub's ")


def test_the_card_every_number_names_is_filed_already_and_of_one_digit():
    """A card the tracker holds before the migration files any, so GitHub links it; of one
    digit, so a run of any length names it (:func:`_named`)."""
    assert SWAPPED_TO in MAPPED.values() and 0 < SWAPPED_TO < 10


def test_every_run_of_digits_is_made_to_name_the_card_at_its_length_and_in_its_form():
    """Each digit keeps its form, a reference its leading zeros, a run its length; a
    reference for anything but a digit is left whole, so ``&#65;`` stays A and so does an
    eight-digit one (GitHub's bound, R-BAL355's review M1), and nine digits stay too many
    for a reference; a run at a line's start, which can be a list's number and no card's,
    is left as written."""
    card = _card("#149 #&#50; &#65; &#12345678; &#123456789; 2026-10-21 &#x32; &#0050;\n"
                 "12. item #7\n> 3) quoted #44", "GH-12")
    assert _named(card) == _card("#001 #&#49; &#65; &#12345678; &#000000001; 0001-01-01 &#x31; "
                                 "&#0049;\n12. item #1\n> 3) quoted #01", "GH-01")


def test_a_card_github_reads_as_built_is_unrefused_after_three_renders_and_the_control():
    """Card #8 decided card, PR #47 pr, rank #149 plain: once written they render as built
    (the pull request's link unwrapped), and with every number naming card 1 and card #8
    written as a letter and its digits, GitHub links no card: the control, then before,
    after and the swap."""
    card = _card("Card #8 cites PR #47 at rank #149.")
    renderer = _Renderer()
    assert not _graded(card, CARD, PR, PLAIN, renderer=renderer)
    assert renderer.asked == [
        CONTROL, "Card #8 cites PR #47 at rank #149.",
        f"Card #8 cites PR [#47]({APP_PULLS}47) at rank #<!-- -->149.",
        f"Card N1 cites PR [#01]({APP_PULLS}1) at rank #<!-- -->001."]


def test_a_card_where_github_could_link_no_card_is_never_rendered():
    """No ``#`` and digit, ``GH-`` and digit or issue URL, written or by reference: nothing
    rendered, not even the control."""
    renderer = _Renderer()
    assert not _graded(_card("No number here, GH-x, #x or /issues/x."), renderer=renderer)
    assert not renderer.asked


def test_a_number_github_links_where_the_scanner_saw_code_is_refused():
    """R-BAL355's card A: a code span wrapping a line hides ``#12`` from the scanner, which
    reads the lines apart; GitHub reads ``pytest tools/quill`` as the span and links #12."""
    card = _card("ran `pytest\ntools/quill` on #12 and `check`")
    assert not numbers(card)
    assert _graded(card) == (_unasked("#1", "ran pytest tools/quill on"),)


@pytest.mark.parametrize("text", [
    "> the log read:\n>\n>     stopped at #12",
    "~~~\nrank #2\n~~~",
    "a `b\nc #2` d",
    "    code #2",
    "<!-- see #2 -->\nvisible",
    "[x](#2) here",
    "[see PR #47](https://example.com)",
    'see <a href="https://github.com/saltyreformed-labs/Shekel/pull/5">PR #6 here</a> now',
    "see https://example.com/#2 now",
], ids=["code-in-a-quote", "tilde-fence", "span-across-lines", "indented-code",
        "html-comment", "link-target", "pr-in-link-text", "pr-in-an-html-link-to-a-pull",
        "autolinked-url"])
def test_a_written_number_github_reads_otherwise_is_refused(text):
    """R-BAL355's card B (a code block inside a quote), the review's H2 and R-BAL355's
    review (L1, L6): each number is taken by the scanner, and written (a marker, or a pull
    request's link where the text says PR), reads otherwise than as built: shown in code,
    breaking a comment, moving a link's target, nesting a link in a link's text, splitting
    the author's own link to a pull request (which check 1 keeps whole), or cutting an
    autolinked URL short (which unwrapping every link would hide)."""
    card = _card(text)
    treatment = PR if "PR" in text else PLAIN
    (refused,) = _graded(card, treatment)
    assert refused.startswith(DIFFERS) and refused.endswith(
        ": decide them otherwise, or reword its source")


def test_a_marker_where_github_links_nothing_reads_the_same():
    """After ``_`` (a mark, or a letter), ``/``, in emphasis and in a bracket that opens no
    link (R-PC111's paycheck menu), the marker reads as the number and links nothing."""
    card = _card("a_#2 b, a/#2, _#3_ and [ #2 (03/26 - 04/08) v ]")
    assert not _graded(card, PLAIN, PLAIN, PLAIN, PLAIN)


@pytest.mark.parametrize("text, before", [
    ("see GH-2 here", "see"),
    ("see saltyreformed-labs/shekel-plan#2 here", "see"),
    ("see &#35;2 here", "see"),
    ("see #&#50; here", "see"),
    ("see &num;2 here", "see"),
    ("see GH&#45;2 here", "see"),
    ("see https://github.com/saltyreformed-labs/shekel-plan/issues/7 here", "see"),
    ("see &#x23;2 here", "see"),
    ("see &#035;2 here", "see"),
    ("see &#X23;2 here", "see"),
    ("see &#35;&#50; here", "see"),
    ("see &#x0000023;2 here", "see"),
    ("see &#00000035;2 here", "see"),
    ("see GH&#x000002D;2 here", "see"),
], ids=["gh", "owner-repo", "reference-hash", "reference-digit", "named-reference",
        "reference-hyphen", "issue-url", "hex-reference-hash", "zero-padded-reference-hash",
        "upper-hex-reference-hash", "reference-hash-and-digit", "seven-hex-digit-hash",
        "eight-decimal-digit-hash", "seven-hex-digit-hyphen"])
def test_every_form_github_links_a_card_in_is_refused(text, before):
    """The review's M1 and the references the scanner reads as spaces, to GitHub's bound of
    eight digits (R-BAL355's review, M1): no candidate, so no decision, and GitHub links a
    card through each (here card 1, every number naming it): refused.  A URL to a card too,
    which no decision can name."""
    card = _card(text)
    assert not numbers(card)
    assert _graded(card) == (_unasked("#1" if "GH" not in text else "GH-1", before),)


def test_a_number_after_a_pull_requests_link_is_refused():
    """``#1#2`` with ``#1`` decided pr: the scanner takes no ``#2`` (after a digit), but
    once ``#1`` is a link, GitHub reads ``#2`` after it and links it (the review's H2)."""
    card = _card("see #1#2 here")
    assert [number.text for number in numbers(card)] == ["#1"]
    assert _graded(card, PR) == (_unasked("#1", "see #1"),)


def test_a_number_in_code_written_with_references_is_not_refused():
    """In code GitHub reads a reference as written and links nothing."""
    assert not _graded(_card("see `&#35;2` in code"))


def test_the_comment_is_graded_as_the_body_is():
    """A card's comment is a Markdown text of its own, rendered and refused by its name."""
    assert _graded(_card("No number.", "see GH-3 here")) == (
        _unasked("GH-1", "see", part="comment"),)


def test_a_number_written_with_a_leading_zero_and_decided_plain_is_refused_where_it_links():
    """GitHub shows a linked ``#02`` as ``#2``, so where card 2 exists the text as built reads
    ``#2`` and its marker ``#02``: refused, as the module docstring says, though the marker
    is right; reword it ``#2``."""
    (refused,) = _graded(_card("rank #02 here"), PLAIN)
    assert refused.startswith(DIFFERS)


def test_a_number_at_a_lines_start_is_left_so_a_paragraph_stays_one():
    """``2.`` cannot open a list inside a paragraph, so GitHub reads ``GH-5`` after it as
    the paragraph's text and links it.  Made ``1.``, it would open a list whose indented
    line is code, where nothing links: so a run at a line's start keeps its digits."""
    assert _graded(_card("para\n2.     code GH-5")) == (_unasked("GH-1", "para 2. code"),)


@pytest.mark.parametrize("text", [
    "| step | card |\n|---|---|\n| a | #8 |",
    "> [!NOTE]\n> see card #8 here",
], ids=["table-cell", "alert"])
def test_a_number_decided_card_where_the_marker_links_is_not_refused(text):
    """R-BAL355's review (M2): the plain marker links in a table cell and an alert, so the
    second check writes a number decided ``card`` as a letter and its digits, which links
    nowhere: no refusal names a number its decision names card."""
    assert not _graded(_card(text), CARD)


def test_a_number_decided_plain_in_a_table_cell_is_refused_as_linking():
    """The marker links in a table cell (R-BAL355's review, M2): once the card the number
    names is filed, GitHub links it, so the second check refuses it."""
    card = _card("| step | card |\n|---|---|\n| a | #8 |")
    assert _graded(card, PLAIN) == (_unasked("#1", "step card a"),)


@pytest.mark.parametrize("text, labels", [
    ("See [the note][2] and [the other][1].\n\n[1]: https://example.com\n"
     "[2]: https://github.com/saltyreformed-labs/shekel-plan/issues/7", ["1", "2"]),
    ("See [^1] and [^2].\n\n[^1]: the first\n[^2]: the second, see GH-7", ["^1", "^2"]),
    ("See [a\\]1][x] #5.\n\n[a\\]1]: https://example.com", ["a\\]1"]),
], ids=["link-labels", "footnote-labels", "escaped-bracket"])
def test_a_part_defining_a_label_that_holds_a_digit_is_refused_unrendered(text, labels):
    """R-BAL355's review (M3): the swap names every number card 1, so labels told apart by
    their digits would read as one, and a link through the second would go ungraded: the
    part is refused before any render of it, the control alone asked."""
    renderer = _Renderer()
    assert _graded(_card(text), *([PLAIN] if "#5" in text else []), renderer=renderer) == (
        f"numbers: balance:R-X: its body defines the label(s) {labels}, holding a digit: the "
        "grade names every number card 1, so two labels told apart by their digits would "
        "read as one, and a link through either is not graded: reword each label without "
        "digits",)
    assert renderer.asked == [CONTROL]


def test_a_footnote_reads_as_built_though_github_draws_its_ids_afresh():
    """R-BAL355's review (L2): GitHub ends a footnote's ids with a hash it draws on each
    render, so the part as built and as filed differ there; read as one, they are alike."""
    assert not _graded(_card("rank #12 here[^note].\n\n[^note]: a note"), PLAIN)


def test_a_check_1_refusal_in_a_comment_names_the_comment():
    """A card's comment is graded by the first check as its body is (R-BAL355's review,
    L6): a marker in a tilde fence shows in code."""
    (refused,) = _graded(_card("No number.", "~~~\nrank #2\n~~~"), PLAIN)
    assert refused.startswith("numbers: balance:R-X: its comment renders differently once its "
                              "numbers are written: GitHub's ")


def test_a_control_linking_its_card_at_one_length_only_refuses_the_grade_whole():
    """The control must link its card at every length (R-BAL355's review, L6): GitHub's
    answer for it with three numbers written as markers links card 1 once, so nothing is
    graded, though the card's swapped text, answered as GitHub does, would be refused."""
    renderer = _Renderer({CONTROL: _github()["#1 #<!-- -->01 #<!-- -->001 #<!-- -->0001"],
                          "see GH-1 here": _github()["see GH-1 here"]})
    assert _graded(_card("see GH-2 here"), renderer=renderer) == (
        "numbers: GitHub's renderer, in saltyreformed-labs/shekel-plan's context, linked "
        f"{CONTROL!r} to card(s) [1], where each must link card 1: nothing is graded (is card 1 "
        "on that tracker, and may this token read it?)",)
    assert renderer.asked == [CONTROL]


def test_a_control_that_links_nothing_refuses_the_grade_whole():
    """A renderer that links no card (here GitHub's own plain-Markdown answers, which link no
    issue, for the control and for the card as every number names card 1) would pass every
    card: the control refuses, and nothing else is rendered."""
    renderer = _Renderer(recording()["unlinked"])
    assert _graded(_card("see GH-2 here"), renderer=renderer) == (
        "numbers: GitHub's renderer, in saltyreformed-labs/shekel-plan's context, linked "
        f"{CONTROL!r} to card(s) [], where each must link card 1: nothing is graded (is card 1 "
        "on that tracker, and may this token read it?)",)
    assert renderer.asked == [CONTROL]
