"""
Shekel Budget App -- the balance seam's READ-PASS resolution context.

The value object that makes "one loan resolution, at one pinned as-of" a
STRUCTURAL property of a read pass rather than a discipline nobody could
enforce.

**Why this exists.**  A loan's resolved state was not a first-class value in
this architecture.  Every surface that wanted any part of a loan -- its
balance, its schedule, its payoff date, its payment feed -- re-derived the whole
thing from scratch, off a clock each producer read for itself
(``date.today()``, called in 12+ places in one ``/savings`` render).  Measured
2026-07-13 on real data: ONE ``compute_dashboard_data`` call ran the loan
resolver **eleven times for two loans** -- the balance maps, the trend window's
honest-history gate, the liability band, the loan tile, the property-equity
card, and an "ever paid off" probe each resolving independently
(``docs/audits/balance_architecture/followup_redundant_loan_resolution.md``).

That was filed as waste.  It was not only waste.  Because there was no single
resolution to compare against, nothing revealed that one of those eleven -- the
``date.max`` "ever paid off" probe -- resolved through a producer that
*structurally cannot read the genesis ledger* (the confirmed view returned
``None`` for any ``as_of`` after today until plan step recurrence:R16-e), and so
answered from the pre-read-switch anchor replay, which was blind to how much cash
a payment actually moved.  The
ten that agreed made the eleventh invisible.  **Redundant derivation is not a
performance smell; it is where a divergence hides.**

**The contract.**  A context is built ONCE per read pass, carries the pinned
``as_of`` and baseline scenario, and lazily memoizes each loan's
:class:`~app.services.balance_at._resolution.ResolvedLoan`.  Every consumer then reads
one resolution, so the loan tile's balance, the net-worth hero, the liability
band, and the debt card are identical BY CONSTRUCTION rather than by the luck of
four producers agreeing.

**Read pass, not request.**  This is deliberately NOT a request-scoped cache
(``flask.g``), for two reasons.  It would break the Flask-free service boundary
(``CLAUDE.md``), and -- the load-bearing one -- it would go STALE: the loan write
paths (``loan_recurrence_sync``, the transfer posting sync) resolve loans in the
middle of a mutation, and a request that writes and then re-renders must see the
post-write loan.  A context is a plain value the caller constructs, so a write
path simply builds a fresh one after its write.  There is no cache-invalidation
class of bug here because there is no cache -- only a memo whose lifetime is the
one read it was built for.

**Two dates, and they are not the same date.**  ``ctx.as_of`` is the resolver's
NOW: the date that decides what is confirmed and what the loan currently owes.
The ``as_of`` argument of :func:`app.services.balance_at.balance_at` is the
VALUATION date: the date to value the account AT, which may be in the past (the
genesis ledger answers) or the future (the schedule projects).  They were
conflated while "now" was an implicit ``date.today()`` inside each producer;
separating them is what lets a caller value an account at any date without the
loan silently resolving at a different one.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

from app.exceptions import ForeignAccountError
from app.models.account import Account
from app.models.scenario import Scenario
from app.services.cash_ledger import AmountBasis, amount_basis
from app.services.income_service import PaycheckPricing, paycheck_pricing
from app.services.loan_ledger import (
    LoanCalendars,
    LoanLedgerWalk,
    load_loan_stream,
    replay_loan_stream,
)
from app.services.pay_calendar import PayCalendar, PeriodWindow, calendar_for
from app.services.recurrence import (
    BooksWalk,
    RecurrenceSpec,
    ResolvedRecurrence,
)
from app.services.scenario_resolver import get_baseline_scenario

from ._definition_books import DefinitionBooks
from ._memoize import _memoize_once, require_scenario
from ._recurrence_memos import RecurrenceMemosMixin

if TYPE_CHECKING:
    # Type-only: all three RECORD types below are defined by seam SIBLINGS that
    # import THIS module at runtime.  They type the caches the seam FILLS (this
    # module never builds a plan, never resolves a loan and never assembles a
    # cash fold), so they carry NO runtime edge back -- the sibling cycle a
    # runtime import would close (finding N-25) stays open.
    from ._cash_fold import AssembledCashFold
    from ._plan import LoanForwardPlan
    from ._resolution import ResolvedLoan

@dataclass(frozen=True)
class BalanceContext(RecurrenceMemosMixin):  # pylint: disable=too-many-instance-attributes
    """One read pass's pinned as-of, scenario, and memoized derivations.

    Pylint: ``too-many-instance-attributes`` (16/7) -- suppressed because the
    sixteen ARE one read pass's state and there is no smaller cohesive object
    inside them: three PINS (``user_id`` / ``scenario`` / ``as_of``) and thirteen
    MEMOS, each keyed by the thing it is a derivation of.  Bundling the memos
    behind a nested record would put an access level in front of state the
    seam fills from five different modules while creating a second object with
    no behaviour of its own.  It reached 8 at plan step C2-c, when the pay
    calendar became a pass-level derivation instead of an argument every caller
    passed by hand, 9 at X-au-c2b (the amount basis), 10 at **X-i4** (the
    cash fold), 11 at balance:X-au-d (the paycheck pricing), 12 at
    recurrence:**R16-b-2** (a rule's resolution), 13 at
    recurrence:**R7d-f-2** (a resolved recurrence's occurrence walk), 14 at
    recurrence:**R16-c-1** (the loan's timeline), 15 at
    pay_calendar:**C18-a** (each account's books floor) and 16 at
    recurrence:**R25** (the loan calendars its walk and its pricer share);
    plan step
    **X-i1** raises it further, because that step's remaining inputs (the contribution feed, the
    standing extra, the contractual schedule) are memos of exactly this kind.
    The count is a property of what a read pass IS rather than a threshold
    this class is drifting past.  *The figure read ``(8/7)`` and "five MEMOS"
    until X-i4, ``(10/7)`` and "seven" until R16-b-2's adversarial review, and
    ``(14/7)`` and "eleven" until ruling R-BAL146's review counted R16-c-1's
    ``_timelines``: each time a memo had joined without it being updated, which is the class
    of claim this file's own ``scenario_id`` docstring already warns about.*

    Frozen: the pinned inputs (``user_id`` / ``scenario`` / ``as_of``) cannot be
    reassigned mid-pass, which is the whole point -- a producer that could move
    the as-of under its consumers would reintroduce the multi-clock problem this
    object exists to kill.  The memo caches are mutable dicts held BY the frozen
    object (excluded from ``eq`` / ``repr``): derived state, not identity, so two
    contexts with the same pins are equal whether or not either has resolved a
    loan yet.

    **SIX derivations this class owns, three it stores in PUBLIC caches, and
    ONE in a PRIVATE one.**  The WALK (:meth:`loan_walk`), the CALENDAR
    (:meth:`calendar`), the AMOUNT BASIS (:meth:`amounts`), the PAYCHECK
    PRICING (:meth:`paychecks`), a rule's RESOLUTION
    (:meth:`resolved_recurrence_of`) and a resolved recurrence's OCCURRENCE
    WALK (:meth:`placements_of`) derive from modules BELOW the two that
    define this class, which import them outright, so all six stay private,
    filled by its own methods -- the last two inherited from
    :class:`~._recurrence_memos.RecurrenceMemosMixin`, split out of this
    module by ruling **R-BAL146**.  *The count read "two" and named only the first two
    until plan step X-i4, having missed ``amounts`` when X-au-c2b added it, and
    "three" until R16-b-2's review, having missed ``paychecks`` -- the same
    omission the attribute-count note above records, one sentence over.*  The RESOLUTION, PLAN and
    PAYOFF caches (:attr:`loans` /
    :attr:`plans` / :attr:`payoffs`) are derived in the
    ``balance_at`` seam modules ABOVE it (``_resolution`` / ``_plan`` /
    ``_positions``, which import THIS
    module); the context cannot import them back to compute them without inverting
    the dependency arrow and closing a real import cycle (finding N-25), so those
    caches are PUBLIC pass-through state the seam FILLS through
    :func:`~._memoize._memoize_once`.  Plan step D-ctx-b retired the earlier design that
    INJECTED the builder into a context method: no builder crosses into the context
    now -- the seam owns the derivation, the context owns the storage.  Plan step
    E1d-a moved the RESOLUTION under that same rule (it was a context METHOD, the
    one surface W9910 cannot see -- finding H1 of step D3's review), which is why
    ``loans`` is a cache here rather than a ``resolved_loan`` method.

    **Every account-keyed cache above is filled through ONE primitive, and that
    is where this pass BINDS the account it values** (plan step **X-i4**,
    finding **N-354**).  :func:`~._memoize._memoize_once` takes the ``account`` rather than
    a bare id and refuses one whose ``user_id`` is not this pass's, so the
    pairing the seam used to state as two independent arguments -- an account
    here, ``ctx.amounts()`` / ``ctx.as_of`` / ``ctx.calendar()`` there, agreeing
    only because every call site happened to name one ``ctx`` -- cannot be
    stated wrongly.  It is a precondition on the one constructor of per-account
    pass state, not a guard repeated at each funnel: there is no way to memoize
    a derivation against this object without going through it, and
    :meth:`loan_walk` open-coded its own store-once lines until X-i4 routed it
    here too.  What it is NOT is a second ownership gate -- see
    :class:`~app.exceptions.ForeignAccountError` for why no upstream gate can
    answer this question at all.  **One per-loan memo is not filled through
    it**: ``_loan_calendars`` (plan step recurrence:R25), the
    :class:`~app.services.loan_ledger.LoanCalendars` the walk and the amount
    basis's loan pricer share, keyed by a bare loan id as the pricer's own memo
    was before it.  It holds contract terms and no balance, it has no public
    accessor, the walk reaches it only after
    :func:`~._memoize._memoize_once` has bound the account, and the pricer
    reaches it for a row the amount model has already checked against this
    pass's scenario (``cash_ledger._amount_source``).

    Exposing THOSE THREE caches hands out no balance the fence must guard: a plan
    is payment RECORDS, a payoff is a ``date``, and a
    :class:`~app.services.balance_at._resolution.ResolvedLoan` carries schedule
    detail and NO balance-at-T (its ``current_balance`` was deleted at the root by
    plan step D2a -- the reason step D3 un-fenced the memo in the first place).
    **That test is what decides which caches are public, and plan step X-i4's
    first build failed it**: it added the CASH FOLD as a fourth PUBLIC cache
    without re-reading this paragraph, and an
    :class:`~app.services.balance_at._cash_fold.AssembledCashFold` carries
    ``seed`` and ``steps`` -- a running total, so a prefix sum over it
    reproduces the seam's own scalar exactly.  Two adversarial reviews each
    measured that bypass independently.  It is ``_cash_folds`` now, filled
    through the one crossing in
    :func:`~app.services.balance_at._cash_fold.assembled_fold`, and the rule
    this paragraph states is the reason rather than a layering accident.

    Nor does exposing them let a consumer FORGE one.  A context is a plain value
    its caller constructs and hands to the seam; writing a fabricated bundle into
    ``loans`` is the caller lying to itself for the length of one read, not
    reaching past a boundary -- the same standing the caller already has by
    passing whatever ``as_of`` and ``scenario`` it likes.  The gate that matters
    is on the DERIVATION (every producer private to this package, W9910), not on
    the dict a pass carries its own answers in.

    Attributes:
        user_id: The owning user.  Every account a context resolves must belong
            to them, and since plan step **X-i4** that is REFUSED rather than
            trusted -- at :func:`~._memoize._memoize_once` for the account, and at
            :meth:`__post_init__` for the ``scenario`` beside it.  *This entry
            read "the caller owns that check (the loaders trust it)" until
            X-i4.*
        scenario: The baseline scenario, or ``None`` for a user with no baseline
            (the degraded state: a loan then resolves from its latest assertion
            with no history rows, and the seam's cash paths cannot run at all -- see
            :func:`~._memoize.require_scenario`).
        as_of: The resolver's NOW for this pass -- the date each loan is
            RESOLVED at.  Not the date an account is VALUED at (see the module
            docstring).
        loans: The read pass's per-loan resolution cache, keyed by ``account.id``
            and FILLED by the seam's
            :func:`~app.services.balance_at._resolution.resolved_loan` (this module
            never resolves a loan).  A ``None`` value is a MEMOIZED "not a
            configured loan", not an empty slot.
        plans: The read pass's per-loan forward-payment-plan cache, keyed by
            ``account.id`` and FILLED by the seam's
            :func:`~app.services.balance_at._plan.memoized_plan` (this module
            never builds a plan).
        payoffs: The read pass's per-loan derived-payoff cache, keyed by
            ``account.id`` and FILLED by the seam's
            :func:`~app.services.balance_at._positions.memoized_payoff`.
        _timelines: The pass's per-loan TIMELINE memo (plan step
            recurrence:R16-c-1), keyed by ``account.id`` and FILLED by the seam's
            :func:`~app.services.balance_at._loan_stream.loan_timeline`: the
            loan's facts walk with its forward plan replayed behind it, one
            :class:`~app.services.loan_ledger.LoanLedgerWalk` for the whole
            timeline.  PRIVATE for the reason ``_cash_folds`` is (below): a
            merged walk carries balance-at-T, and the seam module that derives
            it makes the one named crossing.
        _cash_folds: The pass's per-account cash-fold memo, keyed by
            ``account.id`` and filled by :meth:`cash_fold`.  **PRIVATE, and not
            for the reason ``_walks`` is** (that one is private because this
            module owns its derivation): an
            :class:`~app.services.balance_at._cash_fold.AssembledCashFold`
            carries ``seed`` and ``steps``, which ARE a balance-at-T -- five
            lines of prefix sum over them reproduce
            :func:`~app.services.balance_at.cash_balance_at`'s answer exactly.
            The three PUBLIC caches beside it are public because they carry no
            such thing, which is the argument the paragraph above makes and
            which this one would have falsified.  Two adversarial reviews found
            it public in X-i4's first build and each measured the bypass: a
            consumer importing nothing private, holding only the re-exported
            :class:`BalanceContext`, read a balance the W9910 fence exists to
            make unreachable -- and W9910 sees IMPORTS, ``protected-access``
            sees underscores, so a public dataclass FIELD passed every gate.
        _calendars: The pass's pay-calendar memo, keyed by ``user_id`` and
            filled by :meth:`calendar` -- private for the reason ``_walks`` is,
            because this module owns the derivation rather than storing a
            sibling's.
        _amount_bases: The pass's amount-model memo, keyed by ``scenario_id``
            and filled by :meth:`amounts`.  Private for the same reason.
        _loan_calendars: The loan calendars the pass's walk and its pricer
            share (:class:`~app.services.loan_ledger.LoanCalendars`, plan step
            recurrence:R25): each loan's params and contract calendar, loaded
            at most once and read by BOTH :meth:`loan_walk` and the loan
            pricer inside :meth:`amounts`, so a loan's charges and its
            payments' price read one calendar (ruling **R-R105**).  It is not
            the pass's only home for a loan's terms: the loan resolver's
            bundle loads them again (finding **REC-559**, owned by plan step
            recurrence:R16-f).  Private, with no accessor, for the binding
            reason the paragraph on :func:`~._memoize._memoize_once` above
            gives.
        _paycheck_pricing: The pass's PAYCHECK PRICER, keyed by ``user_id``
            and filled by :meth:`paychecks` (plan step **salary:S3-d**).

            **It replaced a public ``payroll_breakdowns`` dict, and the change
            of shape is the fix.**  That field was a raw
            ``{profile_id: {payday: PaycheckBreakdown}}`` memo that
            :func:`~app.services.projection_inputs.load_payroll_feeds` FILLED
            and that :class:`~app.services.income_service.SalaryPricing` did
            not read.  It was also OPTIONAL at that loader
            (``breakdowns=None`` meant "no memo"), which is how two of its
            four callers came to bypass it inside the step that added it.  A
            :class:`~app.services.income_service.PaycheckPricing` closes the
            second hole outright -- a consumer is handed one, so there is no
            argument to omit -- and memoizes per profile AND per payday, so
            two consumers asking overlapping spans pay for the union.

            **It closed the first at plan step salary:C12** (ledger row
            P63): :class:`~app.services.income_service.SalaryPricing` derived
            a pricer of its own until :meth:`amounts` built the basis over
            this one; the argument is at that class.

            **It exists because a paycheck is expensive and the seam asks for
            one per ACCOUNT.**  ``_contribution_inputs_for_account`` is the
            batch loader over a one-element set, so four seam entries calling
            it once per account re-ran the engine over the owner's WHOLE saved
            window each time: measured at 61 ``calculate_paycheck`` calls on a
            3-account, 10-period fixture against ~7 before ``salary:R14-b``,
            the multiplier being exactly the saved-period count.  The calendar
            memo two fields up exists for the same shape one tier cheaper.

            **PRIVATE, where its predecessor was public**, and the argument
            that made that one public has expired rather than been overruled:
            it was exposed because two callers had to be handed the dict, and
            nothing is handed a dict now -- :meth:`paychecks` is the accessor,
            exactly as :meth:`calendar` and :meth:`amounts` are for the two
            memos above.
        _recurrences: The pass's rule-resolution memo, keyed by the rule's
            SPEC and its definition's BOOKS (what it authors, where its
            accounts' books open and whether it is an envelope, not which row
            it is; :meth:`resolved_for`).  Private
            because this class owns the derivation (its mixin's module imports
            the pure resolver, a leaf below the
            seam), and a ``None`` value is a MEMOIZED "the owner has no pay
            periods", not an empty slot.
        _placements: The pass's occurrence-walk memo, keyed by the COMPOSED
            resolved recurrence the walk is a function of (see
            :meth:`placements_of`).  Private for the reason ``_recurrences``
            is; every stored value is a ``BooksWalk``, and empty halves are a
            legitimate answer (a definition its destination closed before it
            ever fires), so membership rather than truthiness is the test.
        _books_opened_on: ``account_id -> the day its books start`` (plan
            step ``pay_calendar:C18-a``): the books floor :meth:`resolved_for`
            attaches, one read per account per pass; ``None`` is a memoized
            "they start nowhere".  Keyed by rows, so stale after a restatement
            inside the pass -- the convention :meth:`resolved_recurrence_of`
            states for its row-keyed siblings.
    """

    user_id: int
    scenario: Scenario | None
    as_of: date
    _walks: dict[int, LoanLedgerWalk] = field(
        default_factory=dict, repr=False, compare=False,
    )
    loans: "dict[int, ResolvedLoan | None]" = field(
        default_factory=dict, repr=False, compare=False,
    )
    plans: "dict[int, LoanForwardPlan]" = field(
        default_factory=dict, repr=False, compare=False,
    )
    payoffs: "dict[int, date | None]" = field(
        default_factory=dict, repr=False, compare=False,
    )
    _timelines: dict[int, LoanLedgerWalk] = field(
        default_factory=dict, repr=False, compare=False,
    )
    _cash_folds: "dict[int, AssembledCashFold]" = field(
        default_factory=dict, repr=False, compare=False,
    )
    _calendars: "dict[int, PayCalendar]" = field(
        default_factory=dict, repr=False, compare=False,
    )
    _amount_bases: "dict[int, AmountBasis]" = field(
        default_factory=dict, repr=False, compare=False,
    )
    _loan_calendars: LoanCalendars = field(
        default_factory=LoanCalendars, repr=False, compare=False,
    )
    _recurrences: (
        "dict[tuple[RecurrenceSpec, DefinitionBooks], ResolvedRecurrence | None]"
    ) = field(default_factory=dict, repr=False, compare=False)
    _placements: "dict[ResolvedRecurrence, BooksWalk]" = field(
        default_factory=dict, repr=False, compare=False,
    )
    _paycheck_pricing: "dict[int, PaycheckPricing]" = field(
        default_factory=dict, repr=False, compare=False,
    )
    _books_opened_on: "dict[int, date | None]" = field(
        default_factory=dict, repr=False, compare=False,
    )

    def __post_init__(self) -> None:
        """Refuse a pass whose scenario belongs to a different owner.

        **The other half of X-i4's binding, and finding N-354's own sentence one
        field over.**  That row says ``BalanceContext`` "pins a ``user_id`` and
        never checks it against the account handed alongside"; it pinned three
        things and checked none of them against each other.  An adversarial
        review measured the gap: a pass carrying owner 1's ``user_id`` and owner
        2's :class:`~app.models.scenario.Scenario` answered
        ``cash_balance_at`` a real figure and nothing refused it -- the scenario
        is what scopes every row the fold loads, so the pass would report one
        owner's account under another's budget.

        It is checked HERE rather than in :meth:`build`, which resolves the
        baseline itself and cannot get it wrong, because the constructor is
        public, frozen and directly called: ``tests/_test_helpers`` builds one
        for the pay-calendar memo and two loan-sync suites build one by hand.
        A ``__post_init__`` covers every construction path there is, which
        :meth:`build` alone does not -- the same reason
        :func:`~._memoize._memoize_once` holds the account rule rather than each funnel.

        A ``None`` scenario is legal and unchecked: it is the DEGRADED state
        :func:`~._memoize.require_scenario` names, not a foreign one.

        Raises:
            ForeignAccountError: When ``scenario`` belongs to another owner.
        """
        if self.scenario is not None and self.scenario.user_id != self.user_id:
            raise ForeignAccountError(
                f"read pass for user {self.user_id} was built with scenario "
                f"{self.scenario.id}, which belongs to user "
                f"{self.scenario.user_id}. The scenario scopes every row this "
                f"pass folds, so the two must name one owner: build the pass "
                f"through BalanceContext.build, which resolves the owner's own "
                f"baseline"
            )

    @classmethod
    def build(
        cls, user_id: int, as_of: date | None = None,
    ) -> "BalanceContext":
        """Build a context for *user_id*, resolving the baseline scenario once.

        The constructor a route or a top-level producer uses: it performs the
        single baseline-scenario lookup the whole read pass then shares, so the
        scenario is not re-resolved by every producer that needs it.

        Args:
            user_id: The owning user.
            as_of: The resolver's NOW.  Defaults to ``date.today()``, which is
                the basis every existing caller used and which
                :func:`app.utils.dates.display_today` documents as the
                resolver's replay boundary (storage and the replay stay UTC; the
                display timezone is a presentation concern).  Pass an explicit
                date to value a pass at another moment -- a historical read, or
                a caller whose civil window is display-timezone-bound (the
                analytics Taxes tab), which previously supplied a date that
                ``generate_debt_schedules`` silently discarded.

        Returns:
            The :class:`BalanceContext` for this read pass.
        """
        return cls(
            user_id=user_id,
            scenario=get_baseline_scenario(user_id),
            as_of=as_of if as_of is not None else date.today(),
        )

    @property
    def scenario_id(self) -> int:
        """The baseline scenario's id -- the form the loaders and resolver take.

        **It RAISES rather than returning ``None``** (plan step X-v2, ruling
        R-BX), which is what keeps the nullable from escaping this object.
        Many callers read it (this said seventeen, which a grep at plan task
        plan#3388 found stale at twenty-six), and the ones that make the raise
        load-bearing are those that SCOPE A QUERY with it rather than read a
        balance -- the grid's transaction load, both calendar entries, the cash
        detail's anchor resolve, the tax report's profile load, the dashboard
        pulse's unpaid-bill query, the emergency-fund history.  The loan
        route's payment-context load was one until plan task plan#3388 deleted
        the payment list it scoped.  A nullable id reaching any of them is a query
        silently scoped to nothing (which reads exactly like an empty account)
        or an ``AttributeError`` on ``None``, which is a failure the
        application's :class:`~app.exceptions.BaselineMissingError` handler
        cannot answer because it does not wear that name.  One accessor makes
        every dereference, seam read or query scope, fail the same named way at
        its first use.

        **The nullable itself is still :attr:`scenario`, read by the callers
        ruling R-BY carves out**, each documenting why at its own guard:
        :func:`app.services.balance_at.liability_owed_at_dates` (a missing
        baseline is the degenerate case of its own rule).  The second carve-out
        -- the closing-bound writer ``sync_recurring_payment_bounds``, where
        raising would have rolled back the user's edit -- ended at plan step
        R7d-g: the bound it wrote is derived on every read now, and the
        opening-bound sync that survives builds no pass at all.  An earlier
        draft of this paragraph said ONE, and X-v2's adversarial design review
        counted four -- the writer named in :func:`~._memoize.require_scenario`'s docstring, an
        emergency-fund reducer that fabricated ``$0.00``, and a template
        context handed the Scenario ROW.  The last two are gone; a count in a
        docstring is a claim, and this arc has paid for that one before.

        Returns:
            The baseline scenario's id.

        Raises:
            BaselineMissingError: When this pass has no baseline scenario.
        """
        require_scenario(self)
        return self.scenario.id

    @property
    def scenario_id_or_none(self) -> int | None:
        """The baseline scenario's id, or ``None`` -- for a rule that HAS an answer.

        The verbose sibling of :attr:`scenario_id`, and the naming is the point
        (plan step X-v2, ruling R-BX): the obvious name is the one that fails
        loud, and reaching for the nullable is a deliberate act that reads as
        one at the call site.

        **THREE callers, and the count read "exactly two, both inside the
        seam" until plan step X-au-g-2c re-took it.**  The third
        (``retirement_projection``, below) has read this since `731f6b3c`,
        2026-08-16, so the claim was false for a fortnight -- which is the
        failure this very docstring warns about two paragraphs down, committed
        in the paragraph that warns about it.  A count is a claim; re-grep it
        rather than carrying it.

        Two of the three are inside the seam, and both because a missing
        baseline is the degenerate case of their own rule rather than an error:

        * :meth:`amounts_or_none` -- the nullable form of this pass's amount
          basis.  Its one caller, since plan step salary:X-av-2, is the
          Recurring surface's salary row
          (``obligations_aggregator._todays_paycheck``; see that method).
          ``resolve_loan_bundle`` read THIS accessor directly until plan step
          X-au-g-2c and that method from then until plan task plan#3388, for
          one reason: a loan's payment list was the ONE scenario-scoped input
          to its resolution, while its params, anchors and rate history are
          contract facts, so with no baseline the list was empty and the
          CONTRACT terms still resolved (plan step C8e's rule).  That task
          deleted the list (ruling R-R93), and the loan's resolution reads no
          amount basis at all now.
        * :func:`._confirmed_view.confirmed_view` -- the confirmed ledger view
          is scenario-scoped by construction, so with no baseline there is no
          view and the resolver seeds the contract's remaining installments
          from the loan's latest assertion.

        The third is OUTSIDE the seam and takes the nullable for a different
        reason, which is why it is listed apart rather than folded into the
        count: ``retirement_projection`` puts it in a MEMO KEY beside the
        owner and the as-of.  A cache key must be TOTAL over the states its
        pass can be in -- a key that raises for a no-baseline pass would turn a
        degraded read into a 500 at the memo rather than at a figure -- so the
        raise has nothing to protect there.  It scopes no query, which is the
        line the paragraph below draws.

        A third reader tests the nullable directly rather than its id:
        :func:`app.services.balance_at.liability_owed_at_dates`, the one seam
        entry with no :func:`~._memoize.require_scenario` at all.

        Anything else -- and in particular anything that SCOPES A QUERY with
        this id -- takes :attr:`scenario_id` and gets the raise, because a query
        scoped to ``NULL`` returns an empty result that reads exactly like an
        empty account.

        Returns:
            The baseline scenario's id, or ``None`` with no baseline.
        """
        return self.scenario.id if self.scenario is not None else None

    def loan_walk(self, account: Account) -> LoanLedgerWalk:
        """Return *account*'s walk of the facts VISIBLE to this pass, walking once.

        The memo that collapses a read pass's N folds of one loan to one WALK.
        The seam's confirmed view, its retired predicate, its paid-in-year
        figures and -- through the timeline that appends the forward plan to
        this walk's stream (:func:`~app.services.balance_at._loan_stream
        .loan_timeline`) -- its balance, payoff and projected interest all read
        it in a single ``/savings`` render.  The LOAD
        (:func:`~app.services.loan_ledger.load_loan_stream`) is the expensive
        part -- the loan's params, anchors, rate periods, escrow lines and
        settled shadows -- so re-loading per producer is exactly the redundant
        derivation the seam's resolution memo already removes.  The first call
        loads and replays; every later call in the same pass reads that same
        :class:`~app.services.loan_ledger.LoanLedgerWalk`.

        **It replays the facts VISIBLE by this pass's ``as_of``** (plan step
        recurrence:R16-c-1, ruling **R-R91**;
        :func:`~app.services.loan_ledger.load_loan_stream`'s ``visible_by``):
        a payment from its settled day, an assertion from its own date, the
        opening always.  That is the spec's ``as_of`` -- the mark where
        recorded fact becomes projection -- stated ONCE, at the load, so a
        pass pinned to an earlier day answers what the loan looked like on
        that day (plan step ``recurrence:R7d-h``: the pass decides which
        crossing answers), and every reader that needs the bounded set reads
        THIS walk's stream (the plan's latest assertion and settled payments
        included, ``_plan.loan_plan``) rather than deriving the bound again.  The
        ledger's own walk
        (:func:`~app.services.loan_ledger.walk_loan_ledger`) takes no bound and
        is not this memo.  For a pass whose ``as_of`` is on or after every
        recorded fact -- every production pass -- the two are the same walk.
        So this memo is a pure function of the loan and the pass's pinned
        ``scenario`` and ``as_of``, exactly like the resolver memo above.

        **Un-FENCED at plan step D3, the same ground as the resolution memo.**
        The walk is FACTS, not a balance-at-T (plan step D-fold), and the leaf's
        own :func:`~app.services.loan_ledger.walk_loan_ledger` is public and
        deliberately unfenced -- so this memo hands a consumer nothing it could
        not already obtain, and the fold that turns a walk into a balance is a
        seam-private module W9910 protects.  A consumer that wants a loan's
        balance takes :func:`app.services.balance_at.balance_at`.

        **It goes through :func:`~._memoize._memoize_once` since plan step X-i4**, where it
        open-coded the same three store-once lines before.  That was a fourth
        copy of the primitive whose own docstring says a copy is where two memos
        drift on the property they exist to guarantee -- and it was the one
        account-keyed cache on this object that the binding could not reach.

        Args:
            account: The loan account to walk.  Must belong to ``user_id``, and
                since plan step X-i4 that is REFUSED rather than trusted (see
                :func:`~._memoize._memoize_once`).  A non-loan / unconfigured
                account walks to an empty
                :class:`~app.services.loan_ledger.LoanLedgerWalk` (the leaf's own
                no-params contract), which the seam never reaches for -- it
                resolves the schedule first.

        Returns:
            The memoized :class:`~app.services.loan_ledger.LoanLedgerWalk` for
            this loan under the pass's scenario.

        Raises:
            ForeignAccountError: When *account* belongs to another owner.
            BaselineMissingError: When this pass has no baseline scenario --
                ``scenario_id`` scopes the walk.
        """
        return _memoize_once(
            self, self._walks, account,
            lambda: replay_loan_stream(load_loan_stream(
                account.id, self.scenario_id, self._loan_calendars,
                visible_by=self.as_of,
            )),
        )

    def calendar(self) -> PayCalendar:
        """Return the owner's pay calendar for this pass, deriving it once.

        The memo that collapses a read pass's N loads of one pay calendar to
        one.  Plan step **C2-c** put it here: every per-period entry the seam
        publishes needs a period's BOUNDS, those bounds are derived from the
        owner's paydays (``docs/plans/implementation_plan_pay_calendar.md``
        section 1), and a render that asks four of those entries would
        otherwise derive the same 62-payday calendar four times.

        **It is a memo on the PASS, not a cache**, for the reason the class
        docstring gives about the whole object: a write path that records a
        payday and then re-renders builds a fresh context, so there is no
        invalidation class of bug here -- only a memo whose lifetime is the one
        read it was built for.

        Keyed by ``user_id`` rather than held in a bare slot, and the honest
        reason is SHAPE rather than safety -- a first draft of this paragraph
        claimed the key made it impossible to serve one owner's calendar to
        another, and ``frozen=True`` on a dataclass carrying ``user_id`` as a
        field already makes that unreachable (an adversarial review of C2-c
        caught the over-claim).  What the key buys is that this memo reads like
        :meth:`loan_walk`'s beside it and needs no ``None`` sentinel to tell an
        unfilled slot from a legitimately empty answer, which an owner with no
        paydays gives.

        **The derivation is imported outright**, so unlike the three
        pass-through caches beside it this one is filled here: ``pay_calendar``
        is a leaf BELOW the seam (it imports ``pay_schedule_service`` and the
        models and nothing of ``balance_at``), so the arrow stays one-way and
        no import cycle is opened -- the same standing ``loan_ledger`` has
        above.

        Returns:
            The owner's :class:`~app.services.pay_calendar.PayCalendar`.
            **Empty is a legal answer** -- an owner who has never generated a
            schedule -- and the seam's per-period entries answer an empty map
            for it rather than refusing.

        Raises:
            PayCalendarError: The owner has paydays that cannot define a
                calendar.  **The route that reached this from a page is
                closed** (plan step C4-b-2, ledger rows **P8** / **P35**): it
                needed a cadence outside 1..365, which ``resolve_cadence``'s
                fallback could infer for an owner with no
                ``budget.pay_schedule`` row, and ``fk_pay_periods_schedule``
                makes that owner unstorable.  Declared still, because
                ``derive_periods`` refuses payday sets it cannot derive from.
                Loud rather than defaulted: every projected
                horizon is a function of the cadence, so an invented one
                reports a whole schedule the owner never chose.
        """
        if self.user_id not in self._calendars:
            self._calendars[self.user_id] = calendar_for(self.user_id)
        return self._calendars[self.user_id]

    def amounts(self) -> AmountBasis:
        """Return the pass's amount-model basis, building it once.

        The memo that makes "one pricing pass per read pass" structural, and
        plan step **X-au-c2b** put it here -- the override-map half of what plan
        step X-i1 names.  What a row's amount RESOLVES to is a derivation like
        any other on this object: the paycheck engine over the owner's whole
        pay-period set, and each destination loan's P&I, payment day and escrow
        history.  A render that asks four surfaces for a figure would otherwise
        derive all of that once per surface, which is findings **N-268** and
        **N-269** -- the dashboard pulse re-pricing rows the cash fold had just
        priced, and the transfer settle door re-querying the transfer it had
        just loaded.

        **Nothing is resolved until something asks**, so a pass that reads no
        cash figure pays nothing for holding one: both derivations behind
        :class:`~app.services.cash_ledger.AmountBasis` are lazy, each answers
        ``None`` from a row's own columns before it touches them, and the
        pricer this is built over (:meth:`paychecks`) derives the calendar
        only when a paycheck is priced.

        **It pins no as-of, and since plan step X-au-g-2b there is nothing
        left for one to correct.**  The basis read ``date.today()`` for the
        loan half rather than this pass's :attr:`as_of` -- finding **N-40** --
        and the remedy was expected to be plan step **X-i2**, handing every
        memoized loader this pass's clock.  Ruling **R-IJ** closed it a tier
        DOWN instead: a loan's contractual terms resolve on the installment
        they govern, so the derivation takes no date at all and ``cash_ledger``
        makes no clock call anywhere (``test_amount_source
        .TestTheAmountModelReadsNoClock``).  X-i2 keeps every other loader.

        **The derivation is imported outright**, so like :meth:`calendar` beside
        it this memo is filled here rather than by the seam: ``cash_ledger`` is
        a leaf BELOW the seam (it imports no ``balance_at`` module at all --
        its own docstring states the arrow), so filling it here opens no cycle.

        Returns:
            The pass's :class:`~app.services.cash_ledger.AmountBasis`.

        Raises:
            BaselineMissingError: When this pass has no baseline scenario.  A
                row's amount rule resolves against a scenario -- which profile
                prices a paycheck, which loan a payment derives from -- so there
                is no scenario-free answer to substitute.
        """
        scenario_id = self.scenario_id
        if scenario_id not in self._amount_bases:
            # Built OVER this pass's pricer (plan step salary:C12, ledger row
            # P63): the amount model reads the paychecks every other producer
            # under this pass reads, rather than deriving a second set.
            self._amount_bases[scenario_id] = amount_basis(
                self.paychecks(), scenario_id, self._loan_calendars,
            )
        return self._amount_bases[scenario_id]

    def paychecks(self) -> PaycheckPricing:
        """Return the pass's paycheck pricer, building it once.

        **The pass's ONE source of a paycheck** (plan step **salary:S3-d**;
        the amount model's too since **salary:C12**): the payroll feeds take
        it (:func:`~app.services.projection_inputs.load_payroll_feeds`), so do
        the salary route renders, and :meth:`amounts` builds the basis over
        it -- it derived its own until C12, ledger row **P63**.

        **Nothing is resolved until something asks.**  The pricer holds no
        profile and issues no query until a caller names one, so a pass that
        prices no paycheck pays nothing for holding this.

        *Its per-payday memo means a caller CAN ask for a subset and pay for
        only that.*  The two salary routes ask for ``calendar.saved()``, the
        owner's whole saved schedule, because that is the domain each of them
        reports over; the payroll feeds ask for whichever period a consumer
        reads -- the balance seam's saved window, or a 40-year chart's axis
        -- one at a time, since plan step salary:S3-e-2 (ruling
        **R-SAL15**).  :meth:`~app.services.income_service.SalaryPricing
        .net_for` prices the ONE period a row names, through this pricer
        (it read its own until plan step salary:C12, ledger row **P63**).

        Keyed by ``user_id`` for the reason :meth:`calendar` is, and built
        over that same memo -- handed the METHOD, so the calendar is derived
        on the first paycheck priced, not here (plan step salary:C12) -- so a
        pass cannot hold one derivation's paydays and another's paychecks.

        Returns:
            The pass's :class:`~app.services.income_service.PaycheckPricing`.
        """
        if self.user_id not in self._paycheck_pricing:
            self._paycheck_pricing[self.user_id] = paycheck_pricing(
                self.user_id, self.calendar,
            )
        return self._paycheck_pricing[self.user_id]

    def amounts_or_none(self) -> "AmountBasis | None":
        """The pass's amount basis, or ``None`` -- for a rule that HAS an answer.

        The verbose sibling of :meth:`amounts`, and it is
        :attr:`scenario_id_or_none`'s companion in exactly the way ruling
        **R-BX** names: the obvious spelling is the one that FAILS LOUD, and
        reaching for the nullable is a deliberate act that reads as one at the
        call site.  It reads that accessor rather than :attr:`scenario` itself,
        so it adds no third reader of the nullable attribute ruling **R-BY**
        bounds to two.

        **One caller, and its own rule has an answer here.**  Since plan step
        salary:X-av-2 it is ``obligations_aggregator._todays_paycheck``: with
        no baseline no salary profile is in scope, so no definition is priced
        as a paycheck and every other definition is priced by its own series
        (ruling **R-SAL71**; a salary definition is refused one tier down
        since plan step X-bp, its stored copy being gone), where
        :meth:`amounts` would refuse the whole page for an owner missing a
        baseline -- which ruling **R-R30** does only when a definition pays
        into a configured loan.  :func:`._resolution.resolve_loan_bundle` was
        the other from plan step X-au-g-2c, pricing a loan's payment list --
        its one scenario-scoped input, empty with no baseline while the
        CONTRACT terms still resolved (plan step C8e's rule) -- until plan task
        plan#3388 deleted the list (ruling **R-R93**).

        Returns:
            The pass's :class:`~app.services.cash_ledger.AmountBasis`, or
            ``None`` when this pass has no baseline scenario.
        """
        if self.scenario_id_or_none is None:
            return None
        return self.amounts()

    def reported_periods(self) -> PeriodWindow:
        """Return the pay periods every per-period seam entry reports over.

        **The seam's reporting domain, stated ONCE** (plan step C2-c).  Before
        it, all thirteen per-period entries TOOK the domain as an argument, and
        all eight callers in ``app/`` filled that argument with the same value
        -- the owner's complete saved period set, read out of the table as ORM
        rows whose ``end_date`` and ``period_index`` are the two derived
        columns plan step C4-c dropped.  An argument every caller answers
        identically is not a contract; it is the one thing a caller can get
        wrong, and ``_cash_periods``' own predecessor measured that mistake at
        ``$150,000.00`` (a fold read against a window missing its own period).

        Asking it of the pass rather than passing it in also means the answer
        cannot differ BETWEEN entries in one render: the grid's balance row,
        its subtotal rows and the cockpit's net-worth column are the same
        periods with the same bounds by construction.

        The window itself is memoized ON THE CALENDAR
        (:meth:`~app.services.pay_calendar.PayCalendar.saved`) rather than
        here, which is where the derivation lives; a second memo on this
        object would have been a memo of a memo, and an adversarial review of
        C2-c called that correctly.

        Returns:
            The :class:`~app.services.pay_calendar.PeriodWindow` over every
            saved period, ``start_date`` ascending and contiguous.  Empty for
            an owner with no pay periods.

        Raises:
            PayCalendarError: See :meth:`calendar`.  A window whose SAVED
                periods do not cover an unbroken span raises here too, which
                needs an unsaved candidate payday between two saved ones --
                a calendar :func:`~app.services.pay_calendar.calendar_for`
                cannot build (it reads saved rows only).
        """
        return self.calendar().saved()
