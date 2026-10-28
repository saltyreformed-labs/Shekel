"""Integration: a transfer's settle books ONE figure, whichever door asked.

Plan step **X-f2-c3**, ruling **R-FA** applied to the transfer table.  FOUR
doors can move a transfer into the settled band and exactly ONE of them froze
an auto-derived loan payment's live payment-date cash:

* the grid's shadow "Mark Paid" (``routes/transactions/_shadow_mutations``) --
  it called ``loan_payment_service.live_loan_payment_amount`` itself;
* the transfers page's "Mark Done" (``routes/transfers/mutations.mark_done``);
* the transfer full-edit Status dropdown (``_execute_transfer_update``);
* a transaction PATCH landing on a shadow (``_apply_shadow_update``).

The other three booked the stored estimate -- the creation-time escrow -- so
the same payment recorded a different figure depending on which control the
operator pressed.  That is finding **N-219**'s shape on this table, and a ROUTE
holding a money rule is this arc's own root cause 1.
``transfer_service.settle_transfer`` is the rule's name now and
``update_transfer`` dispatches to it, so these grade the FIGURE at the service
and then prove each door reaches it.

**WHICH COLUMN the freeze writes stays ``actual_amount``, and that is a
developer ruling (2026-08-12) rather than an oversight.**  Ruling **R-FH** says
a derivation belongs in the row's OWN amount and finding **N-241** records that
it does not go there; this branch BUILT that move and withdrew it, because two
adversarial reviews measured it unsafe on today's schema -- a manual payment's
base is ``estimated_amount``, so writing the freeze there compounds the standing
extra across settle/revert cycles (`$1,599.10` -> `$1,699.10` -> `$1,799.10`),
and a leftover ``actual_amount`` from a reverted settle would OUTRANK the frozen
figure through ``COALESCE``.  Plan step **X-au-c**'s ``amount_source`` column is
what makes the write idempotent and authoritative, so the move is that step's.
These cases therefore assert ``actual_amount`` AND ``effective_amount``: the
first is where the freeze lands today, the second is what the ledger books, and
the second is the one that must never change.

**The arithmetic is hand-computed and shown.**  Loan $200,000 / 6% / 360mo:
    P&I    = amortize(200000, 0.06, 360)  = 1,199.10
    escrow = 3,600.00 / 12                =   300.00
    PITI   = 1,199.10 + 300.00            = 1,499.10
The template's stored ``default_amount`` is a deliberately stale ``$1.00``, so
a settle that books ``$1.00`` is a settle that missed the freeze and a settle
that books ``$1,499.10`` is one that took it.

**On production this changes `$0.00` today**: ``budget.loan_payment_settings``
holds ZERO rows, so no live transfer is an auto-derived loan payment and all 17
settled transfer shadows on Checking carry ``actual_amount = NULL``.  The route
that writes that settings row (``routes/loan/payment_transfer.py``) is live, so
the split opens on the next loan payment transfer created through the loan page.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.extensions import db
from app.models.transfer import Transfer
from app.services import (
    account_service,
    posting_service,
    status_seam,
    transfer_service,
    transfer_recurrence,
)
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.pay_calendar import FiledRow, calendar_for
from app.utils.dates import display_today
from tests._test_helpers import (
    on_both_sides,
    typed,
    amount_basis_for,
    an_entered_day,
    create_account_of_type,
    create_transfer,
    open_books_before_the_first_assertion,
    transfer_side_figure,
    transfer_side_record,
)
from tests.test_integration.test_loan_transfer_live_amount._helpers import (
    _build_derived_loan_transfer,
    _side_leg,
)
from app.services.row_valuation import settled_contribution
from app.services.transfer_legs import parent_of
from app.models.amount_ownership import AmountOwnership
from tests._test_helpers import rendered_transfer_amount

#: P&I 1,199.10 + escrow 300.00, the figure the freeze captures.
_LIVE_PITI = Decimal("1499.10")
#: The template's deliberately stale stored amount.
_STALE = Decimal("1.00")


def _derived_loan_transfer(seed_user, seed_periods):
    """Return one projected loan-payment transfer.

    Built on the derive-from-loan fixture the live-override integration test
    already owns, so the two grade the same loan with the same arithmetic: a
    $200k / 6% / 360mo mortgage with $3,600/yr escrow behind a recurring
    transfer whose stored amount is stale.

    It returned the transfer AND its expense shadow until plan step
    ``balance:X-bi-6-4d-2``, where a transfer became one row and the shadow
    stopped being born (ruling **R-BAL167** class 4).  Four callers read the
    shadow and every other discarded it: three read only its ``account_id``,
    the transfer's from-account, where the expense twin sat; one read the
    period it was filed in and its due date, both copied off the transfer.
    Each reads the transfer's own column now.
    """
    loan, _escrow, scenario_id, template, _rule, _periods = (
        _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
    )
    transfer_recurrence.generate_for_template(
        template,
        GenerationSchedule.for_period_ids(
            BalanceContext.build(template.user_id), {p.id for p in seed_periods},
        ),
        scenario_id,
    )
    db.session.commit()

    xfer = (
        db.session.query(Transfer)
        .filter_by(to_account_id=loan.id)
        .order_by(Transfer.id)
        .first()
    )
    assert xfer is not None, "expected a generated loan-payment transfer"
    return xfer


def _plain_transfer(
    seed_user, seed_periods, amount="250.00", destination="Savings",
):
    """Return a projected checking-to-savings transfer -- NO loan behind it.

    The control shape: its template carries no ``loan_payment_settings`` row,
    so the freeze resolves to ``None`` and the row books its own estimate.  It
    is what every live transfer on production is today.

    *destination* names the receiving account because ``uq_accounts_user_name``
    is unique per owner, so a case wanting two independent transfers has to ask
    for two destinations.
    """
    savings = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            name=destination,
            account_type_id=seed_user["account"].account_type_id,
            anchor_balance=Decimal("100.00"),
        ),
    )
    db.session.flush()
    # Its BOOKS open before anything this fixture dates (plan step
    # X-f3c-2b, ruling **R-HG**): ``create_account`` opens them on the day it
    # asserts -- the owner's today -- and this suite settles on or before it.
    open_books_before_the_first_assertion(db.session, savings)
    return create_transfer(
        seed_user, db.session, seed_user["account"], savings,
        seed_periods[0], amount=Decimal(amount),
    )


def _sides(xfer_id):
    """Return *xfer_id*'s two side accounts, the from (expense) side first.

    What this file asks a settle about since plan step
    ``balance:X-bi-6-4d-2`` (ruling **R-BAL167** class 4): a side's payment
    record hangs off the TRANSFER and nothing keeps a twin's status, day or
    figure, so each side is read as the transfer and its record, never as its
    twin row (the deleted ``_shadows``, which went with its last caller at plan
    step ``balance:X-bi-6-4d-2``, where no twin is born).
    """
    xfer = db.session.get(Transfer, xfer_id)
    return (xfer.from_account_id, xfer.to_account_id)


def _side_settlement(xfer_id, account_id):
    """Return the settlement *xfer_id*'s side on *account_id* records, or ``None``.

    ``status_seam.recorded_settlement`` of :func:`_side_leg`'s parent (ruling
    **plan#1983**): the record's figure and who wrote it.  It read the
    side-shaped twin ``recorded_leg_settlement`` until that ruling, and the
    ``recorded_settlement(shadow)`` before plan step ``balance:X-bi-6-4d-2``
    (ruling **R-BAL167** class 4).
    """
    return status_seam.recorded_settlement(
        parent_of(_side_leg(xfer_id, account_id)),
    )


class TestTheSettleFreezeIsTheSERVICEs:
    """The amount rule lives at the one chokepoint, so no door can miss it."""

    def test_a_plain_settle_freezes_the_live_payment_date_cash(
        self, app, db, seed_user, seed_periods,
    ):
        """One-click Paid books `$1,499.10`, not the stale `$1.00`.

        The rule's whole point: an auto-derived loan payment's stored estimate
        is the creation-time escrow, and what actually leaves checking is the
        live P&I + escrow-as-of on the shadow's own DUE date -- the same figure
        the genesis split subtracts, so ``cash == split`` holds by construction.

        Each side is read as the transfer and its side's record, where it was
        read off its twin row (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).  The PLAN column asserted empty below is the
        transfer's, where it was each twin's AND the transfer's until plan
        step balance:X-bi-6-4d-2 stopped the twins being born (ruling
        R-BAL167 class 1).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()

            db.session.expire_all()
            for account_id in _sides(xfer.id):
                # What it BOOKS -- the ledger figure, and the whole point.
                assert settled_contribution(
                    parent_of(_side_leg(xfer.id, account_id)),
                ) == _LIVE_PITI
                # What it RECORDS -- the side's record's figure.
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) == _LIVE_PITI
            # The transfer's PLAN column is empty after the settle, and that
            # is what keeps the freeze idempotent (plan step X-au-g-2c-2): a
            # settle that wrote its booked figure there would hand a later
            # settle its own output to derive from.  The stale ``$1.00`` once
            # sat on each shadow, where the deleted ``_manual_shadow_amount``
            # read it as its base; it moved to the PARENT at X-au-g-2c-2 and
            # is GONE at X-au-f, and the shadows themselves are gone since
            # plan step balance:X-bi-6-4d-2.  So the ONE row left stores no
            # figure for a settle to derive from -- the same claim with
            # nothing left to violate it.
            assert db.session.get(Transfer, xfer.id).amount is None

    def test_a_typed_figure_BEATS_the_freeze(
        self, app, db, seed_user, seed_periods,
    ):
        """A human read `$1,512.44` off the statement; the derivation yields.

        The precedence half of the rule.  A figure somebody typed is a FACT
        about money that moved; the freeze is a derivation, and a derivation
        never overwrites a fact.  Each side is read as the transfer and its
        side's record, not its twin row (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                figure=typed(Decimal("1512.44")),
            )
            db.session.commit()

            db.session.expire_all()
            for account_id in _sides(xfer.id):
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) == Decimal("1512.44")
                assert settled_contribution(
                    parent_of(_side_leg(xfer.id, account_id)),
                ) == Decimal("1512.44")

    def test_an_ECHOED_prefill_is_not_written(
        self, app, db, seed_user, seed_periods,
    ):
        """Submitting exactly what the row would book leaves the column NULL.

        The reconcile panel PREFILLS its amount box, so every correctable row
        on the form posts a figure whether the user touched it or not.  Writing
        an untouched echo would populate a column that is NULL on all 17 settled
        transfer shadows in production and destroy the only signal that says a
        human read one off a statement.

        Graded on a NON-loan transfer, where the row books its own estimate:
        the echo is the estimate, and the column must stay NULL.  Each side is
        read as the transfer and its side's record, not its twin row (ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            xfer = _plain_transfer(seed_user, seed_periods)
            db.session.commit()

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                figure=typed(Decimal("250.00")),
            )
            db.session.commit()

            db.session.expire_all()
            for account_id in _sides(xfer.id):
                # An uncorrected settle RECORDS what it booked on the
                # ``resolved`` source (plan step X-au-c3; the covering
                # movement since X-bi-4b-2, the side's record since
                # X-bi-6-4d-2).  This asserted a NULL figure until X-au-c3,
                # because a NULL was the only signal that no human had typed
                # one; the source carries that now, so the record can state
                # the figure AND stay distinguishable.
                assert _side_settlement(xfer.id, account_id).source is (
                    MovementFigureSourceEnum.RESOLVED
                )
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) == Decimal("250.00")
                assert settled_contribution(
                    parent_of(_side_leg(xfer.id, account_id)),
                ) == Decimal("250.00")

    # ``test_an_explicit_None_still_CLEARS_a_typed_actual`` lived here until
    # plan step X-au-c3, and BOTH halves of its premise are gone.  It wrote
    # ``actual_amount = 310.00`` onto an UNSETTLED transfer -- a figure now
    # RECORDS a settle, so the service refuses that outright -- and then had an
    # explicit ``None`` clear it, which was a distinct act only while a settled
    # transfer carrying no figure was a legal state.  A settled row always
    # records what moved, so a ``None`` means what an empty box means (nobody
    # typed one) and there is no clearing act for it to request;
    # ``_update._fields_the_settle_left`` and ``_apply_remaining_fields``
    # document the same removal on the app side.  The act that DOES release a
    # record is leaving the settled band, graded by
    # ``test_transfer_service.test_the_edit_door.TestUpdateTransfer.
    # test_a_REVERT_is_what_clears_a_settled_transfers_figure``.

    def test_is_override_in_the_SAME_call_suppresses_the_freeze(
        self, app, db, seed_user, seed_periods,
    ):
        """Retyping the amount and marking Paid keeps the typed figure.

        The transfer edit route auto-sets ``is_override`` whenever a
        template-linked transfer's amount moves, so this is exactly what a
        "correct the payment and mark it Paid" save sends.  The flag says the
        OPERATOR owns this amount, and reading its PRE-edit value would freeze
        a derived `$1,499.10` straight over the `$1,325.00` the user had just
        typed -- which is why the dispatch runs after the caller-stated facts
        rather than before them.  Each side's figure is read as the transfer
        and its side's record, not its twin row (ruling R-BAL167 class 1, plan
        step balance:X-bi-6-4d-2).  The stored `$1,325.00` is read off the
        transfer, which owns it, where each twin's copy was read until plan
        step balance:X-bi-6-4d-2 stopped the twins being born (ruling R-BAL167
        class 1).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                amount_ownership=AmountOwnership.own(Decimal("1325.00")),
                # The caller states that a HUMAN typed this figure (ruling
                # R-JR, plan step X-au-h).  The route says it by comparing the
                # submitted amount against the one it rendered; a service
                # caller says it here.  ``is_override`` no longer carries the
                # fact -- it means only "this row is the owner's, not the
                # rule's" -- so the retype this case is named for has to be
                # stated rather than inferred from the flag.

                is_override=True,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()

            db.session.expire_all()
            assert db.session.get(Transfer, xfer.id).amount == Decimal("1325.00")
            for account_id in _sides(xfer.id):
                # An uncorrected settle RECORDS what it booked on the
                # ``derived`` basis (plan step X-au-c3).  This asserted a NULL
                # figure until that step, because a NULL was the only signal
                # that no human had typed one; the basis carries that now, so
                # the record can state the figure AND stay distinguishable.
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) == Decimal("1325.00")
                assert settled_contribution(
                    parent_of(_side_leg(xfer.id, account_id)),
                ) == Decimal("1325.00")

    def test_a_re_settle_does_not_rewrite_the_frozen_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """The freeze is ONE-SHOT, and a stale tab must not move money.

        ``done -> done`` is a legal identity transition, so a replayed POST from
        a page left open reaches the service again.  The capture is gated on the
        shadow still being Projected, and the dispatch runs BEFORE the status is
        applied so a genuine first settle still sees that -- but a re-settle
        resolves to nothing and the recorded cash stands, even after the loan's
        escrow moves underneath it.  Each side is read as the transfer and its
        side's record, not its twin row (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            done_id = ref_cache.status_id(StatusEnum.DONE)

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, status_id=done_id,
            )
            db.session.commit()
            db.session.expire_all()
            assert transfer_side_figure(
                db.session, xfer.id, xfer.from_account_id,
            ) == _LIVE_PITI

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, status_id=done_id,
            )
            db.session.commit()

            db.session.expire_all()
            for account_id in _sides(xfer.id):
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) == _LIVE_PITI
                assert settled_contribution(
                    parent_of(_side_leg(xfer.id, account_id)),
                ) == _LIVE_PITI

    def test_settle_amount_publishes_what_a_tick_WILL_book(
        self, app, db, seed_user, seed_periods,
    ):
        """The panel's figure and the booked figure come from one expression.

        ``leg_settle_amount`` is what the reconcile panel renders; the dispatch
        resolves its own figure through the same two functions.  A panel showing
        one number beside a verb that books another is this arc's own root cause
        1 applied to a screen.

        It asked ``settle_amount`` of the expense TWIN until plan step
        ``balance:X-bi-6-4d-2``, which prices a leg off its transfer and its
        side's record instead; the offer is asked of the from-side LEG and the
        booking read off that side's record (ruling R-BAL167 class 1, plan
        step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            leg = _side_leg(xfer.id, xfer.from_account_id)

            offered = transfer_service.leg_settle_amount(leg, amount_basis_for(leg))
            assert offered == _LIVE_PITI

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()

            db.session.expire_all()
            assert settled_contribution(
                parent_of(_side_leg(xfer.id, xfer.from_account_id)),
            ) == offered
            assert transfer_side_figure(
                db.session, xfer.id, xfer.from_account_id,
            ) == offered


class TestEveryDoorReachesTheSameFigure:
    """The census, from the doors themselves -- three of four used to differ."""

    def test_the_transfers_page_mark_done_freezes(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """``POST /transfers/instance/<id>/mark-done``, which did NOT freeze.

        The door that made this a defect rather than a design: it sent
        ``status_id`` alone, so an auto-derived loan payment settled at its
        creation-time escrow here and at the live figure from the grid.  Each
        side is read as the transfer and its side's record, not its twin row
        (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id = xfer.id

        response = auth_client.post(f"/transfers/instance/{xfer_id}/mark-done")
        assert response.status_code == 200

        with app.app_context():
            for account_id in _sides(xfer_id):
                assert settled_contribution(
                    parent_of(_side_leg(xfer_id, account_id)),
                ) == _LIVE_PITI
                assert transfer_side_figure(
                    db.session, xfer_id, account_id,
                ) == _LIVE_PITI

    def test_the_grid_leg_mark_done_still_freezes(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """The grid's Mark Paid on a transfer LEG, the door that always froze.

        **Re-expressed at plan step balance:X-bi-6-1 (ruling R-BAL87)**: it
        was ``POST /transactions/<shadow>/mark-done``, deleted with the shadow
        branch; the grid's Mark Paid posts to the transfer's own door with
        ``leg_account_id`` now.  The control for the move: the rule left the
        route for the service, so the figure it books must be unchanged.  Each
        side is read as the transfer and its side's record, not its twin row
        (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).  The leg
        pressed is the transfer's from-side, where it was the expense twin's
        account until plan step balance:X-bi-6-4d-2 stopped the twin being
        born -- the same account (ruling R-BAL167 class 1).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id, leg_account_id = xfer.id, xfer.from_account_id

        response = auth_client.post(
            f"/transfers/instance/{xfer_id}/mark-done",
            data={"leg_account_id": str(leg_account_id)},
        )
        assert response.status_code == 200
        assert f'data-leg-account-id="{leg_account_id}"' in response.data.decode()

        with app.app_context():
            for account_id in _sides(xfer_id):
                assert settled_contribution(
                    parent_of(_side_leg(xfer_id, account_id)),
                ) == _LIVE_PITI
                assert transfer_side_figure(
                    db.session, xfer_id, account_id,
                ) == _LIVE_PITI

    def test_the_transfer_full_edit_status_dropdown_freezes(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """``PATCH /transfers/instance/<id>`` with the payload the FORM sends.

        The third door, and the one whose transaction twin is finding
        **N-219**: it flipped the status through the service without ever
        asking what the payment was worth.

        **It posts ``amount`` because the real form always does**, and that is
        the whole point of this case rather than a detail.  An earlier version
        submitted ``status_id`` alone, which no browser can produce:
        ``_transfer_full_edit.html`` renders the Amount input on every editable
        row and an HTML form posts every input it renders.  Against that
        realistic payload the door did NOT freeze -- the route set
        ``is_override`` on the mere PRESENCE of the field, which tells the
        settle "the operator owns this amount" and suppresses the derivation.
        Measured before the fix: `$1.00` of cash booked against a `$1,000.00`
        interest + `$300.00` escrow split.

        **Since plan step X-au-h the route asks whether a HUMAN AUTHORED the
        figure, not whether it MOVED** (ruling R-JR): the form posts the figure
        it rendered, and an echo is one that comes back equal to it.  The
        payload below carries that companion, which is what makes this an echo
        rather than a retype -- and the door drops an unauthored figure rather
        than forwarding it, so the service never sees one.  The paragraph that
        described the old moved-against-the-stored-column test is gone with the
        comparison it named.  Each side is read as the transfer and its side's
        record, not its twin row (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id = xfer.id
            version = xfer.version_id
            # Exactly what the rendered form carries back: the value already in
            # the box, unchanged, beside the status the user did change.
            rendered_amount = rendered_transfer_amount(xfer)
            period_id = xfer.pay_period_id

        response = auth_client.patch(
            f"/transfers/instance/{xfer_id}",
            data={
                "amount": rendered_amount,
                # What the box was RENDERED with (ruling R-JR, plan step
                # X-au-h).  Equal to the figure above, which is what makes this
                # an ECHO rather than a retype -- and the echo is the whole
                # subject of this case.  Omitting it would land the save on the
                # fail-closed arm, raise the flag, suppress the freeze, and
                # reproduce the very defect the case exists to prevent.
                "amount_as_rendered": rendered_amount,
                "pay_period_id": period_id,
                # **No ``due_date``, and its absence is what a browser sends.**
                # The popover renders a GENERATED transfer's due date as TEXT
                # since plan step X-au-f (finding **BAL-476**): the date is its
                # definition's and it is now what prices the row, so the form
                # offers no input and the route REFUSES the field
                # (``_reject_generated_due_date_edit``).  Posting it here would
                # be testing a request no form can produce, against a gate that
                # correctly answers 400.
                "status_id": ref_cache.status_id(StatusEnum.DONE),
                "version_id": version,
            },
        )
        assert response.status_code == 200, response.data

        with app.app_context():
            for account_id in _sides(xfer_id):
                assert settled_contribution(
                    parent_of(_side_leg(xfer_id, account_id)),
                ) == _LIVE_PITI
                assert transfer_side_figure(
                    db.session, xfer_id, account_id,
                ) == _LIVE_PITI

    def test_a_transfer_PATCH_from_a_grid_leg_freezes(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """The transfer PATCH asked from a grid LEG -- the FOURTH door.

        **Re-expressed at plan step balance:X-bi-6-1 (ruling R-BAL87)**: it
        was ``PATCH /transactions/<shadow id>``, branched to
        ``_shadow_mutations._apply_shadow_update``, which mapped the submitted
        transaction fields onto transfer-service kwargs.  That module is
        deleted; the leg's popover posts the transfer PATCH with
        ``leg_account_id`` (its Status dropdown is how a grid user settles a
        transfer from the form), so a settling ``status_id`` arriving here is
        a settle that must freeze like any other.  Without this case the
        census is a claim in a docstring: three doors graded, one asserted.
        Each side is read as the transfer and its side's record, not its twin
        row (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).  The leg
        asked from is the transfer's from-side, where it was the expense
        twin's account until plan step balance:X-bi-6-4d-2 stopped the twin
        being born -- the same account (ruling R-BAL167 class 1).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id, leg_account_id = xfer.id, xfer.from_account_id
            version = xfer.version_id

        response = auth_client.patch(
            f"/transfers/instance/{xfer_id}",
            data={
                "status_id": ref_cache.status_id(StatusEnum.DONE),
                "version_id": version,
                "leg_account_id": str(leg_account_id),
            },
        )
        assert response.status_code == 200, response.data
        assert f'data-leg-account-id="{leg_account_id}"' in response.data.decode()

        with app.app_context():
            for account_id in _sides(xfer_id):
                assert settled_contribution(
                    parent_of(_side_leg(xfer_id, account_id)),
                ) == _LIVE_PITI
                assert transfer_side_figure(
                    db.session, xfer_id, account_id,
                ) == _LIVE_PITI

    def test_the_reconcile_panels_tick_freezes_and_dates_by_the_STATEMENT(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """The FIFTH door, which plan step X-f2-c3 opens.

        It is the only one that knows a day, so it grades the two rules
        together: the freeze books the live figure, and both sides' records
        date the money as having moved on the day the STATEMENT covers rather
        than the day the operator got round to reconciling.  Each side is read
        as the transfer and its side's record, not its twin row (ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).  The landing day is
        read off the TRANSFER's own period and due date, where it was read off
        the expense twin's until plan step balance:X-bi-6-4d-2 stopped the twin
        being born; the twin copied both from the transfer, so the day is the
        same (ruling R-BAL167 class 1).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id = xfer.id
            account_id = xfer.from_account_id
            # The day the panel measures the offer against is the row's own
            # LANDING day, not its period's start: a loan payment carries a
            # due date, so the two differ and a statement asserted before the
            # landing day would (correctly) offer nothing.
            period = calendar_for(seed_user["user"].id).require_period(
                FiledRow.for_row(xfer),
            )
            observed = period.attribution_day(xfer.due_date)

        response = auth_client.patch(
            f"/accounts/{account_id}/true-up",
            data={
                "anchor_balance": "4537.66",
                "observed_on": observed.isoformat(),
            },
        )
        assert response.status_code == 200, response.data

        response = auth_client.post(
            f"/accounts/{account_id}/reconcile",
            data={"transfer_ids": [str(xfer_id)]},
        )
        assert response.status_code == 200, response.data

        with app.app_context():
            for side_account_id in _sides(xfer_id):
                assert settled_contribution(
                    parent_of(_side_leg(xfer_id, side_account_id)),
                ) == _LIVE_PITI
                assert transfer_side_figure(
                    db.session, xfer_id, side_account_id,
                ) == _LIVE_PITI
                assert transfer_side_record(
                    db.session, xfer_id, side_account_id,
                ).settled_on == observed


class TestTheNamedVerbItself:
    """``settle_transfer``: the surface every settle-only door now calls.

    Its behaviour was reachable only THROUGH those doors until an adversarial
    review pointed out that a public verb with no direct case is a verb whose
    own contract is ungraded -- and then found a real defect in exactly the
    branch nothing exercised.
    """

    def test_it_returns_whether_a_HUMAN_s_figure_was_booked(
        self, app, db, seed_user, seed_periods,
    ):
        """The return value is what the reconcile writer counts (**N-231**).

        Three shapes in one case, because the answer is a three-way decision
        and grading one arm would leave the other two free to invert: nobody
        typed a figure, somebody typed the panel's own prefill back, and
        somebody typed a different one.  The record is read off each
        transfer's from-side record, not its expense twin (ruling R-BAL167
        class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            nothing_typed = _plain_transfer(
                seed_user, seed_periods, destination="Savings",
            )
            echoed = _plain_transfer(
                seed_user, seed_periods, amount="120.00",
                destination="Vacation",
            )
            corrected = _plain_transfer(
                seed_user, seed_periods, amount="80.00",
                destination="Emergency",
            )
            db.session.commit()
            owner = seed_user["user"].id

            assert transfer_service.settle_transfer(
                nothing_typed.id, owner,
            ) is False
            assert transfer_service.settle_transfer(
                echoed.id, owner, submitted=typed(Decimal("120.00")),
            ) is False
            assert transfer_service.settle_transfer(
                corrected.id, owner, submitted=typed(Decimal("95.50")),
            ) is True

            db.session.commit()
            db.session.expire_all()
            # And the RECORD follows the same three-way decision: the two
            # uncorrected settles book what they resolved on the ``resolved``
            # source, the corrected one books the human's figure and says so.
            def _record(transfer):
                return _side_settlement(transfer.id, transfer.from_account_id)
            assert _record(nothing_typed).source is MovementFigureSourceEnum.RESOLVED
            assert _record(echoed) == status_seam.Settlement(
                Decimal("120.00"), MovementFigureSourceEnum.RESOLVED,
            )
            assert _record(corrected) == status_seam.Settlement(
                Decimal("95.50"), MovementFigureSourceEnum.TYPED,
            )

    def test_settling_an_ALREADY_settled_transfer_writes_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A settle is idempotent, and the defect this grades was measured.

        ``enters_settled_band`` is False for ``done -> done``, so no settle
        runs -- and before plan step X-f2-c3's review the kwargs fell through
        to the ordinary field arms anyway: ``actual_amount`` was written
        VERBATIM past the echo rule, and ``settled_on`` re-dated both legs
        through ruling **R-ED**'s correction door, moving the posted
        ``entry_date`` (findings **N-146** / **N-178**).  The verb returned
        ``False`` throughout, so the reconcile writer's count under-reported a
        write it had just made.

        Shown to FIRE: deleting the ``settle_only`` arm in
        ``_apply_transfer_updates`` fails every assertion below.  Each side's
        record and day are read off the side's record, not its twin row
        (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            xfer = _plain_transfer(seed_user, seed_periods)
            db.session.commit()
            owner = seed_user["user"].id
            first_day = display_today() - timedelta(days=3)

            transfer_service.settle_transfer(
                xfer.id, owner,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_entered_day(first_day),
                ),
            )
            db.session.commit()
            db.session.expire_all()
            assert transfer_side_record(
                db.session, xfer.id, xfer.from_account_id,
            ).settled_on == first_day

            # A stale tab replays the settle, carrying a figure and a later day.
            assert transfer_service.settle_transfer(
                xfer.id, owner,
                submitted=typed(Decimal("999.99")),
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_entered_day(display_today()),
                ),
            ) is False
            db.session.commit()

            db.session.expire_all()
            for account_id in _sides(xfer.id):
                # The echoed-past-the-rule write did not happen: the record
                # still says ``resolved`` at what the FIRST settle booked, not
                # ``typed`` at the replayed $999.99.
                assert _side_settlement(xfer.id, account_id).source is (
                    MovementFigureSourceEnum.RESOLVED
                )
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) == Decimal("250.00")
                assert settled_contribution(
                    parent_of(_side_leg(xfer.id, account_id)),
                ) == Decimal("250.00")
                # ... and the day the money moved was not moved.
                assert transfer_side_record(
                    db.session, xfer.id, account_id,
                ).settled_on == first_day

    # ``test_a_derived_freeze_emits_its_own_event`` lived here until plan step
    # X-au-f, and it is DELETED with the event it graded (ruling **R-BAL12**).
    # ``EVT_TRANSFER_AMOUNT_FROZEN``'s predicate was
    # ``booked != rows.transfer.amount``; that column is EMPTY for a generated
    # transfer now, so the comparison fired on EVERY settle including a plain
    # savings transfer (finding **N-451**), and re-pointing it at the RESOLVED
    # figure makes it fire on NONE -- the two sides then share one producer and
    # the comparison is an identity.  An event that cannot fire is a fence the
    # design made unnecessary, so it went with its predicate, its ``log_events``
    # registration and this case rather than being kept as a green check that
    # measures nothing.  What it recorded is on the record instead: the
    # covering movement's ``figure_source_id`` says whether a booked figure
    # was the app's own resolution or a human's correction, for EVERY settle
    # rather than for the subset a predicate happened to select.

    def test_a_manual_payment_with_NO_extra_books_its_series_price(
        self, app, db, seed_user, seed_periods, caplog,
    ):
        """A manual payment with no extra books what its DEFINITION states.

        **Renamed at plan step X-au-f**, with the event it was named for
        (ruling **R-BAL12**): "reports no freeze" described a log assertion that
        no longer exists, and a name is the first thing the next reader trusts.
        What survives is the money half, which is the half that could ever fail.

        A MANUAL payment with no standing extra books exactly what its transfer
        states, so nothing was derived over the operator's head and there is
        nothing to record.  The deleted ``LoanPricing.live_cash`` answered
        ``None`` for this shape -- its candidate map admitted only derive-mode
        payments and manual ones carrying an extra -- so the old
        ``frozen is not None`` predicate emitted nothing here.

        **A first replacement asked the amount model for the RULE**
        (``AmountRule.LOAN_PAYMENT``), which is true of ANY transfer whose
        template carries a settings row, so the event began firing on settles
        where no derivation decided anything -- wider than what it replaced, and
        contradicting the sentence the code writes about itself.  An adversarial
        review of this step found it.  The predicate compares the BOOKED figure
        against the transfer's own, which reproduces the old extension exactly.
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            xfer.template.settings.derive_from_loan = False
            xfer.template.settings.extra_principal = Decimal("0.00")
            db.session.commit()

            with caplog.at_level(logging.INFO):
                transfer_service.settle_transfer(xfer.id, seed_user["user"].id)
                db.session.commit()

            # It books what its DEFINITION states, so nothing was derived over
            # anybody's head.  The log assertion that stood beside this went
            # with ``EVT_TRANSFER_AMOUNT_FROZEN`` at plan step X-au-f (ruling
            # **R-BAL12**); this half is the one that could ever fail, and the
            # case is kept for it -- a manual payment with no extra must book
            # its series price and not the loan's contract.  Read off each
            # side's record, not its twin row (ruling R-BAL167 class 1, plan
            # step balance:X-bi-6-4d-2).
            for account_id in _sides(xfer.id):
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) == _STALE

    def test_a_re_settle_HONOURS_a_retained_correction(
        self, app, db, seed_user, seed_periods, caplog,
    ):
        """A re-settle honours the figure a human retained.

        **Renamed at plan step X-au-f** with ``EVT_TRANSFER_AMOUNT_FROZEN``
        (ruling **R-BAL12**); the money assertion is what it always graded.

        Settle with a correction, revert -- which RELEASES the assertion and
        KEEPS what moved (plan step X-au-c3) -- then settle again with nobody
        typing anything.  The second settle HONOURS the retained ``$1,512.44``,
        so no derivation decided the figure.

        **The guard did not test ``held`` until this step**, so that second
        settle logged ``transfer_amount_frozen`` carrying the DERIVATION's
        ``$1,499.10`` as though it were the booked figure -- two numbers apart,
        in a record whose whole purpose is to say which was booked.  The fix
        shipped with no test until this case; the ``frozen_amount`` assertion in
        its sibling above cannot see it, because that path never retains.
        Each side's figure is read off its record, not its twin row (ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            user_id = seed_user["user"].id
            corrected = Decimal("1512.44")

            transfer_service.settle_transfer(
                xfer.id, user_id, submitted=typed(corrected),
            )
            db.session.commit()
            transfer_service.update_transfer(
                xfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()

            with caplog.at_level(logging.INFO):
                transfer_service.settle_transfer(xfer.id, user_id)
                db.session.commit()

            db.session.expire_all()
            for account_id in _sides(xfer.id):
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) == corrected

    def test_a_settle_carrying_a_CORRECTION_books_the_humans_figure(
        self, app, db, seed_user, seed_periods, caplog,
    ):
        """A human's correction beats the derivation and is what gets booked.

        **Renamed at plan step X-au-f** with the event (ruling **R-BAL12**).

        A human's correction beats the freeze, so the figure booked is theirs
        and no freeze happened.  Without this the event could be emitted on
        every settle of a loan payment and still pass its sibling above.  Each
        side's figure is read off its record, not its twin row (ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            transfer_service.settle_transfer(
                xfer.id, seed_user["user"].id,
                submitted=typed(Decimal("1512.44")),
            )
            db.session.commit()

            db.session.expire_all()
            for account_id in _sides(xfer.id):
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) == Decimal("1512.44")


class TestATransfersOfferIsWhatItsReSettleBOOKS:
    """The TRANSFER half of finding **C1**, which nothing graded.

    ``transaction_service.settle_amount`` and ``transfer_service.leg_settle_amount``
    both answer a retained ``corrected`` record before they price anything, so
    the reconcile panel's PREFILL equals what a tick BOOKS on either kind of
    row.  The transaction half is pinned by
    ``test_full_edit_settle_door.TestWhatAReSettleBooksIsWhatTheOfferSHOWED``;
    the transfer half was pinned by nothing.

    **Measured 2026-08-17 by an adversarial mutation pass**: deleting the
    ``honoured_correction`` arm from ``transfer_service._settle.settle_amount``
    left the whole 9,626-test suite GREEN.  ``settle()`` reads the retained
    correction independently, so the mutant separates the two -- the panel
    offers the row's derived figure and the tick books the retained one, which
    is exactly the defect the transaction side documents and refuses.
    """

    def test_the_offer_equals_the_booking_on_a_reverted_transfer(
        self, app, db, seed_user, seed_periods,
    ):
        """A reverted transfer offers the figure it will re-book, not its plan.

        Shown to FIRE (against ``settle_amount``, before plan step
        ``balance:X-bi-6-4d-2``): dropping its ``honoured_correction`` arm
        makes the offer ``$250.00`` while the settle still books ``$95.50``.

        The retained figure is the transfer's, and the offer is asked of the
        from-side's LEG (``leg_settle_amount``, whose retained arm is
        ``honoured_correction`` of the leg's parent, ruling plan#1983), where
        both were asked of the expense twin until that step (ruling R-BAL167
        class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            xfer = _plain_transfer(seed_user, seed_periods, amount="250.00")
            db.session.commit()
            owner = seed_user["user"].id
            xfer_id = xfer.id

            # Settle at a HUMAN's figure, then revert -- the round trip the
            # full-edit card instructs.
            assert transfer_service.settle_transfer(
                xfer_id, owner, submitted=typed(Decimal("95.50")),
            ) is True
            db.session.commit()
            transfer_service.update_transfer(
                xfer_id, owner,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            db.session.expire_all()

            checking = xfer.from_account_id
            # The record SURVIVES the revert and the assertion does not.
            assert _side_settlement(xfer_id, checking) == status_seam.Settlement(
                Decimal("95.50"), MovementFigureSourceEnum.TYPED,
            )
            assert transfer_side_record(
                db.session, xfer_id, checking,
            ).settled_on is None

            # THE CLAIM: what the panel would offer is what a tick will book.
            leg = _side_leg(xfer_id, checking)
            offered = transfer_service.leg_settle_amount(leg, amount_basis_for(leg))
            assert offered == Decimal("95.50"), (
                f"the panel would offer {offered} for a transfer whose "
                "re-settle books its retained correction"
            )

            transfer_service.settle_transfer(xfer_id, owner)
            db.session.commit()
            db.session.expire_all()
            assert transfer_side_figure(db.session, xfer_id, checking) == offered


class TestOneBrokenPairCannotStopTheDeployResync:
    """``resync_all_cash_postings`` skips a transfer it cannot post, and goes on.

    **The refusal that made this necessary is correct, and its blast radius was
    not** (developer ruling, 2026-08-17).  The one-entry writer's figure read
    (deleted with that writer at plan step ``balance:X-bi-6-3``) gained a
    settled-status predicate at plan step X-au-c3 and raised ``PostingError``
    when the income shadow was not settled -- right for a single write path,
    where a caller asking what one transfer settled at must not be handed a
    fabricated figure.

    But this batch selects transfers by the PARENT's status and runs at
    container start (``scripts/init_database.py``) with no isolation, so one
    Transfer-Invariant-4 drift -- the exact corruption ``restore_transfer``
    exists to repair -- made the app unbootable for every user, and the operator
    could not reach the screen that would name the row.  Before the predicate it
    would have posted a figure and carried on.

    **That drift has no row to live on since plan step balance:X-bi-6-4d-2**: a
    transfer is one row, and no transaction can name it:
    ``ck_transactions_names_no_transfer`` refused a twin (ruling **R-BAL258**)
    until plan step balance:X-bi-6-5 dropped the column.  The batch's blast
    radius is still the subject, so the case below breaks a transfer the way
    one still CAN be broken -- its to-side's record carried onto an account
    whose ledger pairing is gone, ``test_posting_service.py``'s recipe --
    beside a healthy transfer the resync genuinely has to re-post.
    """

    @staticmethod
    def _movement_entry_count(xfer_id):
        """Return how many journal entries *xfer_id*'s two side records hold.

        A side's record hangs off the transfer by its side link since plan
        step balance:X-bi-6-4d-2, and a settled side posts ONE entry against
        transit (plan step ``balance:X-bi-6-3``), so a settled transfer at
        target holds two.
        """
        return db.session.execute(db.text(
            "SELECT count(*) FROM budget.journal_entries je"
            "  JOIN budget.transaction_entries te"
            "    ON te.id = je.transaction_entry_id"
            " WHERE te.expense_transfer_id = :t OR te.income_transfer_id = :t"
        ), {"t": xfer_id}).scalar()

    def test_it_skips_the_broken_transfer_and_still_posts_the_healthy_one(
        self, app, db, seed_user, seed_periods, caplog,
    ):
        """The resync refuses one transfer, re-posts the other, and returns.

        **Re-expressed at plan step balance:X-bi-6-4d-2** (ruling **R-BAL167**
        class 1, the substitution stated; it was
        ``test_it_skips_the_broken_pair_and_still_posts_the_healthy_one``, and
        at cp2c of that step briefly
        ``test_the_drift_it_skipped_is_refused_and_the_batch_still_completes``).
        Its drift was the INCOME twin's status moved out of the settled band
        under a settled parent, a row no transfer has now.  The broken
        transfer below is broken the way one still can be: its to-account
        re-pointed by raw SQL onto an account whose ``budget.ledger_accounts``
        pairing was deleted (the side's record follows on its key's ON UPDATE
        CASCADE), so the pair's door refuses it naming that account.  And
        where both transfers were already at target before -- so nothing told
        a resync that reached them from one that reached neither -- the
        healthy one's two entries are deleted, so the resync has a re-post to
        make and reports it.

        **The broken transfer is created FIRST**, and the resync walks
        transfers in id order, so the refusal comes before the reach: a batch
        that stopped at its first refusal would not reach the healthy one.

        Arithmetic: two plain transfers settled go-forward, ``$60.00`` to
        Savings (broken) and ``$45.00`` to Vacation (healthy), each holding
        two movement entries.  The first resync reports ``(0, 1)`` -- no
        transaction, one transfer re-posted -- logs exactly the broken one as
        skipped, and leaves the healthy one holding two entries again and the
        broken one the two it had.  A second resync reports ``(0, 0)``.

        Shown to FIRE (``cp2c-a``): a resync walking no transfer, one with the
        per-transfer ``except PostingError`` deleted, and one that stops at
        its first refusal each fail it.  What it cannot see is a savepoint
        rolling back only PART of a refused family's writes, because the
        broken transfer here writes nothing before its refusal; that half is
        ``test_posting_service.py``'s ``test_a_refused_transfer_holding_no_
        legacy_net_is_still_skipped``, the sibling where a skip still returns.
        ``test_a_refused_transfer_is_skipped_whole`` grades the same savepoint
        on a family holding a LEGACY net, and the resync RAISES there since
        ruling **R-BAL104**, so the batch does not complete in that case.
        """
        with app.app_context():
            broken = _plain_transfer(
                seed_user, seed_periods, amount="60.00",
                destination="Savings",
            )
            healthy = _plain_transfer(
                seed_user, seed_periods, amount="45.00",
                destination="Vacation",
            )
            db.session.commit()
            owner = seed_user["user"].id
            broken_id, healthy_id = broken.id, healthy.id
            # The premise of the ordering argument in the docstring.
            assert broken_id < healthy_id

            transfer_service.settle_transfer(broken_id, owner)
            transfer_service.settle_transfer(healthy_id, owner)
            db.session.commit()
            assert self._movement_entry_count(broken_id) == 2
            assert self._movement_entry_count(healthy_id) == 2

            # The healthy transfer needs a re-post: its two entries go.
            db.session.execute(db.text(
                "DELETE FROM budget.journal_entries WHERE id IN ("
                "  SELECT je.id FROM budget.journal_entries je"
                "  JOIN budget.transaction_entries te"
                "    ON te.id = je.transaction_entry_id"
                "  WHERE te.expense_transfer_id = :t"
                "     OR te.income_transfer_id = :t)"
            ), {"t": healthy_id})
            # The broken transfer: an account created (which pairs it), its
            # pairing removed, and the broken transfer's to-account re-pointed
            # onto it, its side's record moving with it on the key's cascade.
            unpaired = create_account_of_type(
                seed_user, db.session, "Savings", "Unpaired Savings",
            )
            db.session.commit()
            unpaired_id = unpaired.id
            db.session.execute(db.text(
                "DELETE FROM budget.ledger_accounts WHERE account_id = :a"
            ), {"a": unpaired_id})
            db.session.execute(db.text(
                "UPDATE budget.transfers SET to_account_id = :u WHERE id = :t"
            ), {"u": unpaired_id, "t": broken_id})
            db.session.commit()
            db.session.expire_all()
            assert self._movement_entry_count(healthy_id) == 0
            assert transfer_side_record(
                db.session, broken_id, unpaired_id,
            ) is not None

            with caplog.at_level(
                logging.WARNING, logger="app.services.posting_service",
            ):
                # The batch completes rather than raising, and it REACHED the
                # healthy transfer past the refused one: that is the claim.
                assert posting_service.resync_all_cash_postings() == (0, 1)
            db.session.commit()

            assert (
                f"skipped 1 transfer(s) whose family could not be posted: "
                f"[{broken_id}]."
            ) in caplog.text
            assert self._movement_entry_count(healthy_id) == 2
            assert self._movement_entry_count(broken_id) == 2

            # At target now, so a further pass re-posts nothing -- and still
            # skips the broken transfer rather than raising.
            assert posting_service.resync_all_cash_postings() == (0, 0)
