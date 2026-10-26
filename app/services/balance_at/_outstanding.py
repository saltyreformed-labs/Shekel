"""Balance-at-T seam -- what an account's own BOOKS cannot explain.

Plan step **X-f3c-3** (shipped at ``a7dbfdca``),
and it moves no money: it is the INSTRUMENT plan step X-f3c-4's acceptance
act needs, built one step ahead of it so the figure is on screen before
anything offers to book it.

**One figure per account, and never a per-assertion correction.**  The owner's
latest declared balance, less what the account's books produce for that same
day -- its stored opening equity plus every posting dated on or before it.
Ruling **R-FN** is why it is ONE figure: the per-assertion plug is DEFINED as
whatever forces the ledger to the balance just declared, so the plugs telescope
and their net is a function of the LAST assertion alone.  Booking them
individually would record over ``$1,500`` of "spending" on one day and a
comparable "income" the next, either side of the mortgage payment that caused
both.

**It is the POST-CUTOVER balance function, read today.**  Plan step X-f3c-5
makes ``balance(T)`` exactly ``opening equity + SUM(postings <= T)`` for a
PLAIN account; :func:`_books_balance_at` below is that function, assembled out
of the same walk and sampled through the same
:func:`~._fold.sample_cumulative` the shipping fold uses.  So this step does
not model the cutover, it EVALUATES it -- and what the difference measures is
precisely what the flip will leave unexplained on the day it lands.  Measured
on a clone of the dev database 2026-09-01: Checking asserts ``$2,501.31`` for
2026-08-18 where its books produce ``$131.29``, a difference of
``$2,370.02``.

**PLAIN accounts only, and that is the same scope**
:func:`~._cash_flow.records_balance_at` **already states.**  A modelled
account's assertion is not a check against recorded cash: an IRA has no record
of a price movement to discard, so the reset IS mark-to-market and the same
subtraction is its GAIN (ruling **R-FO**).  Captioning that as something the
books cannot explain would name a model-vs-market difference as untracked
spend -- on the clone above it would read ``$4,523.33`` on the Roth IRA, which
is return, not a defect.  Widening the question to those kinds is finding
**N-213**.

**Its own module rather than a fifth entry in** :mod:`._cash_flow`, for the
reason :mod:`._cash_periods` is one: this is the assembled fold read a fourth
way, it shares exactly one input with the readings beside it
(:class:`~._cash_fold.AssembledCashFold`), and the dependency runs one way.
The placement also has a job at X-f3c-5 -- the flip is then a matter of
pointing the PLAIN fold at the reading in here, rather than editing the reading
back out of a module that answers four other questions.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.cash_ledger import dated_deltas
from app.utils.books_boundary import first_movement_day
from app.utils.money import round_money

from . import _cash_fold
from ._context import BalanceContext
from ._fold import sample_cumulative
from ._inputs import _require_scenario


@dataclass(frozen=True)
class BooksSpan:
    """The days an account's books cover, up to a declared balance.

    The span the outstanding difference ACCUMULATED OVER, carried on the
    difference itself so a reader asking whether anything has checked those
    days cannot ask about a different range than the figure was taken from.
    That pairing is the shape this seam keeps closing (finding **N-354**): two
    dates and a figure passed as three arguments agree only because one caller
    spelled them consistently.

    Attributes:
        first_day: The day AFTER the books opened
            (:func:`~app.utils.books_boundary.first_movement_day`, the one
            spelling of that day since plan step ``balance:X-f3c-2b-2c``).  An
            opening equity is the CLOSING balance for ``opened_on`` (ruling
            **R-HG**), so that day is already inside the level the difference
            is measured from and no movement may be dated on it -- a
            deferrable constraint trigger makes such a row unstorable since
            plan step X-f3c-2b-1.
        last_day: The day of the account's latest assertion -- the declaration
            the difference is measured against.
    """

    first_day: date
    last_day: date

    @property
    def is_empty(self) -> bool:
        """Return whether the books cover no day before the declaration.

        **True is an ordinary state, not a defect**, and it is the state every
        account is CREATED in: ``account_service.create_account`` writes the
        opening and the origination assertion for one day from one typed
        figure, so a brand-new account's latest assertion falls ON
        ``opened_on`` and there is nothing between the two to explain.

        **It is reachable ONLY that way, and a first draft claimed otherwise.**
        That draft said this is also finding **N-400**'s state -- a back-dated
        assertion landing strictly BELOW the books -- which cannot happen:
        ``opened_on`` is the origination assertion's own day and the
        restatement door refuses moving it past any asserted day
        (``cash_ledger.reject_books_open_after_an_assertion``), so the LATEST
        assertion is never below ``opened_on`` however many are back-dated
        under it.  A back-dated assertion is simply not the governing one.  The
        test that was written to build N-400's state measured exactly that and
        is now named for it.

        Returns:
            True when the latest assertion is dated ON the day the books
            opened, so no day lies between the two.
        """
        return self.last_day < self.first_day


@dataclass(frozen=True)
class CashOutstandingDifference:
    """What an account's owner declared, beside what its books produce.

    The output of :func:`cash_outstanding_difference`, and the whole of what
    plan step X-f3c-4 needs from the balance seam: the figure, the day it is
    about, and the span it accumulated over.

    **Both sides are published, never the difference alone.**  A single number
    on screen cannot be checked against anything, and this arc has twice
    measured a surface publishing two different quantities under one label
    "Difference" -- the balance-history card's per-assertion correction beside
    the true-up form's records-vs-typed gap (see
    :class:`~._cash_flow.CashAnchorRow`).  Naming both sides is what lets a
    reader do the subtraction themselves.

    **It carries no ``account_id``, and that absence is the reasoned one.**  A
    first build added the field citing
    :attr:`~._cash_fold.AssembledCashFold.account_id`'s pairing argument, and
    adversarial review found no reader for it: the composing door
    (:func:`app.services.outstanding_difference.outstanding_difference`)
    resolves this value and its bank comparison from ONE account, so there is
    no mis-pairing left for a check to catch.  A guard that cannot fire is the
    *born dead* shape ``lessons.md`` names -- the same defect this step
    RECORDS one module over as finding **N-433**, which would be a poor thing
    to write down there and commit here.

    Attributes:
        opened_on: The civil day the account's books opened.
        opening_equity: What they opened WITH -- the stored
            ``budget.account_openings`` level the books figure starts from
            (plan step X-f3c-2a, ruling **R-GX**), cent-quantized and
            LEDGER-NATIVE.
        asserted_on: The civil day of the account's latest assertion.
        asserted: The balance the owner declared for that day, cent-quantized.
        books: What the account's books produce for that SAME day --
            :attr:`opening_equity` plus every posting dated on or before it,
            with no assertion applied.  Cent-quantized.
    """

    opened_on: date
    opening_equity: Decimal
    asserted_on: date
    asserted: Decimal
    books: Decimal

    @property
    def span(self) -> BooksSpan:
        """Return the days this difference accumulated over.

        Returns:
            The :class:`BooksSpan` from the day after the books opened through
            the assertion's own day.
        """
        return BooksSpan(
            first_day=first_movement_day(self.opened_on),
            last_day=self.asserted_on,
        )

    @property
    def amount(self) -> Decimal:
        """Return what the books cannot explain, signed.

        **POSITIVE means the owner declared MORE than the books hold** -- money
        the account has that nothing recorded put there, which is income the
        app never saw or a budgeted bill that never left.  Negative is the
        mirror: spend the app never recorded, or a payment that moved earlier
        than it was budgeted to.  It is the same convention, in the same
        direction, as the true-up form's own preview
        (``accounts.difference.difference_verdict``), which is why the two
        share one verdict rather than each mapping a sign to a meaning.

        **The SIGN is shared and the FIGURE is not**, and the two surfaces
        render on one page, so the distinction is load-bearing rather than
        pedantic: that preview measures against
        :func:`~._cash_flow.records_balance_at`, the running balance just
        before a day's assertion, which already contains every EARLIER
        assertion's reset; this measures against the records alone.  They
        differ by the net of every prior correction -- ``$0.00`` here against
        ``-$1,000.00`` there on the fixture
        ``TestItIsOneFigureAndNotThePerAssertionPlug`` builds -- so each
        surface writes its own sentences.  Publishing two money figures under
        one vocabulary is the defect this arc has now measured three times.

        ``0.00`` is the state this whole arc is aiming at, and it is a real
        answer rather than an empty one: every movement the owner's bank showed
        is recorded, so their declaration and their books say the same thing.

        Returns:
            ``asserted - books``, exact to the cent (both sides are
            cent-quantized before they reach this).
        """
        return self.asserted - self.books


def cash_outstanding_difference(
    account: Account, ctx: BalanceContext,
) -> "CashOutstandingDifference | None":
    """Return what *account*'s books cannot explain, or ``None``.

    Plan step **X-f3c-3**.  Reads only -- no writes, no commit, and no money
    moves: the figure is a subtraction over facts the fold has already loaded.

    **The latest assertion is read off the walk's ORDER, never re-derived.**
    :func:`app.services.cash_ledger.cash_anchor_facts` loads its rows
    ``(observed_on, created_at, id)`` ascending -- BUSINESS date first -- so the
    LAST fact is the one :func:`app.services.cash_ledger.resolve_anchor` calls
    current, which is exactly what
    :attr:`~app.services.cash_ledger.CashLedgerWalk.reconciled_through` reads
    and for the same reason.  A ``max()`` here would be a second statement of
    that order, and the two agreed for free only while ``observed_on`` was
    derived from ``created_at``; plan step 2 made the column user-supplied and
    broke that.

    **The books side is the fold's own prefix sum, not a fresh subtraction.**
    :func:`_books_balance_at` seeds at the assembly's stored opening equity and
    samples the leaf's :func:`~app.services.cash_ledger.dated_deltas` through
    the shared :func:`~._fold.sample_cumulative` -- the same re-key the posting
    writer consumes and the same sampling core every other balance in this
    package is read off.  Summing ``walk.source_facts`` here instead would be a
    third statement of "which day does this event count from, and for how
    much", which is precisely how the fold and the posted ledger drift apart.

    **Cross-checked against a producer that shares no code with it.**  On a
    clone of the dev database migrated to head, 2026-09-01, Checking's figure
    is ``$2,370.02`` -- and the persisted double-entry ledger, written by
    ``account_posting_service`` and read by SQL that touches this package at no
    point, carries exactly ``$2,370.02`` of ``account_trueup`` postings on that
    account's linked ledger account with ``-$2,370.02`` against
    ``Checking -- Opening`` equity.  That equity leg is finding **N-171**, and
    it is what plan step X-f3c-4 moves onto the income statement.

    **THREE figures name that leg across the corpus and they do not contradict
    each other -- they are one quantity measured on three dates.**
    Finding N-171's card says ``$15,065.08`` gross / ``-$1,495.10`` net
    "over four months"; X-f3c-4's card says ``$1,776.88``;
    this says ``$2,370.02`` on 2026-09-01.  The plug is the NET of every
    assertion's correction, so it moves every time the owner declares a
    balance -- which is why each of the three needs its date carried beside it,
    and why X-f3c-4 must RE-DERIVE rather than quote any of them.

    Args:
        account: The account to measure.  Must belong to ``ctx.user_id``, which
            :func:`~._cash_fold.assembled_fold` REFUSES rather than trusts.
            Must be attached to ``db.session``.  Its KIND is consulted once,
            for the scope below.
        ctx: The read pass's :class:`~._context.BalanceContext`.  Its scenario
            scopes the postings the books side folds; assertions and the
            opening are per-ACCOUNT and are the same in every scenario.  Its
            ``as_of`` decides nothing here -- every assertion has already
            happened (:func:`app.services.anchor_service.resolve_observation_day`
            refuses a future day at both write doors), so there is no clock to
            bound them by.

    Returns:
        The :class:`CashOutstandingDifference`, or ``None`` in the two cases
        where the question does not apply: an account whose balance carries a
        MODELLED tier (see the module docstring -- ruling **R-FO**, finding
        **N-213**), and an account carrying no assertion at all, which has
        declared nothing for its books to disagree with.  The second is
        production-unreachable, and what makes it so TODAY is
        ``account_service.create_account``, which writes an origination
        assertion and CHECKS that it landed; migration ``cfb15e782f86``
        backfilled the accounts that predated the rule and its own header
        records that its data path is now permanently unreachable, so it
        CLOSED the gap rather than holding it closed.  Answered rather than
        raised, exactly as the walk answers it.

    Raises:
        BaselineMissingError: When *ctx* carries no baseline scenario.
        ForeignAccountError: When *account* belongs to another owner.
        PayCalendarError: When the owner's paydays cannot define a calendar,
            and :exc:`RuntimeError` when a planned row names a pay period that
            calendar does not hold.  **Neither is this function's own**: both
            come out of :func:`~._cash_fold.assembled_fold`, which is the door
            it takes the walk through, and they are listed because a caller
            reading only this signature would not know it assembles a whole
            fold to read three fields off it.
    """
    _require_scenario(ctx)
    if classify_account(account) is not AccountProjectionKind.PLAIN:
        return None
    folded = _cash_fold.assembled_fold(account, ctx)
    if not folded.walk.anchor_facts:
        return None
    # The GOVERNING assertion: the loader's order makes it the last fact, and
    # this reads that order rather than restating it (see above).
    governing = folded.walk.anchor_facts[-1]
    return CashOutstandingDifference(
        opened_on=folded.walk.opening.opened_on,
        opening_equity=round_money(folded.walk.opening.opening_equity),
        asserted_on=governing.observed_on,
        asserted=round_money(governing.anchor_balance),
        books=_books_balance_at(folded, governing.observed_on),
    )


def _books_balance_at(
    folded: "_cash_fold.AssembledCashFold", day: date,
) -> Decimal:
    """Return what *folded*'s account's BOOKS produce at *day*.

    ``opening equity + SUM(postings <= day)``, and every word of that is plan
    step **X-f3c-5**'s balance function for a PLAIN account -- built here one
    step early so the difference it leaves can be shown before the flip lands.

    **Three tiers go in and only ONE comes out, which is what makes it
    different from** :func:`~._cash_fold.balances_at`.  That reading samples
    the seed, the recorded steps, the assertion RESETS and the still-projected
    plan; this one samples the seed and the recorded steps alone.  Dropping the
    resets is the whole point (ruling **R-FN**: an assertion is a CHECK, and
    this is what it is checked against).  Dropping the PLAN is the definition
    rather than an optimisation: the BOOKS are what the account has RECORDED,
    and what it is projected to hold is a different question this figure must
    not answer.

    *A first draft justified the omission by arguing it changed nothing -- that
    ruling R-G clamps a still-projected row to ``as_of + 1`` and an assertion's
    day has already happened.  That is true only for a pass reading at TODAY.*
    :meth:`~._context.BalanceContext.build` *takes an ``as_of``, and a pass
    pinned to a past one clamps the plan to a day that can precede the latest
    assertion -- so the two readings would then differ, and an argument that
    the choice is free would be false exactly where it was load-bearing.  The
    choice is not free; it is correct.*

    **It calls** :func:`~app.services.cash_ledger.dated_deltas` **a second time
    for this pass, and that is deliberate rather than overlooked.**  The
    assembly already merged those steps with the assertion corrections into one
    sorted list (:func:`~._cash_fold._actual_steps`), so there is no recorded
    tier left on the record to read back; the alternatives are a tenth field on
    :class:`~._cash_fold.AssembledCashFold` -- which already carries a
    ``too-many-instance-attributes`` disable -- or summing ``source_facts``
    here, which would be a second statement of the one clock.  ``dated_deltas``
    is a PURE function of the walk, so calling it twice cannot produce two
    answers; what it costs is one list build and one sort over the account's
    settled rows (354 on the real Checking account).

    Args:
        folded: The account's :class:`~._cash_fold.AssembledCashFold`.
        day: The civil day to value the books at.

    Returns:
        The cent-quantized ``Decimal``.  A day before every posting reads the
        opening equity flat -- the honest fold of an empty prefix, and the same
        totality every other reading in this package has.
    """
    return sample_cumulative(
        folded.seed, dated_deltas(folded.walk), [day],
    )[day]
