"""The amount model's constraints, each fired by writing the state it refuses: a figure and a
source paired on a transaction and on a transfer (both unpaired states refused by name, the
paired act accepted), an ad-hoc transfer declaring no source (and the dedupe index still seeing
two identical ad-hoc transfers), one pricing link per row, and the RESTRICT foreign key on a
named amount source.
"""

from decimal import Decimal

import pytest
import sqlalchemy.exc

from app import ref_cache
from app.enums import AmountSourceEnum
from app.models.ref import AmountSource
from app.models.amount_ownership import AmountOwnership

from ._helpers import (
    _make_transaction,
    _insert_transaction_row,
    _insert_transfer_row,
    _make_transfer,
)


class TestTransactionAmountOwnership:
    """``ck_transactions_amount_ownership`` refuses both unpaired states."""

    def test_a_declared_source_may_not_keep_a_figure(
        self, app, db, seed_user, seed_periods
    ):
        """Declaring a source while keeping the figure is refused.

        The forgetful-writer shape, and the one with money in it: a write that
        stamps the declaration without emptying the column leaves a figure
        that no longer follows its own inputs, the stored derived amount
        ruling R-FI forbids.  The mapping cannot say it
        (``test_amount_ownership_attribute.py``), so the writer graded here is
        a Core INSERT, refused by ``ck_transactions_amount_ownership``.
        """
        with app.app_context():
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transactions_amount_ownership",
            ):
                _insert_transaction_row(
                    seed_user, seed_periods,
                    figure=Decimal("300.00"),
                    source_id=ref_cache.amount_source_id(
                        AmountSourceEnum.TEMPLATE
                    ),
                )
            db.session.rollback()

    def test_an_empty_figure_needs_a_declared_source(
        self, app, db, seed_user, seed_periods
    ):
        """Emptying the figure with no source declared is refused.

        The mirror shape: a row with neither a figure nor a statement of who
        prices it is unpriceable, and nothing on the row would say why.
        """
        with app.app_context():
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transactions_amount_ownership",
            ):
                _insert_transaction_row(
                    seed_user, seed_periods, figure=None, source_id=None,
                )
            db.session.rollback()

    def test_declaring_a_source_and_emptying_the_figure_is_accepted(
        self, app, db, seed_user, seed_periods
    ):
        """The legitimate cutover act -- both columns moved together -- is allowed.

        Without this the suite could not tell a correct constraint from one that
        refuses every write to the pair.
        """
        with app.app_context():
            template_source = ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE
            )
            txn = _make_transaction(
                seed_user, seed_periods,
                amount_ownership=AmountOwnership.derived(template_source),
            )
            db.session.add(txn)
            db.session.flush()

            assert txn.estimated_amount is None
            assert txn.amount_source_id == template_source

    def test_a_row_that_owns_its_amount_is_accepted(
        self, app, db, seed_user, seed_periods
    ):
        """A row that owns its amount -- a figure and no source -- is accepted."""
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods)
            db.session.add(txn)
            db.session.flush()

            assert txn.estimated_amount == Decimal("300.00")
            assert txn.amount_source_id is None


class TestTransferAmountOwnership:
    """``ck_transfers_amount_ownership`` is the same rule on the second column."""

    def test_a_declared_source_may_not_keep_a_figure(
        self, app, db, seed_full_user_data
    ):
        """A transfer declaring a source while holding a figure is refused.

        The same shape as the transaction case above, on the second table.
        This application's mapping cannot say it
        (``test_amount_ownership_attribute.py``: the pair is one attribute and
        the illegal shape is unsayable), so the writer this grades is one
        outside the application: a Core INSERT.
        """
        with app.app_context():
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transfers_amount_ownership",
            ):
                _insert_transfer_row(
                    seed_full_user_data,
                    figure=Decimal("100.00"),
                    source_id=ref_cache.amount_source_id(
                        AmountSourceEnum.TEMPLATE
                    ),
                )
            db.session.rollback()

    def test_an_empty_figure_needs_a_declared_source(
        self, app, db, seed_full_user_data
    ):
        """A transfer with no figure and no source is refused."""
        with app.app_context():
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transfers_amount_ownership",
            ):
                _insert_transfer_row(
                    seed_full_user_data, figure=None, source_id=None,
                )
            db.session.rollback()

    def test_declaring_a_source_and_emptying_the_figure_is_accepted(
        self, app, db, seed_full_user_data
    ):
        """A derived transfer -- source declared, no figure -- is allowed.

        ``ck_transfers_positive_amount`` (``amount > 0``) does NOT block this: a
        comparison with NULL is UNKNOWN and a CHECK admits UNKNOWN, so the
        ownership pairing is the only thing deciding when the column may be
        empty.  Stated as a test because a reader meeting ``amount > 0`` on a
        nullable column has to work that out.
        """
        with app.app_context():
            template_source = ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE
            )
            xfer = _make_transfer(
                seed_full_user_data,
                amount_ownership=AmountOwnership.derived(template_source),
            )
            db.session.flush()

            assert xfer.amount is None
            assert xfer.amount_source_id == template_source


