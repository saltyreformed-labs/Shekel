"""WHICH transfers are an account's payments, and which of them have HAPPENED.

The half of :mod:`app.services.loan_loaders` that answers *which payments*,
against :mod:`._terms`' *what are this loan's contractual facts*.  It owns the
two producers -- :func:`settled_income_legs` and
:func:`projected_income_legs` -- and the partition value that composes them
(:func:`income_legs`).

A payment INTO an account is the to-side LEG of a transfer into it
(:class:`~app.services.transfer_legs.TransferLeg`): a payment received by a
loan, or a contribution into an investment account.  **Both halves are legs
read off ``budget.transfers`` since plan step balance:X-bi-6-4b**; the settled
half was the loan-side income SHADOW row until then.  The module and its
names kept the shadow era's spelling -- ``_shadows``, ``ShadowSets``,
``income_shadows``, ``settled_income_shadows`` -- until plan step
balance:X-bi-6-4d-2 renamed them for what they return.

**Its two jobs are both rule-14 answers, and plan step balance:X-bl-2a is where
they became one each.**  Settled-ness had two derivations -- this module's
status-id filter and ``loan_payment_service.get_payment_history``'s read of the
``is_settled`` column -- agreeing under a pinned parity; and the amount model's
eager-load set was baked into the query, so a ROW loader decided what its
callers would traverse.  The partition is single now, and the load is the
caller's statement.

**The two halves are ONE relation, each other's complement, since X-bi-6-4b**
(ruling **R-BAL140**).  A payment is PLANNED while its transfer is Projected
-- :func:`~app.services.transfer_legs.planned_transfer_legs`' test -- and
SETTLED otherwise, so each half is the other's exact complement over the
live, non-excluded transfers into the account and no payment can be counted
twice or not at all.  From X-bi-6a to X-bi-6-4b the settled half was the
shadow rows keyed on the SHADOW's status while the plan half keyed on the
parent's, so a status drift no door writes was counted twice (a settled
shadow holding no movement under a Projected parent) or not at all (a
Projected shadow under a settled parent).  From X-bi-6-4b each half asked
the side's money too (rulings **R-BAL79**, **R-BAL140**), so a Projected
transfer whose to-side record was dated counted once, as settled, until plan
step balance:X-bi-6-5's third leaf: the status-band rule
(:mod:`app.side_band_infrastructure`) refuses that state at commit, a record
being dated exactly while its transfer is Paid or Received, so the status
alone partitions.

A LEAF: models, the ref cache, the shared balance predicates and
:mod:`app.services.transfer_legs`, whose one join attaches each settled leg's
covering movement as its record.  Flask-isolated, reads only, no commits.  No
amount is read off a side's record for a payment that has settled: a record
holds no figure since plan step balance:X-bi-6-4d-2, and a settled leg is
worth the figure its transfer stores
(:func:`app.services.row_valuation.settled_figure` of its parent, ruling
**plan#1983**; the transfer paragraph at the end of ``CLAUDE.md``'s
Architecture).
"""

from dataclasses import dataclass

from app.extensions import db
from app.models.transfer import Transfer
from app.services.transfer_legs import (
    TransferLeg,
    grid_transfer_legs,
    planned_transfer_legs,
)
from app.utils.amount_relationships import transfer_period_load_option
from app.utils.balance_predicates import (
    balance_excluded_status_ids,
    is_projected_clause,
    settled_status_ids,
)


@dataclass(frozen=True)
class IncomeLegs:
    """A loan's income payments, PARTITIONED by whether their cash has moved.

    The project's single answer to "which of this loan's payments have
    happened?", and the reason it is one value rather than two functions is
    :class:`CLAUDE.md` rule 14: settled-ness is one fact, so it is decided in
    one place (:func:`income_legs`) and read from here.

    **Both halves are legs of transfers since plan step balance:X-bi-6-4b**,
    each the other's complement (ruling **R-BAL140**): one status test,
    :func:`~app.utils.balance_predicates.is_projected_clause`, the plan half
    applies and the settled half negates.
    Until then the settled half was the shadow rows -- one table partitioned
    by ``status_id``, which is ``docs/design/from_scratch_architecture.md``'s
    named root cause: a PLAN and a RECORD OF WHAT HAPPENED in one row, with a
    status column pretending the first turns into the second.

    Attributes:
        settled: The payments that have happened -- one
            :class:`~app.services.transfer_legs.TransferLeg` per live,
            non-excluded transfer INTO the account that is not Projected, its
            covering movement attached as its ``record`` -- ascending by
            ``(pay_period.start_date, transfer id)``.
        projected: The payments still planned, as one
            :class:`~app.services.transfer_legs.TransferLeg` per
            still-projected transfer INTO the account, ascending by
            ``(pay_period.start_date, transfer id)``.
            The exact complement of ``settled``: a payment is in one half
            and never both.
    """

    settled: list[TransferLeg]
    projected: list[TransferLeg]


