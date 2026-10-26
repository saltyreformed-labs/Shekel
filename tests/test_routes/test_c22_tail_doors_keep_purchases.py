"""Plan step ``pay_calendar:C22``: truncate and regenerate refuse a paycheck holding a purchase.

**Finding PC-524, re-measured first** (the step's own instruction).  It was
reproduced on dev ``c3fa6954`` (2026-09-25) in seven cases: "Remove the
tail" and "Regenerate the tail" deleted a purchase the owner had entered,
through the pay period's cascade -- UNASKED on an untouched template row,
which the discard count calls regenerable, and after Confirm & discard on
an override or a rule-less envelope, which it counts.  Plan step
``credit_card:CC-5-4a-4`` (rulings **R-CC54**, **R-CC65**), which reached dev
on 2026-09-29, closed the deletion before this step began: the lock
classifier calls a paycheck holding any payment or purchase
``HOLDS_MOVEMENT`` -- a hard lock, ahead of the discard count, so no
confirmation reaches it -- and ``fk_transaction_entries_transaction_id`` no
longer cascades.  Measured on dev ``5b6a18f1``, all seven were refused and
deleted nothing.  :class:`TestPC524AllSevenAreRefused` keeps them, through
the routes, posting what the forms emit.

**What C22 changed is the refusal's words** (ruling **R-PC116**, developer
2026-10-08, replacing ruling **R-CC66**'s period-only sentence, which
collided with **R-PC112**'s): each locked paycheck is named with every
payment and purchase it holds, its amount and its row --
"The 2026-11-16 paycheck holds 2 payments or purchases you entered (Kroger,
$87.43, in Groceries; Rent's payment, $1,200.00). Remove or move them
first." -- read through ``pay_period_locks.movements_held_in``, the named
reading of the queries the lock classified by.  Sentences are read off the
session's flashes or the exception, so no HTML escaping stands between a
test and the text.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum, TxnTypeEnum
from app.exceptions import PayPeriodLocked
from app.extensions import db as _db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import pay_period_admin, transaction_service, transfer_service
from app.services.pay_calendar import calendar_for
from app.services.pay_period_locks import (
    PeriodLockReason,
    classify_schedule_locks,
    movements_held_in,
)
from app.services.settle_day import SettleDay
from app.utils.dates import display_today
from tests._test_helpers import (
    add_txn,
    all_periods,
    cadence_form_values,
    create_account_of_type,
    create_settled_transfer,
    generate_row_of,
    make_expense_template,
    one_off_row_of,
    repriced_by_the_owner,
    shift_form_value,
)

#: The paycheck holding today in ``seed_periods_today``.
_CURRENT = 4
#: A paycheck inside both doors' tails.
_FUTURE = 7


def _groceries(seed_user, period):
    """The untouched row a recurring $300.00 Groceries envelope's rule wrote."""
    template = make_expense_template(
        _db.session, seed_user, amount="300.00", name="Groceries",
        category_key="Groceries", is_envelope=True,
    )
    return generate_row_of(template, period)


def _overridden_groceries(seed_user, period):
    """That row re-priced to $250.00 by the owner: an override."""
    return repriced_by_the_owner(_groceries(seed_user, period), "250.00")


def _one_off_groceries(seed_user, period):
    """A one-off Groceries envelope: a rule-less definition's row."""
    return one_off_row_of(
        period, name="Groceries", amount="300.00",
        user_id=seed_user["user"].id, account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        category_id=seed_user["categories"]["Groceries"].id,
        is_envelope=True,
    )


def _purchase(client, row_id, amount="87.43", description="Kroger", *,
              direction="charge"):
    """Record a purchase on *row_id* through the add-purchase form.

    The form's controls for a one-account owner: the figure, charge or
    refund, the store and the day -- the ``CC`` box unticked, so absent.
    """
    response = client.post(f"/transactions/{row_id}/entries", data={
        "amount": amount, "direction": direction, "description": description,
        "purchased_on": display_today().isoformat(),
    })
    assert response.status_code == 200