class TestAdHocTransferOwnsItsAmount:
    """``ck_transfers_adhoc_owns_amount``: no definition, no declaration.

    ``cash_ledger.resolve_transfer_amount`` answers OWN for a transfer with no
    template, so a declaration on an ad-hoc transfer names a relation that cannot
    be reached.  The reason it is a CONSTRAINT and not a comment is
    ``uq_transfers_adhoc_dedupe``: its key includes ``amount``, and an ad-hoc
    transfer with a NULL figure would slip past the index that exists to stop a
    double-submit from doubling a projected debit and credit (F-050 / C-22).
    """

    def test_an_adhoc_transfer_may_not_declare_a_source(
        self, app, db, seed_full_user_data
    ):
        """A transfer with no template and a declared source is refused."""
        with app.app_context():
            xfer = _make_transfer(
                seed_full_user_data,
                transfer_template_id=None,
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
                ),
            )
            db.session.add(xfer)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transfers_adhoc_owns_amount",
            ):
                db.session.flush()
            db.session.rollback()

    def test_the_dedupe_index_still_sees_two_identical_adhoc_transfers(
        self, app, db, seed_full_user_data
    ):
        """The guard the constraint protects, shown still guarding.

        This is the control that gives the constraint its meaning: with a figure
        present, ``uq_transfers_adhoc_dedupe`` refuses the second of two identical
        ad-hoc transfers.  Without ``ck_transfers_adhoc_owns_amount`` the same
        pair could evade it by carrying no figure at all.
        """
        with app.app_context():
            first = _make_transfer(
                seed_full_user_data, transfer_template_id=None,
            )
            db.session.add(first)
            db.session.flush()

            second = _make_transfer(
                seed_full_user_data, transfer_template_id=None,
            )
            db.session.add(second)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="uq_transfers_adhoc_dedupe",
            ):
                db.session.flush()
            db.session.rollback()


class TestOnePricingLink:
    """``ck_transactions_one_pricing_link``: a row is priced through exactly one relation.

    It read "at most one" until plan step ``balance:X-bi-7d-2`` re-cut it to
    ``= 1``.  Measured before it was imposed, on a 2026-08-12 production clone at
    ``a9d3c15e7f42``: 997 rows -- 606 template-linked, 342 transfer shadows, 21
    CC paybacks, 28 with no link -- and 0 holding two of the three.
    """

    def test_a_template_row_may_not_also_name_a_transfer(
        self, app, db, seed_full_user_data
    ):
        """template_id + transfer_id on one row is refused.

        Two links means two candidate answers for "who prices this row", with
        only dispatch ORDER separating them -- which is the link-derived
        discriminator ruling R-FI refused, arriving as data instead of as code.

        **Refused by ``ck_transactions_names_no_transfer`` since plan step
        ``balance:X-bi-6-4d-2``** (ruling **R-BAL258**; re-expressed under
        ruling **R-BAL167** class 2): a row may name no transfer at all, so
        the row this plants is refused before the pricing-link count is
        reached (PostgreSQL checks a table's CHECKs in name order).  The
        pricing link's ``transfer_id`` term goes with the column at
        ``X-bi-6-5``.
        """
        with app.app_context():
            data = seed_full_user_data
            xfer = _make_transfer(data)
            db.session.flush()

            # Period 1, not 0, and it is REQUIRED rather than a precaution:
            # the fixture's row is the engine's (plan step balance:X-cf) and
            # already answers period 0's occurrence, so ``generate_row_of``
            # refuses to write a second one there ("wrote 0 rows") before the
            # constraint under test is ever reached.  It began as a precaution
            # against the undated index firing first, while the fixture's row
            # was hand-built; that cause is gone and this one replaced it.
            _make_transaction(
                data, data["periods"],
                pay_period_id=data["periods"][1].id,
                template_id=data["template"].id,
                transfer_id=xfer.id,
            )
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transactions_names_no_transfer",
            ):
                db.session.flush()
            db.session.rollback()

    def test_a_payback_may_not_also_name_a_template(
        self, app, db, seed_full_user_data
    ):
        """credit_payback_for_id + template_id on one row is refused.

        The third link is the one ruling R-FI's own evidence turns on: a CC
        payback carries NEITHER template nor transfer while its amount IS
        derived, which is why the discriminator is declared rather than read off
        the links.  Its exclusivity is the same convention.
        """
        with app.app_context():
            data = seed_full_user_data
            source_row = _make_transaction(data, data["periods"])
            db.session.add(source_row)
            db.session.flush()

            _make_transaction(
                data, data["periods"],
                pay_period_id=data["periods"][1].id,
                name="Payback control",
                credit_payback_for_id=source_row.id,
                template_id=data["template"].id,
            )
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transactions_one_pricing_link",
            ):
                db.session.flush()
            db.session.rollback()

    def test_exactly_one_link_is_accepted(
        self, app, db, seed_full_user_data
    ):
        """A template-linked row -- the commonest shape on production -- is allowed."""
        with app.app_context():
            data = seed_full_user_data
            txn = _make_transaction(
                data, data["periods"],
                pay_period_id=data["periods"][1].id,
                template_id=data["template"].id,
            )
            db.session.flush()

            assert txn.template_id == data["template"].id


class TestAmountSourceReferentialIntegrity:
    """The FK is RESTRICT, so a referenced source row cannot vanish."""

    def test_deleting_a_named_source_is_refused(
        self, app, db, seed_user, seed_periods
    ):
        """Deleting a ``ref.amount_sources`` row a derived row names is refused.

        With SET NULL or NO ACTION this DELETE would convert every derived row
        naming it into a row claiming to own an amount it does not have -- the
        exact state ``ck_transactions_amount_ownership`` exists to forbid,
        arriving through the ref table's back door.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
                ),
            )
            db.session.add(txn)
            db.session.flush()

            source_row = (
                db.session.query(AmountSource)
                .filter_by(name=AmountSourceEnum.TEMPLATE.value).one()
            )
            db.session.delete(source_row)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="fk_transactions_amount_source_id",
            ):
                db.session.flush()
            db.session.rollback()