def income_legs(
    account_id: int, scenario_id: int, *, options: tuple, leg_options: tuple,
) -> IncomeLegs:
    """Return an account's income payments split into SETTLED and PROJECTED.

    **The single derivation of "which payments are settled, and in what
    order"** (plan step **balance:X-bl-2a**).  Every settled-payment consumer
    reaches this one -- the fold's event stream
    (:func:`app.services.loan_ledger.loan_event_stream`), the ledger's
    per-payment principal reader, the escrow forward-only guard, the resolver's
    payment feed -- so no two of them can classify a payment differently.  It was
    two narrowed queries and a third rule in
    ``loan_payment_service.get_payment_history``, which read ``txn.status.is_settled``
    where these filtered on ``settled_status_ids()``; the two agreed under a
    pinned parity, which is rule 14's tell rather than its answer.

    **Two producers over ONE relation, each the other's complement** (plan
    step balance:X-bi-6-4b, ruling **R-BAL140**).  The settled half is
    :func:`settled_income_legs`, the projected half
    :func:`projected_income_legs`; both select transfers INTO the account,
    and the settled half's membership is the plan half's one status test
    negated (:func:`~app.utils.balance_predicates.is_projected_clause`).
    It was the plan half's two-term test negated by hand, a status test and a
    dated-record test, the complement held by a test rather than by one
    expression, until plan step balance:X-bi-6-5's third leaf deleted the
    dated-record terms.  In a fresh session that is five statements -- the settled
    transfers, their periods, their movements through the one join, the
    projected transfers, their periods (the period load is a ``selectinload``
    each producer adds) -- fewer when the periods are already loaded and more
    with whatever the caller's options add; a consumer wanting one half pays
    for that half alone.

    **The classification is TOTAL and refuses what it cannot place.**  A
    transfer here is settled, or it is ``Projected``; the balance-excluded
    statuses are dropped in SQL, so nothing else can arrive today.  That is a
    claim about the ``ref.statuses`` seed, not about this code, and a set
    defined by everything-except is exactly the shape that claims members
    nobody censused -- so a sixth status RAISES in
    :func:`settled_income_legs` rather than falling silently out of every
    loan's schedule and balance.

    Args:
        account_id: The account receiving the transfers.
        scenario_id: The budget scenario to scope to.
        options: The loader options for every relationship the CALLER will
            traverse on the SETTLED legs' parents, rooted at
            :class:`~app.models.transfer.Transfer` -- ``()`` for every caller
            today: a settled leg is worth the figure its transfer stores, a
            column of the parent this loads, and is dated by its record,
            which the one join attaches.  ``Transfer.pay_period`` is added
            regardless, because the producer reads it -- it is the sort key.
        leg_options: The same statement for the PROJECTED legs' parents
            (:func:`~app.utils.amount_relationships.transfer_pricing_load_options`
            for a caller that prices them, ``()`` for one that reads dates);
            ``Transfer.pay_period`` is added regardless, for the same reason.

    Returns:
        The :class:`IncomeLegs` for the account; both halves ``[]`` when it has
        no income payments.

    Raises:
        ValueError: When a transfer carries a status that is neither settled
            nor ``Projected``.  The message names the transfer and the status,
            because a silently dropped payment is a balance that is quietly
            wrong.
    """
    return IncomeLegs(
        settled=settled_income_legs(
            account_id, scenario_id, options=options,
        ),
        projected=projected_income_legs(
            account_id, scenario_id, options=leg_options,
        ),
    )


