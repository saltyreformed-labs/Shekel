"""The amount declaration a transfer is born with, made by the constructor rather than a later
writer, and the eager load that keeps a priced leg set off an N+1.
"""

from decimal import Decimal

import pytest
import sqlalchemy.exc

from app.extensions import db as _db
from app.models.transfer import Transfer
from app.services.cash_ledger import (
    derived_amount_basis,
    leg_amounts_by_key,
    transfer_pricing_load_options,
)
from app.services.transfer_legs import planned_transfer_legs
from tests._test_helpers import (
    a_twin_of,
    assert_a_twin_is_refused,
    constraint_name_from,
    write_past_the_amount_seam,
    capture_sql_statements,
    transfer_amount,
)
from tests.test_integration.test_transfer_settle_freeze import _derived_loan_transfer

from ._helpers import _CONTRACT, _plain_transfer


class TestAShadowIsBornDerived:
    """The declaration is made by the constructor, not by a later writer.

    Since plan step balance:X-bi-6-4d-2 the constructor makes ONE row, so what
    the first case asked of the two legs it built is asked of the transfer
    (ruling **R-BAL167** class 1, with a refusal of either twin added), and
    the second, which planted a figure on a leg, asserts that row unstorable
    (class 2).
    """

    def test_the_transfer_owns_its_figure_and_neither_twin_can_be_stored(
        self, app, db, seed_user, seed_periods,
    ):
        """``create_transfer`` births one owning row, and no leg row beside it.

        **Renamed and re-expressed at plan step balance:X-bi-6-4d-2** (it was
        ``test_both_legs_declare_their_parent_and_store_nothing``; ruling
        **R-BAL167** class 1, with a refusal ADDED: it planted nothing, it read
        the two legs ``create_transfer`` made, so the twin refused below is a
        shape this case adds rather than one it planted).  It asserted three
        things per leg -- declared
        ``PARENT_TRANSFER``, stored nothing, worth the transfer's figure --
        because the leg was a ROW that could hold a copy.  No leg row is born
        now and the database refuses one on either side
        (``ck_transactions_names_no_transfer``, ruling **R-BAL258**), so the
        copy is not merely derived but absent.  What each leg is worth is the
        transfer's own resolution (**R-BAL13**), asserted as `$250.00` here
        where it was asserted per leg.  "Owns its figure" is the stored
        `$250.00` (``ck_transfers_amount_ownership`` pairs a stored figure with
        no source): an ``owns_its_amount`` read of this AD-HOC transfer could
        not fail under ``ck_transfers_adhoc_owns_amount``, so none is taken
        (the cp2c review's L-4; the AD-HOC retype case asserts that CHECK's
        refusal).
        """
        with app.app_context():
            xfer = _plain_transfer(seed_user, seed_periods)

            assert xfer.amount == Decimal("250.00")
            assert transfer_amount(xfer) == Decimal("250.00")
            db.session.commit()
            for is_income in (False, True):
                assert_a_twin_is_refused(
                    db.session, db.session.get(Transfer, xfer.id), is_income,
                )

    def test_a_leg_cannot_hold_a_figure_while_it_declares_a_parent(
        self, app, db, seed_user, seed_periods,
    ):
        """The drift the deleted corrector repaired is unconstructible.

        This is the whole of "structural rather than maintained": there is no
        window -- soft-deleted, mid-edit or otherwise -- in which a leg's figure
        can disagree with its transfer, because the pair cannot be written.

        **The rival figure is written past the MAPPING since plan step
        X-au-k**, and it has to be: the pair is one attribute now, so
        ``state_own_amount`` would release the leg's declaration and produce a
        leg that legitimately owns the figure -- a different row, and not the
        drifted one this case is about. Reaching the private column is the only
        way left to construct the drift, and the database still refuses it.

        **Re-expressed at plan step balance:X-bi-6-4d-2** (ruling **R-BAL167**
        class 2): no leg row is born to write the figure onto, so the planted
        row is the twin :func:`~tests._test_helpers.a_twin_of` builds -- the
        shape ``_build_shadow`` built -- with the rival figure written past
        the seam.  That row breaks TWO checks, this one and
        ``ck_transactions_names_no_transfer`` (ruling **R-BAL258**).  The
        PostgreSQL ``CREATE TABLE`` reference states that a table's CHECK
        constraints are tested in alphabetical order of name, and the refusal
        reported was MEASURED as this one, on either side, before this case
        asserted it.  The same twin without the figure is refused by the other
        check (the case above), so the figure is what this check answers.
        """
        with app.app_context():
            xfer = _plain_transfer(seed_user, seed_periods)
            db.session.commit()

            for is_income in (False, True):
                twin = a_twin_of(db.session.get(Transfer, xfer.id), is_income)
                write_past_the_amount_seam(twin, Decimal("999.00"))
                db.session.add(twin)
                # The constraint IS the claim: a bare ``IntegrityError`` would
                # be satisfied by an FK, a NOT NULL or a unique index just as
                # well.
                with pytest.raises(sqlalchemy.exc.IntegrityError) as excinfo:
                    db.session.flush()
                db.session.rollback()
                assert constraint_name_from(excinfo.value) == (
                    "ck_transactions_amount_ownership"
                ), f"the figure-carrying twin was refused by: {excinfo.value}"


