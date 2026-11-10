"""
Shekel Budget App -- The statement import page, its write door and its undo

"What did my bank actually say?" -- the page that records a statement, the POST
that performs one, and the POST that takes one back.  Plan steps
**bank_import:X-f6a-1** and **X-f6a-4**, ruling **R-FP**.

**The undo is here because a refusal owes one** (finding **N-302**).  Recording
what a bank said is append-only by design -- an observation quietly rewritten is
what ruling **R-FL** exists to prevent -- and that left every refusal terminal:
a restated line, or a first import that named the wrong Shekel account, ended
that account's ability to import at all, while the message told the owner it
"needs a human before anything overwrites it", a promise nothing in ``app/``
could keep.  ``delete_statement_import`` is the human's hands.  It destroys what
the BANK said, and since ruling **R-GG** it MOVES MONEY where the review had
CREATED a row from one of those lines: such a row goes with its line, and the
receipt says how many and how much.  A settle day an accepted match wrote is
the app's own record and stays.

**It RECORDS, and since plan step ``bank_import:X-ge`` it also FILES what the
owner has already decided** (ruling **R-GH**).  Recording still moves no
figure: :func:`~app.services.statement_import.record_statement` writes
``budget.bank_statement_lines`` and stops.  What moves one is the second act in
the same request -- :func:`~app.services.statement_match.file_new_swipes` turns
each NEW swipe line whose merchant carries a standing rule into a purchase in
the destination that rule names, dated by the bank, receipted on this page with
the one-click undo ruling **R-GG** built.  **Consent for that was given when
the rule was stated**, which is R-GH's whole sentence; every act that would
MODIFY a row the owner made by hand -- re-date, re-price, settle, group-match
-- is still a proposal on the review screen (``X-f6a-2``) needing its tick.

**The page says which of the two it is doing**, and that is part of the design
rather than a caveat: it said "This records what your bank said. It changes no
balance." until this step, a sentence that would have become false the first
time a rule fired.

**Why it lives in the accounts package.**  A statement is a fact about ONE
account, the account is how ownership is checked, and the mapping from a bank's
account to a Shekel account is per-account state.  The subject boundary against
its siblings is the same one ``reconcile`` and ``difference`` cut along:
``anchor`` owns the balance ASSERTION's write door, ``reconcile`` owns what is
still OUTSTANDING against one, and this owns what the BANK said, which none of
them has ever had.

**It gates on the account KIND as well as on ownership**
(:func:`~app.routes.accounts._cash_page.load_cash_account_or_404`), because a
loan, a property or a 401(k) has no bank statement to import and its own detail
page 404s -- so without that gate this page renders for them with a back link
that dead-ends, and records bank lines against an account with no cash ledger.
The gate exists because the project has already paid twice for its absence.

Services boundary: this module owns the HTTP-shaped concerns -- ownership, the
upload, form parsing, flashes and redirects -- and delegates every read and
write to :mod:`app.services.statement_import`.
"""

import logging
from dataclasses import dataclass

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.enums import StatementBalanceEvidenceEnum, StatementSourceEnum
from app.exceptions import BaselineMissingError, ValidationError
from app.routes.accounts._bp import accounts_bp
from app.routes.accounts._statement_doors import (
    StatementDoorContext,
    refusal_sentence,
    run_statement_door,
)
from app.routes.accounts._cash_page import load_cash_account_or_404
from app.routes.accounts._statement_release import release_and_return
from app.schemas.validation import form_payload
from app.schemas.validation.statements import (
    StatementImportDeleteSchema,
    StatementUploadSchema,
)
from app.services.statement_import import (
    StatementImportError,
    available_sources,
    delete_import,
    import_history,
    recent_lines,
    record_statement,
    recorded_span,
)
from app.services.bank_feed import feed_state
from app.services.pay_calendar import PayCalendarError
from app.services.statement_match import (
    RECEIPT_LIMIT,
    ReviewScope,
    RuleFiling,
    file_new_swipes,
    rule_filed_acts,
)
from app.utils.auth_helpers import require_owner
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_IMPORT_DELETED,
    EVT_STATEMENT_IMPORTED,
    EVT_STATEMENT_RULES_FILED,
    log_event,
)

_logger = logging.getLogger(__name__)

#: The form field the file arrives in.
_FILE_FIELD = "statement_file"

#: One schema instance each, constructed at import like every sibling's.
_upload_schema = StatementUploadSchema()
_delete_schema = StatementImportDeleteSchema()


