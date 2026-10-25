"""X-cx's migration, its NUMBERS: each bare ``#N`` a card's text holds, decided one by one
(ruling ``balance:R-BAL277``), and graded by GitHub's own renderer before anything is filed
(ruling ``balance:R-BAL355``).

GitHub links every bare ``#N`` in a card's Markdown to the tracker's card N, and leaves a
"mentioned this" line on that card.  Some numbers the cards carry mean a tracker card
(R-BAL244's "card #8"), some an app pull request (N-87's "PR #47"), and most a rank, an
installment or an id (R-BAL133's "#149 of about 180").  The input file decides each one
(:attr:`_migrate_input.Decisions.numbers`), and :func:`link`, the ONE transform, writes
each decision into its card's text, which every phase then files and grades
(``migrate.prepare``: built, its census taken, then linked here):

- ``card``: left as written, a link to the tracker's card N;
- ``pr``: ``[#N](https://github.com/<organization>/Shekel/pull/N)``, a link to the app's
  pull request N (:data:`APP_PULLS`), reading ``#N``;
- ``plain``: ``#<!-- -->N``, which GitHub stores as sent and renders as ``#N``, linking
  nothing and leaving no "mentioned this" line, in a paragraph, a list or a quote (the
  rehearsal's probe M6-3, 2026-10-12; card N's timeline, 2026-10-22).  In a table cell, an
  alert and a paragraph holding inline math it links (measured 2026-10-22), and the grade
  refuses it there.

**What is decided** is each :data:`CANDIDATE` :func:`numbers` takes: a ``#N`` with no
letter or digit beside it, outside the code spans and fences it reads on each line, a
character reference read as spaces.  It reads Markdown a line at a time, where GitHub reads
it by block, so it can take a number GitHub does not link (in a code span that wraps a
line, say) and miss one GitHub does (after a code span that wraps a line).  So the scanner
only lists what the input file decides; the grade below refuses a card GitHub would read
otherwise.  A card's decisions are written for its text as built (:func:`decided_from`,
hashed), so a card whose text changed since has them refused until they are decided and
reviewed again, as a stale name is.  A card's TITLE holding a candidate, or any other form
GitHub could link a card by (:data:`_LINKABLE`), is refused: a title is not Markdown and
takes no marker, so the name is reworded.

**The grade** (:func:`grade_rendered`): before ``check --target`` plans, or ``history`` or ``open``
writes, anything, each part of a card (its body, its comment) where GitHub could link a
card is rendered by GitHub's Markdown API in the tracker's context, which stores nothing
(``_tracker.Tracker.render``).  Such a part holds ``#`` and a digit, ``GH-`` and a digit
(any case), or ``/issues/`` or ``/pull/`` and a digit, as written or with its character
references read (:data:`_LINKABLE`): ``#N``, ``GH-N``, ``owner/repo#N`` and an issue's URL
each linked the tracker's card 1 (measured 2026-10-21 through the API, the tracker as
context; ``GH-N`` and ``owner/repo#N`` are no candidate).  Three checks, each refusal naming
the card:

1. **It reads as built.**  The part as filed must render as the part as built, once each
   link to a card of the tracker, and each link a ``pr`` decision writes, is unwrapped to
   its text, and what GitHub draws afresh on each render (a footnote's ids, inline math's
   and a diagram's, measured 2026-10-22) is read as one; the first difference is quoted.
   A marker in code (a code block inside a quote, a tilde fence, a code span across lines),
   a ``pr`` link inside a link's text (the author's own link to a pull request among them),
   and a rewrite inside an HTML comment or a link's target are refused this way.  GitHub
   shows a linked number as it reads it (``#001`` as ``#1``, measured 2026-10-21), so a
   number written with leading zeros and decided ``plain`` or ``pr`` is refused wherever
   its card exists: by the first phase that finds that card filed, which is ``open`` for a
   card ``history`` files, before ``open`` writes (no card holds one, measured 2026-10-22).
2. **No number links a card unasked.**  GitHub links a number only to a card that EXISTS, and
   most cards the numbers name are not filed yet, so the part is rendered with every number
   made to name :data:`SWAPPED_TO`, a card filed already (:func:`_named`), and each number
   decided ``card`` written as a letter and its digits (:data:`_UNLINKED`), which links in
   no container: it must hold no link to a card of the tracker.  GitHub links one only where
   a number no decision names ``card`` stands, and each is refused, quoting it and the text
   before it.  ``GH-N``, ``owner/repo#N`` naming the tracker, a number written with character
   references, one the scanner took for code, and ``#2`` after a ``pr`` link in ``#1#2`` are
   refused this way, and so is a URL to a tracker card, which no decision can name (write
   ``#N`` and decide it ``card``).
3. **The renderer links.**  Once a grade, before any part, :data:`SWAPPED_TO`'s number
   written at each length a card's number has (:data:`_CARD_DIGITS`) must link that card,
   or the grade is refused whole and nothing is graded: a token or a context that linked
   nothing would pass every card.

The second check's text is written by :func:`_written`, the transform :func:`link` files by,
on the part with each run of digits made to name the card at its own length and with each
digit in its own form, as itself or as a character reference (GitHub reads a number's
leading zeros as nothing: ``#000000000000000000001``, 21 digits, linked card 1, measured
2026-10-21).  A run at a line's start, after spaces and ``>``, is left as written, as no
card's number can start a line and an ordered list's number there decides whether the list
interrupts a paragraph (only one starting at 1 does).  A digit is otherwise no Markdown mark
but in a label, where two labels told apart by their digits alone (``[^1]``, ``[^2]``) would
read as one: so a part defining a link or footnote label that holds a digit or a character
reference is refused before it is rendered (no card holds one, measured 2026-10-22).

**What it does not grade**: whether a decision is RIGHT (``card #149`` linking whatever card
ends up numbered 149, say); the input file's reviewer grades that, as it grades a name.  A
link the author wrote relative to the card's own page (``[x](./1)``) is read by the
browser, not by GitHub's renderer, and nothing here reads it.  A reference to another
repository's issue or pull request (``owner/name#N``, or its URL) is GitHub's link to that
repository, which no decision is for: the grade reads only links to the tracker's cards
(none stands on a card, measured 2026-10-22).
"""
from __future__ import annotations

