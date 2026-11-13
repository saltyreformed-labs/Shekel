"""The partition holds every status once, loads only what its caller states, and refuses the rest.

:func:`app.services.loan_loaders.income_legs` splits the transfers into a loan
into its SETTLED and PROJECTED halves, and these three controls grade what
that split claims of itself:

* :class:`TestEveryStatusLandsOnce` -- the two halves together are every live,
  non-excluded transfer into the loan, once.  That is a claim about
  ``ref.statuses``, not about this code, so it is asserted over a loan
  carrying one transfer in EVERY status rather than assumed.
* :class:`TestTheCallerStatesItsOwnEagerLoad` -- a caller that states no load
  is charged none: the amount model's tables are not read.
* :class:`TestThePartitionIsTotal` -- a transfer the partition cannot place
  RAISES rather than vanishing.

All three lived in ``test_loan_installments.py``, the tests of the loan
payment feed's DATE half (``loan_ledger.payment_installments``, plan step
**balance:X-bl-2a**), until plan task plan#3389 deleted that feed (plan step
recurrence:R16-e).  The feed unioned these two halves and dated each payment,
so the first two graded the partition THROUGH it; they ask the partition
directly since, as ``test_what_neither_half_holds.py`` does since plan task
plan#3388, with every expected answer unchanged.  The third asked it directly
already and moved verbatim.
"""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.amount_ownership import AmountOwnership
from app.models.transfer import Transfer
from app.services import loan_loaders, status_seam
from app.services.loan_loaders import _legs
from app.services.transfer_service import (
    TransferSpec,
    create_transfer,
    settle_transfer,
)
from app.utils.balance_predicates import balance_excluded_status_ids
from tests._test_helpers import (
    an_entered_day,
    create_loan_account,
    generate_transfer_of,
    make_transfer_template,
    on_both_sides,
)


def _make_loan(seed_user):
    """Build a mortgage whose payments are due on the 1st."""
    return create_loan_account(
        seed_user, db.session, name="Installment Mortgage",
        principal=Decimal("250000.00"), rate=Decimal("0.06000"), term=360,
        origination_date=date(2024, 1, 1),
    )


def _transfer_to_loan(
    seed_user, loan, period, amount, status_enum, settled_on=None,
):
    """Create a transfer into *loan*, through the write door.

    Through :func:`~app.services.transfer_service.create_transfer` rather than
    by inserting rows, so the transfer this file reads is the one production
    writes: one row since plan step balance:X-bi-6-4d-2 (ruling **R-BAL258**),
    where it came with the pair of shadows this file read until then.

    **A SETTLED one is created Projected, then settled** through the settle
    verb, because the create makes no settled transfer (ruling **R-BAL379**);
    it was created in its settled status until then, and the settle stores
    what that create stored: the transfer's own figure on both sides, on
    *settled_on* (``entered``) or, with none, on the owner's today.
    **Received is then laid on BARE**: the verb settles a transfer with
    ``Done`` and no transfer door writes ``Received``, which the
    every-status case below still needs as a member of ``ref.statuses``
    (approved inside ruling R-BAL379's question: the case builds its state
    another way, its expected answer unchanged).  It is laid over the SETTLED
    transfer, so both sides keep their dated records, as the band rule
    requires; the bare lay is the technique
    ``test_routes/test_transfers/_helpers._received_transfer_of`` uses, which
    lays Received over a Projected transfer holding none.  The lay is one more
    write, so the transfer's version counter is the one stored fact that
    differs from what that create wrote (measured by 4d-4a's review).  A day
    stated beside an unsettled status is refused before any row exists, by
    the seam's own refusal, as that create refused it.
    """
    status_id = ref_cache.status_id(status_enum)
    settled = status_id in _settled_ids()
    stated = None if settled_on is None else an_entered_day(settled_on)
    status_seam.reject_settle_day_without_settled_status(status_id, stated)
    transfer = create_transfer(
        TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=loan.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(amount),
            status_id=(
                ref_cache.status_id(StatusEnum.PROJECTED) if settled
                else status_id
            ),
            category_id=seed_user["categories"]["Rent"].id,
        ),
    )
    if not settled:
        return transfer
    settle_transfer(
        transfer.id, seed_user["user"].id,
        side_days=(
            () if stated is None
            else on_both_sides(seed_user["account"].id, loan.id, stated)
        ),
    )
    if transfer.status_id != status_id:
        transfer.status_id = status_id
        db.session.flush()
    return transfer