@accounts_bp.route("/accounts/<int:account_id>/statements")
@require_owner
def statements(account_id):
    """Render what the bank has said about this account, and the upload form.

    Args:
        account_id: The account whose statements to show.

    Returns:
        The rendered page, or a 404 when the account is not the caller's or is
        a kind that has no bank statement -- the security response rule's
        answer for both "not found" and "not yours".
    """
    account = load_cash_account_or_404(account_id)

    return render_template(
        "accounts/statements.html",
        account=account,
        sources=available_sources(),
        span=recorded_span(account_id),
        imports=import_history(current_user.id, account_id),
        lines=recent_lines(account_id),
        evidence_copy=EVIDENCE_COPY,
        # **The receipt ruling R-GH asks for, and it is a READ** (plan step
        # ``bank_import:X-ge``).  What a standing rule filed is stored -- the
        # purchase, the match naming it, and ``applied_by_rule`` saying a rule
        # performed it -- so this is here rather than in the flash the import
        # sets: a flash cannot carry an Undo control, it rides in the signed
        # session cookie which this screen's sibling has already measured
        # overflowing, and it is gone on the next page load.  An owner who
        # comes back tomorrow to check what their rules have been doing sees
        # the same list.
        filed_by_rules=rule_filed_acts(current_user.id, account_id),
        # The BOUND, stated once (in the service) and rendered rather than
        # spelled a second time in the markup: the card says how many it
        # shows, and a template literal would be a second copy of a number
        # only one of the two could keep true.
        filed_limit=RECEIPT_LIMIT,
        # The bank feed panel (plan step ``bank_import:X-f6b-2``): the
        # OWNER's feed, rendered on every cash account's statements page from
        # ONE template the panel's own doors also answer with.  The page
        # renders it with no listing and no error: Bridge is asked only by a
        # press (ruling **R-BI27**), never by a page load, because every
        # request spends one of Bridge's daily allowance.
        feed=feed_state(current_user.id),
        listing=None,
        listing_error=None,
        panel_error=None,
        mappable=[],
    )


@dataclass(frozen=True)
class _EvidenceCopy:
    """What a screen says about one way a stated balance was pinned.

    Attributes:
        label: The short form, for a table cell.
        sentence: The full form, for the import receipt and the cell's title.
        badge: The Bootstrap class the label wears.  **It lives here rather
            than in a template conditional** so no surface has to ask which
            level it is holding: a template testing
            ``evidence is StatementBalanceEvidenceEnum.UNCORROBORATED`` would
            be one more place to update when a fourth rung exists, and the
            display decision is the display layer's to make once.  (This named
            an ``ASSUMED_LAST_DAY`` member until plan step
            ``bank_import:X-gc``; no such member has existed since X-f6e-1
            replaced that enum with
            :class:`~app.enums.StatementBalanceEvidenceEnum`.)
    """

    label: str
    sentence: str
    badge: str


#: What the screens say about each way a file's stated balance was pinned.
#: **Keyed by the enum MEMBER, not by its ``name`` string**, so the
#: project-wide IDs-for-logic rule holds here exactly as it does in a query --
#: the template subscripts this map with the member the service handed it and
#: compares no string at all.  ONE map rather than one per surface: the receipt
#: and the imports table say the same thing about the same fact, and two
#: spellings would be two places for a fourth basis to be forgotten.
#: **PUBLIC because it has readers in two route modules** -- this page's
#: receipt and imports table, and the books-vs-bank page, which names the
#: same fact about the same anchor.  A message with two readers is part of
#: the interface, which is finding **N-33**'s shape stated rather than left
#: to a convention (:mod:`app.routes.accounts.difference` imports
#: ``LOAN_ANCHOR_REFUSAL`` from ``_door_meaning`` for the same reason).
EVIDENCE_COPY = {
    StatementBalanceEvidenceEnum.FILE_CHAIN: _EvidenceCopy(
        label="proved by the file",
        sentence=(
            "Its stated balance is proved by the file's own per-line running "
            "balance, so nothing outside it was needed."
        ),
        badge="text-bg-secondary",
    ),
    StatementBalanceEvidenceEnum.CORROBORATED: _EvidenceCopy(
        label="corroborated",
        sentence=(
            "Its stated balance agrees with the statements already recorded "
            "for this account, so two of them say the same thing."
        ),
        badge="text-bg-secondary",
    ),
    # **The badge is neutral like its two siblings, and the sentence
    # prescribes nothing** (plan step ``bank_import:X-gc``).  It said "Export
    # once with your bank's running-balance option ticked" -- an option SECU
    # dropped between the developer's 2026-07-19 and 2026-08-16 pulls, so
    # every export he can take today carries no balance column and the
    # instruction names an act he cannot perform.
    #
    # **What replaces it states the RULE and diagnoses nothing**, because the
    # cause is per-import and this map is per-LEVEL: an import lands here
    # either because the record cannot reach back to its own first day (a
    # COVERAGE gap) or because the chain behind it is itself unconfirmed.  A
    # first draft said *nothing can confirm it until a self-proving statement
    # is recorded for this account*, which was measured FALSE twice over on
    # 2026-08-25 -- the developer HAS a chained 2026-07-19 export on disk, it
    # reaches ``file_chain`` when imported (306 lines, `$2,229.73` at
    # 07-17, measured), and a statement opening 2026-07-18 then reads
    # ``corroborated``.  What actually holds his imports at this rung is a
    # 17-day gap between 2026-07-17 and 2026-08-03.
    StatementBalanceEvidenceEnum.UNCORROBORATED: _EvidenceCopy(
        label="uncorroborated",
        sentence=(
            "Nothing has confirmed its stated balance, so it is taken at face "
            "value.  It is checked against what this account has already "
            "recorded, and stays unconfirmed when that record cannot reach "
            "back to this file's first day, or is itself unconfirmed -- a "
            "figure is only ever as firm as the weakest one behind it."
        ),
        badge="text-bg-secondary",
    ),
}


