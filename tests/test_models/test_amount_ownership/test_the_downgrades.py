"""Two migrations' downgrade guards, driven directly: b3f7c2a9d514 refuses to restore NOT NULL over
a derived transaction or transfer, and c9a4e7b21d58 refuses to restore a transfer shadow's
figure from a parent that states none; each passes a population that owns its figures.
"""

import re
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import AmountSourceEnum
from app.models.amount_ownership import AmountOwnership
from tests._test_helpers import (
    load_migration_module,
    repriced_by_the_owner,
    run_migration_callable,
)

from ._helpers import _make_transaction, _make_transfer, _legs_of


_MIGRATION = load_migration_module("b3f7c2a9d514_amount_ownership.py")


_SHADOW_CUTOVER = load_migration_module(
    "c9a4e7b21d58_a_transfer_shadow_is_derived.py",
)

#: Plan step ``balance:X-bi-6-4d-2``'s revision, which deletes every transfer
#: shadow and refuses a new one.  Its downgrade builds two for every transfer,
#: each declared derived from its parent with no figure of its own
#: (``_BUILT_SQL``): the shadows ``c9a4e7b21d58`` declared, which every case
#: below that probes a shadow builds first.
_TWINS_DELETED = load_migration_module("82f7b7ed4332_a_transfer_is_one_row.py")