import html
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from tools.ci.arc_steps import fenced_lines
from tools.quill._migrate_bodies import Built
from tools.quill._migrate_input import NUMBER, TREATMENTS, Numbered
from tools.quill._migrate_source import MAPPED, source_hash
from tools.quill.setup_tracker import ORG

#: The treatments, by the names the input file writes them in (one home: its
#: :data:`_migrate_input.TREATMENTS`, which this unpacking holds to exactly three).
CARD, PR, PLAIN = TREATMENTS
#: A ``#N`` the input file decides (the module docstring): a :data:`_migrate_input.NUMBER`,
#: its number group 1, with no letter or digit beside it.  An ``_`` beside it is no letter
#: here, as Markdown may take it as a mark.
CANDIDATE = re.compile(rf"(?<![A-Za-z0-9]){NUMBER.pattern}(?![A-Za-z0-9])")
#: The most digits GitHub reads in a decimal or a hex character reference, where CommonMark
#: says seven and six: ``#&#00000055;`` and ``#&#x00000037;`` linked card 7, and with one
#: more zero each linked nothing (measured 2026-10-22).  The one bound :func:`numbers` and
#: the grade's swap read references by.
_REFERENCE_DIGITS = 8
#: A character reference, decimal, hex or named, within :data:`_REFERENCE_DIGITS`, which
#: GitHub reads as the character it stands for.
_REFERENCE = re.compile(rf"&(?:#\d{{1,{_REFERENCE_DIGITS}}}"
                        rf"|#[xX][0-9A-Fa-f]{{1,{_REFERENCE_DIGITS}}}|[A-Za-z][A-Za-z0-9]*);")