def _take_flashes(client):
    """The session's pending flash messages, raw, consumed."""
    with client.session_transaction() as sess:
        return [message for _category, message in sess.pop("_flashes", [])]


def _truncate_form(periods, where):
    """What the Remove-the-tail card posts to keep through the paycheck before *where*."""
    return {"keep_through_period_id": str(periods[where - 1].id)}


def _regenerate_form(periods, _where):
    """What the Regenerate card posts: every control it renders.

    The corrected first payday is the plan's next one after today's
    paycheck, so the rebuilt tail opens where the writer allows.
    """
    return {
        "new_start_date": periods[_CURRENT + 1].start_date.isoformat(),
        "num_periods": "3",
        **cadence_form_values(),
        "shift": shift_form_value(),
    }


_FORMS = {"truncate": _truncate_form, "regenerate": _regenerate_form}


def _confirm_panel_body(response, door):
    """The hidden inputs the discard-confirm panel in *response* re-posts.

    Parsed from the panel the page rendered, so the test posts what the
    browser would, not a body written by hand.
    """
    html = response.data.decode()
    start = html.index('class="alert alert-warning"')
    panel = html[start:html.index("</form>", start)]
    assert f'action="/pay-periods/{door}"' in panel
    return dict(re.findall(
        r'<input type="hidden" name="([a-z_]+)" value="([^"]*)"', panel,
    ))


def _kroger_refusal(period):
    """Ruling R-PC116's sentence for one $87.43 Kroger purchase in Groceries."""
    return (
        f"The {period.start_date.isoformat()} paycheck holds 1 purchase you "
        f"entered (Kroger, $87.43, in Groceries). Remove or move it first."
    )


class TestPC524AllSevenAreRefused:
    """PC-524's seven cases, each refused by name with nothing deleted."""

    @pytest.mark.parametrize(("door", "where"), [
        ("truncate", _CURRENT),
        ("truncate", _FUTURE),
        ("regenerate", _FUTURE),
    ])
    def test_an_untouched_template_row_is_refused_unasked(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, app, db, auth_client, seed_user, seed_periods_today, door, where,
    ):
        """Cases 1, 2 and 5: no confirmation was ever asked; now a refusal.

        The current paycheck (truncate only: regenerate keeps every paycheck
        that has started) and a future one.  The ledger row's reproduction on
        ``c3fa6954`` had truncate take the current paycheck's purchase and say
        "Removed 5 pay period(s).".

        Pylint: ``too-many-arguments`` / ``too-many-positional-arguments``
        (8/5) -- five fixtures and the two columns of one parametrized case.
        """
        with app.app_context():
            periods = seed_periods_today
            row_id = _groceries(seed_user, periods[where]).id
            db.session.commit()
            _purchase(auth_client, row_id)
            self._refused_and_kept(
                auth_client, seed_user, door, periods, where, row_id,
                _FORMS[door](periods, where),
            )

    @pytest.mark.parametrize("door", ["truncate", "regenerate"])
    @pytest.mark.parametrize("build", [_overridden_groceries, _one_off_groceries])
    def test_a_discardable_row_is_refused_even_through_the_confirm_panel(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, app, db, auth_client, seed_user, seed_periods_today, door, build,
    ):
        """Cases 3, 4, 6 and 7: the panel that once confirmed the loss refuses too.

        The panel is rendered BEFORE the purchase exists -- the owner opens
        it, enters the purchase in another tab, then presses Confirm &
        discard -- so the re-post is the body the page really emitted.  With
        the purchase there first, the lock refuses before any panel renders.

        Pylint: ``too-many-arguments`` / ``too-many-positional-arguments``
        (8/5) -- five fixtures and the two columns of one parametrized case.
        """
        with app.app_context():
            periods = seed_periods_today
            row_id = build(seed_user, periods[_FUTURE]).id
            db.session.commit()
            form = _FORMS[door](periods, _FUTURE)
            panel = auth_client.post(f"/pay-periods/{door}", data=form)
            assert panel.status_code == 422
            confirmed = _confirm_panel_body(panel, door)
            assert confirmed["confirm_discard"] == "true"
            _purchase(auth_client, row_id)

            self._refused_and_kept(
                auth_client, seed_user, door, periods, _FUTURE, row_id, form,
            )
            self._refused_and_kept(
                auth_client, seed_user, door, periods, _FUTURE, row_id,
                confirmed,
            )

    @staticmethod
    def _refused_and_kept(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        client, seed_user, door, periods, where, row_id, body,
    ):
        """Post *body* to *door*; assert the ruled refusal and that nothing went.

        Committed and re-read from the database before the money is asserted,
        so a refusal that rolled back is told apart from one that flushed a
        delete into the session.

        Pylint: ``too-many-arguments`` / ``too-many-positional-arguments``
        (7/5) -- the client and owner, the door and its form, and the
        paycheck and row the refusal must name and keep.
        """
        before = [p.start_date for p in all_periods(seed_user["user"].id)]

        response = client.post(f"/pay-periods/{door}", data=body)

        assert response.status_code == 302
        assert _take_flashes(client) == [_kroger_refusal(periods[where])]
        _db.session.commit()
        _db.session.expire_all()
        assert [
            p.start_date for p in all_periods(seed_user["user"].id)
        ] == before
        assert _db.session.get(Transaction, row_id) is not None
        purchases = _db.session.query(
            TransactionEntry.description, TransactionEntry.amount,
        ).filter_by(transaction_id=row_id).all()
        assert purchases == [("Kroger", Decimal("87.43"))]


