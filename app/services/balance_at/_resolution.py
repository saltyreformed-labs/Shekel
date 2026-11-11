"""Balance-at-T seam -- the db-facing WHOLE-LOAN read that seeds the resolver.

Plan step **E1d-a** (``docs/audits/balance_architecture/README.md``).  The pure
:mod:`app.services.loan_resolver` takes plain data and returns a
:class:`~app.services.loan_resolver.LoanState`; this module is the db-facing entry
every surface (net worth, /savings tile, debt-strategy, home equity, the loan
route card, the equity chart) resolves a loan account through.  It loads the
loan's inputs ONCE, threads in the genesis-ledger confirmed view and the loan's
standing overpayment, and delegates -- so no surface can drift on HOW a loan is
resolved, and none can silently fall back to the contractual (extra-free)
trajectory.

**Why it lives INSIDE the seam** (plan step E1d-a; developer ruling 2026-07-24).
It was the public module ``app.services.loan_resolution``, one hop outside the
balance seam, kept honest by a hand-written W9909 completeness ruling -- the
Phase-D shape the arc has been deleting everywhere else.  Two reasons closed it:

* Its ONE production caller was the read pass's context memo, i.e. the seam.  A
  module with a single in-package caller is a private of that package.
* Plan step E1d makes the confirmed seed a FOLD of the loan's events -- a
  balance-at-T, produced in the seam.  Phase D's invariant is that every balance
  producer is private to ``balance_at`` (enforced by W9910), so the composer that
  consumes that seed belongs on the same side of the boundary as the seed.

Moving it in DELETES its fence entry rather than shrinking it: the
``app.services.loan_resolution`` W9909 scope is gone, and W9910 now protects the
whole chain structurally, name-independently.

**The read pass's memo lives here, not on the context** (plan step D-ctx-b's
rule, applied): the seam owns the derivation, the context owns the storage.
:func:`resolved_loan` fills the pass's public
:attr:`~app.services.balance_at.BalanceContext.loans` cache through the shared
store-once primitive, exactly as
:func:`~app.services.balance_at._plan.memoized_plan` fills ``plans``.

It also hosts one PURE (no-I/O) producer, :func:`contractual_schedule_from_origination`:
the property equity chart's from-origination contractual schedule, which seeds the
same resolver composer with a synthesized origination anchor instead of a confirmed
view.  It lives here beside :func:`resolve_loan_bundle` because it too composes the
loaders' anchor synthesis with the pure resolver; the caller supplies its one loaded
input (the rate-change feed), so the function itself stays I/O-free.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from dataclasses import dataclass
from datetime import date

from app.models.account import Account
from app.models.loan_params import LoanParams
from app.services import loan_resolver
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.amortization_engine import AmortizationRow, RateChangeRecord
from app.services.loan_loaders import (
    load_loan_anchor_facts,
    load_loan_params,
    synthesize_origination_anchor,
)
from app.services.loan_payment_service import LoanContext, load_loan_context
from app.services.recurrence import RecurrenceOwner
from app.models.transfer_template import TransferTemplate
from app.services import recurring_transfer_query
from app.services.recurring_transfer_query import destination_account

from ._confirmed_view import confirmed_view
from ._context import BalanceContext
from ._memoize import _memoize_once


@dataclass(frozen=True)
class ResolvedLoan:
    """Everything one loan resolution produced, from ONE load of its inputs.

    The unit of work :class:`~app.services.balance_at.BalanceContext`
    memoizes: a loan's loaded inputs AND the :class:`LoanState` they resolve to,
    bundled so that every surface reading any part of a loan -- its balance, its
    schedule, its payment / rate / payoff, its payment feed -- reads ONE
    resolution rather than triggering its own.

    Before this bundle existed, a single ``/savings`` render ran the resolver
    ELEVEN times for two loans (measured 2026-07-13), because each consumer
    re-derived the loan from scratch: the balance maps, the trend window's
    honest-history gate, the liability band, the loan tile, the property-equity
    card, and the "ever paid off" probe each loaded and resolved independently.
    They agreed on the balance, which is what made the waste look harmless --
    but the probe among them resolved through a producer that CANNOT read the
    genesis ledger, and no gate could see that because there was no single
    resolution to compare against
    (``docs/audits/balance_architecture/followup_redundant_loan_resolution.md``).

    Attributes:
        params: The loan's :class:`~app.models.loan_params.LoanParams`.
        anchor_facts: The loan's anchor facts (the synthesized origination
            anchor plus any balance true-ups), from
            :func:`~app.services.loan_loaders.load_loan_anchor_facts`.
        context: The loan's loaded :class:`~app.services.loan_payment_service.LoanContext`
            (payments, rate changes, escrow lines, contractual P&I).  Carried
            because the payment feed answers questions the ``LoanState`` cannot
            -- "has this loan ever had a confirmed payment?" above all -- and
            re-loading it was one of the redundant resolutions.
        state: The resolved :class:`~app.services.loan_resolver.LoanState` as of
            the context's ``as_of``: the committed (plan-aware) schedule, the
            payment, the rate, and the life-of-loan interest.  No balance and no
            payoff -- the ``balance_at`` seam derives both from the fold (plan
            steps C8d / D2a).
        standing: (A property, not a field.)  The loan's STANDING recurring
            payment -- the oldest of :attr:`definitions`, read off that list
            so the identity has ONE home -- or ``None`` when the loan has no
            recurring payment at all.  An IDENTITY and nothing more, since
            plan step R7d-g-3: what the recurrence form locks on and the
            opening-bound sync writes for (:func:`is_standing_loan_payment`;
            ruling **R-R81**).  It carried the definition's ``extra_principal``
            beside it as a ``StandingPayment`` from plan step R7d-a, and the
            extra was threaded into :func:`resolve_loan_bundle`'s resolve --
            the ONE picked definition's overpayment applied to every month of
            the committed schedule (plan ledger row **D49**).  Nothing is
            threaded now (ruling **R-R88**, which re-ruled R-R83's seam clause at R7d-g-3): a
            projected row's cash carries its own definition's extra through
            amount rule 4, so the resolver adding one again paid it twice on
            every row-covered month, and the months no row covers are priced
            from every definition's own occurrences by the seam's forward PLAN
            (:func:`app.services.balance_at._plan.loan_plan`), which is what
            sums :attr:`definitions`.
        definitions: EVERY active recurring transfer paying into the loan
            (:func:`~app.services.recurring_transfer_query.active_recurring_transfer_templates`),
            oldest first, loaded ONCE here (plan step R16-b-2).  The forward
            plan walks each on its own cadence; :attr:`standing` is the first
            of them, so "which definitions pay in" and "which one is the
            standing payment" are one query and cannot disagree.  ``[]`` for a
            loan with no definition, whose plan is then the contract's.
        recorded_start: (A property, not a field.)  The day the app's record
            of the loan STARTS: its ``tracking_start`` assertion's date for a
            loan imported mid-life, else its origination (rulings **R-R111**,
            **R-R114**); no payment moves it.
            Before it the app's record of the loan has not started: the
            ledger carries the origination principal forward, moved only by
            what is dated there -- a true-up, a ``$0.00`` close, or a payment
            whose money moved there (one recorded after the tracking start
            with an earlier day, or one already held when a tracking start
            was written before plan step recurrence:R16-c-2 made its doors
            refuse) -- so the readers that ask "since when is this loan's balance
            real?" -- the property chart's pre-tracking estimate, the net-worth
            trend's honest start and the loan chart's first month -- read it
            here.  They read the FIRST confirmed schedule row's date until
            plan step recurrence:R16-c-2, which ruling R-R109 broke: a row
            dated by the installment its payment pays can fall before the
            tracking start.
    """

    params: LoanParams
    anchor_facts: list
    context: LoanContext
    state: loan_resolver.LoanState
    definitions: list[TransferTemplate]

    @property
    def standing(self) -> TransferTemplate | None:
        """The oldest of :attr:`definitions`, or ``None``: DERIVED, never stored beside it."""
        return self.definitions[0] if self.definitions else None

    @property
    def recorded_start(self) -> date:
        """The day the loan's record starts: DERIVED from its statements alone.

        The tracking-start assertion's date, else the origination (rulings
        **R-R111** and **R-R114**).  **No payment moves it**: whether a
        statement added to a loan with a recorded history starts the record
        or only corrects the balance is decided ONCE, where a tracking start
        is written: the dashboard's door
        (:func:`app.services.loan_anchor_service.record_loan_tracking_start`)
        and the setup door
        (:func:`app.services.loan_anchor_service.stage_loan_tracking_start`)
        refuse a date on or before the day a payment into the loan moved
        money (rulings R-R114, R-R115, R-BAL155), so the owner records a
        true-up, which starts nothing.  Ruling R-R113 read the payment feed
        here instead, and review 5 of plan step recurrence:R16-c-2 measured
        two ways that misfired on a loan imported mid-life: a ``$0.00`` close
        is dated by the installment it skips (ruling R-R107), so a close due
        after the statement counted as recorded before it; and one real
        payment dated the day before the statement put the untracked
        origination principal back in every earlier month, as confirmed.
        Reading the statements alone also keeps this a CONTRACT fact, the
        same with or without a baseline scenario (plan step C8e).
        """
        return next(
            (fact.anchor_date for fact in self.anchor_facts if fact.is_tracking_start),
            self.params.origination_date,
        )


def resolved_loan(
    account: Account, ctx: BalanceContext,
) -> ResolvedLoan | None:
    """Return *account*'s resolution for this read pass, resolving it at most once.

    The seam's ONE funnel for a whole-loan read: it fills the read pass's per-loan
    resolution cache (:attr:`~app.services.balance_at.BalanceContext.loans`) from
    :func:`resolve_loan_bundle` through the shared store-once primitive
    (``_memoize._memoize_once``), so a loan is loaded and resolved at most once per
    pass however many surfaces ask.  Every seam consumer that wants a loan's
    schedule, payment, rate, payment feed, or standing extra goes through here, so
    the loan tile's figures, the net-worth hero, the liability band, and the debt
    card read ONE resolution -- identical BY CONSTRUCTION rather than by the luck
    of four producers agreeing.

    Before that memo existed, a single ``compute_dashboard_data`` call ran the
    resolver ELEVEN times for two loans (measured 2026-07-13), and the redundancy
    was not merely waste: one of the eleven resolved through a producer that could
    not read the genesis ledger, and the ten that agreed made the eleventh
    invisible.

    A ``None`` result (the account has no :class:`~app.models.loan_params.LoanParams`
    -- it is not a configured loan) is memoized TOO, so a non-loan account asked
    repeatedly does not re-issue its params query each time.  That is why the
    store-once primitive tests MEMBERSHIP rather than truthiness.

    **It is a seam function, not a context method** (plan step E1d-a, applying
    D-ctx-b's rule).  It was ``BalanceContext.resolved_loan``, which put a public
    method on an object every route legitimately holds -- the one surface W9910
    cannot see, since the gate reads imports and never attribute access (finding
    H1 of plan step D3's adversarial review, which is why ``_context`` carries the
    seam's last W9909 completeness ruling).  Moving the derivation here shrinks
    that surface: the context now stores, and the seam derives.

    Args:
        account: The account to resolve.  Must belong to ``ctx.user_id`` (the
            caller owns the ownership check -- the loaders trust it).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`,
            whose ``scenario`` scopes the payment history and whose ``as_of`` is
            the date the loan is RESOLVED at.

    Returns:
        The pass's memoized :class:`ResolvedLoan`, or ``None`` when *account* is
        not a configured loan.
    """
    return _memoize_once(
        ctx, ctx.loans, account, lambda: resolve_loan_bundle(account, ctx),
    )


def configured_loan(
    account: Account, ctx: BalanceContext,
) -> "ResolvedLoan | None":
    """Return *account*'s resolution iff it is a CONFIGURED LOAN, else ``None``.

    The seam's ONE spelling of "does this account's balance come from an
    amortization schedule?", and the gate every balance surface splits on: the
    kind-correct dispatch (:func:`._kind_correct.balance_at_dates`, which the
    scalar :func:`._kind_correct.balance_at` and the forward liability band
    :func:`._liability.liability_owed_at_dates` both read) and the per-period
    map (:func:`._inputs._account_balance_map`) ask it here -- TWO call sites
    in ``app/`` since plan step credit_card:CC-1 (a grep of
    ``configured_loan(`` at that step's as-built), which folded the band's own
    ask into the dispatch it was duplicating.

    **It is one function because it was three spellings** (plan step X-g3b-0).
    The scalar wrote ``classify_account(...) is AMORTIZING and
    resolved_loan(account, ctx) is not None``, the map tested MEMBERSHIP in a
    ``debt_schedules`` bundle it built and then discarded the values of, and the
    band decomposed the same rule into two separate guard clauses.  The three
    were equivalent by an argument recorded in a docstring rather than by
    construction -- and plan Section 8's own lesson is that a DRY refactor of a
    PREDICATE can move money, which cuts both ways: a predicate stated three
    times can move money when one statement is edited and the others are not.
    Nothing enforced the agreement, so nothing would have caught the drift.

    **BOTH halves are load-bearing and neither implies the other.**
    :func:`resolve_loan_bundle` tests for a
    :class:`~app.models.loan_params.LoanParams` row and never consults the
    account's KIND, so a params row on a non-amortizing account would resolve
    here; the classifier test is what keeps such a data defect off the
    amortization path instead of silently amortizing a savings account.  The
    resolver test is what degrades a Mortgage-typed account whose terms were
    never entered -- two clicks in the UI produce one -- to the cash producer
    rather than letting it reach :func:`._positions.positions`' fail-loud.

    It returns the RESOLUTION rather than a bool because that is what
    :func:`resolved_loan` already hands back; narrowing it to a bool would throw
    information away for nothing.  Both call sites discard it today and test it
    against ``None``.

    Args:
        account: The account to test.  Must belong to ``ctx.user_id`` (the
            caller owns the ownership check -- the loaders trust it).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`,
            whose ``as_of`` is the date the loan is RESOLVED at.

    Returns:
        The pass's memoized :class:`ResolvedLoan`, or ``None`` when *account* is
        not an amortizing account with loan terms.
    """
    if classify_account(account) is not AccountProjectionKind.AMORTIZING:
        return None
    return resolved_loan(account, ctx)


def resolve_loan_bundle(
    account: Account, ctx: BalanceContext,
) -> ResolvedLoan | None:
    """Load a loan's inputs ONCE and resolve it -- the whole-loan read.

    The single db-facing loan read the whole app resolves through: it loads the
    loan's params, anchor facts, payment context and every definition paying
    into it, seeds the pure resolver with the confirmed view, and returns all
    of it bundled as a :class:`ResolvedLoan`.  :func:`resolved_loan` memoizes
    it per pass, so a read pass resolves each loan exactly once no matter how many
    surfaces ask.

    Two seeded inputs go into the pure resolver, and routing every resolution
    through here is what makes it structurally impossible for a surface to miss
    either:

    * **The genesis-ledger confirmed view**
      (:func:`~app.services.balance_at._confirmed_view.confirmed_view`): its
      balance seeds the schedule composer's forward starting balance, and its
      rows become the schedule's confirmed slice, so the history and the
      projection cannot desync off-schedule.  Since plan step E1d-b that view is
      the FOLD of the loan's recorded events, not a read of the posted ledger, so
      a cold posting cache no longer drops a loan back to the money-blind anchor
      replay (finding B-12).  When the view cannot answer (``None`` -- no
      baseline scenario, or a loan that has not originated by ``ctx.as_of``) the
      composer seeds the contract from the loan's latest assertion, with no
      history rows (the replay it fell back to went at plan step
      recurrence:R16-e).  The loan's
      displayed BALANCE is not derived here at all (plan step D2a): the seam folds
      it from the same recorded events.
    * **The loan's DEFINITIONS**, loaded ONCE here: every active recurring
      transfer into it (:attr:`ResolvedLoan.definitions`, plan step R16-b-2),
      which the seam's forward plan sums, and the oldest of them as the
      STANDING PAYMENT (:attr:`ResolvedLoan.standing`, the identity alone).
      Nothing off a definition is threaded into the resolve since plan step
      R7d-g-3 -- see that attribute for the extra that was, and why it went.

    Returning the loaded ``context`` alongside the ``state`` is what removes the
    last reason for a consumer to re-load: the loan tile previously called
    :func:`load_loan_context` and :func:`load_loan_anchor_facts` itself (to run a
    second ``date.max`` resolver probe), which is precisely the duplication this
    bundle exists to make unnecessary.

    Returns ``None`` when the account has no ``LoanParams`` row (it is not a
    configured loan); the caller skips it.  A configured loan is always
    resolvable -- its origination anchor fact is synthesized from the immutable
    params -- so there is no anchor-based short-circuit here.

    Args:
        account: The loan account to resolve.  The caller owns the ownership
            check (the loaders trust it).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
            Its ``scenario`` scopes the payment history AND the confirmed seed
            (``None`` when the user has no baseline -- the loan then resolves from
            its anchor with no payment feed, the documented degraded state), and
            its ``as_of`` is the date the loan is resolved AT (the resolver's
            "now": what counts as confirmed).

    Returns:
        The :class:`ResolvedLoan`, or ``None`` if the account has no
        ``LoanParams``.
    """
    params = load_loan_params(account.id)
    if params is None:
        return None
    anchor_facts = load_loan_anchor_facts(params)
    # ``amounts_or_none``, deliberately, and it is the same nullability this
    # line has always spelled: a loan's payment feed is the ONE scenario-scoped
    # input to its resolution, and its params, anchors and rate history are
    # contract facts.  With no baseline the feed is empty and the CONTRACT terms
    # still resolve -- plan step C8e's rule, and what keeps escrow and rate
    # editing working for a user whose baseline is missing.  Every other reader
    # takes the raising ``amounts`` / ``scenario_id`` (ruling R-BX).
    #
    # It was ``ctx.scenario_id_or_none`` until plan step X-au-g-2c, which routed
    # ``get_payment_history`` through the amount model: the loader now takes the
    # BASIS that prices the feed rather than an id that only scopes it, so the
    # feed and its figures cannot come from two different scenarios.  Passing
    # the pass's own basis is also what keeps this read to ONE pricing pass --
    # ``ctx.amounts()`` is memoized, so a render resolving several loans runs
    # the paycheck engine once (findings N-268, N-269).
    context = load_loan_context(
        account.id, ctx.amounts_or_none(), params,
    )
    # ONE query for every definition into the loan (plan step R16-b-2); the
    # standing payment is its oldest member, read off the same list rather
    # than fetched again through the singular query (finding N-511's shape).
    # Reached through the module rather than an imported name, so the query
    # is resolved at CALL time where it is defined -- which is where the
    # "one lookup per pass" control counts it.
    definitions = recurring_transfer_query.active_recurring_transfer_templates(
        account.id, account.user_id,
    )
    state = loan_resolver.resolve_loan(
        loan_resolver.LoanInputs(params, anchor_facts, context.rate_changes),
        ctx.as_of,
        confirmed_view=confirmed_view(account, ctx),
    )
    return ResolvedLoan(
        params=params,
        anchor_facts=anchor_facts,
        context=context,
        state=state,
        definitions=definitions,
    )


def is_standing_loan_payment(
    template: RecurrenceOwner, ctx: BalanceContext,
) -> bool:
    """Return whether *template* is the STANDING payment of the loan it pays into.

    **The ONE identity behind every per-bound rule the recurrence form applies
    to a loan payment, read from the pass** (plan step R7d-f).  It replaced
    ``owns_validity_window(template)``, which answered "does this module write
    BOTH of this definition's bounds" -- a premise ruling **R-R29** made false
    (only the OPENING bound stays written; the CLOSING bound is derived by
    :func:`~app.services.loan_recurrence_sync.loan_payment_window` for EVERY
    recurring transfer into a loan under **R-R35**) -- and which re-ran two
    queries the pass already held (plan ledger row **N-511**:
    ``load_loan_params`` after ``loan_figures`` had just proved the params
    exist, and ``active_recurring_transfer_template`` after
    ``resolved_loan(...).standing`` had memoised the very same lookup).  It
    reads that memo (:func:`resolved_loan`), so the composed door, the form's
    two locks, the three refusals and the forward plan read ONE producer and
    the lookup runs once per pass however many of them ask.  *Until plan
    step R7d-g-3 a public accessor beside the figures,
    ``_loan_figures.loan_standing_payment``, handed the same value out and
    nothing called it; it went with the ``StandingPayment`` bundle it
    returned.*

    **It lived in ``loan_recurrence_sync`` until plan step R16-b-2** (ruling
    **R-R70**, developer 2026-09-11).  The forward plan
    (:func:`~app.services.balance_at._plan.loan_plan`) sums every definition
    paying into a loan and honours each one's AUTHORED closing, which until
    plan step R7d-g it read through an arm of this identity (``NEVER_ENDS``
    for the standing payment, ruling **R-R56**) -- and that module imports
    this seam, so the seam could not reach the identity there without a
    cycle.  The arm is gone with the column's writers; the question this
    answers is still the seam's own ("is this the payment the pass resolved
    the loan with"), so it lives beside the memo it reads -- HERE rather than
    in ``_loan_figures``, because the plan (``_plan``) reads it and
    ``_loan_figures`` sits ABOVE the plan in the seam's internal DAG.

    **What the identity decides is stated PER BOUND**, which is the split
    R7d-f owed:

    * The OPENING bound.  ``loan_recurrence_sync._sync_loan_cadence`` writes
      and re-writes ``starts_on`` for this definition and no other -- the
      loan's first contractual installment -- so its "Starts on" control
      renders locked and a submission stating one is refused
      (:data:`~app.routes._recurrence_form_refusals.LOAN_PAYMENT_BOUND_IS_DERIVED`).
      A SECOND recurring transfer into the same loan carries its OWNER's
      start (ruling **R-R81**, taken at plan step R7d-g; the doors that
      derived one for it until then are re-cut at R7d-g-2), never re-synced,
      so its control stays the owner's.
    * The CLOSING bound.  The loan's own payment runs to the payoff and has
      NO authored stop: archiving is the door to stop it early (ruling
      **R-R59**, developer 2026-09-05, taken at R7d-f).  Its "Ends" control
      renders the composed door's answer, locked; a stated bound is
      refused -- at create as well, where the identity is asked the other way
      round, a loan holding no active payment making the new definition its
      payment (``settle_first_occurrence``, ruling **R-R60**), and at the two
      edits that make a definition such a transfer
      (``settle_destination_for_update``, rulings **R-R76** and **R-R77**).
      Since plan step R7d-g the column holds nothing the app wrote: a stored
      closing bound is its owner's word for EVERY definition and binds
      beside the derived one (ruling **R-R82** keeps one authored before the
      definition became the standing payment), so the cache arm ruling
      **R-R56** stated here (``authored_closing``) is deleted with the
      column's writers.

    **"Standing" is the seam's own word** (:attr:`ResolvedLoan.standing`):
    the loan's oldest ACTIVE recurring transfer.  That search is the one
    ruling **R-R35** wants deleted rather than answered; it survives here
    because the opening bound's writer still targets it (ruling **R-R81**).
    It stopped PRICING the forward plan at plan step R16-b-2 (**D47**) and
    the committed schedule at R7d-g-3 (**D49**), which is the half R-R35 was
    about.

    **Not the same question as "is this a loan payment".**
    :func:`~app.routes._recurrence_form_refusals.is_loan_payment` keeps the
    settings-row reading, and correctly: what it decides is whether clearing
    the recurrence would strand a standing ``extra_principal``, which is a
    property of that row rather than of the loan's payment identity.

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate`` a form
            is rendering, a refusal is judging or the plan is summing.  A
            transaction template can never pay into an account and answers
            ``False`` before any query; ``getattr`` on the FK column
            (:func:`~app.services.recurring_transfer_query.destination_account`)
            is what keeps this kind-agnostic, the same way
            :func:`~app.services.loan_recurrence_sync.loan_payment_window` is.
            **Must belong to ``ctx.user_id``**: the pass refuses to memoise a
            foreign loan (``ForeignAccountError`` from its store-once
            primitive, ``_memoize._memoize_once``) rather than answering
            about it.
        ctx: The read pass.  Its per-loan resolution memo is where the
            standing payment is read from, so a render or a refusal that has
            already resolved the loan pays nothing more here; one that has not
            resolves it once, and everything after it on the pass reads that.

    Returns:
        ``True`` when the destination is a configured loan and *template* is
        the recurring transfer the seam resolves as that loan's payment.
    """
    if not template.recurs:
        return False
    account = destination_account(template)
    if account is None:
        return False
    resolved = resolved_loan(account, ctx)
    if resolved is None or resolved.standing is None:
        return False
    return resolved.standing.id == template.id


def contractual_schedule_from_origination(
    loan_params: LoanParams,
    rate_changes: list[RateChangeRecord] | None,
) -> list[AmortizationRow]:
    """Return a loan's pure contractual amortization schedule FROM ORIGINATION.

    The property equity chart's pre-tracking debt line (the (a) contractual
    back-projection,
    ``docs/plans/historical/implementation_plan_property_equity_chart_rebuild.md``): a
    mid-life-imported loan records no PAYMENT before its ``tracking_start``, so
    the confirmed walk holds the origination balance flat across the
    origination-to-tracking-start window and :func:`resolve_loan_bundle`'s
    schedule carries no amortization there.  *This said the ledger "opens at its
    ``tracking_start``" until plan step X-an-b -- true before step C1, which made
    the origination the opening ALWAYS; the months are held flat now rather than
    absent, and this producer is still what fills them.*  It supplies
    them as the contractual schedule the loan's origination terms imply
    (``original_principal`` amortized over ``term_months`` at the rate-period
    rates), so the chart can draw the pre-tracking debt as an ``estimated`` tier
    and clip it at the tracking-start seam.

    Forks NO amortization math: it seeds
    :func:`loan_resolver.compute_payoff_scenarios` with a synthesized ORIGINATION
    anchor (:func:`loan_loaders.synthesize_origination_anchor`) and no confirmed
    view, and returns its ``original_forward`` -- the pure contractual reference
    (no override, no extra).  Seeding through the composer inherits the
    resolver's EXACT first-payment-date and remaining-term convention (the first
    installment after the anchor, which for the origination is
    :func:`~app.services.installment_calendar.first_installment_date`, with the
    full ``term_months`` remaining), so the back-projection lands on the same
    monthly grid as the resolved schedule and the two cannot drift at the
    seam.

    Pure: takes loaded data (the caller supplies ``rate_changes`` via
    :func:`loan_loaders.load_rate_changes`); performs no I/O.

    Args:
        loan_params: The loan's :class:`LoanParams` (its immutable
            ``origination_date`` / ``original_principal`` / ``term_months`` /
            ``payment_day`` / ARM cadence).
        rate_changes: The loan's :class:`RateChangeRecord` feed (the origination
            row plus any ARM adjustments), so each pre-tracking month is governed
            by its correct per-period rate.  Must contain the origination row
            (:func:`loan_resolver.resolve_periods` raises on an empty feed).

    Returns:
        The contractual :class:`AmortizationRow` list from origination to payoff,
        each ``is_confirmed=False`` (a contractual estimate, never recorded
        fact).  The caller clips it to the months before the loan's tracking
        start.
    """
    scenarios = loan_resolver.compute_payoff_scenarios(
        loan_inputs=loan_resolver.LoanInputs(
            loan_params,
            [synthesize_origination_anchor(loan_params)],
            rate_changes,
        ),
        confirmed_view=None,
    )
    return list(scenarios.original_forward)
