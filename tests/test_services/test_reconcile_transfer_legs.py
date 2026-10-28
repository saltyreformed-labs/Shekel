"""
Shekel Budget App -- The reconcile panel and carry-forward walk TRANSFERS (leaf X-bi-6-4c-2)

Plan step ``balance:X-bi-6-4c-2`` moved three readers off a transfer's SHADOW
rows: the reconcile panel offers the transfer's LEG on the statement's account
(``transfer_legs.offerable_transfer_legs``), carry-forward walks the source
period's transfers, and the recurrence engine's records predicate asks
``transfer_legs.transfers_holding_records``.  These tests pin what that leaf
changed and what it declared:

* a leg is keyed by ``(transfer id, account id)`` (ruling **R-BAL87**) and
  ticked under its own form fields (ruling **R-BAL145**), so a transfer whose
  id equals a bill's id is offered and settled beside it rather than
  overwriting it;
* the loader's scope, and that the PARENT's status decides it;
* the transfer block's heading is the leg's label from the endpoints' current
  names (declared), each side is offered on its own account's statement as
  a TRANSFER, and the detail page, the panel's fragment and the true-up's
  modal (beside a bill) offer the transfer's tick (ruling **R-BAL235**
  retired **R-BAL148**'s warning);
* a carry-forward transfer plan is labelled by its FROM side, in transfer-id
  order (finding **BAL-546**, declared).

Plan step ``balance:X-bi-6-4d-2`` deleted three cases that planted a
transfer's shadow (ruling **R-BAL313**) -- the records predicate's own case
(a movement under a hidden shadow), a broken pair offered and settled, and a
loader case under a Cancelled shadow -- and re-expressed two panel cases
that hid one: no shadow can be stored since revision ``82f7b7ed4332``
deleted them (ruling **R-BAL258**), and what each deleted case still read
on a plain transfer is read by another passing case.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from sqlalchemy import text

from app import ref_cache
from app.enums import MovementFigureSourceEnum, SettledDayBasisEnum, StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import (
    account_service,
    carry_forward_service,
    cash_ledger,
    reconcile_service,
    transfer_legs,
)
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import calendar_for
from app.services.settle_day import SettleDay, recorded_settle_day
from tests._test_helpers import (
    create_transfer,
    generate_row_of,
    make_expense_template,
    open_books_before_the_first_assertion,
    refused_by_database_rule,
    settle_day_columns,
    transfer_side_record,
)

#: The civil day the panel's statement is presented for; every period-0 row
#: here lands on or before it.
_OBSERVED_ON = date(2026, 1, 10)


def _reconciled(seed_user, account_id=None):
    """Return the Statement for *account_id* (the seed account by default).

    The account's REAL governing assertion presented for :data:`_OBSERVED_ON`:
    the id must exist (the clearing link's key refuses one that does not); the
    day is the test's.
    """
    account = seed_user["account"].id if account_id is None else account_id
    return reconcile_service.Statement(
        calendar_for(seed_user["user"].id), account,
        replace(
            cash_ledger.governing_anchor(account), observed_on=_OBSERVED_ON,
        ),
    )


def _savings(seed_user, name="Savings"):
    """Create a second cash account whose books open before any day used here."""
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            name=name,
            account_type_id=seed_user["account"].account_type_id,
            anchor_balance=Decimal("100.00"),
        ),
    )
    db.session.flush()
    open_books_before_the_first_assertion(db.session, account)
    return account


def _settle(seed_user, *, transaction_ids=(), transfer_ids=(),
            corrections=None, transfer_corrections=None):
    """Run the write union with each arm's ticks in its own field."""
    return reconcile_service.record_reconciliation(
        reconcile_service.ReconcileSubmission(
            statement=_reconciled(seed_user),
            entry_ids=set(),
            transaction_ids=set(transaction_ids),
            corrections=corrections or {},
            transfer_ids=set(transfer_ids),
            transfer_corrections=transfer_corrections or {},
        ),
    )


def _bill(seed_user, period, name="Electricity"):
    """Create one projected bill in *period*, due on its payday."""
    template = make_expense_template(
        db.session, seed_user, amount="180.00",
        name=name, category_key="Groceries",
    )
    return generate_row_of(template, period)


def _transfer_numbered_like(seed_user, period, row_id, to_account):
    """Create a transfer out of the seed account whose ID equals *row_id*.

    The transfers sequence is set so its next value is *row_id* -- a state
    production reaches on its own whenever the two tables' sequences cross,
    and the one the old single ``transaction_ids`` field could not tell apart.
    """
    db.session.execute(
        text("SELECT setval('budget.transfers_id_seq', :v, false)"),
        {"v": row_id},
    )
    transfer = create_transfer(
        seed_user, db.session, seed_user["account"], to_account, period,
        amount=Decimal("75.00"),
    )
    db.session.commit()
    assert transfer.id == row_id, "the collision was not built"
    return transfer


class TestALegIsKeyedApartFromARow:
    """A transfer id equal to a bill's id is two offers, not one.

    Before this leaf the transfer arm offered the SHADOW row, keyed by the
    shadow's id in one map with the bills, and ticks for both rode one
    ``transaction_ids`` field; keying a leg by its transfer id in that map
    would have silently overwritten the bill's offer.  The leg's key is a pair
    and its tick has its own field (ruling **R-BAL145**).
    """

    def test_a_transfer_whose_id_equals_a_bills_id_is_offered_beside_it(
        self, app, db, seed_user, seed_periods,
    ):
        """Both blocks are published, each under its own key and kind.

        And the leg's caption day, printed beside its tick, is its period's
        first day.
        """
        with app.app_context():
            bill = _bill(seed_user, seed_periods[0])
            db.session.commit()
            transfer = _transfer_numbered_like(
                seed_user, seed_periods[0], bill.id, _savings(seed_user),
            )

            groups = reconcile_service.outstanding_set(
                _reconciled(seed_user),
            ).groups
            by_key = {group.key: group for group in groups}

            assert by_key[bill.id].kind is reconcile_service.OfferKind.BILL
            leg_key = (transfer.id, seed_user["account"].id)
            assert by_key[leg_key].kind is (
                reconcile_service.OfferKind.TRANSFER
            )
            # The day the panel prints beside the leg's tick: the day the
            # transfer lands, its period's first.  Moved here at plan step
            # balance:X-bi-6-4d-2 from the deleted
            # test_a_transfer_whose_twin_was_deleted_is_offered_and_settles
            # (ruling R-BAL313): measured 2026-10-18 without this read, a day
            # added to the expense side's caption failed no test.
            assert by_key[leg_key].settle.attributed_on == (
                seed_periods[0].start_date
            )
            # What each tick POSTS: the same number under two fields.
            assert by_key[bill.id].settle.tick_form.ids_field == (
                "transaction_ids"
            )
            assert by_key[bill.id].settle.amount_field == (
                f"settled_amount-{bill.id}"
            )
            assert by_key[leg_key].settle.tick_form.ids_field == "transfer_ids"
            assert by_key[leg_key].settle.tick_id == transfer.id
            assert by_key[leg_key].settle.amount_field == (
                f"transfer_amount-{transfer.id}"
            )

    def test_each_tick_settles_only_its_own_arms_item(
        self, app, db, seed_user, seed_periods,
    ):
        """The bill's tick leaves the transfer Projected, and the reverse."""
        with app.app_context():
            bill = _bill(seed_user, seed_periods[0])
            db.session.commit()
            transfer = _transfer_numbered_like(
                seed_user, seed_periods[0], bill.id, _savings(seed_user),
            )
            projected = ref_cache.status_id(StatusEnum.PROJECTED)

            assert _settle(seed_user, transaction_ids=[bill.id]) == 1
            db.session.commit()
            db.session.expire_all()
            assert db.session.get(Transaction, bill.id).status_id != projected
            assert db.session.get(Transfer, transfer.id).status_id == projected

            assert _settle(seed_user, transfer_ids=[transfer.id]) == 1
            db.session.commit()
            db.session.expire_all()
            assert db.session.get(Transfer, transfer.id).status_id != projected


class TestTheOfferableLegLoader:
    """``transfer_legs.offerable_transfer_legs``: its scope, and who decides."""

    def test_either_side_is_offered_on_its_own_account(
        self, app, db, seed_user, seed_periods,
    ):
        """The from-side on the source account, the to-side on the destination."""
        with app.app_context():
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            period_ids = {seed_periods[0].id}
            owner = seed_user["user"].id

            (out_leg,) = transfer_legs.offerable_transfer_legs(
                seed_user["account"].id, owner, period_ids, options=(),
            )
            (in_leg,) = transfer_legs.offerable_transfer_legs(
                savings.id, owner, period_ids, options=(),
            )

            assert (out_leg.transfer.id, out_leg.is_income) == (
                transfer.id, False,
            )
            assert (in_leg.transfer.id, in_leg.is_income) == (transfer.id, True)

    def test_the_owner_the_window_and_the_narrowing_each_exclude(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """Another owner, a period outside the window, an unnamed transfer."""
        with app.app_context():
            savings = _savings(seed_user)
            inside = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            later = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[1],
            )
            db.session.commit()
            account = seed_user["account"].id

            def offered(owner, period_ids, transfer_ids=None):
                return {
                    leg.transfer.id
                    for leg in transfer_legs.offerable_transfer_legs(
                        account, owner, period_ids, options=(),
                        transfer_ids=transfer_ids,
                    )
                }

            owner = seed_user["user"].id
            both = {seed_periods[0].id, seed_periods[1].id}
            assert offered(owner, both) == {inside.id, later.id}
            assert offered(owner, {seed_periods[0].id}) == {inside.id}
            assert offered(owner, set()) == set()
            assert offered(owner, both, transfer_ids={later.id}) == {later.id}
            assert offered(seed_second_user["user"].id, both) == set()

    def test_a_parent_that_is_not_projected_is_not_offered(
        self, app, db, seed_user, seed_periods,
    ):
        """A Paid transfer offers no leg: the PARENT's status decides.

        Set Paid around the service with neither side holding a record, the
        shape the status-band rule stores as a ``$0.00`` close, beside the
        figure that close stores, ``0.00`` and typed (ruling **R-BAL365**):
        since plan step ``balance:X-bi-6-4d-2``'s 4d-4b-2 leaf the band
        trigger's figure arm refuses a settled transfer storing no figure.
        Until plan
        step ``balance:X-bi-6-4d-2`` its shadows stayed Projected under it,
        and this case read one as the drift's control (ruling **R-BAL167**
        class 1); the shadow scope leaf ``X-bi-6-4c-2`` replaced offered that
        Projected shadow, and a tick then no-opped the settle and linked a
        Projected row.
        """
        with app.app_context():
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            db.session.execute(
                text(
                    "UPDATE budget.transfers SET status_id = :s, "
                    "settled_figure = 0.00, settled_figure_source_id = :f "
                    "WHERE id = :t"
                ),
                {
                    "s": ref_cache.status_id(StatusEnum.DONE),
                    "f": ref_cache.movement_figure_source_id(
                        MovementFigureSourceEnum.TYPED,
                    ),
                    "t": transfer.id,
                },
            )
            db.session.commit()
            db.session.expire_all()

            assert transfer_legs.offerable_transfer_legs(
                seed_user["account"].id, seed_user["user"].id,
                {seed_periods[0].id}, options=(),
            ) == []

    def test_a_dated_record_under_a_projected_transfer_is_refused_at_commit(
        self, app, db, seed_user, seed_periods,
    ):
        """A side's dated record under a Projected transfer is refused at commit (R-BAL79's state).

        The loader offered savings' side alone: checking's dated movement
        meant its money had moved.  **The state is REFUSED at commit since
        plan step ``balance:X-bi-6-4d-2``** (ruling **R-BAL167** class 2):
        the movement hung off the Checking shadow, which no reader reads
        since, and the side's own DATED record under a Projected transfer
        (ruling **R-BAL88**) is what the status-band rule refuses -- so the
        record is staged on the Checking side, stating no figure (a side's
        record states none since that step's 4d-4 half; the transfer stores
        it), and the commit asserted refused.  The loader's answers on it
        (Checking's side withheld, savings' offered) are not asserted: no
        commit can leave the state they were asked of.
        """
        with app.app_context():
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("75.00"),
            )
            db.session.commit()
            transfer_id = transfer.id
            day = seed_periods[0].start_date
            db.session.add(TransactionEntry(
                expense_transfer_id=transfer_id,
                account_id=seed_user["account"].id,
                owner_id=transfer.user_id,
                user_id=transfer.user_id,
                description="Drift record",
                purchased_on=day,
                covers_settlement=True,
                **settle_day_columns(day),
            ))
            db.session.flush()

            with refused_by_database_rule(
                r"is not settled but 1 of its payment records are dated",
            ) as caught:
                db.session.commit()
            db.session.rollback()

            assert f"transfer {transfer_id} " in str(caught.value)
            assert transfer_side_record(
                db.session, transfer_id, seed_user["account"].id,
            ) is None