#: An inline code span: a run of backticks, then text, then a run of as many.
_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`).*?(?<!`)\1(?!`)")
#: Where a ``pr`` number links: the app's pull requests, in the organization it moved into
#: at L5 (PR #583), by its own name, so no link leans on GitHub's redirect from the old one.
APP_PULLS = f"https://github.com/{ORG}/Shekel/pull/"
#: The grade's own writing of a number decided ``card``, in its second check: a letter for
#: its ``#``, which links in no container, where the ``plain`` marker links in a table
#: cell, an alert and a paragraph holding inline math (measured 2026-10-22).
_UNLINKED = "unlinked"
#: What each treatment writes in place of ``#N``, given N's digits, and what the grade
#: writes for a number decided ``card`` (:data:`_UNLINKED`).
_WRITES = {
    CARD: lambda digits: f"#{digits}",
    PR: lambda digits: f"[#{digits}]({APP_PULLS}{int(digits)})",
    PLAIN: lambda digits: f"#<!-- -->{digits}",
    _UNLINKED: lambda digits: f"N{digits}",
}
#: The parts of a card's text a candidate may sit in and be rewritten.
_PARTS = ("body", "comment")
#: The count a report gives the candidates of a card refused, decided or not.
UNDECIDED = "undecided"
#: Text where GitHub could link a card (the module docstring): read as written, and again
#: with its character references read.
_LINKABLE = re.compile(r"#\d|gh-\d|/(?:issues|pull)/\d", re.IGNORECASE)
#: One digit as GitHub reads it: itself, or a decimal or hex character reference standing
#: for it, its leading zeros within :data:`_REFERENCE`'s bound.
_DIGIT = re.compile(rf"[0-9]|&#(?=\d{{1,{_REFERENCE_DIGITS}}};)0*(?:4[89]|5[0-7]);"
                    rf"|&#[xX](?=[0-9A-Fa-f]{{1,{_REFERENCE_DIGITS}}};)0*3[0-9];")
#: A run of digits (:data:`_DIGIT`), or a character reference standing for anything else,
#: matched first so that its own digits are no run.
_RUN = re.compile(rf"(?P<run>(?:{_DIGIT.pattern})+)|{_REFERENCE.pattern}")
#: What may stand before a run at a line's start: indentation and a quote's marks.
_LINE_START = " \t>"
#: The card every number names in the grade's second check: one filed before the migration
#: (:data:`_migrate_source.MAPPED`), so GitHub links it while the cards are not yet filed.
#: Of one digit, so a run of any length can name it (a test holds it to one).
SWAPPED_TO = min(MAPPED.values())
#: The lengths the grade's control writes its card's number at, 1 to this: a card number's
#: own lengths, as about 2,000 cards stand once the migration and the history cards after it
#: are filed.  A longer run the swap writes is that number with more leading zeros, which
#: GitHub links as it links these (to 21 digits, measured 2026-10-21).
_CARD_DIGITS = 4
#: A link in GitHub's HTML: its attributes and its text.
_ANCHOR = re.compile(r"<a\b(?P<attributes>[^>]*)>(?P<text>.*?)</a>", re.DOTALL)
#: The attribute of a link to one of the app's pull requests, its number group 1.
_APP_PULL = re.compile(rf'\bhref="{re.escape(APP_PULLS)}(\d+)"')
#: The text of the link a ``pr`` decision writes: ``#`` and the number, group 1.
_PR_TEXT = re.compile(r"#(\d+)")
#: What GitHub draws afresh on each render of one text (measured 2026-10-22): inline
#: math's run id and a diagram's identity (group 1, the attribute), and the hash ending a
#: footnote's ids (group 2, the id before it).
_FRESH = re.compile(r'\b(data-run-id|data-identity)="[^"]*"'
                    r'|(user-content-fn(?:ref)?-[^"]*?)-[0-9a-f]{32}(?=")')
#: A link or footnote label's definition, its label group 1: a label may hold an escaped
#: bracket, and is read wherever it stands, so none is missed.
_DEFINITION = re.compile(r"\[((?:\\.|[^\\\]])*)\]:")
#: What in a label the swap may rewrite: a digit, or a character reference.
_SWAPPABLE = re.compile(r"\d|&#")
#: An HTML tag, which a quote of rendered text leaves out.
_TAG = re.compile(r"<[^>]*>")
#: How much of the text around a difference or before a link a refusal quotes.
_QUOTED = 40


@dataclass(frozen=True)
class Number:
    """One candidate in a card's text: the part it sits in, its line's index there, its
    span on that line, its text (``#47``) and the character before it ("" at a line's
    start)."""

    part: str
    line: int
    start: int
    stop: int
    text: str
    before: str


@dataclass(frozen=True)
class Linked:
    """Every card's text with its numbers written as decided (a refused card's as built),
    how many candidates each treatment took (:data:`UNDECIDED` for a refused card's), and
    what is refused."""

    built: Mapping[str, Built]
    counts: Counter
    refusals: tuple[str, ...]


def _lines(text: str) -> list[str]:
    """``text``'s lines, split at ``\\n`` alone, so joining them with it gives ``text`` back
    whatever else it holds (``str.splitlines`` splits at more)."""
    return text.split("\n")


def _blanked(found: re.Match) -> str:
    """What ``found`` is read as where it is no candidate's: spaces, as many."""
    return " " * len(found.group(0))


def _readable(line: str) -> str:
    """``line`` with each code span blanked, the same length: where :func:`numbers` reads
    for candidates on it, at the same positions."""
    return _CODE_SPAN.sub(_blanked, line)


def _unfenced(text: str) -> list[tuple[int, str]]:
    """Each line of ``text`` outside every fenced block, with its index."""
    lines = _lines(text)
    return [(index, line) for index, (line, fenced) in enumerate(zip(lines, fenced_lines(lines)))
            if not fenced]


def numbers(built: Built) -> list[Number]:
    """Every candidate in ``built``'s body and comment, in reading order: what its decisions
    are listed in.  A character reference is read as spaces, as many, never as its own
    digits nor as the character it stands for: in ``&#65;#2`` the ``#2`` is taken, after
    ``;``, where GitHub reads ``A#2`` (a number written with references is the grade's,
    :func:`grade_rendered`)."""
    found = []
    for part in _PARTS:
        for index, line in _unfenced(getattr(built, part) or ""):
            readable = _REFERENCE.sub(_blanked, _readable(line))
            found += [Number(part, index, match.start(), match.end(), match.group(0),
                             line[match.start() - 1] if match.start() else "")
                      for match in CANDIDATE.finditer(readable)]
    return found


def decided_from(built: Built) -> str:
    """The hash a card's decisions record (:func:`_migrate_source.source_hash`): of its body's
    and its comment's lines outside fenced blocks, where its candidates sit.  Its As filed
    block, fenced, is left out, so a cell or a commit it quotes moves no decision."""
    return source_hash("\n".join(line for part in _PARTS
                                 for _, line in _unfenced(getattr(built, part) or "")))


def link(built: Mapping[str, Built], decided: Mapping[str, Numbered]) -> Linked:
    """Every card's text with its candidates written as ``decided`` (the module docstring),
    and what is refused: a refused card is left as built, its candidates counted
    :data:`UNDECIDED`.

    Args:
        built: Each card's text as :func:`_migrate_bodies.build` makes it, by key.
        decided: Each card's decisions as the input file states them well
            (:func:`_migrate_input.grade`), by key.

    Returns:
        The :class:`Linked`.
    """
    linked, counts, refusals = {}, Counter(), []
    for key, card in built.items():
        found = numbers(card)
        problems = _problems(card, found, decided.get(key))
        refusals += [f"numbers: {key}: {problem}" for problem in problems]
        if problems:
            linked[key] = card
            counts[UNDECIDED] += len(found)
            continue
        if not found:
            linked[key] = card
            continue
        treatments = [treatment for _, treatment in decided[key].each]
        linked[key] = _written(card, found, treatments)
        counts.update(treatments)
    return Linked(linked, counts, tuple(refusals))


def _problems(card: Built, found: Sequence[Number], decision: Numbered | None) -> list[str]:
    """Why ``card``'s candidates ``found`` cannot be written as ``decision`` (None: the input
    file decides none of them): its title's candidates, or any other form GitHub could
    link a card in (:func:`_linkable`), first, then the decision against the text."""
    problems = [f"its title holds {match.group(0)}, which no marker can stop in a title: "
                "reword its name" for match in CANDIDATE.finditer(card.title)]
    if not problems and _linkable(card.title):
        problems.append("its title holds a number GitHub could link a card by (GH-N, "
                        "owner/name#N, a card's URL, or one written with character "
                        "references), which no marker can stop in a title: reword its name")
    if decision is None:
        return problems + ([
            f"its card holds {len(found)} bare #N, none decided: "
            + "; ".join(f"{number.text} at {_shown(card, number)!r}" for number in found)
            + f"; its text's hash is {decided_from(card)}"] if found else [])
    return problems + _against(card, found, decision)


def _against(card: Built, found: Sequence[Number], decision: Numbered) -> list[str]:
    """Why ``decision`` does not decide ``card``'s candidates ``found``: its hash is not the
    card's text's, or it decides other numbers."""
    now = decided_from(card)
    if not found:
        return [f"decides {len(decision.each)} number(s), and its card holds no bare #N: "
                "drop its entry"]
    if decision.source != now:
        return [f"its text changed since its numbers were decided (hash {decision.source}, "
                f"now {now}): decide and review them again"]
    given = [text for text, _ in decision.each]
    if given != [number.text for number in found]:
        return [f"decides {', '.join(given)}, and its card holds "
                f"{', '.join(number.text for number in found)}, in that order"]
    return []


def _shown(card: Built, number: Number) -> str:
    """``number`` with the text around it on its line, as a refusal quotes it."""
    line = _lines(getattr(card, number.part))[number.line]
    return line[max(0, number.start - 40):number.stop + 20]


def _written(card: Built, found: Sequence[Number], treatments: Sequence[str]) -> Built:
    """``card`` with each candidate ``found`` written as its treatment, part by part and
    line by line, each line's last first so the spans before it hold."""
    texts = {part: _lines(getattr(card, part) or "") for part in _PARTS}
    for number, treatment in sorted(zip(found, treatments, strict=True), key=lambda pair: (
            pair[0].part, pair[0].line, -pair[0].start)):
        lines = texts[number.part]
        line = lines[number.line]
        lines[number.line] = (line[:number.start] + _WRITES[treatment](number.text[1:])
                              + line[number.stop:])
    return Built(card.title, "\n".join(texts["body"]),
                 None if card.comment is None else "\n".join(texts["comment"]))


def grade_rendered(built: Mapping[str, Built], filed: Mapping[str, Built],
                   decided: Mapping[str, Numbered], render: Callable[[str], str],
                   tracker: str) -> tuple[str, ...]:
    """What GitHub's renderer refuses of the cards' numbers (the module docstring's three
    checks), each part where GitHub could link a card rendered by ``render``.

    Args:
        built: Each card's text as built, by key, before its numbers are written.
        filed: Each card's text as :func:`link` wrote it, by key: what is filed.
        decided: Each card's decisions, by key, as :func:`link` took them with no refusal:
            the grade runs only then, so every candidate a card holds is decided.
        render: GitHub's HTML for a part's Markdown, in the tracker's context
            (``_tracker.Tracker.render``).
        tracker: The tracker's ``owner/name``, whose cards a link names.

    Returns:
        Each refusal: none when every part renders as built, no number links a card
        unasked and no label holds a digit; the control's alone when it fails.
    """
    graded = {key: parts for key, card in built.items()
              if (parts := [part for part in _PARTS if _linkable(getattr(card, part))])}
    if not graded:
        return ()
    if failed := _control(render, tracker):
        return (failed,)
    refusals = []
    for key, parts in graded.items():
        treatments = [treatment for _, treatment in decided[key].each] if key in decided else []
        named = _named(built[key])
        unasked = _written(named, numbers(named), [_UNLINKED if treatment == CARD else treatment
                                                   for treatment in treatments])
        for part in parts:
            if labels := _swappable_labels(getattr(built[key], part)):
                refusals.append(
                    f"numbers: {key}: its {part} defines the label(s) {labels}, holding a digit: "
                    f"the grade names every number card {SWAPPED_TO}, so two labels told apart "
                    "by their digits would read as one, and a link through either is not "
                    "graded: reword each label without digits")
                continue
            if getattr(built[key], part) != getattr(filed[key], part):
                refusals += _differs(key, part, render(getattr(built[key], part)),
                                     render(getattr(filed[key], part)), tracker)
            refusals += [f"numbers: {key}: GitHub links a card of the tracker from its {part} "
                         f"that no decision names card: {text!r} after {before!r} (every "
                         f"number named card {SWAPPED_TO} here, so each link stands for one "
                         "GitHub makes once its card is filed): reword its source, or decide it "
                         "(a number decided plain still links in a table cell, an alert or a "
                         "paragraph holding math)"
                         for _, text, before in _links(render(getattr(unasked, part)), tracker)]
    return tuple(refusals)


def _swappable_labels(text: str) -> list[str]:
    """Each link or footnote label ``text`` defines that holds what the swap rewrites
    (:data:`_SWAPPABLE`), so that two of them told apart by it alone would read as one."""
    return [found.group(1) for found in _DEFINITION.finditer(text)
            if _SWAPPABLE.search(found.group(1))]


def _linkable(text: str | None) -> bool:
    """Whether GitHub could link a card in ``text`` (:data:`_LINKABLE`)."""
    return text is not None and bool(_LINKABLE.search(text)
                                     or _LINKABLE.search(html.unescape(text)))


def _named(card: Built) -> Built:
    """``card``'s body and comment with each run of digits (:data:`_RUN`) made to name
    :data:`SWAPPED_TO`, at its own length and with each digit in its own form (``#149`` as
    ``#001``, ``#&#50;`` as ``#&#49;``), but a run at a line's start; a character reference
    standing for anything but a digit is left whole, and the title as built."""
    def swapped(text: str) -> str:
        """``text``, its runs named (:func:`_named`)."""
        def run(found: re.Match) -> str:
            """``found``, its run of digits named, if it is one off a line's start."""
            line = text[text.rfind("\n", 0, found.start()) + 1:found.start()]
            if found.group("run") is None or not line.strip(_LINE_START):
                return found.group(0)
            units = _DIGIT.findall(found.group("run"))
            return "".join(_as(unit, digit) for unit, digit
                           in zip(units, str(SWAPPED_TO).zfill(len(units)), strict=True))
        return _RUN.sub(run, text)
    return Built(card.title, swapped(card.body),
                 None if card.comment is None else swapped(card.comment))


def _as(unit: str, digit: str) -> str:
    """``digit`` written in ``unit``'s form (:data:`_DIGIT`): itself, or a decimal or hex
    character reference with the same leading zeros."""
    if not unit.startswith("&"):
        return digit
    if unit[2] in "xX":
        return f"{unit[:-2]}{digit};"
    return f"{unit[:-3]}{ord(digit)};"


def _control(render: Callable[[str], str], tracker: str) -> str | None:
    """Why the grade cannot trust ``render`` (the module docstring's third check), or None:
    :data:`SWAPPED_TO`'s number, written at each length to :data:`_CARD_DIGITS`, must link
    that card, and only it."""
    written = " ".join(f"#{str(SWAPPED_TO).zfill(length)}"
                       for length in range(1, _CARD_DIGITS + 1))
    linked = [number for number, _, _ in _links(render(written), tracker)]
    if linked == [SWAPPED_TO] * _CARD_DIGITS:
        return None
    return (f"numbers: GitHub's renderer, in {tracker}'s context, linked {written!r} to card(s) "
            f"{linked}, where each must link card {SWAPPED_TO}: nothing is graded (is card "
            f"{SWAPPED_TO} on that tracker, and may this token read it?)")


def _names(tracker: str) -> re.Pattern:
    """A link's attribute naming a card of ``tracker``, an issue or a pull request, its
    number group 1."""
    return re.compile(rf"/{re.escape(tracker)}/(?:issues|pull)/(\d+)", re.IGNORECASE)


def _links(rendered: str, tracker: str) -> list[tuple[int, str, str]]:
    """Each link in ``rendered`` to a card of ``tracker``, by any of its attributes: the
    number it links, its text, and the text before it, each as a reader sees it."""
    names = _names(tracker)
    return [(int(named.group(1)), _seen(anchor.group("text")),
             _seen(rendered[:anchor.start()])[-_QUOTED:])
            for anchor in _ANCHOR.finditer(rendered)
            if (named := names.search(anchor.group("attributes")))]


def _seen(rendered: str) -> str:
    """``rendered`` as a reader sees it: its tags left out, its references read, its
    whitespace one space."""
    return " ".join(html.unescape(_TAG.sub("", rendered)).split())


def _unwrapped(rendered: str, tracker: str) -> str:
    """``rendered`` with each link to a card of ``tracker``, and each link a ``pr`` decision
    writes (``#N`` to the app's pull request N, :data:`_APP_PULL`), unwrapped to its text:
    what a decision may change.  A link the author wrote to one of the app's pull requests
    stays whole, so a ``pr`` link that splits it reads otherwise."""
    names = _names(tracker)

    def text(anchor: re.Match) -> str:
        """``anchor`` unwrapped to its text if it is such a link, else whole."""
        attributes, shown = anchor.group("attributes"), anchor.group("text")
        pull, number = _APP_PULL.search(attributes), _PR_TEXT.fullmatch(shown)
        written = pull is not None and number is not None and int(number.group(1)) == int(
            pull.group(1))
        return shown if names.search(attributes) or written else anchor.group(0)
    return _ANCHOR.sub(text, rendered)


def _drawn(found: re.Match) -> str:
    """What GitHub drew afresh in ``found`` (:data:`_FRESH`), read as one: the attribute
    emptied, or the id without its hash."""
    return f'{found.group(1)}=""' if found.group(1) else found.group(2)


def _differs(key: str, part: str, before: str, after: str, tracker: str) -> list[str]:
    """The refusal of ``key``'s ``part`` when GitHub renders it ``before`` as built and
    ``after`` as filed otherwise than :func:`_unwrapped` allows, once what GitHub draws
    afresh on each render is read as one (:func:`_drawn`); none when they are alike."""
    before, after = (_FRESH.sub(_drawn, _unwrapped(rendered, tracker))
                     for rendered in (before, after))
    if before == after:
        return []
    at = next((index for index, (one, other) in enumerate(zip(before, after)) if one != other),
              min(len(before), len(after)))
    around = slice(max(0, at - _QUOTED), at + _QUOTED)
    return [f"numbers: {key}: its {part} renders differently once its numbers are written: "
            f"GitHub's {before[around]!r} becomes {after[around]!r}: decide them otherwise, "
            "or reword its source"]