class TestTheDowngradeRefusesToInventAFigure:
    """Migration ``b3f7c2a9d514``'s only non-DDL logic, driven directly.

    ``refuse_rows_without_a_figure`` is module-level for exactly this reason (the
    pattern the previous revision uses for its backfill): a guard nothing
    exercises is a guard nobody has seen work, and this one stands between a
    downgrade and a ``NOT NULL`` restore it cannot satisfy honestly.

    Definition of Done item 7 asks for both directions.  The upgrade's DDL is
    exercised on every test-template rebuild -- ``scripts/build_test_template.py``
    replays the Alembic chain's upgrades rather than calling ``create_all`` --
    and the upgrade/downgrade round trip was run against a production clone (997
    transactions, 171 transfers) before this leaf shipped.  What no rebuild can
    reach is the refusal: a template build replays the upgrades over an empty
    database, and the cutovers that empty a figure (plan steps X-au-d..X-au-i,
    the first in the chain ``c9a4e7b21d58``) come after this revision in it.
    """

    def test_it_passes_when_every_row_owns_its_figure(
        self, app, db, seed_user, seed_periods
    ):
        """The state the chain leaves: no row is derived, so a downgrade is safe."""
        with app.app_context():
            db.session.add(_make_transaction(seed_user, seed_periods))
            db.session.flush()

            # Returns None rather than raising -- the assertion is the absence of
            # a refusal, so the negative control below is what gives it meaning.
            assert _MIGRATION.refuse_rows_without_a_figure(
                db.session.connection()
            ) is None

    def test_it_refuses_and_names_a_derived_transaction(
        self, app, db, seed_user, seed_periods
    ):
        """One derived row is enough to stop the downgrade, and it is NAMED.

        The id matters: the operator's next act is to downgrade the cutover that
        emptied that column, and a refusal that does not say which rows are
        derived cannot tell them which one.
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

            with pytest.raises(RuntimeError, match=str(txn.id)):
                _MIGRATION.refuse_rows_without_a_figure(db.session.connection())

    def test_it_refuses_a_derived_transfer_too(self, db, seed_full_user_data):
        """The second table is probed, not just the first.

        Both columns are in the guard's loop, and a guard that checked only
        ``transactions`` would let a downgrade fail mid-DDL on the transfers
        ``SET NOT NULL`` -- after it had already dropped the constraints.

        The fixture's own transaction is the ENGINE's row since plan step
        balance:X-cf and so is derived, which is the state the FIRST arm
        refuses; the guard raises at the first table it finds a NULL in, so
        that row would answer for the transfer's arm.  The owner re-prices it
        first -- the two acts the re-price door performs -- and the world is
        then the one this case is about: every transaction owns its figure
        and one transfer does not.

        **No transfer has a shadow since plan step balance:X-bi-6-4d-2**
        (``82f7b7ed4332``), and the engine's transfer is derived already, so
        the re-price alone leaves it the ONE row with no figure.  Until 4d-2
        each of its two shadows was given a figure of its own first, since
        they declared their parent and the guard reads transactions first;
        4d-2's leaf rebuilt them by that revision's downgrade only to hide them
        the same way, and its review measured the case grading its name
        identically without them, so they went.
        """
        fixture_row = seed_full_user_data["transaction"]
        repriced_by_the_owner(fixture_row, "1200.00")
        _make_transfer(seed_full_user_data)

        with pytest.raises(RuntimeError, match="budget.transfers.amount"):
            _MIGRATION.refuse_rows_without_a_figure(db.session.connection())


class TestTheShadowCutoverDowngradeRefusesToInventAFigure:
    """Migration ``c9a4e7b21d58``'s only non-DDL logic, driven directly.

    Plan step **X-au-g-2c-2** declares every transfer SHADOW derived; its
    downgrade restores each shadow's figure from the parent transfer's own
    ``amount``.  ``refuse_a_shadow_whose_parent_states_no_figure`` is
    module-level for the reason ``b3f7c2a9d514``'s guard is: a guard nothing
    exercises is a guard nobody has seen work.

    **The DDL-free halves were driven against a copy of PRODUCTION before this
    leaf shipped** (2026-09-01, stamp ``a4c6f1d92b73`` restored into a throwaway
    database and migrated to ``dev``'s head): the upgrade declared 350 shadows
    and touched no other row, and the downgrade was BYTE-IDENTICAL over all
    1,028 transactions.  What no replay can reach is this refusal: a template
    build replays the chain's UPGRADES over an empty database, and the cutover
    that derives a parent transfer's figure (plan step X-au-f,
    ``b7e4c1f38a20``) comes after this revision in the chain.

    **No transfer has a shadow since plan step balance:X-bi-6-4d-2**
    (``82f7b7ed4332`` deletes them and refuses a new one), so every case here
    first runs that revision's downgrade, which builds the engine transfer's
    two, each DECLARED derived from its parent: the population this guard
    probes, at the revision it ran on.  Setup only: no assertion changed, and
    the accepting case gains one, its precondition that both are declared.
    """

    def test_it_passes_when_every_parent_states_a_figure(
        self, app, db, seed_full_user_data,
    ):
        """The state the chain leaves: every parent owns an amount.

        Returns ``None`` rather than raising, so the negative controls below
        are what give this meaning.  The engine's transfer's two legs, built
        by ``82f7b7ed4332``'s downgrade, are DECLARED, exactly what this
        cutover writes, and asserted so, since a probe over no shadow passes
        whatever it asks; its parent is given a figure of its own bare, the
        pre-X-au-f world the probe was written for.
        """
        with app.app_context():
            td = seed_full_user_data
            xfer = _make_transfer(
                td, amount_ownership=AmountOwnership.own(Decimal("100.00")),
            )
            run_migration_callable(_TWINS_DELETED.downgrade, db.session)
            parent_transfer = ref_cache.amount_source_id(
                AmountSourceEnum.PARENT_TRANSFER,
            )
            assert [leg.amount_source_id for leg in _legs_of(xfer)] == [
                parent_transfer, parent_transfer,
            ]

            assert _SHADOW_CUTOVER.refuse_a_shadow_whose_parent_states_no_figure(
                db.session.connection(),
            ) is None

    def test_it_refuses_and_names_the_shadow_whose_parent_is_derived(
        self, app, db, seed_full_user_data,
    ):
        """A parent with no figure stops the downgrade, and the SHADOW is named.

        The id matters: the operator's next act is to downgrade the cutover
        that emptied the parent's column (plan step X-au-f), and a refusal that
        does not say which rows are stranded cannot tell them where to look.
        """
        with app.app_context():
            td = seed_full_user_data
            # The engine's transfer and the two legs ``82f7b7ed4332``'s
            # downgrade builds for it ARE this state: a derived parent with
            # two declared legs.  Both legs must be named.
            xfer = _make_transfer(td)
            run_migration_callable(_TWINS_DELETED.downgrade, db.session)
            expense, income = _legs_of(xfer)

            # Anchored on the ids LIST rather than the bare digits.  The
            # message also carries the revision id ``c9a4e7b21d58``, whose
            # digits include 9, 4, 7, 2, 1, 5 and 8 -- so ``match=str(txn.id)``
            # is ``re.search`` over a string that already contains most small
            # ids, and would pass on a guard that named the TRANSFER instead of
            # the shadow.  That is the exact property this case exists for.
            with pytest.raises(RuntimeError) as raised:
                _SHADOW_CUTOVER.refuse_a_shadow_whose_parent_states_no_figure(
                    db.session.connection(),
                )
            for leg in (expense, income):
                assert re.search(
                    rf"\(ids [^)]*\b{leg.id}\b", str(raised.value),
                ), f"leg {leg.id} not named in: {raised.value}"

    def test_a_derived_parent_with_no_declared_shadow_does_not_refuse(
        self, app, db, seed_full_user_data,
    ):
        """The probe is scoped to DECLARED shadows, not to derived parents.

        The mutation this rules out is a guard written as "any transfer with no
        amount", which would refuse a downgrade that had nothing to restore --
        turning a safe round trip into a dead end. The parent here is derived
        and its shadow owns its own figure, so there is no restore to attempt.
        """
        with app.app_context():
            td = seed_full_user_data
            # The engine's parent is derived; ``82f7b7ed4332``'s downgrade
            # builds its two legs declared, and both are given a figure of
            # their own, bare, so NO leg is declared.
            xfer = _make_transfer(td)
            run_migration_callable(_TWINS_DELETED.downgrade, db.session)
            for leg in _legs_of(xfer):
                leg.amount_ownership = AmountOwnership.own(Decimal("25.00"))
            db.session.flush()

            assert _SHADOW_CUTOVER.refuse_a_shadow_whose_parent_states_no_figure(
                db.session.connection(),
            ) is None