class TestATickStatesItsOwnSide:
    """The reconcile tick states the day for the side on ITS account only.

    Plan step ``balance:X-bi-6-4c-3`` (design D5, ruling **R-BAL142**): the
    tick asserted a balance for the seed account, so the side on that account
    takes the statement day as ``asserted`` and its clearing link; the other
    side, on an account whose statement nobody reconciled, BORROWS that day and
    holds no link.  The tick stated the day for BOTH sides until that step.
    """

    def test_the_ticked_side_is_asserted_and_linked_and_the_far_side_borrows(
        self, app, db, seed_user, seed_periods,
    ):
        """Checking asserted on the statement day with its link; Savings borrowed, unlinked.

        Each side is its RECORD on the transfer since plan step
        ``balance:X-bi-6-4d-2`` (ruling **R-BAL88**), read by its own SQL,
        where each shadow carried the day and the link until then (ruling
        **R-BAL167** class 1).
        """
        with app.app_context():
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("75.00"),
            )
            db.session.commit()

            assert _settle(seed_user, transfer_ids=[transfer.id]) == 1
            db.session.commit()
            db.session.expire_all()

            ticked, far = (
                transfer_side_record(db.session, transfer.id, account_id)
                for account_id in (seed_user["account"].id, savings.id)
            )
            assert recorded_settle_day(ticked) == SettleDay(
                day=_OBSERVED_ON, basis=SettledDayBasisEnum.ASSERTED,
            )
            assert ticked.reconciled_by_id is not None
            assert recorded_settle_day(far) == SettleDay(
                day=_OBSERVED_ON, basis=SettledDayBasisEnum.BORROWED,
            )
            assert far.reconciled_by_id is None