def settled_income_legs(
    account_id: int, scenario_id: int, *, options: tuple,
) -> list[TransferLeg]:
    """Return a loan's SETTLED payments as legs of their transfers, in payment order.

    The SETTLED half of :func:`income_legs`, which is where the derivation
    itself lives since plan step **balance:X-bl-2a**.  **It returns LEGS since
    plan step balance:X-bi-6-4b.**  What follows describes the set: one
    :class:`~app.services.transfer_legs.TransferLeg` per live, non-excluded
    transfer INTO the account in the scenario that is not Projected (ruling
    **R-BAL140**) -- each carrying its covering movement as its ``record``
    through the one join (:func:`~app.services.transfer_legs.grid_transfer_legs`),
    and NOTHING ELSE.  Every settled-payment consumer reads this ONE set, so no
    two can disagree on which payments are settled: the fold's event stream
    (:func:`app.services.loan_ledger.walk_loan_ledger`), the fold's display bound
    (:func:`app.services.loan_ledger.confirmed_shadows_through`), the ledger's
    per-payment principal reader, the asset contribution pass,
    :func:`_settled_payment_due_dates` (the escrow forward-only guard's
    boundary :func:`latest_settled_payment_due_date`, since finding N-34), and
    the tracking-start refusal (``loan_anchor_service``, rulings **R-R114**,
    **R-R115**, **R-BAL155**), which asks each leg the day its money moved.
    *A tracking-start guard that read this set's DUE dates was deleted at plan
    step ``recurrence:R20``; the refusal that returned at R16-c-2 reads the
    movement's day instead.*

    **The set is keyed on the TRANSFER, never on its movement alone.**  A
    ``$0.00`` close -- a transfer settled with no movement, ruling **R-BAL82**
    -- is a payment of nothing that still settles an installment, so the walk
    must see it; :func:`~app.services.transfer_legs.recorded_transfer_legs`,
    which is movement-rooted, would drop it.  Its leg carries no record and is
    dated by the installment it skips (ruling **R-BAL139**,
    :func:`app.services.loan_ledger.payment_visible_on`).

    **Each transfer that arrives must be settled, and the gap is the
    refusal.**  The query excludes the balance-excluded statuses and
    ``Projected``; a sixth status that is neither settled nor Projected is
    admitted by the ``NOT Projected`` clause, so it is refused by name HERE,
    at the one door every settled-payment reader passes, rather than
    wherever a reader first values it.  The query admitted a Projected
    transfer whose to-side record was dated too (the status drift ruling
    R-BAL140 counted once, by its movement), until plan step
    balance:X-bi-6-5's third leaf: the status-band rule refuses that state
    at commit.

    Two bounds the resolver's
    :func:`app.services.rate_period_engine.is_confirmed_payment_eligible` filter
    applies are deliberately ABSENT:

    * **No post-anchor LOWER bound.**  The fold walks EVERY settled payment from
      origination, because an anchor is a running-balance RESET
      (:func:`app.services.loan_ledger.walk_loan_ledger`), not a payment exclusion.
      A pre-anchor payment is split and posted (its principal effect is later
      subsumed by the anchor correction), never silently dropped.
    * **No has-happened-yet UPPER bound.**  Settlement is the confirming event: the
      Step-2 cash entry posts the moment a payment settles, so the split correction
      must post in the SAME moment or the loan-linked ledger holds raw cash with no
      interest / escrow backout from the payment's period start until the next loan
      write (the 2026-07-02 adversarial review's H2 -- demonstrated as a ~$1,636
      understatement on the real Mortgage).  The READERS apply their own bound, so
      posting early changes when the fact is RECORDED, never when it is SHOWN.

      **That bound is the payment's SETTLED day, not its pay period, on every
      reader** -- the fold's since step C2 (:func:`app.services.loan_ledger.payment_visible_on`),
      the resolver's replay since plan step **X-an**.  This paragraph used to say
      the readers' PERIOD bound kept an early-settled payment out of every
      displayed balance until its period began, and finding **N-187** is that
      sentence being false in both directions at once: an early-settled payment
      IS shown from the day its cash moved, and the resolver was the one reader
      still waiting for the period -- so it planned an installment the ledger had
      already paid down.

    Sorted by pay-period start -- the app's canonical payment chronology and the
    order the fold's running balance is walked in -- with the TRANSFER's id as
    the deterministic tie-breaker, the key the projected half already used.  It
    was the shadow's id until X-bi-6-4b, and the two orders agree wherever a
    transfer's shadows were written with it -- measured, not argued: 0
    inversions on the 2026-09-23 21:17 production dump.  A shadow re-created
    later takes a later id; the transfer's id is immune to that.  The order is
    immaterial to the guards (they take a ``min`` / ``max`` / set), and
    load-bearing for the walk, so it
    is applied ONCE here rather than by each caller.  The resolver's
    biweekly-collision redistribution (a display fix) is NOT applied, and is
    immaterial to a sequentially walked running balance.  ``pay_period`` is
    eager-loaded HERE, which reads it itself as the sort key.

    Args:
        account_id: The loan account whose settled payments to load.
        scenario_id: The budget scenario to scope to.
        options: The loader options for every relationship the CALLER will
            traverse on the parents, rooted at
            :class:`~app.models.transfer.Transfer` -- ``()`` for every caller
            today: a settled leg is worth the figure its transfer stores
            (:func:`~app.services.row_valuation.settled_contribution` of
            its parent), a column of the parent this loads, and dated from its record,
            which the join attaches, or from the parent's own columns.

    Returns:
        Every settled payment's leg, ascending by ``(pay_period.start_date,
        transfer id)``; ``[]`` when the loan has no settled payment.

    Raises:
        ValueError: When a transfer carries a status that is neither settled
            nor ``Projected``.  Named here because this view is the door the
            fold's walk, the posting reader and the escrow forward-only guard
            reach it through, so a broken status seed surfaces on every loan
            surface at once rather than on one.
    """
    transfers = (
        db.session.query(Transfer)
        .options(transfer_period_load_option(), *options)
        .filter(
            Transfer.to_account_id == account_id,
            Transfer.scenario_id == scenario_id,
            Transfer.is_deleted.is_(False),
            ~Transfer.status_id.in_(balance_excluded_status_ids()),
            # The plan half's one status test, negated: a payment is planned
            # exactly while its transfer is Projected
            # (``transfer_legs.planned_transfer_legs``).
            ~is_projected_clause(Transfer),
        )
        .all()
    )
    settled_ids = settled_status_ids()
    for transfer in transfers:
        # The SHARED predicate -- ``settled_status_ids`` is the one "which
        # statuses are settled" answer (D6-09).  An inline ``status_id ==``
        # here would be a second status rule in a module whose whole subject
        # is having one, and ``TestNoInlineStatusBusinessLogic`` refuses it.
        # The query admits no Projected transfer, so what is not settled here
        # is neither.
        if transfer.status_id not in settled_ids:
            raise ValueError(
                f"transfer {transfer.id} into account {account_id} carries "
                f"status_id {transfer.status_id}, which is neither settled nor "
                f"Projected: this partition is the app's one settled-payment "
                f"derivation and cannot place the payment"
            )
    legs = grid_transfer_legs(
        transfers, lambda transfer: (transfer.to_account_id,),
    )
    legs.sort(key=lambda leg: (leg.pay_period.start_date, leg.transfer.id))
    return legs