def _balance_sentence(outcome):
    """Return what the receipt says about the file's own stated balance.

    Args:
        outcome: The :class:`~app.services.statement_import.ImportOutcome`.

    Returns:
        One sentence, plus the PLACEMENT whenever the day the figure is placed
        at differs from the day the file names.

    **The gap between those two days is the number the owner has to judge**,
    and it is stated for every evidence level rather than only the weakest.  An
    ordinary export's header sits a day past its last line; the developer's
    2026-01-02..2026-03-31 export, pulled 2026-08-23, sits **145 days** past it
    and `$255.41` out.  A gap the owner can see is a gap the owner can judge.
    An earlier draft said *"that assumes nothing moved between"* those days,
    which stopped being true when the weakest-link rule made ``uncorroborated``
    cover solved days too -- a solved day assumes nothing about the span; it is
    the OPENING behind it that is unconfirmed.
    """
    if outcome.balance is None:
        return "It states no balance, so there was none to check."
    if not outcome.balance.is_anchored:
        # **It asserts no cause** (plan step ``bank_import:X-gr``, finding
        # **BI-489**, developer ruling 2026-09-12).  This arm is
        # :func:`~app.services.statement_import.resolve_anchor` finding no
        # candidate day that reconciles the figure with the opening the app
        # already holds, and that has two causes the solve cannot tell apart:
        # a date-range export whose header states a balance its lines stop
        # short of, and an ordinary export whose lines reach fine while the
        # RECORDED opening is what does not agree.  It read "which its own
        # lines do not reach", the first cause, on files in the second state.
        return (
            f"It states {outcome.balance.stated} as of "
            f"{outcome.balance.stated_on}, which no day it covers reconciles "
            f"with what was already recorded before it, so nothing here could "
            f"place that figure and no balance was recorded from it."
        )
    copy = EVIDENCE_COPY[outcome.balance.evidence]
    placement = (
        ""
        if outcome.balance.effective_on == outcome.balance.stated_on
        else (
            f"  The figure is placed at {outcome.balance.effective_on}, where "
            f"the file states it as of {outcome.balance.stated_on}."
        )
    )
    # **A GUESSED day is said out loud** (plan step ``bank_import:X-gc``,
    # ruling **R-GN**).  The evidence level cannot carry it: the
    # solve-against-an-unconfirmed-opening arm and the assume-the-last-line arm
    # both mint ``uncorroborated``, so a receipt reading the level alone
    # reports a proven placement and a guess in identical words.
    guessed = (
        ""
        if outcome.balance.day_is_solved
        else (
            "  Its day was taken from its last line rather than worked out: "
            "nothing already recorded for this account reaches back to this "
            "file's first day."
        )
    )
    return f"{copy.sentence}{placement}{guessed}"