#: How many payments the eager-load control builds.  More than one, because the
#: whole distinction it grades is ONE query for the set against one per row.
_SEAM_SHADOWS = 4
#: How many of them are PROJECTED, each generated by a DEFINITION OF ITS OWN.
#: Since plan step balance:X-bi-6a the projected half is read off the parent
#: transfers and priced through their definitions, and a definition is
#: identity-mapped: two projected payments of ONE template lazy-load it once,
#: which is indistinguishable from an eager load.  Two templates make a lazy
#: walk two statements against an eager load's one.
_SEAM_PROJECTED = 2


def _seam_shadows(seed_user, loan, seed_periods):
    """Create :data:`_SEAM_SHADOWS` payments on *loan*, settled and projected.

    A mixed set, so both halves of the partition are non-empty and the eager
    load has to cover rows from each: two settled AD-HOC payments (the record
    half, valued from what they recorded) and :data:`_SEAM_PROJECTED` projected
    payments each GENERATED from its own definition, so the plan half has a
    pricing chain to walk (``Transfer.template``) and a table to be measured
    on.  An ad-hoc projected transfer owns its figure and reaches no table
    past ``budget.transfers``, which is why the first draft of this fixture
    could not grade the leg path at all.
    """
    for index in range(_SEAM_SHADOWS - _SEAM_PROJECTED):
        period = seed_periods[index + 1]
        _transfer_to_loan(
            seed_user, loan, period, Decimal("1500.00"),
            status_enum=StatusEnum.DONE, settled_on=period.start_date,
        )
    for index in range(_SEAM_PROJECTED):
        template = make_transfer_template(
            db.session, seed_user, loan, amount="1500.00",
        )
        # The helper names every definition alike and the name is unique per
        # owner, so each is renamed before the next is built.
        template.name = f"Loan payment definition {index}"
        db.session.flush()
        generate_transfer_of(
            template, seed_periods[_SEAM_SHADOWS - _SEAM_PROJECTED + index + 1],
        )
    db.session.commit()


def _reads_of(table, statements):
    """Return how many of *statements* read *table*.

    The COUNT is the measurement: one statement is an eager load of the whole
    set, N is the per-row walk, zero is no load at all.  Three different facts
    that "did any statement mention it" cannot tell apart.
    """
    return sum(1 for statement in statements if table in statement)


def _settled_ids():
    """Return the settled status ids -- the band the producer's settled set is.

    Read through the shared accessor rather than named here, so a case that
    seeds "one transfer per status" cannot disagree with the producer about
    which of them need a settle day.
    """
    # Pylint: ``import-outside-toplevel`` -- deferred so the ref cache is warm
    # inside the app context each case builds.
    # pylint: disable=import-outside-toplevel
    from app.utils.balance_predicates import settled_status_ids
    return settled_status_ids()


@contextmanager
def _statements_issued():
    """Record every SQL statement the engine executes inside the block.

    At the Engine level rather than a session's, so it sees what the PROCESS
    issued whichever session issued it -- the same probe
    ``test_a_loans_price_reads_its_terms`` uses.  It cannot see ``BEGIN`` / ``COMMIT`` /
    ``ROLLBACK``, which psycopg issues through the connection, so the
    assertions below name TABLES rather than counting a total.

    Yields:
        The list of normalised SQL strings, appended to as the block runs.
    """
    seen = []

    def _record(conn, cursor, statement, params, context, executemany):  # noqa: ARG001
        seen.append(" ".join(statement.split()))

    event.listen(Engine, "before_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", _record)