def _settle_and_revert(row):
    """Mark *row* Paid today, then set it back: its payment is KEPT (R-BAL61)."""
    transaction_service.settle_transaction(row, settle_day=SettleDay(
        day=display_today(), basis=SettledDayBasisEnum.ENTERED,
    ))
    _db.session.commit()
    transaction_service.apply_requested_status(
        row, ref_cache.status_id(StatusEnum.PROJECTED),
    )
    _db.session.commit()


def _rent_holding_its_payment(seed_user, period):
    """R-PC115's case: a $1,200.00 Rent paid and set back, keeping its payment."""
    rent = generate_row_of(make_expense_template(_db.session, seed_user), period)
    _db.session.commit()
    _settle_and_revert(rent)
    return rent


def _savings(seed_user):
    """A $0.00 Savings account for the transfers, through the shared helper."""
    savings = create_account_of_type(
        seed_user, _db.session, "Savings", "Savings",
        anchor_balance=Decimal("0.00"),
    )
    _db.session.commit()
    return savings


def _truncate_after(seed_user, period):
    """Truncate keeping through *period*; return the refusal."""
    with pytest.raises(PayPeriodLocked) as caught:
        pay_period_admin.truncate_pay_periods(
            seed_user["user"].id, period.id, confirm_discard=True,
        )
    _db.session.rollback()
    return caught.value