def _import_flash(outcome):
    """Return the flash text and category for a successful import.

    **A file whose figure the app could not CHECK gets a warning, not a green
    tick**, and there are three such states.  Two are absences: a file stating
    no balance at all, and one stating a balance its own lines cannot reach.
    The third is the one this step added, on the developer's ruling **R-GN**
    (2026-08-25) -- a placement the app GUESSED.

    **The evidence level cannot tell a guess from a proof, and reading it alone
    was measured wrong.**  ``uncorroborated`` is minted by two different arms of
    :func:`~app.services.statement_import.resolve_anchor`: a solve against an
    opening that is itself unconfirmed, where the DAY is proven and only the
    chain is weak; and the arm that has nothing to solve against and takes the
    file's last line.  A first version of this predicate exempted the level
    outright, which green-ticked the second -- reproduced 2026-08-25 through
    the real producers on the developer's own 2026-01-02..2026-03-31 export,
    whose header names a day **145 days** past its last line and `$255.41` from
    what its own 139 lines imply: as an account's first import it came back
    ``effective_on=2026-03-31``, ``is_anchored=True``, category ``success``.
    :attr:`~app.services.statement_import.ImportedBalance.day_is_solved` is the
    fact that separates them, and :func:`_balance_sentence` says it in words
    beside this colour.

    **What the ruling removed, and what it did not.**  It removed the warning
    that fired on the corroboration LEVEL, which SECU's dropped running-balance
    column had made the developer's permanent normal state -- the badge still
    reads ``uncorroborated`` and the sentence still says nothing has confirmed
    the figure.  It did not remove the warning on a guessed day, which is
    ACTIONABLE where the other was not: it clears by importing the span that is
    missing, and it does not depend on that column returning.  Measured on the
    developer's real books 2026-08-25 -- one contiguous recorded run,
    2026-01-02 to 2026-08-21, so his next import's day is SOLVED and reports
    green.

    **The signal used to be ``opening_balance is None`` and that was measured
    WRONG too** (plan step ``bank_import:X-f6e-1``).  That column was derived
    from the per-line running-balance chain, which SECU stopped exporting
    between the developer's 2026-07-19 and 2026-08-16 pulls -- so the warning
    fired on every modern import, claiming "a missing line would not have been
    detected" while ``_secu_csv._verify_against_totals`` had already checked
    the line list against the file's own ``Totals:`` row and would have
    detected exactly that.

    **It says which OTHER imports' placements this one released** (plan step
    ``bank_import:X-gr``, finding **BI-488**), the way :func:`_removal_flash`
    has said it for the delete door since ``aa31bedf``.  A fresh line recorded
    at or before another import's placed day means that placement was worked
    out without it, so :func:`~app.services.statement_import.record_statement`
    releases it -- and until this clause the receipt said nothing, leaving the
    owner to learn that an import had taken a checked balance away from an
    earlier one from the imports table's badge, if they looked.  The clause
    renders only when the count is non-zero, and only on the branch that
    recorded a fresh line, because a line already recorded undercuts nothing.

    Args:
        outcome: The :class:`~app.services.statement_import.ImportOutcome`.

    Returns:
        ``(message, category)``.
    """
    unproven = (
        outcome.balance is None
        or not outcome.balance.is_anchored
        or not outcome.balance.day_is_solved
    )
    balance = f"  {_balance_sentence(outcome)}"
    if not outcome.recorded_count:
        # **The balance sentence belongs on THIS branch too**, and leaving it
        # off was a defect only driving the real app exposed: re-importing the
        # developer's own 2026-08-16 export added 0 of its 361 lines and yet
        # RECORDED an anchor -- its stated `$4,747.63` placed at 2026-08-13 --
        # while the receipt said "Nothing new" and nothing else.  A line is not
        # the only thing an import can learn.
        return (
            f"Nothing new: all {outcome.line_count} line(s) in this file were "
            f"already recorded.{balance}",
            "warning" if unproven else "info",
        )
    known = (
        f", and {outcome.already_known} were already known."
        if outcome.already_known else "."
    )
    released = (
        f"  {outcome.anchors_released} other import(s) had placed a stated "
        f"balance at or after the earliest day this file adds a line on, so "
        f"those placements were released: they were worked out without these "
        f"lines, and the account holds no checked balance from them."
        if outcome.anchors_released else ""
    )
    return (
        f"Recorded {outcome.recorded_count} new line(s) from "
        f"{outcome.declared_start} to {outcome.declared_end}{known}{balance}"
        f"{released}",
        "warning" if unproven else "success",
    )


def _counted(reasons: "list[str]") -> str:
    """Return *reasons* as one sentence, de-duplicated and counted.

    **Forty copies of one sentence is not forty facts** -- the rule
    :func:`~app.routes.accounts._statement_doors.refusal_sentence` already
    applies to a schema's own errors, applied here to the reasons a filing pass
    gives.  It is what bounds this flash: the search bounds are a closed set of
    four sentences and a door's refusal repeats across every line it refuses,
    so a pass over a whole year's statement still says each thing once.

    Args:
        reasons: One sentence per line, in the pass's own order, with
            repetitions.

    Returns:
        The sentences joined, each carrying how many lines gave it, in first
        sighting order.  Empty string for no reasons, so a caller can splice it
        without a guard.
    """
    counts: "dict[str, int]" = {}
    for reason in reasons:
        counts[reason] = counts.get(reason, 0) + 1
    return "; ".join(
        reason if count == 1 else f"{reason} ({count} lines)"
        for reason, count in counts.items()
    )


