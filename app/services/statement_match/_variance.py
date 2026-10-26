"""What a match's two sides come to, and the member that closes the gap.

Plan steps ``bank_import:X-f6d-4`` and ``bank_import:X-gj-3a``; rulings
**R-GD**, **R-FN** and **R-HT(b)**.

**THE DOOR cannot say which member a difference belongs to, and it does not
guess.**  Three rows summing to one deposit, five cents short, is not five
cents this module can attribute: nothing it reads says which of them the bank
varied.  So where nothing says, it RECORDS the missing movement as an ordinary
row with no category and the row becomes a member of the match -- the identity
``Sigma(lines) == Sigma(members)`` holding BY CONSTRUCTION rather than by a
refusal, which is the balance test ceasing to be a fence the door enforces and
becoming an invariant it maintains.

**THE OWNER can say, and since plan step ``bank_import:X-gj-3a`` they may**
(ruling **R-HT(b)**: *where the residue lands -- onto a named row, re-pricing
it, or its own uncategorized row*).  That is not the paragraph above being
weakened: a stated attribution is a DECISION, and this module still derives
none.  What it costs is one refusal the lone-row shape never needed
(:func:`_reject_unrepresentable_landing`) and one thing this module must not
claim -- that a group's gap is *five cents the bank moved that the app has no
row for at all*.  It says that where the owner says nothing; it does not say
it as a fact about the world.  An earlier version of this paragraph did, and
:class:`~._landing.DifferenceLanding`'s own measured population then
asserted the negation.  *That read "eight hundred lines below it" while the two
shared a file; plan step ``bank_import:X-gq`` moved the class out.*

**Ruling R-FN is what the row is**, and its mechanism already exists: an
ordinary transaction with ``category_id`` NULL books to the per-owner
``Uncategorized Income`` / ``Uncategorized Expense`` ledger account through
``ledger_account_service.get_or_create_category_ledger_account``, needs no new
ledger-account kind, and is CATEGORIZABLE later through the ordinary edit
door -- which is the mechanism that shrinks the bucket.  It is emphatically NOT
a plug to ``anchor_equity``: that is what R-FN replaced, and plan step
``balance:X-f4`` deletes the machinery it would have used.

**The measured population is finding salary:N-391, the bank-side half of the
retired N-239.**  On a
production clone carrying the developer's own 376 recorded lines, SEVEN payroll
deposits are 2-3 app rows summing ``$0.04``-``$0.06`` under what the employer
actually paid -- ``+$0.35`` across the span -- and before this step every one
was refused outright, so the deposit could not be recorded at all and its
member rows never had their day corrected either.  **Measured by APPLYING all
seven through this door and reading the ledger** (``-0.35`` in Uncategorized
Income), rather than by a query that approximates the matcher: an adversarial
review's own replay found six, missing the deposit whose rows settled five days
after the line posted -- which the day window admits and a same-day heuristic
does not.  Ruling **R-GD**'s "6 of 16" is an earlier, different count, taken
before any of this arc's tiers existed.  this makes the residue VISIBLE as seven
named rows instead of six refusals, which is what ruling R-GD calls the louder
instrument.

**The CAUSE sentence here read "``paycheck_calculator``'s rounding residue is
the cause and plan step ``balance:X-aw`` owns it" and both halves are now
false** (corrected at plan step ``bank_import:X-gj-3a``).  `N-239` called the
calculator's residue-apportionment and the bank-side gap ONE mechanism, and
`N-391` records that as MEASURED FALSE -- `X-aw` shipped and the gap did not
move, because the residue rule's whole range was `{$3,525.96, $3,525.97}`.
What `N-391` says instead is that ``salary_profiles.annual_salary`` divided to
a per-paycheck gross four cents under the stub the employer issues, and its
owner was ``salary:X-av``.  Plan step ``salary:X-av-3a`` removed the division:
the salary is now what one paycheck pays, so the owner can hold the stub's
figure -- though its migration wrote the divided one, and the gap stays until
the owner fixes it.  A cause sentence nobody re-checks is exactly the shape
that decays.

**Nothing PROPOSES a residual and nothing may.**  R-GD's own measurement is
why: ``n choose k`` puts 20 leftover lines within 1% of some same-day row set,
a Van Loan transfer inside a Capital One payment at **0.0064%**, tighter than
the six true payroll groups at 0.0019%.  A subset sum hits any target, so a
near miss over a group is coincidence.  The residual exists only where the
OWNER built the group by hand and agreed to the figure.

**The figure the owner agreed to TRAVELS, and the door reconciles it.**  That
is finding **N-336**'s lesson applied to the one number no other guard checks:
``_resolve._reject_moved_since_review`` compares each ROW against the state the
screen described, so a per-row figure cannot drift -- but the SUM is a second
derivation over those rows, and no per-row guard can see it being wrong.  So
the submission carries what was shown
(:attr:`~._submission.MatchSubmission.consent`) and
:func:`reject_unrecordable` refuses the act when this module's own arithmetic
disagrees.

**The screen gets that figure from HERE**, through
:func:`~._preview.preview_hand_build`, which runs these same reads and
refusals without the writes.  A first version of this step summed the two
sides in the BROWSER and posted the result back; the panel is server-rendered
now, so the figure the owner accepts and the figure this module tests are one
derivation rather than two in two languages -- which also removed a
``ROUND_HALF_EVEN`` quantizer that had been repairing a sub-cent consent into
agreement.

**WHERE THE DIFFERENCE LANDS MOVED TO** :mod:`._landing` **at plan step
``bank_import:X-gq``**, a pure move that took :class:`~._landing
.DifferenceLanding`, :func:`~._landing.corrected_figure` and their reader out
of this file and left every word of this docstring where it was.  The argument
above is ONE argument and spans both halves, so dividing it would have meant
rewriting it; the cost of leaving it whole is finding **bank_import:BI-484**.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no Flask import.  :func:`mint` MUTATES and does NOT
commit -- the route owns the unit of work.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from app.exceptions import ValidationError
from app.services.pay_calendar import DerivedPeriod
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_RESIDUAL_RECORDED,
    log_event,
)
from app.utils.money import MONEY_COLUMN_MAX

from ._landing import DifferenceLanding
from ._offers import MatchDays, merchant_label
from ._subjects import CandidateRow, RowKind
from ._sides import MatchSides
from ._scope import ReviewScope
from ._uncategorized import MovementToRecord, mint_uncategorized

_logger = logging.getLogger(__name__)

#: What a residual row is CALLED, before its merchant.  The label leads with
#: WHAT the row is rather than with who paid it, so the seven of them sort
#: together on any name-ordered list and none of them reads as a budget line
#: somebody planned (developer, 2026-08-23).
_NAME_PREFIX: str = "Statement difference"


def _named_for(lines) -> str:
    """Return what to call the row that closes *lines*' gap.

    The merchant of the LATEST line, which is the line whose day the whole
    match posts on (:attr:`~._offers.MatchDays.posts_on`) -- so the row's name
    and the row's day name the same movement.  Ties broken by id, because a
    name that depends on which of two same-day lines a query happened to return
    first is a name that changes without the record changing.

    :func:`~._offers.merchant_label` is what supplies it, and it is total: the
    bank's own merchant where the source names one, else the whole description.

    **It does NOT cut the name to the column, and that is the refactor rather
    than an omission** (plan step ``bank_import:X-gf-1``).
    ``budget.transactions.name`` has ONE bound and
    :func:`~._uncategorized.mint_uncategorized` is the one writer of that
    column here, so it applies it -- for its other caller as well as this one.
    Cutting here too would be the same rule in two places, which is what this
    package calls a root cause rather than a belt.

    Args:
        lines: The match's bank lines, at least one.

    Returns:
        The composed name, uncut.
    """
    latest = max(lines, key=lambda line: (line.posted_on, line.id))
    named = merchant_label(latest.merchant_name, latest.description)
    return f"{_NAME_PREFIX}: {named}"


#: The last clause of every refusal this module writes.  One spelling, because
#: an owner learns to trust the promise only if it is the same promise: a
#: refused match leaves the database exactly as it was, and the batch quotes
#: these sentences verbatim (**R-FZ(a)**).
_NOTHING = "  Nothing was changed."


def _reject_opposed_movements(sides: MatchSides, rows: int) -> None:
    """Refuse a match whose two sides are not the same DIRECTION of movement.

    **A fact about the PAIR, so it refuses every shape** -- one row or twenty.
    It was unreachable for a group until plan step ``bank_import:X-f6d-4``,
    because the group's own refusal short-circuited it, and that mattered the
    moment a group's difference became something this door WRITES: a `-100.00`
    line against a `+50.00` row would otherwise have recorded `-150.00` of
    spending the owner never claimed.  Nothing legitimate is caught -- a group
    whose rows explain a line sums NEAR it, not opposite to it -- and mixed
    signs WITHIN a group stay legal, because a net deposit really is a gross
    income row less a deduction.

    Args:
        sides: What the two halves come to.
        rows: How many app rows the match names, for the sentence's noun.

    Raises:
        ValidationError: When one side is money in and the other money out.
    """
    if not sides.oppose:
        return
    theirs = "this row is" if rows == 1 else f"the {rows} rows you picked are"
    raise ValidationError(
        f"Your bank shows {sides.bank:+,.2f} and {theirs} "
        f"{sides.app:+,.2f}.  One is money leaving the account and the other "
        f"is money coming in, so they are not the same movement." + _NOTHING
    )


def _reject_unstorable(sides: MatchSides) -> None:
    """Refuse a match whose figures are larger than this app can record.

    **A door that SUMS is not bounded by the columns it sums.**  Every figure
    here descends from a ``Numeric(12, 2)`` column, but a match may name ANY
    NUMBER per side since ``bank_import:X-go`` deleted the schema's member cap,
    so the total can leave the domain any one of them lives in and this is its
    ONLY bound.  Both remedies this module offers then write an unstorable
    figure: :func:`~._landing.corrected_figure` onto the row a match names, and
    :func:`mint` into a new one.

    **What that costs without this refusal is the whole PASS, not the item.**
    ``NumericValueOutOfRange`` (SQLSTATE 22003) is not a ``ValidationError``, so
    :func:`~._batch._run`'s SAVEPOINT does not catch it: it propagates to the
    route's ``except SQLAlchemyError``, rolls back every item that had landed,
    and reaches the owner as "Something went wrong".  Measured by adversarial
    security review 2026-08-23, which reproduced it from three imported lines
    at the column's ceiling.  It is the shape plan step ``X-f2-c3`` already
    paid for one tier up (finding **N-256**), on a figure a schema bound could
    reach; this one no schema bound can, because the sender does not state it.

    Args:
        sides: What the two halves come to.

    Raises:
        ValidationError: When either side, or their difference, is outside
            :data:`~app.utils.money.MONEY_COLUMN_MAX`.
    """
    for figure in (sides.bank, sides.app, sides.difference):
        if abs(figure) > MONEY_COLUMN_MAX:
            raise ValidationError(
                f"These figures come to {figure:+,.2f}, which is larger than "
                f"this app can record.  Match fewer lines at a time."
                + _NOTHING
            )


def _reject_uncorrectable_row(
    row: CandidateRow, sides: MatchSides,
) -> None:
    """Refuse a match whose difference cannot be written to *row*.

    **This REPLACES a blanket refusal, on ruling R-GD(a).**  Until 2026-08-22
    any match whose two sides did not sum to the same figure was refused and
    the owner sent away to retype the number the statement already carried.
    That refusal was not neutral: a line the screen would not explain is the
    line the merchant rule offers to RECORD, so the cheapest act left was to
    enter the movement a SECOND time -- measured at `$356.61` booked for one
    `$178.29` Geico payment, finding **N-335**.  The bank's figure is the
    record, so where it names ONE row it is simply written to that row.

    Two row shapes are still refused, and each is a genuine indeterminacy
    rather than a tolerance:

    * a row whose FIGURE IS NOT ITS OWN TO STATE, which the row now SAYS
      (:attr:`~._subjects.CandidateRow.states_own_figure`) rather than this door
      re-deriving.  A difference on one says a CARD PURCHASE is missing or
      wrong, which is a different repair on a different row.  **The census
      that answers it is TWO published predicates and it moved to the
      candidate constructor at plan step X-f6d-1**, because the PROPOSER has
      to ask the same question and is pure: a near miss offered on such a
      row is an Accept button that can never succeed.  **Its one reachable
      subject is the CC PAYBACK** (ruling **R-BAL81**, plan step
      ``balance:X-bi-4a``): the census's other member, an envelope holding
      purchases, is worth ``0`` to the offer and is never a candidate, so
      the sentence below is written for the payback -- through ``X-bi-4a``'s
      first cut it named the envelope's purchases, and an owner reading it
      beside a payback was told to fix a purchase the row does not hold;
    * one side of a TRANSFER (:attr:`~._subjects.CandidateRow.transfer_id`,
      a LEG or a leg's payment).  A transfer is one row, whose one figure
      both of its sides carry (the transfer paragraph of ``CLAUDE.md``'s
      Architecture), so correcting one side means correcting the TRANSFER,
      which is not this door.

    **BOTH are asked of every member of a GROUP too** (plan step X-f6d-4), and
    that is a correction rather than a widening.  A first version of this step
    dispatched on ``len(rows) == 1``, so ticking one extra row turned either
    refusal into a residual: measured by adversarial design review 2026-08-23,
    an envelope short `$180.00` of purchases booked `$180.00` to Uncategorized
    instead of naming the missing purchase, on a screen whose own sentence says
    that is what to fix.  The census is asked per ROW because that is what it
    is about; what a GROUP adds is only that nothing says WHICH member the
    remainder belongs to (:func:`_reject_unaccepted_difference`).

    **Both are asked of a SETTLEMENT as of a TRANSACTION** (plan step
    ``credit_card:CC-5-4a-1``): a row's payment matched on the row's terms
    carries the row's own answer to both -- the door that writes the figure
    is the row's -- so the one kind exempt here is the PURCHASE, which
    states its own figure and belongs to no transfer.

    Args:
        row: One app row the match names, already priced.
        sides: What the two halves come to.

    Raises:
        ValidationError: With the figures in the message, and naming which of
            the two it is, so the sentence says what to do next.
    """
    if row.kind is RowKind.PURCHASE:
        return
    if row.transfer_id is not None:
        raise ValidationError(
            f"Your bank shows {sides.bank:+,.2f} and these do not match it.  "
            f'"{row.label}" is one half of a transfer, and a transfer\'s two '
            f"halves must stay equal, so change the transfer itself and then "
            f"match it." + _NOTHING
        )
    if not row.states_own_figure:
        raise ValidationError(
            f"These do not add up.  Your bank shows {sides.bank:+,.2f} and "
            f'what you picked comes to {sides.app:+,.2f}.  "{row.label}" '
            f"repays what was spent on the card under the row it names, so "
            f"it has no figure of its own to correct -- the difference is a "
            f"card purchase that is missing or wrong under that row, and "
            f"that is what to fix." + _NOTHING
        )


def _reject_unaccepted_difference(
    sides: MatchSides, rows: int, accepted: "Decimal | None",
) -> None:
    """Refuse a difference the owner did not agree to, or did not see.

    Plan step ``bank_import:X-f6d-4``, rulings **R-GD(a)** and **R-FN**; the
    shape rule below is the developer's ruling of 2026-08-30 (plan step
    ``bank_import:X-gj-1b``).  What the two remedies have in common is that
    both WRITE the gap somewhere the owner did not type it -- onto the single
    row a match names, or into a new uncategorized row -- so both are gated on
    the same consent, and this is the gate that makes "accepts" true rather
    than decorative.

    **ONE rule: a difference this door would WRITE must have been reviewed,
    and the figure reviewed must be the figure derived.**  It reads three ways
    and they are the same sentence:

    * there is a difference and the submission states none.  The shipped
      behaviour, naming both sums and the gap and saying what to do about it;
    * there is a difference and the submission states a different one.  The
      per-row guard (:func:`~._resolve._reject_moved_since_review`) cannot see
      this: it reconciles each ROW against the state the screen described, and
      the difference is a subtraction OVER those rows.  A screen that states
      one total over a door that writes another is finding **N-336** one tier
      up, and so is a screen whose total was true when it was drawn;
    * there is NO difference and the submission states one anyway.  It earns
      its own sentence, because the comparison below would report a figure
      that "now comes to +0.00" when what happened is that the rows stopped
      disagreeing.

      **It is NOT reachable by a screen whose rows moved**, which an earlier
      draft of this said: :func:`~._resolve._reject_moved_since_review` runs
      inside ``resolve_rows``, which is evaluated as an ARGUMENT to
      :func:`~._accept.record_match` and so strictly before this gate -- a row
      whose figure or revision drifted is refused there, by name, with its own
      reload sentence.  What reaches here is a body that states a figure over
      rows that add up: a crafted one, or the race in which the owner ticks
      the consent and then ticks a row and presses Apply before the re-price
      returns.  The arm is kept because this function is total over its
      inputs, not because a stale page produces it.

    **The gate turns on THIS DOOR's derivation and never on the submission's
    shape**, which is the whole of the 2026-08-30 ruling.  It exempted *a
    single line against a single row* until then, on the premise that the near
    tier offers exactly that shape and its card states the figure -- so a
    SHAPE was read as a proxy for *the screen already disclosed this*.  A proxy
    for a fact the wire could carry is a guess, and the Reconcile page's
    tickable proposal rows manufacture the shape: untick one row of a proposed
    group and what arrives is one line against one row, indistinguishable from
    the tier's own offer.  Measured 2026-08-30 on a clone of the developer's
    production data, over the 137 proposals his account offers:

    * the exemption was load-bearing for **2** -- line 338 writing `-$0.15`
      and line 218 writing `-$0.04`, both onto ``Groceries: Walmart``;
    * **3** carried more than one row, and unticking down to one wrote, with
      nothing asked, `$25.51` or `$59.77` on line 253, up to `$437.76` on line
      257, and up to **`$2,572.36`** on line 358 -- the bank's `$2,611.90`
      payroll deposit written onto a `$39.54` ``Phone Allowance`` row.

    An allowlist that existed for four cents permitted twenty-five hundred
    dollars.  The two near-tier proposals state their figures on the wire now
    (:func:`app.jinja_filters.stated_difference`), and no shape is exempt
    from anything.  **Every one of the 137 proposals now emits that
    field**, not only the two that carry a figure: it is what a match
    says it was reviewed against, and a card that emitted it only when
    it was non-zero would make its absence mean two things.

    **A ZERO difference requires no figure, and the reason is the WRITE PATH
    rather than who derived the predicate.**  Both tests are the door's own
    arithmetic over a submission its sender shaped, so *sender-controlled
    versus door-derived* does not separate them -- an earlier draft of this
    paragraph said it did, and an adversarial review measured that false.  What
    separates them is what each predicate is ABOUT.  The old one predicted
    *what the screen had disclosed*, which this door cannot know and so had to
    proxy.  This one predicts *what this door is about to write*, which it can
    check.  At a zero difference the bank leaves the attributed member exactly
    what it already holds -- :class:`~._landing.DifferenceLanding` subtracts
    the OTHER
    members from a bank total the two sides agree on -- so
    :func:`~._landing.corrected_figure` answers ``None`` whether a member is
    named or
    not, and :func:`~._accept.record_match` resolves no residual period
    because the difference is zero.  The two writers a difference has are both
    provably idle and there is no act to consent to.

    **That clause used to read "``bank_cash_for`` answers ``None`` for a
    group", and plan step ``bank_import:X-gj-3a`` made it false** without
    changing the conclusion: an ATTRIBUTED group does name a member.  Restated
    against what the figure comes to rather than against which arm produced
    it, because this is the stated reason a money control may be skipped and a
    reason nobody re-checks is the one that decays.  **The rule is therefore "no figure is required
    exactly where no figure would be written", and it is not a shape.**

    Requiring one anyway would also have refused every exact match built with
    scripting off, where the panel never re-renders and so can state no figure
    -- a real capability, removed to gate a write that does not happen.

    **THE CONSENT BINDS THE FIGURE AND THE REMEDY TOGETHER, because they
    arrive as ONE value** (plan step ``bank_import:X-gp``, ruling **R-BI2**,
    which SUPERSEDES **R-IV**).  From plan step ``bank_import:X-gj-3a`` until
    then a submission carried two fields -- ``residual-<line>``, the figure,
    and ``difference_on-<line>``, the member it lands on -- and this gate
    compared only the first, so the consent rendered under *record the
    difference as a row with no category* could be paired with an attribution
    that re-priced a budget row instead: two acts with different
    consequences under one agreement.  R-IV ruled that bound known and
    accepted on the VERIFIED ground that the select sat inside the element
    whose change re-rendered the box unticked -- a DOM event holding a money
    consent together -- and ``X-gi-2a`` renders the pane on a page where
    nothing swaps, which is the premise falsified.  What the developer refused
    in every interim wording was hiding that, so the widening R-IV rejected
    is what shipped: the pane draws ONE control whose options ARE the acts,
    each labelled with the figures it writes, and each option's value is a
    :class:`~._submission.ReviewedDifference` carrying the figure AND the
    member.  The landing this gate's caller derives is read off that value's
    row half (:class:`~._landing.DifferenceLanding`), and *accepted* here is
    its figure half -- two halves of one thing the owner ticked, so there is
    no second field for a body to pair differently.  Comparing the row half
    against the derived landing would be comparing a value with itself; what
    IS compared about it is that it names one of the submitted rows, as a
    whole reviewed value, in :func:`~._resolve.resolve_rows`.

    **What scripting off can and cannot consent to is unchanged by this
    step.**  A tier's proposal states its figure on the card that offers it
    (``stated_difference``) and the ``?open=`` pane prices the proposal's rows
    on its first render and draws the one act for them, so a near miss is
    corrected with no script at all.  A set the owner builds BY HAND on that
    pane is priced only at the press: nothing re-renders to state its
    difference, so the owner cannot have seen what would be written, and a
    difference is refused here while an exact set is recorded -- the ruling
    of 2026-08-30 working, not a casualty of this step.  **The refusal is
    what states it** since plan step ``bank_import:X-gi-2a``: the page
    re-draws the pane from the submitted form, rows ticked and the acts
    offered, so the next press can carry the consent this gate compares.

    Args:
        sides: What the two halves come to.
        rows: How many app rows the match names, for the sentence.
        accepted: The figure the submission states it was reviewed against,
            or ``None`` where it states none.  **A PRECONDITION and never a
            payload**: what the door writes is :attr:`MatchSides.difference`,
            derived here from the rows the ids name, and this is only ever
            compared against it.

    Raises:
        ValidationError: Naming both sums and the difference.
    """
    difference = sides.difference
    if not difference:
        if accepted is not None and accepted != difference:
            raise ValidationError(
                f"This match was reviewed against a difference of "
                f"{accepted:+,.2f} and now adds up exactly.  Reload the page "
                f"to review it against what your records hold now." + _NOTHING
            )
        return
    if accepted is None:
        remedy = (
            # **THREE remedies since plan step ``bank_import:X-gj-3a``**, and
            # this sentence enumerates them because ruling **R-FZ(a)** makes a
            # refusal the service's own words: a screen that offers a control
            # the refusal does not mention teaches the owner that the app has
            # fewer answers than it has.  Since ``X-gp`` two of the three are
            # options of ONE control, and the sentence says so.
            "correct the one you know is wrong, or choose what to do with "
            "the difference: write it to the row it belongs to, or record it "
            "as a row with no category that you can categorise later"
            if rows != 1
            else "tick the box to write your bank's figure to it"
        )
        subject = (
            f"the {rows} rows you picked come to" if rows != 1
            else "the row you picked is"
        )
        raise ValidationError(
            f"These do not add up.  Your bank shows {sides.bank:+,.2f} and "
            f"{subject} {sides.app:+,.2f}, a difference of "
            f"{difference:+,.2f}.  Either {remedy}." + _NOTHING
        )
    if accepted != difference:
        raise ValidationError(
            f"This match was reviewed against a difference of "
            f"{accepted:+,.2f} and now comes to {difference:+,.2f}.  Reload "
            f"the page to review it against what your records hold now."
            + _NOTHING
        )


def _reject_unrepresentable_landing(
    landing: "DifferenceLanding", sides: MatchSides,
) -> None:
    """Refuse a difference the named member's own row cannot hold.

    Plan step ``bank_import:X-gj-3a`` -- **and it is a refusal the LONE-ROW
    shape never needed, which is why the widening had to bring one.**  A
    transaction stores a non-negative GROSS whose direction comes from its
    ``transaction_type_id`` and never from the figure
    (:func:`~._landing.corrected_figure`'s own note), so the only cash it can
    be moved to
    is cash of the direction it already has.  For a match naming ONE row that
    was guaranteed: :func:`_reject_opposed_movements` compares the two sums,
    and with one row ``sides.app`` IS that row's cash, so the bank's figure
    could never oppose it.

    **A group breaks the guarantee, and this module's own docstring says
    why**: *mixed signs WITHIN a group stay legal, because a net deposit
    really is a gross income row less a deduction*.  So the select offers the
    negative member too, and the bank can leave it a figure of the other sign.

    Worked example, which is the shape it exists for.  A `+$2,060.00` deposit
    against a `+$2,050.00` salary row and a `-$50.00` deduction row comes to
    `+$2,000.00`, `$60.00` short.  Attribute that to the DEDUCTION and the bank
    says that row is worth `+$10.00` -- and an expense row cannot be worth
    `+$10.00`.  Without this refusal the door writes `$10.00` as a magnitude,
    the row settles at `-$10.00`, and
    :func:`~._accept._reject_drifted_under_the_act` catches the arithmetic and
    reports *"Applying this match moved one of its own rows"* -- which is
    false, nothing drifted, and it sends the owner to match the rows one at a
    time when what they did was point at the wrong member.  **A guard that
    catches the right state under the wrong name is not the guard**, which is
    the shape finding **N-332** records one column over.

    **A PURCHASE is asked only about ZERO**, because it stores a SIGNED amount
    since ruling **R-II** -- a refund is a negative purchase -- so either
    direction is representable and only *worth nothing* is not
    (``ck_transaction_entries_positive_amount``, now ``amount <> 0``).

    Args:
        landing: Where this match's difference goes.
        sides: What the two halves come to, for the figures in the sentence.

    Raises:
        ValidationError: When the named member cannot hold the figure the bank
            leaves it.
    """
    row = landing.on_row
    if row is None or landing.bank_cash is None:
        return
    if landing.bank_cash == 0:
        raise ValidationError(
            f"Your bank shows {sides.bank:+,.2f}, and the other rows you "
            f"ticked already come to that -- so putting the difference on "
            f'"{row.label}" would leave it worth nothing, which is not a row '
            f"this app can hold.  Put the difference somewhere else, or untick "
            f'"{row.label}".' + _NOTHING
        )
    if row.kind is RowKind.PURCHASE:
        return
    if (landing.bank_cash < 0) != (row.cash_amount < 0):
        raise ValidationError(
            f'Your bank leaves "{row.label}" at {landing.bank_cash:+,.2f}, '
            f"and this row records money moving the other way -- so it cannot "
            f"carry this difference.  Put it on a row that moves the same way, "
            f"or record it as a row with no category." + _NOTHING
        )


def landing_refusal(
    landing: "DifferenceLanding", sides: MatchSides,
) -> "str | None":
    """Return why the difference cannot land where *landing* says, or ``None``.

    Plan step ``bank_import:X-gp``.  **The pane offers every act as an option
    of ONE control, so it has to ask this of every landing rather than of the
    one the owner named** -- and an option the door would refuse is rendered
    disabled with this sentence beside it, which is ruling **R-HW**'s shape
    one control down: *a verb whose door does not exist renders its
    explanation and no submitting control*.  A control whose submission can
    never succeed is the shape this package has closed repeatedly, and an
    option that vanished without a word would leave the owner unable to see
    why a member they can plainly tick is not one the gap can land on.

    :func:`_reject_unrepresentable_landing` stays the ONE statement of the
    rule; this is that refusal read as a sentence, the way
    :meth:`~._preview.HandTotals.refused` reads every other.  The refusals
    that are about the PAIR or about EVERY ROW are landing-independent and the
    pane asks them once, through :func:`reject_unrecordable` with no landing;
    this is the one arm that differs per option.

    Args:
        landing: Where the difference would go under one option.
        sides: What the two halves come to.

    Returns:
        The door's own sentence where that landing cannot hold the figure the
        bank leaves it, else ``None``.
    """
    try:
        _reject_unrepresentable_landing(landing, sides)
    except ValidationError as exc:
        return str(exc)
    return None


def reject_unrecordable(
    rows: "list[CandidateRow]",
    sides: MatchSides,
    accepted: "Decimal | None",
    landing: "DifferenceLanding | None" = None,
) -> None:
    """Refuse a match whose difference this door cannot honestly record.

    **The partition is by WHAT the refusal is about**, and stating it that way
    is what made two of them reach a group (plan step ``bank_import:X-f6d-4``):

    * :func:`_reject_opposed_movements` and :func:`_reject_unstorable` are
      about the PAIR of sums;
    * :func:`_reject_uncorrectable_row` is about each named ROW, and is asked
      of every member rather than only of a lone one;
    * :func:`_reject_unaccepted_difference` is about the OWNER's consent to
      the gap, whichever way it would be written;
    * :func:`_reject_unrepresentable_landing` is about whether the member the
      gap is written TO can hold it (plan step ``bank_import:X-gj-3a``).

    **WHETHER the attribution names a member of this match is NOT asked here**,
    and a first version of that step did ask it.  The attribution is a pointer
    into the SUBMITTED rows, so that refusal belongs where the submission's own
    shape is graded -- :func:`~._resolve.resolve_rows`, beside *this match
    names the same row more than once* -- and it is stronger there: that door
    compares the whole reviewed value, so an attribution disagreeing with its
    own row about the figure is refused too, where a check over these
    already-resolved rows could only compare the subject.  Asking in both
    places would have left this one unable to fire on any path, which is a
    control that measures nothing.

    **FIVE refusals live in this module**, and they are the ones about the two
    sides DISAGREEING.  :mod:`._accept` states its own and :mod:`._resolve`
    states its own; the counts are written down because this arc has shipped a
    taxonomy that did not add up before, and a sixth added here is what has to
    change this sentence.

    Args:
        rows: The submitted app rows, already priced.
        sides: What the two halves come to, derived once for the whole act.
        accepted: The difference the owner agreed to, or ``None``.
        landing: Where the difference would go
            (:class:`~._landing.DifferenceLanding`), or ``None`` where the
            caller has not derived one.  **Defaulted, and it is the one
            argument here that is**: the door that takes a SUBMISSION derives
            the landing before it refuses; the two that BUILD their own match
            name a single row each, where ruling **R-GD**'s determinacy
            answers the question and there is nothing about the landing to
            refuse; and the pane's preview asks the landing question of every
            option separately (:func:`landing_refusal`) since plan step
            ``bank_import:X-gp``, so it passes none here.

    Raises:
        ValidationError: From whichever arm fires, with the figures in it.
    """
    _reject_opposed_movements(sides, len(rows))
    _reject_unstorable(sides)
    if landing is not None:
        _reject_unrepresentable_landing(landing, sides)
    if sides.difference:
        for row in rows:
            _reject_uncorrectable_row(row, sides)
    # **Asked of every match, in every shape.**  The one shape this used to
    # pass unasked -- a single line against a single row -- was a proxy for
    # *the screen already disclosed this figure*, and the Reconcile page's
    # tickable proposal rows manufacture it; see that function for what the
    # proxy was measured to be worth.
    _reject_unaccepted_difference(sides, len(rows), accepted)




def mint(
    difference: Decimal,
    period: DerivedPeriod,
    scope: ReviewScope,
    lines,
    days: MatchDays,
) -> CandidateRow:
    """Record the movement a group's rows do not account for.

    Ruling **R-FN**'s ordinary accepted row, returned as the match member that
    makes the group add up.

    **The ROW is minted by :func:`~._uncategorized.mint_uncategorized` and not
    here** (plan step ``bank_import:X-gf-1``, ruling **bank_import:R-GW**).  Every clause of
    what that row IS -- no category, born Projected and settled through the
    app's own verb, the bank's posting day on the ``observed`` basis, owning
    its amount, baseline scenario -- is decided by the fact that a bank
    statement is why it exists, which is equally true of the line ruling
    **bank_import:R-GW** records as INCOME.  Two spellings of one money rule is this
    package's own root cause, so there is one writer and this function supplies
    the two things that are genuinely its own: what the row is CALLED, and the
    event that says a MATCH's difference is what produced it.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        difference: The DOOR's own figure
            (:attr:`MatchSides.difference`), already reconciled against the one
            the owner accepted by :func:`reject_unrecordable`.  Passed rather
            than re-derived here, because the value that was tested and the
            value that is written must be one.
        period: The paycheck this movement belongs to, resolved by the
            caller through :meth:`~._scope.ReviewScope.period_holding` for the
            reason :func:`~._uncategorized.mint_uncategorized` states.
        scope: The pass, which is the ONE statement of whose account and whose
            baseline scenario this row belongs to.
        lines: The match's bank lines, for the row's name.
        days: The days the match writes, derived once for the whole act.

    Returns:
        The new row as a :class:`~._subjects.CandidateRow`, so the caller can
        record it as a member exactly like every other one.

    Raises:
        PostingError: From the ledger reconcile, on a broken invariant.
        RuntimeError: When the candidate constructor refuses the row just
            created, which is the shared writer's contract.
    """
    candidate = mint_uncategorized(
        MovementToRecord(
            name=_named_for(lines),
            signed_amount=difference,
            period=period,
            posts_on=days.posts_on,
        ),
        scope,
    )
    log_event(
        _logger, logging.INFO, EVT_STATEMENT_RESIDUAL_RECORDED, BUSINESS,
        "A matched group's difference was recorded as an uncategorized row.",
        user_id=scope.owner_id,
        account_id=scope.account_id,
        transaction_id=candidate.row_id,
        pay_period_id=period.period_id,
        amount=str(difference),
        posts_on=days.posts_on.isoformat(),
        line_count=len(lines),
    )
    return candidate
