"""The ONE writer of a row's amount-ownership pair.

Plan step **X-au-g-2c-2**.  ``app.services.amount_ownership`` is the write half
of ruling **R-FI**: a row either STATES ITS OWN figure or DECLARES the relation
that prices it, and the two columns that say which move together or not at all.
:mod:`app.services.cash_ledger._amount_source` is the read half, graded in
``tests/test_services/test_amount_source.py``; the schema's own refusals are
graded in ``tests/test_models/test_amount_ownership/test_the_constraints.py``.

**What is asserted here is that the SEAM cannot express the half-write**, not
that the CHECK refuses it -- the constraint's tests already own that.  The
difference matters: a constraint catches a mistake at the flush, and this seam
is what stops the mistake being writable at all.
"""

from decimal import Decimal

import pytest

from app.enums import AmountSourceEnum
from app.extensions import db
from app.models.amount_ownership import AmountOwnership, from_columns
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.amount_ownership import (
    declare_derived,
    owns_its_amount,
    state_own_amount,
)
from app import ref_cache
from tests._test_helpers import (
    add_txn,
    generate_transfer_of,
    repriced_by_the_owner,
    transfer_repriced_by_the_owner,
)


def _an_own_row(seed_user, seed_periods):
    """Return a transaction that OWNS `$60.00`, the way the re-price door makes one.

    ``add_txn`` places a ONE-OFF since plan step balance:X-bi-7c -- a rule-less
    definition's row, TEMPLATE-priced with ``estimated_amount`` already
    ``None`` -- so a case about the seam's OWN -> DERIVED direction starting
    from it graded an identity act (found by 7c-1's adversarial review).  The
    owner's re-price (:func:`repriced_by_the_owner`, the edit door's two acts)
    is what makes a row own its figure today.
    """
    return repriced_by_the_owner(
        add_txn(db.session, seed_user, seed_periods[0], "Gas", "60.00"),
        "60.00",
    )


class TestEveryOwnedTableMapsThePairAsOneAttribute:
    """The completeness the ``_FIGURE_COLUMNS`` registry used to hand-keep.

    **The registry is GONE at plan step X-au-k, and this is what replaced it.**
    The seam carried a ``{model: figure column name}`` dict because a
    transaction stores an owned figure in ``estimated_amount`` and a transfer
    in ``amount``, so an act had to look up which column to write; both models
    expose the pair under the SAME attribute now, so there is nothing to
    dispatch on and nothing to keep in step.  What the dict also bought was a
    COMPLETENESS predicate -- a third table brought under the amount model
    would fail loudly rather than be written through a default -- and that
    predicate is worth more than the dispatch was, so it is restated here
    against the MAPPERS instead of against a hand-written dict.

    It is strictly stronger than the version it replaces, for a reason the old
    file's own second test named: ``setattr`` on a declarative class does not
    fail, so a model that carried ``amount_source_id`` without the composite
    would take an ``amount_ownership`` assignment, put it nowhere, and let the
    ``commit`` after it succeed.  A census over ``mapper.composites`` cannot be
    satisfied that way -- an unmapped instance attribute never appears there.
    """

    def test_every_table_with_a_source_column_maps_the_composite(self, app):
        """The two sets are equal: source column present iff pair mapped.

        Discovered from the mappers rather than restated, so a table that gains
        ``amount_source_id`` without the composite beside it fails HERE rather
        than at the first row somebody tries to declare.

        **The left-hand census reads the TABLE's column names, not the
        mapper's attribute keys, and that distinction is load-bearing.**
        ``mapper.columns`` is keyed by ATTRIBUTE, so it answers
        ``_amount_source_id`` on both tables since plan step X-au-k made the
        halves private -- and a census written against it reads EMPTY, which
        two empty sets would then have called equal.  The question this test
        asks is about the SCHEMA ("which tables carry the column"), so it asks
        the schema.  Measured: written against ``mapper.columns`` first, and
        it went vacuous exactly that way.
        """
        with app.app_context():
            carrying = {
                mapper.class_ for mapper in db.Model.registry.mappers
                if "amount_source_id" in mapper.local_table.c
            }
            mapping_the_pair = {
                mapper.class_ for mapper in db.Model.registry.mappers
                if any(c.key == "amount_ownership" for c in mapper.composites)
            }

            # Non-vacuous FIRST: two empty sets are equal, and a census that
            # measured nothing would pass this test forever.
            assert carrying == {Transaction, Transfer}
            assert mapping_the_pair == carrying

    def test_each_composite_covers_its_own_tables_two_columns(self, app):
        """The composite maps the FIGURE and the SOURCE, in that order.

        A composite over the wrong columns -- or over the right ones in the
        wrong order -- would build every value object with its halves swapped,
        and both halves are nullable, so nothing else would notice.  The figure
        column differs by table, which is the difference the deleted registry
        existed to carry, so it is named per model here.
        """
        expected = {
            Transaction: ("estimated_amount", "amount_source_id"),
            Transfer: ("amount", "amount_source_id"),
        }
        with app.app_context():
            for model, columns in expected.items():
                composite = next(
                    c for c in model.__mapper__.composites
                    if c.key == "amount_ownership"
                )
                assert tuple(c.name for c in composite.columns) == columns
                # The FACTORY, not the class: it answers ``None`` for a row
                # that has stated no ownership, which is what lets
                # ``AmountOwnership`` be total over R-FI's two states.  Mapping
                # the class directly is the edit this assertion exists to
                # catch.
                assert composite.composite_class is from_columns

    def test_the_public_names_are_read_only_on_every_owned_table(self, app):
        """No table under the amount model has a writable half of the pair.

        The census above says the pair is MAPPED as one attribute; this says
        the halves are not ALSO reachable under their public names, which is
        the property that makes "one writer" structural rather than observed.
        A model that mapped the composite and left the columns publicly
        writable would satisfy every other test in this class.
        """
        halves = {
            Transaction: ("estimated_amount", "amount_source_id"),
            Transfer: ("amount", "amount_source_id"),
        }
        with app.app_context():
            for model, names in halves.items():
                for name in names:
                    with pytest.raises(AttributeError):
                        setattr(model(), name, Decimal("1.00"))