class TestTheBlockReadsTheLeg:
    """What the panel prints for a transfer."""

    def test_the_heading_is_the_legs_label_from_the_CURRENT_account_name(
        self, app, db, seed_user, seed_periods,
    ):
        """DECLARED: a renamed endpoint re-labels the block.

        The block was headed by the shadow's stored ``name``, written when the
        transfer was created; it is the leg's label now, composed from the
        endpoints' current names -- the change leaf X-bi-6-1 made on the grid.
        The shadow's stale copy was this case's control until plan step
        ``balance:X-bi-6-4d-2`` deleted the shadows (ruling **R-BAL167**
        class 1).
        """
        with app.app_context():
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            savings.name = "Emergency Fund"
            db.session.commit()

            (block,) = [
                group for group in reconcile_service.outstanding_set(
                    _reconciled(seed_user),
                ).groups
                if group.key == (transfer.id, seed_user["account"].id)
            ]

            assert block.name == "Transfer to Emergency Fund"

    def test_the_income_side_is_offered_on_its_own_statement_as_a_TRANSFER(
        self, app, db, seed_user, seed_periods,
    ):
        """The savings statement offers the INCOME side, under its own key, as a TRANSFER.

        The cases above read the from-side on the seed account's panel; the
        same transfer's to-side is the savings account's to tick, keyed by
        ``(transfer id, savings id)`` and offered as the one act a transfer
        is (ruling **R-FC**): the tally counts it a deposit, the section
        does not.  Its caption day, printed beside the tick, is the day the
        transfer lands, its period's first.  Added at plan step
        ``balance:X-bi-6-4d-2``: measured 2026-10-18, offering the income
        side as a Deposit failed no test, nor did a day added to its caption.
        """
        with app.app_context():
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()

            groups = reconcile_service.outstanding_set(
                _reconciled(seed_user, savings.id),
            ).groups
            by_key = {group.key: group for group in groups}

            income_side = by_key[(transfer.id, savings.id)]
            assert income_side.kind is reconcile_service.OfferKind.TRANSFER
            assert income_side.settle.attributed_on == seed_periods[0].start_date

    def test_the_panel_offers_its_tick(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The detail page and the panel's fragment offer the tick; the true-up opens its modal.

        The true-up (whose response builds the prompt after its write), the
        cash detail page (which builds the panel inline), and the panel's own
        fragment.  With the transfer offerable there IS something to tick, so
        the true-up opens its modal (``prompt_fragment`` gates on ticks).
        Until plan step ``balance:X-bi-6-4d-2`` the transfer's savings shadow
        was hidden here and each door was read for no "damaged" warning
        (ruling **R-BAL235**); no shadow can be stored now, and no template
        prints that word.
        """
        with app.app_context():
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("500.00"),
            )
            db.session.commit()
            account_id = seed_user["account"].id
            transfer_id = transfer.id

        response = auth_client.patch(
            f"/accounts/{account_id}/true-up",
            data={
                "anchor_balance": "4537.66",
                "observed_on": _OBSERVED_ON.isoformat(),
            },
        )
        assert response.status_code == 200, response.data
        assert b"reconcileModal" in response.data
        tick = f'name="transfer_ids" value="{transfer_id}"'
        page = auth_client.get(f"/accounts/{account_id}/details")
        assert page.status_code == 200, page.data
        assert tick in page.data.decode()
        response = auth_client.get(f"/accounts/{account_id}/reconcile")
        assert response.status_code == 200, response.data
        body = response.data.decode()
        assert tick in body, body

    def test_the_true_up_modal_offers_the_transfer_beside_a_bill(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """With a bill to tick too, the post-true-up modal still offers the transfer's tick.

        Until plan step ``balance:X-bi-6-4d-2`` the transfer's savings shadow
        was hidden here and the modal was read for no "damaged" warning
        (ruling **R-BAL235**); no shadow can be stored now, and no template
        prints that word.
        """
        with app.app_context():
            _bill(seed_user, seed_periods[0])
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("500.00"),
            )
            db.session.commit()
            account_id = seed_user["account"].id
            transfer_id = transfer.id

        response = auth_client.patch(
            f"/accounts/{account_id}/true-up",
            data={
                "anchor_balance": "4537.66",
                "observed_on": _OBSERVED_ON.isoformat(),
            },
        )
        assert response.status_code == 200, response.data
        body = response.data.decode()
        assert "reconcileModal" in body
        assert f'name="transfer_ids" value="{transfer_id}"' in body


class TestCarryForwardWalksTransfers:
    """The preview's transfer plans: one per transfer, labelled, ordered."""

    def test_a_transfer_plan_is_labelled_by_its_FROM_side_in_transfer_id_order(
        self, app, db, seed_user, seed_periods,
    ):
        """DECLARED (BAL-546): "Transfer to <account>", ordered by transfer id.

        The label was whichever shadow the context's unordered query returned
        first, and so was the order; the figure is the transfer's resolved
        amount, as it was.
        """
        with app.app_context():
            savings = _savings(seed_user)
            brokerage = _savings(seed_user, name="Brokerage")
            first = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("40.00"),
            )
            second = create_transfer(
                seed_user, db.session, brokerage, seed_user["account"],
                seed_periods[0], amount=Decimal("60.00"),
            )
            db.session.commit()

            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id,
                seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            plans = [
                plan for plan in preview.plans
                if plan.kind == carry_forward_service.PLAN_KIND_TRANSFER
            ]

            assert [plan.item.transfer.id for plan in plans] == [
                first.id, second.id,
            ]
            assert [plan.item.name for plan in plans] == [
                "Transfer to Savings",
                f"Transfer to {seed_user['account'].name}",
            ]
            assert [plan.budget for plan in plans] == [
                Decimal("40.00"), Decimal("60.00"),
            ]