def projected_income_legs(
    account_id: int, scenario_id: int, *, options: tuple,
) -> list[TransferLeg]:
    """Return a loan's PROJECTED payments as legs of their parents, in payment order.

    The forward analogue of :func:`settled_income_legs`: one
    :class:`~app.services.transfer_legs.TransferLeg` per live
    still-projected transfer INTO the account, derived from the parent row
    in ``budget.transfers`` (ruling **R-BAL13**), through
    :func:`~app.services.transfer_legs.planned_transfer_legs`.  It was
    ``projected_income_shadows``, the shadow-income query narrowed to the
    PROJECTED status, until plan step **balance:X-bi-6a**; the payment
    RECORDS a loan's forward projection folds (plan step C6, the PLANNED
    tier) are the parents now, and from then on the shadow rows had no
    projected reader left.

    **Complementary with the settled set, so no payment is counted twice or
    dropped** (ruling **R-BAL140**), which is what lets the C6c settled-slot
    de-dup stay deleted.  Both halves select the same transfers and part on
    ONE status test,
    :func:`~app.utils.balance_predicates.is_projected_clause`, which this
    half's loader applies and the settled half negates, so the complement is
    one expression's (``TestTheSettledHalfComplementsThePlanHalf`` grades
    it).

    **The income side is the TO-side.**  The leaf loader returns every leg the
    account is on; a payment INTO the account is the leg whose parent names
    it as ``to_account_id``, which is the same rule the transfer service
    wrote as the income shadow's TYPE until plan step balance:X-bi-6-4d-2
    deleted the shadows.  A transfer OUT of a loan account is
    not a payment and is not returned.

    Carries no period bound and NO cash: the plan builder resolves each leg's
    cash (:func:`app.services.cash_ledger.resolve_amount` over the
    parent), its due date (:func:`loan_payment_due_date`, which takes a leg)
    and its escrow as the plan is assembled.

    Args:
        account_id: The loan account whose projected payments to load.
        scenario_id: The budget scenario to scope to.
        options: The loader options for every relationship the CALLER will
            traverse on the parents, rooted at
            :class:`~app.models.transfer.Transfer`
            (:func:`~app.utils.amount_relationships.transfer_pricing_load_options`
            for a caller that prices them, ``()`` for one that reads dates).
            ``Transfer.pay_period`` is added here regardless, because THIS
            function reads it -- it is the sort key.

    Returns:
        Every projected leg into the account, ascending by
        ``(pay_period.start_date, transfer id)``; ``[]`` when the loan has no
        projected payment.
    """
    legs = [
        leg for leg in planned_transfer_legs(
            account_id, scenario_id,
            options=(transfer_period_load_option(), *options),
        )
        if leg.is_income
    ]
    legs.sort(key=lambda leg: (leg.pay_period.start_date, leg.transfer.id))
    return legs