def _filing_sentence(filing) -> str:
    """Return what the import receipt says about the owner's standing rules.

    **It names MONEY and not only a count** (ruling **R-GD(a)**'s rule, on the
    one act in this app nobody presses): a receipt for work the owner did not
    ask for in this request has to say what it moved, or it is a consent to an
    amount nobody stated.

    **It says what was WITHHELD in the same breath**, because a bound that
    says nothing about what it dropped reads as a clean sweep -- the sentence
    :class:`~app.services.statement_match.ReviewBounds` is built around.  A
    line a rule answers for and this pass would not file is not a silence: it
    is the difference between *your rules ran* and *your rules ran on some of
    it*.

    Args:
        filing: The :class:`~app.services.statement_match.RuleFiling`.

    Returns:
        One or more sentences, or the empty string for a pass with nothing to
        report -- which is every re-import of an overlapping span, because it
        records no fresh line and a rule fires on nothing else (**R-GI**).
    """
    if filing.says_nothing:
        return ""
    if filing.unavailable is not None:
        return f"  Your standing rules did not run: {filing.unavailable}."
    parts = []
    if filing.outcome.applied:
        envelopes = (
            f", creating {filing.outcome.envelopes_created} budget line(s) to "
            f"hold them"
            if filing.outcome.envelopes_created else ""
        )
        # **The card below is BOUNDED and this sentence may not promise it is
        # not.**  A first version read "each one is listed under 'Filed by your
        # rules' below and can be undone there", which the step's own
        # measurement makes false by 60 on the developer's first real import:
        # 80 lines file and ``RECEIPT_LIMIT`` shows 20.  The place every act is
        # listed without a bound is the REGISTER's accepted list -- and only
        # behind its own *show everything* link, the tab itself rendering the
        # newest 50 (ruling **bank_import:R-GX**) -- so the TAB is where the
        # sentence sends an owner who wants all of them.  It said "the review
        # screen" until plan step ``bank_import:X-gf-2`` moved those acts off
        # it, at which point the sentence named a page listing none of them;
        # and it said "the page of what you have already decided" -- the
        # register -- until ``bank_import:X-gj-1c`` split those acts into two
        # TABS by who agreed to them (**R-GT**).  The acts this receipt is
        # about are the ones nobody pressed, so the tab that holds all of them
        # is *Filed by rules* and the other one holds none.  Twice now the
        # destination has moved and the sentence has had to move with it,
        # which is why it names a place rather than describing one.
        # **The two DIRECTIONS are named separately, and neither is assumed**
        # (ruling **R-HT(a)**, plan step ``bank_import:X-gj-2a``).  This read
        # "filed N of them as purchases worth X" while a rule could only ever
        # file spending; a rule now also files a DEPOSIT a standing income
        # answer covers, so the flat sentence would have called income a
        # purchase and -- because ``filed_count`` then read ``recorded_count``
        # alone -- reported "0" for a pass that filed only deposits.  The net
        # figure is stated as a net rather than as spending, because a pass
        # carrying both can come to any sign.
        # **A THIRD direction since plan step ``bank_import:X-gj-2b-3``**
        # (ruling **bank_import:R-II**): a rule also files a merchant CREDIT,
        # which this sentence would have called a purchase.  Named separately
        # for the reason the paragraph above names the second: the flat
        # sentence describes an act half its members did not perform.
        classes = []
        if filing.outcome.recorded_count:
            classes.append(
                f"{filing.outcome.recorded_count} as purchases"
            )
        if filing.outcome.refunded_count:
            classes.append(
                f"{filing.outcome.refunded_count} as refunds"
            )
        if filing.outcome.deposited_count:
            classes.append(
                f"{filing.outcome.deposited_count} as income"
            )
        parts.append(
            f"  Your standing rules filed {' and '.join(classes)}, "
            f"{filing.filed_total:+,.2f} in total{envelopes}.  The most "
            f"recent are under 'Filed by your rules' below, each with an undo; "
            f"every one of them is on the Filed by rules tab of the "
            f"Reconcile screen."
        )
    if filing.withheld:
        parts.append(
            f"  {len(filing.withheld)} line(s) your rules answer for were "
            f"left for you to review instead: "
            f"{_counted([item.reason for item in filing.withheld])}."
        )
    if filing.outcome.refused:
        parts.append(
            f"  {filing.outcome.refused_count} line(s) your rules answer for "
            f"could not be filed: "
            f"{_counted([item.reason for item in filing.outcome.refused])}."
        )
    return "".join(parts)