class TestEveryStatusLandsOnce:
    """The two halves together are every live, non-excluded transfer into the loan."""

    def test_the_two_halves_partition_the_transfers_into_the_loan(
        self, app, db, seed_user, seed_periods,
    ):
        """The partition answers EVERY live, non-excluded transfer into the loan, ONCE.

        **The control the partition's whole shape rests on.**  It reads the
        SETTLED half and the PROJECTED half, whose union equals "every live,
        non-excluded transfer into the loan" only while those two halves
        exhaust the band the balance-excluded statuses leave standing.  That
        is a claim about the ``ref.statuses`` seed -- exactly the kind of set
        defined by subtraction that nobody re-censuses -- so a transfer is
        created in EVERY status and the two are compared directly.  Seeding a
        sixth status that is neither projected nor settled nor
        balance-excluded would fail here rather than silently dropping its
        payments from every loan schedule.

        **Re-expressed at plan step balance:X-bi-6-4b** (developer approval
        under rule 5, 2026-09-24).  It compared the feed against
        ``query_shadow_income``'s shadow rows, a query that step deleted when
        both halves became legs of transfers; the claim is unchanged and is
        now stated over the transfers themselves.  The comparison is of
        SORTED LISTS, not sets, so a payment answered twice fails it -- the
        "exactly once" the claim always made.  **It read the two halves
        through the date feed** ``loan_ledger.payment_installments``, which
        unioned them, until plan task plan#3389 deleted the feed; it unions
        them here, and its expected answer is unchanged.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            for index, status in enumerate(StatusEnum):
                _transfer_to_loan(
                    seed_user, loan, seed_periods[index], Decimal("1500.00"),
                    status_enum=status,
                    settled_on=(
                        seed_periods[index].start_date
                        if ref_cache.status_id(status) in _settled_ids()
                        else None
                    ),
                )
            db.session.commit()
            scenario_id = seed_user["scenario"].id
            # The premise, read back after the commit: one transfer in each
            # status.  Received is laid on bare after the settle verb (the
            # helper says why), and a lay that stopped landing would leave a
            # second Paid transfer where the Received one belongs: the
            # comparison below would still pass, and so would a loader that
            # drops Received rows from its settled half (measured by 4d-4a's
            # review: that loader mutant fails this case only while the lay
            # lands).
            assert sorted(
                transfer.status_id
                for transfer in db.session.query(Transfer).filter(
                    Transfer.to_account_id == loan.id,
                )
            ) == sorted(ref_cache.status_id(status) for status in StatusEnum)

            queried = sorted(
                transfer.id
                for transfer in db.session.query(Transfer).filter(
                    Transfer.to_account_id == loan.id,
                    Transfer.scenario_id == scenario_id,
                    Transfer.is_deleted.is_(False),
                    ~Transfer.status_id.in_(balance_excluded_status_ids()),
                )
            )
            halves = loan_loaders.income_legs(
                loan.id, scenario_id, options=(), leg_options=(),
            )
            produced = sorted(
                leg.transfer.id for leg in [*halves.settled, *halves.projected]
            )

            # Non-vacuous in both directions: the query must have kept some
            # transfers and dropped some, or "the two agree" says nothing.
            assert queried, "no transfer into the loan survived to partition"
            assert len(queried) < len(list(StatusEnum)), (
                "no status was excluded -- this case would pass over a "
                "producer that simply read every transfer"
            )
            assert produced == queried


#: The amount model's chain, named by the table it reads rather than by the
#: option that asks for it, so the control grades what reached the DATABASE
#: and not what a caller passed.
#:
#: **The count matters, not the presence, and a mutation measured that.**  A
#: LAZY load reads the same table -- once per row -- so "some statement named
#: it" passes over a caller that states no options at all: spliced that way, an
#: earlier draft of the priced-feed control (deleted with that feed at plan
#: task plan#3388) stayed GREEN.  An eager
#: ``selectinload`` issues ONE query for the whole set, so the fixtures build
#: several payments and the assertions count.
#:
#: **It was ``budget.transfers`` until plan step balance:X-bi-6a**, the table
#: ``Transaction.transfer`` walked to price a projected SHADOW -- the one true
#: N+1 in ``pricing_load_options``.  That step made ``budget.transfers`` the
#: PLAN relation itself: the projected half of the partition is loaded FROM
#: it, once, whatever a caller states, so a count against it grades the
#: loader's own statement and not the caller's.  The pricing chain now starts
#: one table past it, at the parent's DEFINITION (``Transfer.template``),
#: which is reached only for a template-generated parent -- the fixture builds
#: two, on two definitions, for the reason :data:`_SEAM_PROJECTED` states.  The
#: tables past THAT (``template_amount_versions``, ``loan_payment_settings``)
#: are still not asserted: a chained ``selectinload`` issues no query when the
#: step before it found nothing, and a control that names a table its fixture
#: cannot reach grades the fixture.
_PRICING_TABLE = "budget.transfer_templates"
#: The PLAN relation, read exactly once PER HALF by the partition -- each
#: half's own statement (the projected half's since plan step
#: balance:X-bi-6a, the settled half's since balance:X-bi-6-4b), asserted so a
#: payment that arrived through a per-row walk would be seen.  *It was once,
#: for the projected half alone, until the settled half moved onto transfers
#: (rule-5 re-expression, developer approval 2026-09-24).*
_PLAN_TABLE = "budget.transfers"


class TestTheCallerStatesItsOwnEagerLoad:
    """A load that costs a round trip is the CONSUMER's declaration, not the loader's.

    Plan step **balance:X-bl-2a**.  ``query_shadow_income`` baked
    ``pricing_load_options()`` into itself, so a ROW loader decided what its
    callers would traverse -- and a producer cannot know that.  Three of its six
    consumers never touched the chain they paid five statements for; finding
    **N-296** is the same defect inverted, seven batch callers reaching a loader
    without it and paying a query per definition.

    **It was a DISJOINT pair until plan task plan#3388**: this case asserted
    the date feed reads no pricing table, and its sibling that the priced feed
    (``loan_payment_service.get_payment_history``) read them all.  That task
    deleted the priced feed and its case with it (ruling R-R93), and plan task
    plan#3389 deleted the date feed this case read the partition through, so
    it asks the partition directly.  The one caller that states a pricing load
    is the contribution reader
    (:func:`app.services.recorded_contributions.load_transfer_contributions_for_accounts`).
    """

    def test_a_caller_stating_no_load_reads_no_pricing_table(
        self, app, db, seed_user, seed_periods,
    ):
        """``income_legs(options=(), leg_options=())`` prices nothing.

        The whole point of the seam: a caller that reads no figure must not
        pay for -- or be able to fail on -- the tier that prices one.  The
        plan relation itself is read TWICE -- each half's own load (see
        ``_PLAN_TABLE``), not a pricing walk.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            _seam_shadows(seed_user, loan, seed_periods)
            db.session.expire_all()

            with _statements_issued() as statements:
                halves = loan_loaders.income_legs(
                    loan.id, seed_user["scenario"].id,
                    options=(), leg_options=(),
                )

            assert len(halves.settled) + len(halves.projected) == _SEAM_SHADOWS, (
                "the partition came back short -- a control over what a load "
                "reads must first have loaded something"
            )
            assert len({
                leg.transfer.transfer_template_id for leg in halves.projected
            }) == _SEAM_PROJECTED, (
                "the projected payments do not carry distinct definitions -- "
                "the count below could not tell a lazy walk from an eager load"
            )
            reads = _reads_of(_PRICING_TABLE, statements)
            assert reads == 0, (
                f"the partition issued {reads} statement(s) against "
                f"{_PRICING_TABLE}: the amount model's eager load has moved "
                f"back into the loader, so a caller reading no figure is "
                f"paying for the tier that prices one"
            )
            assert _reads_of(_PLAN_TABLE, statements) == 2, (
                "the plan relation was read other than once per half: each "
                "half is loaded from it in ONE statement, whatever the caller "
                "states"
            )


class TestThePartitionIsTotal:
    """A shadow the partition cannot place RAISES rather than vanishing."""

    def test_an_unplaceable_status_is_refused(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A settled row stops being settled: the producer refuses it by name.

        The arm that keeps "settled or Projected, nothing else" from being a
        claim nobody checks.  Constructing a sixth ``ref.statuses`` row would
        mutate a process-wide cache other tests share, so the SEED is left alone
        and the producer's own predicate is narrowed instead -- which puts a real
        row in exactly the state a sixth status would: neither settled nor
        Projected.  The alternative to raising is that the row falls out of the
        loan's schedule, its balance and its plan, silently.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            _transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
                status_enum=StatusEnum.DONE,
                settled_on=seed_periods[1].start_date,
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id
            # It places the row before the predicate is narrowed, so the refusal
            # below is the narrowing and not the fixture.
            assert _legs.income_legs(
                loan.id, scenario_id, options=(), leg_options=(),
            ).settled

            monkeypatch.setattr(_legs, "settled_status_ids", frozenset)

            with pytest.raises(ValueError, match="neither settled nor Projected"):
                _legs.income_legs(
                    loan.id, scenario_id, options=(), leg_options=(),
                )
