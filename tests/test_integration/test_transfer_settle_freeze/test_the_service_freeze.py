"""The settle freeze is the service's: the amount rule at the one chokepoint."""

from decimal import Decimal

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.models.transfer import Transfer
from app.services import transaction_service, transfer_service
from tests._test_helpers import typed, amount_basis_for, transfer_side_figure
from tests.test_integration.test_loan_transfer_live_amount._helpers import _side_leg
from app.services.row_valuation import settled_contribution
from app.services.transfer_legs import parent_of
from app.models.amount_ownership import AmountOwnership

from ._helpers import _LIVE_PITI, _derived_loan_transfer, _plain_transfer, _sides, _side_settlement


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

        ``transaction_service.settle_amount`` of the leg's transfer is what the
        reconcile panel renders, and the dispatch books it: one function since
        ruling plan#1983's settle fold, which deleted the leg's twin
        ``leg_settle_amount``.  A panel showing
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

            offered = transaction_service.settle_amount(
                parent_of(leg), amount_basis_for(leg),
            )
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