@accounts_bp.route(
    "/accounts/<int:account_id>/statements", methods=["POST"],
)
@require_owner
def import_statement(account_id):
    """Record an uploaded statement, and file what standing rules answer for.

    Every refusal the service can raise is a :class:`StatementImportError`
    subclass carrying a sentence written for the person who uploaded the file,
    so this handler has ONE except arm for the domain and renders the message
    rather than inventing one per failure.  The unit of work is the request:
    the service stages and flushes, this commits, and any refusal rolls the
    whole thing back -- which is what makes "nothing was imported", the phrase
    every one of those messages ends with, true rather than reassuring.

    **This door MOVES MONEY since plan step ``bank_import:X-ge``** (ruling
    **R-GH**), and it is the only one in the app that does so without an act
    in the same request.  Recording a statement still moves no figure; what
    moves one is :func:`~app.services.statement_match.file_new_swipes`, which
    turns the NEW swipe lines the owner has already stated a rule for into
    purchases in the destinations those rules name.  Consent was given when the
    rule was stated, every application is receipted with the one-click undo
    ruling **R-GG** built, and every act that would MODIFY a row the owner made
    by hand is still a proposal on the review screen needing its tick.

    **TWO acts, ONE unit of work, in this order.**  The scope is derived AFTER
    the import has staged its lines -- a pass built before them cannot see the
    swipes the rules are for -- and the whole request commits once, so a
    failure outside a designed refusal leaves neither the lines nor the
    purchases.  ``ReviewScope.build``'s own two failures are deliberately not
    caught: they mean the pay calendar cannot be resolved or no scenario can
    price a row, states in which every money surface is already unreachable,
    and recording a statement is IDEMPOTENT -- so the owner repairs the setup
    and imports the same file again at no cost, which is a better trade than a
    silently half-run import.

    **The success events are emitted AFTER the commit**, not by the services: a
    business event asserting "a bank statement was recorded" -- or that money
    was filed under a rule -- must not sit in the log when the transaction that
    would have done it failed.  The filing has its OWN event beside the
    import's, because they are different acts with different consequences.

    Args:
        account_id: The account to import into.

    Returns:
        A redirect back to the statements page, with a flash saying what
        happened.  The filed lines themselves are on that page, listed with
        their undo controls, rather than in the flash: a flash cannot carry a
        form, and it is gone on the next load.
    """
    account = load_cash_account_or_404(account_id)
    target = url_for("accounts.statements", account_id=account_id)

    errors = _upload_schema.validate(request.form)
    if errors:
        flash(refusal_sentence(errors), "warning")
        return redirect(target)
    source = StatementSourceEnum(_upload_schema.load(request.form)["source"])

    upload = request.files.get(_FILE_FIELD)
    if upload is None or not upload.filename:
        flash("Choose a file to import.", "warning")
        return redirect(target)

    # Read the whole upload into memory deliberately: the parser needs the
    # entire file to check it against its own summary and its running-balance
    # chain, so a streaming read would buy nothing.  The BYTE size is bounded
    # by ``MAX_CONTENT_LENGTH`` before this handler runs and the LINE count by
    # the adapter's own ceiling -- both, because a CSV parse expands its input
    # many times over in list overhead.
    payload = upload.read()

    def _file_under_rules(outcome):
        """Return what standing rules filed, or why they could not be asked.

        **Recording what the bank said does not depend on the budget being
        derivable, and this arm is the whole of what makes that true.**
        ``ReviewScope.build`` raises ``PayCalendarError`` when the owner's
        paydays cannot define a calendar -- two on one day is enough -- and
        ``BaselineMissingError`` when no scenario can price a row.  Neither is a
        fact about the statement.  Both have an application-level handler
        (``error_handlers.pay_calendar_underivable`` since plan step
        ``pay_calendar:C4-b-2``, and the baseline recovery page before it), so
        letting either propagate would not 500 -- it would ROLL THE IMPORT
        BACK and answer with a recovery page, on a request whose own GET
        renders perfectly well without a calendar: ``statements()`` builds no
        scope at all.  This door degrades the FILING ahead of those handlers
        because the recording is what the owner asked for and it needs
        neither.  *Until 2026-09-03 this paragraph said no handler existed,
        which was true when written and false since C4-b-2 (finding P82).*

        **It does not contradict ruling R-BW**, which sends a request whose
        ANSWER needs a baseline to the setup-recovery page.  This request's
        answer is *what did the import record*, and that needs none; what is
        undefined is the FILING, and saying so is the honest report of it.

        Args:
            outcome: What :func:`record_statement` just did.

        Returns:
            The :class:`~app.services.statement_match.RuleFiling`.
        """
        try:
            scope = ReviewScope.build(current_user.id, account.id)
        except (PayCalendarError, BaselineMissingError):
            _logger.warning(
                "user_id=%d imported into account %d and the standing rules "
                "could not be consulted: the pass could not be derived",
                current_user.id, account_id, exc_info=True,
            )
            return RuleFiling.could_not_run(
                "your pay calendar or your baseline scenario could not be "
                "worked out, and a rule files into a budget the app has to be "
                "able to derive. Your bank's lines are recorded either way -- "
                "fix that and import the same file again, which is safe"
            )
        return file_new_swipes(scope, outcome.import_id)

    def _record_and_file():
        """Record the file, then file what standing rules answer for.

        Both acts, in the ONE unit of work the caller commits.  The scope is
        built HERE rather than by the service beneath it -- only a route builds
        a read pass -- and it is built after :func:`record_statement` has
        staged its lines, because those lines are what the rules are for.

        Returns:
            ``(ImportOutcome, RuleFiling)``.
        """
        outcome = record_statement(
            account_id=account.id,
            user_id=current_user.id,
            source=source,
            file_name=upload.filename,
            payload=payload,
        )
        return outcome, _file_under_rules(outcome)

    def _report(result):
        """Log both business events and return the flash, AFTER the commit.

        An event asserting "a bank statement was recorded" -- or that money was
        filed under a standing rule -- must not sit in the log when the
        transaction that would have done it failed, which is why both are here
        rather than in the services.

        Args:
            result: ``(ImportOutcome, RuleFiling)`` from
                :func:`_record_and_file`.

        Returns:
            ``(message, category)``.
        """
        outcome, filing = result
        log_event(
            _logger, logging.INFO, EVT_STATEMENT_IMPORTED, BUSINESS,
            "Recorded a bank statement.",
            account_id=account.id, source=source.value,
            import_id=outcome.import_id,
            line_count=outcome.line_count,
            recorded_count=outcome.recorded_count,
            declared_start=outcome.declared_start.isoformat(),
            declared_end=outcome.declared_end.isoformat(),
            anchors_released=outcome.anchors_released,
        )
        # **Only when it did something**, which is what keeps the log honest
        # about the act rather than about the door: a re-import of an
        # overlapping span records no fresh line, so no rule can fire on it,
        # and an event saying a pass filed nothing on every such import would
        # be the noise that makes the ones that DID file unfindable.
        if not filing.says_nothing:
            log_event(
                _logger, logging.INFO, EVT_STATEMENT_RULES_FILED, BUSINESS,
                "Standing rules filed new swipe lines at import.",
                user_id=current_user.id,
                account_id=account.id,
                import_id=outcome.import_id,
                filed_count=filing.filed_count,
                filed_total=str(filing.filed_total),
                envelopes_created=filing.outcome.envelopes_created,
                refused_count=filing.outcome.refused_count,
                withheld_count=len(filing.withheld),
            )
        message, category = _import_flash(outcome)
        return f"{message}{_filing_sentence(filing)}", category

    return run_statement_door(
        StatementDoorContext(
            logger=_logger,
            refusal=StatementImportError,
            log_message=(
                "user_id=%d failed to import a statement for account %d"
            ),
            log_args=(current_user.id, account_id),
            flash_message=(
                "Something went wrong saving this statement.  Nothing was "
                "imported, and nothing was filed."
            ),
            target=target,
        ),
        _record_and_file,
        _report,
    )