class TestTheFormCarriesATransfersTickInItsOwnFields:
    """The route end to end: the panel renders, and parses, both fields."""

    def test_a_bill_and_a_transfer_of_one_number_both_settle_through_the_form(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """One POST, one number in two fields: two settles, no partial notice."""
        with app.app_context():
            bill = _bill(seed_user, seed_periods[0])
            db.session.commit()
            transfer = _transfer_numbered_like(
                seed_user, seed_periods[0], bill.id, _savings(seed_user),
            )
            account_id = seed_user["account"].id
            number = bill.id

        # The route reconciles against the account's governing assertion, so
        # one is declared through the true-up door for the day both period-0
        # items are overdue by.
        response = auth_client.patch(
            f"/accounts/{account_id}/true-up",
            data={
                "anchor_balance": "4537.66",
                "observed_on": _OBSERVED_ON.isoformat(),
            },
        )
        assert response.status_code == 200, response.data

        body = auth_client.get(f"/accounts/{account_id}/reconcile").data.decode()
        assert f'name="transaction_ids" value="{number}"' in body
        assert f'name="settled_amount-{number}"' in body
        assert f'name="transfer_ids" value="{number}"' in body
        assert f'name="transfer_amount-{number}"' in body
        # The two checkboxes' DOM ids differ too (the template prefixes the
        # panel id with ``reconcile-``), so each label still names its box.
        control = f"reconcile-reconcile-panel-{account_id}"
        assert f'id="{control}-t{number}"' in body
        assert f'id="{control}-l{number}"' in body

        response = auth_client.post(
            f"/accounts/{account_id}/reconcile",
            data={
                "transaction_ids": [str(number)],
                f"settled_amount-{number}": "180.00",
                "transfer_ids": [str(number)],
                f"transfer_amount-{number}": "75.00",
            },
        )
        assert response.status_code == 200, response.data
        assert b"already been settled" not in response.data

        with app.app_context():
            projected = ref_cache.status_id(StatusEnum.PROJECTED)
            assert db.session.get(Transaction, number).status_id != projected
            assert db.session.get(Transfer, transfer.id).status_id != (
                projected
            )