class TestTheAmountModelsOwnEagerLoad:
    """``transfer_pricing_load_options`` keeps a priced leg set off an N+1.

    **Re-expressed onto TRANSFER legs at plan step balance:X-bi-6-4d-2**
    (ruling **R-BAL167** class 1).  The pair loaded SHADOW rows with
    ``pricing_load_options`` and priced them with ``amounts_by_id``; no shadow
    is born since that step, and a leg is read off its transfer (**R-BAL13**),
    so the row set is the legs :func:`~app.services.transfer_legs.planned_transfer_legs`
    loads -- the cash fold's plan-half loader -- with and without
    :func:`~app.utils.amount_relationships.transfer_pricing_load_options`,
    priced by :func:`~app.services.cash_ledger.leg_amounts_by_key`, the batch
    the grid publishes.  ``pricing_load_options`` composed that same tuple
    under ``Transaction.transfer`` until the same step deleted that entry,
    which loaded nothing once no row named a transfer, so
    ``transfer_pricing_load_options`` is the one statement of a transfer's
    pricing graph and the one this class grades.

    **The tuple's zero-statement claim was pinned before this step too**: the
    shadow-row pair priced rows loaded with ``pricing_load_options``, which
    composed this same tuple then, and
    ``test_loan_installments.test_the_priced_feed_reads_every_pricing_table``
    pins it by a statement count.  What is new here is narrower: the old
    warm-up priced one leg, which loaded the one definition's ``settings``
    before the capture opened, and this warm-up pays the LOAN resolve and
    nothing else -- it asks the basis's loan derivation directly -- so
    dropping the per-DEFINITION settings chain from the tuple now shows as a
    statement, as dropping the per-ROW period chain always did (both
    measured).
    """

    @staticmethod
    def _legs_and_warm_basis(seed_user, seed_periods, options):
        """Load the loan payment's checking legs and pay the loan resolve.

        Returns the legs and a basis whose loan derivation already holds the
        loan, so the capture measures the legs' own loads alone.  The
        installment's dates are read off the transfer BEFORE the session is
        expired, so reading them loads nothing the capture would miss.
        """
        xfer = _derived_loan_transfer(seed_user, seed_periods)
        checking, loan_id = xfer.from_account_id, xfer.to_account_id
        due_date, period_start = xfer.due_date, xfer.pay_period.start_date
        scenario_id = seed_user["scenario"].id
        _db.session.commit()
        _db.session.expire_all()

        legs = planned_transfer_legs(checking, scenario_id, options=options)
        basis = derived_amount_basis(seed_user["user"].id, scenario_id)
        # The LOAN resolve is a query and is not what this measures, so it is
        # paid once outside the capture -- through the loan derivation itself,
        # so no leg's relationships are loaded by the warm-up.
        assert basis.loans.derive_cash(
            due_date, period_start, loan_id, Decimal("0.00"),
        ) == _CONTRACT
        return legs, basis

    def test_a_loaded_row_set_prices_with_no_further_query(
        self, app, seed_user, seed_periods,
    ):
        """Pricing legs loaded WITH the options issues no statement at all.

        The direct control on the claim the options carry: a loan payment's
        leg is priced off its transfer, which walks ``template -> settings``
        and its own pay period (rule 4's derive arm), once per row, on the
        busiest surfaces in the app.

        Zero rather than "fewer", because zero is the claim: everything the
        rule reads is already in the identity map, so a rule that started
        loading a per-row relationship the tuple does not list (a transfer's
        movements, say), or issuing a statement of its own, would show up
        here as a statement rather than as a slow page nobody measures.  Two
        things it cannot see.  The transfer's ``lazy="joined"`` relationships
        -- ``from_account``, ``to_account``, ``status`` and ``category``
        (``app/models/transfer.py``) -- are loaded by the loader's OWN
        statement, so a rule that started reading one adds no statement here
        (measured: one reading ``from_account`` added none).  And this fixture
        is a derive-mode loan payment, so the series chain
        (``template -> amount_versions``), which only rule 3 and rule 4's
        manual arm read, is not exercised here.
        """
        with app.app_context():
            legs, basis = self._legs_and_warm_basis(
                seed_user, seed_periods, transfer_pricing_load_options(),
            )
            assert len(legs) >= 2

            priced, statements = capture_sql_statements(
                lambda: leg_amounts_by_key(legs, basis),
            )

            assert statements == [], (
                "pricing a loaded row set must issue no query; got "
                f"{[text for text, _params in statements]}"
            )
            assert set(priced.values()) == {_CONTRACT}

    def test_without_the_options_the_same_row_set_DOES_query(
        self, app, seed_user, seed_periods,
    ):
        """The negative control: the options are what make the zero above.

        Without it the case would pass just as well on a set SQLAlchemy had
        already loaded for some other reason, and would keep passing if the
        options were deleted -- which is the shape a green gate that measures
        nothing takes.
        """
        with app.app_context():
            legs, basis = self._legs_and_warm_basis(seed_user, seed_periods, ())

            _priced, statements = capture_sql_statements(
                lambda: leg_amounts_by_key(legs, basis),
            )

            assert statements, (
                "an unloaded row set must reach the database for its parents"
            )