def _removal_flash(account_id: int, removal) -> tuple:
    """Log the business event and return the flash, AFTER the commit.

    **It reports what was actually removed rather than "done".**  A destructive
    act whose report is a single word leaves the owner unable to tell a no-op
    from a much larger removal than they meant -- and this one can reach past
    the import itself, releasing matches and clearing the source-account
    pairing.  Every figure here was counted as the act ran, because afterwards
    the rows are gone.

    **Every figure the receipt carries is rendered, and each clause renders
    only when its figure is non-zero.**  Two of them were not, until plan step
    ``bank_import:X-gi-4`` (finding **N-470**): ``anchors_released`` and
    ``merchants_forgotten`` were computed, typed and tested at the service
    tier and read by nothing here -- the first with a docstring saying it was
    "reported rather than silent".  A receipt field with no surface is a
    promise the receipt does not keep, so both are in the flash and in the
    event's own kwargs now, and each has a route case for the sentence being
    PRESENT and a paired one for it being ABSENT, because a presence-only
    assertion passes against a receipt that always prints it.

    **The event is emitted HERE rather than in the service**, because a
    business event asserting that an import was destroyed must not sit in the
    log when the transaction that would have destroyed it failed.  This is the
    ``_report`` shape ``import_statement`` uses, on the door where a false
    entry costs most.

    Args:
        account_id: The account the import belonged to.
        removal: The :class:`~app.services.statement_import.ImportRemoval`.

    Returns:
        ``(message, category)``.
    """
    log_event(
        _logger, logging.INFO, EVT_STATEMENT_IMPORT_DELETED, BUSINESS,
        "A recorded statement import was deleted.",
        user_id=current_user.id, account_id=account_id,
        import_id=removal.import_id, file_name=removal.file_name,
        declared_start=removal.declared_start.isoformat(),
        declared_end=removal.declared_end.isoformat(),
        lines_removed=removal.lines_removed,
        matches_released=removal.matches_released,
        rows_removed=removal.rows_removed,
        cash_removed=str(removal.cash_removed),
        anchors_released=removal.anchors_released,
        identity_forgotten=removal.identity_forgotten,
        merchants_forgotten=removal.merchants_forgotten,
        skips_forgotten=removal.skips_forgotten,
    )
    released = (
        f"  {removal.matches_released} accepted match(es) were undone, so "
        f"their rows can be matched again -- the days those matches corrected "
        f"are unchanged."
        if removal.matches_released else ""
    )
    # **The one sentence on this receipt that reports MONEY** (plan step
    # ``bank_import:X-f6f``, ruling **R-GG**): a row the review created from
    # one of these lines goes with the line, so this act is no longer
    # balance-neutral and a report that said only "N matches undone" would
    # hide it.
    removed_rows = (
        f"  {removal.rows_removed} row(s) created from those "
        f"lines were removed with them, worth {removal.cash_removed:+,.2f}."
        if removal.rows_removed else ""
    )
    # **The owner's own ANSWERS, which the cascade takes silently** (plan step
    # ``bank_import:X-gj-4a``).  A skip claims nothing but its line, so it goes
    # with the line rather than refusing the delete -- and what that costs is
    # questions the owner has already answered being asked again the next time
    # the same file is imported.  Said here because a figure computed and never
    # rendered is a promise the receipt does not keep.
    unskipped = (
        f"  {removal.skips_forgotten} line(s) you had skipped went with them, "
        f"so re-importing this span will ask about those again."
        if removal.skips_forgotten else ""
    )
    # **A checked balance that rested on these lines no longer stands**
    # (:func:`~app.services.statement_import._anchor.release_anchors_from`).
    # A stated balance is placed on a day by the lines at or before it, so a
    # LATER import whose placement reached back over the lines just removed
    # has lost its evidence, and the imports table now shows it "not placed".
    # An account that had a checked bank balance and now has none is a change
    # the owner should see stated here, not discover later.
    unplaced = (
        f"  {removal.anchors_released} other import(s) had placed a stated "
        f"balance on these lines, so those placements were released and the "
        f"account holds no checked balance from them; a later import can "
        f"place one again."
        if removal.anchors_released else ""
    )
    # **Merchants nothing has a reason to remember** (plan step
    # ``bank_import:X-gd-1``): named by no surviving line and carrying no
    # standing answer, so keeping them would be the table with no ceiling.
    # A merchant the owner HAS answered for stays, answer and all, which is
    # why the count is of the unanswered ones alone.
    forgotten_merchants = (
        f"  {removal.merchants_forgotten} merchant(s) were forgotten: nothing "
        f"else named them and you had stated no rule for them."
        if removal.merchants_forgotten else ""
    )
    forgotten = (
        "  This was the last import for this account from that source, so the "
        "app no longer records which bank account it is; the next import will "
        "learn that again."
        if removal.identity_forgotten else ""
    )
    return (
        f"Deleted the import of '{removal.file_name}' covering "
        f"{removal.declared_start} to {removal.declared_end}, and the "
        f"{removal.lines_removed} bank line(s) it had recorded."
        f"{released}{removed_rows}{unskipped}{unplaced}{forgotten_merchants}"
        f"{forgotten}",
        "info",
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/delete", methods=["POST"],
)
@require_owner
def delete_statement_import(account_id):
    """Undo one recorded import, so a refusal stops being terminal.

    **The repair door finding N-302 says a refusal owes** (plan step
    ``bank_import:X-f6a-4``).  A restated line, or a first import that named
    the wrong Shekel account, used to end this account's ability to import at
    all: nothing in ``app/`` deleted an import, a line or a recorded pairing,
    and the refusal's own message promised a human repair the app could not
    perform.

    **It MOVES MONEY where the review CREATED a row from these lines** (ruling
    **R-GG**): a purchase a bank line became, a recorded difference, or a budget
    line minted to hold one goes with the line, and :func:`_removal_flash`
    reports the count and the figure.  *It said "It moves NO money" until plan
    step ``bank_import:X-gr`` (finding **BI-487**), a sentence true under
    ruling **R-GB** and false since R-GG amended it.*  What else it destroys
    is what the BANK said; a settle day an accepted match wrote is the app's
    own record and stays, which is the rule ``release_match`` already states
    for the same reason.

    **It is a plain POST-redirect-GET**, like ``release_filed_match`` and
    unlike the review screen's batch: it names ONE act and either does it or
    refuses it, so a flash carries the whole answer.

    Args:
        account_id: The account whose import to delete.

    Returns:
        A redirect back to the statements page, with a flash saying exactly
        what was removed.
    """
    account = load_cash_account_or_404(account_id)
    target = url_for("accounts.statements", account_id=account_id)

    payload = form_payload(request.form, _delete_schema)
    errors = _delete_schema.validate(payload)
    if errors:
        flash(refusal_sentence(errors), "warning")
        return redirect(target)

    import_id = _delete_schema.load(payload)["import_id"]
    return run_statement_door(
        StatementDoorContext(
            logger=_logger,
            refusal=ValidationError,
            log_message="user_id=%d failed to delete an import on account %d",
            log_args=(current_user.id, account_id),
            flash_message=(
                "Something went wrong deleting that import.  Nothing was "
                "changed."
            ),
            target=target,
        ),
        lambda: delete_import(import_id, current_user.id, account.id),
        lambda removal: _removal_flash(account.id, removal),
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/statements/release", methods=["POST"],
)
@require_owner
def release_filed_match(account_id):
    """Undo one match from this page's receipt, and come back to it.

    Plan step ``bank_import:X-gf-2``.  The receipt above lists what standing
    rules filed at import (ruling **R-GH**), each act carrying the same undo
    the register offers -- and the owner reading a receipt of twenty acts
    belongs on the receipt afterwards.  It posted to the review screen's own
    release door until this step, which redirected there: a different page,
    mid-receipt, and after the split a page that lists no accepted match at
    all.

    The act, its refusals and its wording are
    :func:`~._statement_release.release_and_return`'s; what this route owns is
    the ownership proof and the page to return to.

    Args:
        account_id: The account whose import receipt this is.

    Returns:
        A redirect back to the bank statements page.
    """
    account = load_cash_account_or_404(account_id)
    return release_and_return(account, "accounts.statements")