class TestTheRefusalNamesWhatEachPaycheckHolds:
    """Ruling R-PC116's sentence in each of its shapes."""

    def test_the_ruled_example_a_purchase_and_a_kept_payment(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The ruling's own words: Kroger in Groceries, and Rent's payment.

        Two movements of two kinds in one paycheck, so the noun is "payments
        or purchases" and the remedy is plural.  Rent was paid $1,200.00 and
        set back to Projected, and kept the payment it recorded.
        """
        with app.app_context():
            periods = seed_periods_today
            groceries = _groceries(seed_user, periods[_FUTURE])
            db.session.commit()
            _purchase(auth_client, groceries.id)
            _rent_holding_its_payment(seed_user, periods[_FUTURE])

            refusal = _truncate_after(seed_user, periods[_FUTURE - 1])

            assert str(refusal) == (
                f"The {periods[_FUTURE].start_date.isoformat()} paycheck holds "
                "2 payments or purchases you entered (Kroger, $87.43, in "
                "Groceries; Rent's payment, $1,200.00). Remove or move them "
                "first."
            )
            assert refusal.blocking == {
                periods[_FUTURE].id: PeriodLockReason.HOLDS_MOVEMENT,
            }

    def test_two_purchases_in_one_row_are_each_named(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Two equal purchases are two purchases, named in the order entered."""
        with app.app_context():
            periods = seed_periods_today
            groceries = _groceries(seed_user, periods[_FUTURE])
            db.session.commit()
            _purchase(auth_client, groceries.id)
            _purchase(auth_client, groceries.id)
            _purchase(auth_client, groceries.id, "12.00", "Target")

            refusal = _truncate_after(seed_user, periods[_FUTURE - 1])

            assert str(refusal) == (
                f"The {periods[_FUTURE].start_date.isoformat()} paycheck holds "
                "3 purchases you entered (Kroger, $87.43, in Groceries; "
                "Kroger, $87.43, in Groceries; Target, $12.00, in Groceries). "
                "Remove or move them first."
            )

    def test_each_paycheck_is_its_own_sentence_and_a_refund_reads_negative(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Two locked paychecks, one movement each: the remedy is plural.

        The refund is the grid's spelling, ``-$12.00``, not ``$-12.00``.
        """
        with app.app_context():
            periods = seed_periods_today
            first = _groceries(seed_user, periods[_FUTURE])
            later = _one_off_groceries(seed_user, periods[_FUTURE + 1])
            db.session.commit()
            _purchase(auth_client, first.id)
            _purchase(auth_client, later.id, "12.00", direction="refund")

            refusal = _truncate_after(seed_user, periods[_FUTURE - 1])

            assert str(refusal) == (
                f"The {periods[_FUTURE].start_date.isoformat()} paycheck holds "
                "1 purchase you entered (Kroger, $87.43, in Groceries). "
                f"The {periods[_FUTURE + 1].start_date.isoformat()} paycheck "
                "holds 1 purchase you entered (Kroger, -$12.00, in Groceries). "
                "Remove or move them first."
            )

    def test_a_transfer_holding_its_payment_is_named_once(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Both legs keep the payment; the owner sees one transfer, one payment.

        The $500.00 one is named "Savings".  The $250.00 one has its name
        cleared: every door names a transfer (its own name, or "Checking to
        Savings"), but ``budget.transfers.name`` admits none, and every
        pay-period refusal calls such a transfer "a transfer".
        """
        with app.app_context():
            periods = seed_periods_today
            savings = _savings(seed_user)
            for amount, name in (("500.00", "Savings"), ("250.00", None)):
                xfer = create_settled_transfer(
                    seed_user, db.session, seed_user["account"], savings,
                    periods[_FUTURE], amount=Decimal(amount),
                    settled_on=display_today(), name=name,
                )
                db.session.commit()
                transfer_service.update_transfer(
                    xfer.id, seed_user["user"].id,
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                )
                db.session.commit()
            xfer.name = None
            db.session.commit()

            refusal = _truncate_after(seed_user, periods[_FUTURE - 1])

            assert str(refusal) == (
                f"The {periods[_FUTURE].start_date.isoformat()} paycheck holds "
                "2 payments you entered (Savings's payment, $500.00; a "
                "transfer's payment, $250.00). Remove or move them first."
            )

    def test_a_paycheck_locked_for_another_reason_is_counted_beside_it(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """A settled row in the NEXT paycheck: named, then counted.

        The paycheck holding the purchase is named; the one locked for its
        settled row is counted in the sentence the refusal has always ended
        with.
        """
        with app.app_context():
            periods = seed_periods_today
            groceries = _groceries(seed_user, periods[_FUTURE])
            db.session.commit()
            _purchase(auth_client, groceries.id)
            add_txn(
                db.session, seed_user, periods[_FUTURE + 1], "Paid", "100.00",
                status_enum=StatusEnum.DONE,
            )
            db.session.commit()

            refusal = _truncate_after(seed_user, periods[_FUTURE - 1])

            assert str(refusal) == (
                f"{_kroger_refusal(periods[_FUTURE])} Operation refused: 1 pay "
                "period(s) are locked (past, settled, or holding posted ledger "
                "entries) and cannot be deleted or rebuilt."
            )

    def test_a_settled_row_in_the_same_paycheck_outranks_the_purchase(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The lock's precedence decides whether a purchase is named at all.

        ``SETTLED_TXN`` ranks above ``HOLDS_MOVEMENT``
        (``pay_period_locks._resolve_lock``), so a paycheck holding a Paid
        row AND a purchase is locked as settled, and the refusal counts it
        without naming the purchase: the purchase is not what the owner
        could remove to unlock it.
        """
        with app.app_context():
            periods = seed_periods_today
            groceries = _groceries(seed_user, periods[_FUTURE])
            db.session.commit()
            _purchase(auth_client, groceries.id)
            add_txn(
                db.session, seed_user, periods[_FUTURE], "Paid", "100.00",
                status_enum=StatusEnum.DONE,
            )
            db.session.commit()

            refusal = _truncate_after(seed_user, periods[_FUTURE - 1])

            assert refusal.blocking == {
                periods[_FUTURE].id: PeriodLockReason.SETTLED_TXN,
            }
            assert str(refusal) == (
                "Operation refused: 1 pay period(s) are locked (past, settled, "
                "or holding posted ledger entries) and cannot be deleted or "
                "rebuilt."
            )


class TestTheNamedReaderIsTheLocksReading:
    """``movements_held_in`` names exactly the paychecks the lock calls holding."""

    def test_every_holding_paycheck_and_no_other_is_named(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """A purchase, a kept row payment and a live transfer's; a HIDDEN transfer holds none.

        The hidden one was finding ``balance:BAL-532``'s state: a settled
        transfer soft-deleted, whose legs kept their payments, so the lock
        held its paycheck and the sentence named it.  Since plan step
        ``balance:X-bi-6-4d-2`` the delete takes them off the books (ruling
        **R-CC75**, "Same as a one-off"), so that paycheck holds nothing and
        is named by neither, like the fifth future paycheck.  (Re-expressed
        with the developer's approval, 2026-10-08: it expected four holding
        paychecks; the other three and their figures are unchanged.)
        """
        with app.app_context():
            periods = seed_periods_today
            groceries = _groceries(seed_user, periods[5])
            db.session.commit()
            _purchase(auth_client, groceries.id)
            rent = _rent_holding_its_payment(seed_user, periods[6])
            savings = _savings(seed_user)
            live = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                periods[7], amount=Decimal("60.00"),
                settled_on=display_today(), name="Savings",
            )
            hidden = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                periods[8], amount=Decimal("70.00"),
                settled_on=display_today(), name="Emergency",
            )
            db.session.commit()
            transfer_service.update_transfer(
                live.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            transfer_service.delete_transfer(
                hidden.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            assert db.session.get(Transfer, hidden.id).is_deleted is True
            future = [period.id for period in periods[5:]]

            held = movements_held_in(future)
            locks = classify_schedule_locks(
                calendar_for(seed_user["user"].id), as_of=display_today(),
            )

            assert {movement.period_id for movement in held} == {
                period_id for period_id in future
                if locks[period_id] is PeriodLockReason.HOLDS_MOVEMENT
            } == {periods[n].id for n in (5, 6, 7)}
            assert _db.session.query(TransactionEntry).filter(
                (TransactionEntry.expense_transfer_id == hidden.id)
                | (TransactionEntry.income_transfer_id == hidden.id),
            ).count() == 0, "the deleted transfer still holds a payment"
            assert [
                (m.period_id, m.item, m.item_name, m.is_payment, m.amount)
                for m in held
            ] == [
                (periods[5].id, ("row", groceries.id), "Groceries", False,
                 Decimal("87.43")),
                (periods[6].id, ("row", rent.id), "Rent", True,
                 Decimal("1200.00")),
                (periods[7].id, ("transfer", live.id), "Savings", True,
                 Decimal("60.00")),
            ]