class TestTheTwoActs:
    """Each act writes BOTH columns, so neither state can be half-written."""

    def test_declaring_a_transaction_empties_its_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """``declare_derived`` sets the source and clears ``estimated_amount``.

        Flushed, because a pair written only in memory would satisfy this
        assertion while the CHECK refused it -- and the CHECK is what the seam
        exists to keep satisfiable.
        """
        with app.app_context():
            txn = _an_own_row(seed_user, seed_periods)
            db.session.flush()

            assert txn.estimated_amount == Decimal("60.00"), (
                "the row must OWN a figure first, or the emptying below is "
                "an identity act graded by nothing"
            )
            declare_derived(txn, AmountSourceEnum.TEMPLATE)
            db.session.flush()

            assert txn.estimated_amount is None
            assert txn.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )
            assert owns_its_amount(txn) is False

    def test_stating_an_own_amount_clears_the_declaration(
        self, app, db, seed_user, seed_periods,
    ):
        """``state_own_amount`` writes the figure and clears the source.

        The other direction, and the one the transfer door takes when a human
        authors a figure (ruling **R-IO**).
        """
        with app.app_context():
            txn = _an_own_row(seed_user, seed_periods)
            declare_derived(txn, AmountSourceEnum.TEMPLATE)
            db.session.flush()

            state_own_amount(txn, Decimal("42.50"))
            db.session.flush()

            assert txn.estimated_amount == Decimal("42.50")
            assert txn.amount_source_id is None
            assert owns_its_amount(txn) is True

    def test_a_transfers_figure_column_is_its_own(
        self, app, db, seed_full_user_data,
    ):
        """The SECOND table is written through the same two functions.

        ``budget.transfers`` stores an owned figure in ``amount`` where a
        transaction uses ``estimated_amount``, and since plan step X-au-k what
        keeps one seam serving both is that the two tables expose the pair
        under the same ATTRIBUTE name -- the seam names no column at all.
        Without this case the seam could serve only the transaction shape.
        """
        with app.app_context():
            td = seed_full_user_data
            # ``ck_transfers_adhoc_owns_amount`` refuses a declaration on a
            # transfer no definition prices, so this has to be a GENERATED one
            # to be a legal row at all: the engine's transfer of the fixture's
            # definition (plan step balance:X-ch), taken for the OWNER through
            # the transfer door so the declare below has a figure to release.
            xfer = transfer_repriced_by_the_owner(
                generate_transfer_of(td["transfer_template"], td["periods"][0]),
                "250.00",
            )
            assert xfer.amount == Decimal("250.00")

            declare_derived(xfer, AmountSourceEnum.TEMPLATE)
            db.session.flush()
            assert xfer.amount is None
            assert owns_its_amount(xfer) is False

            state_own_amount(xfer, Decimal("300.00"))
            db.session.flush()
            assert xfer.amount == Decimal("300.00")
            assert xfer.amount_source_id is None

    def test_both_acts_are_idempotent(
        self, app, db, seed_user, seed_periods,
    ):
        """Re-declaring a derived row and re-stating an owned one are no-ops.

        Load-bearing rather than incidental: ``_update._apply_amount``
        re-declares BOTH legs on every definition-driven amount write, so the
        ordinary case -- a leg that is already derived -- must cost nothing and
        change nothing.  A version bump is not asserted either way; what is
        asserted is that the pair does not move.
        """
        with app.app_context():
            txn = _an_own_row(seed_user, seed_periods)
            db.session.flush()

            declare_derived(txn, AmountSourceEnum.TEMPLATE)
            db.session.flush()
            declare_derived(txn, AmountSourceEnum.TEMPLATE)
            db.session.flush()
            assert txn.estimated_amount is None
            assert txn.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )

            state_own_amount(txn, Decimal("42.50"))
            db.session.flush()
            state_own_amount(txn, Decimal("42.50"))
            db.session.flush()
            assert txn.estimated_amount == Decimal("42.50")
            assert txn.amount_source_id is None

    def test_a_declaration_can_be_switched_between_relations(
        self, app, db, seed_user, seed_periods,
    ):
        """Re-declaring under a DIFFERENT relation replaces the first.

        The state a mode change would need if a relation ever moved.  It is
        asserted because ``declare_derived`` writes the source unconditionally
        rather than only when it is absent, and a reader would be entitled to
        assume the opposite from the idempotence above.
        """
        with app.app_context():
            txn = _an_own_row(seed_user, seed_periods)
            db.session.flush()

            declare_derived(txn, AmountSourceEnum.TEMPLATE)
            db.session.flush()
            declare_derived(txn, AmountSourceEnum.PARENT_TRANSFER)
            db.session.flush()

            assert txn.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.PARENT_TRANSFER,
            )
