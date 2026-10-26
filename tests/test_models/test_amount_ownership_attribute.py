"""The amount-ownership pair is ONE attribute, and the illegal shape is unsayable.

Plan step **X-au-k**, ruling **balance:R-IW**.  Its sibling
``test_amount_ownership/test_the_constraints.py`` grades the DATABASE tier -- what
``ck_transactions_amount_ownership`` refuses a writer that is not this
application.  This module grades the tier above it: what this application can
express at all.

**The TYPE is total over ruling R-FI's two states**, and everything else is
refused.  A figure beside a relation is the stale derived amount the arc
deletes; an empty pair is a row that has stated no ownership, and it is spelled
``None`` on the attribute rather than as an instance of the class.

**The indirection that makes the type total is `from_columns`, and it is
load-bearing.**  SQLAlchemy builds a composite from raw column values inside
its OWN machinery -- ``get_history`` for the pre-change side of an attribute
that may never have been set, ``Session.is_modified`` on a pending row -- and
both hand it ``(None, None)``.  A validating class mapped DIRECTLY as the
composite therefore raises from inside a path no caller entered; the factory
absorbs the empty pair and answers ``None``.  A first version of this step
mapped the class directly and had to weaken the type to survive it.
:class:`TestTheFactoryAbsorbsTheEmptyPair` is the regression control, because
a later edit "simplifying" the factory away would break exactly it.

**Every test here is a FIRING CONTROL** (``docs/plans/verification.md``
standard 4): each writes the state the mapping is supposed to refuse and
asserts the refusal, rather than asserting that a guard exists.
"""

from decimal import Decimal

import pytest
from sqlalchemy import inspect, select

from app import ref_cache
from app.enums import AmountSourceEnum, StatusEnum
from app.extensions import db
from app.models.amount_ownership import AmountOwnership, from_columns
from app.models.ref import TransactionType
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from tests._test_helpers import bare_expense_template


def _row(seed_user, seed_periods, ownership=None, **overrides):
    """Return an unflushed Projected expense row carrying *ownership*.

    Args:
        seed_user: The ``seed_user`` fixture payload.
        seed_periods: The ``seed_periods`` fixture list.
        ownership: The :class:`AmountOwnership` to state, or ``None`` to state
            none at all -- which is the fourth shape above.
        **overrides: Any other column values.

    Returns:
        The unflushed :class:`~app.models.transaction.Transaction`.

    **BARE on purpose; its pricing link is a rule-less definition of the
    owner's** (plan step ``balance:X-bi-7d-1``).  The subject here is a
    CONSTRAINT of ``budget.transactions``, and a control that reached the row
    through a door would grade the door -- so the row is still constructed by
    hand.  What it stopped being is LINK-LESS: the family's cutover
    (``X-bi-7d-2``) re-cuts ``ck_transactions_one_pricing_link`` to ``= 1``,
    so every row staged here names its own definition
    (:func:`~tests._test_helpers.bare_expense_template`) and carries the
    paycheck's start as the day it is due and the occurrence it answers --
    the shape ``one_off.place_row_of`` writes -- unless the case states
    otherwise.  The link is never the subject.
    """
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    definition = bare_expense_template(
        db.session, seed_user, name="Attribute control definition",
    )
    fields = {
        "user_id": seed_periods[0].user_id,
        "template_id": definition.id,
        "pay_period_id": seed_periods[0].id,
        "scenario_id": seed_user["scenario"].id,
        "account_id": seed_user["account"].id,
        "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": "Attribute control",
        "category_id": seed_user["categories"]["Rent"].id,
        "transaction_type_id": expense_type.id,
        "due_date": seed_periods[0].start_date,
        "occurs_on": seed_periods[0].start_date,
    }
    if ownership is not None:
        fields["amount_ownership"] = ownership
    fields.update(overrides)
    return Transaction(**fields)


class TestTheTypeRefusesTheIllegalShape:
    """``AmountOwnership`` cannot hold a figure beside a relation."""

    def test_a_figure_beside_a_relation_has_no_expression(self):
        """The one shape ruling R-FI forbids cannot be constructed.

        This is the whole of what X-au-k buys over the CHECK alone: before it,
        the state was reachable through the ORM and refused only at FLUSH,
        after the unit of work was already built.
        """
        with pytest.raises(ValueError, match="never both"):
            AmountOwnership(Decimal("10.00"), 1)

    def test_own_refuses_an_absent_figure(self):
        """``own(None)`` is the half-write spelled as an act, and is refused."""
        with pytest.raises(ValueError, match="own\\(\\) needs a figure"):
            AmountOwnership.own(None)

    def test_derived_refuses_an_absent_relation(self):
        """``derived(None)`` is the mirror half-write, and is refused."""
        with pytest.raises(ValueError, match="derived\\(\\) needs the relation"):
            AmountOwnership.derived(None)

    def test_the_two_legal_shapes_are_accepted(self):
        """Both legal states construct, or the type would refuse every write.

        A type that refused everything would pass all three tests above.
        """
        assert AmountOwnership.own(Decimal("10.00")).figure == Decimal("10.00")
        assert AmountOwnership.own(Decimal("10.00")).source_id is None
        assert AmountOwnership.derived(7).source_id == 7
        assert AmountOwnership.derived(7).figure is None


class TestTheEmptyPairIsTheDatabasesToRefuse:
    """No ownership stated: refused by the TYPE, and again at the INSERT."""

    def test_the_type_has_no_member_for_it(self):
        """``AmountOwnership(None, None)`` is not a value this type can hold."""
        with pytest.raises(ValueError, match="never neither"):
            AmountOwnership(None, None)

    def test_the_factory_answers_none_instead(self):
        """What SQLAlchemy gets for a row whose two columns are both NULL."""
        assert from_columns(None, None) is None
        assert from_columns(Decimal("1.00"), None) == AmountOwnership.own(
            Decimal("1.00"),
        )

    def test_a_row_stating_no_ownership_is_refused_at_the_database(
        self, app, db, seed_user, seed_periods,
    ):
        """A row that never states its ownership, stopped by the CHECK.

        The half of the biconditional that did NOT move up a tier: a row still
        being built carries ``amount_ownership is None`` legitimately, and the
        INSERT is where that stops being legitimate.  It is why the CHECK is
        not redundant after this step.
        """
        with app.app_context():
            db.session.add(_row(seed_user, seed_periods))
            with pytest.raises(
                Exception, match="ck_transactions_amount_ownership",
            ):
                db.session.flush()
            db.session.rollback()


class TestTheFactoryAbsorbsTheEmptyPair:
    """Map the CLASS instead of the factory and SQLAlchemy raises at you.

    The regression control for
    :func:`~app.models.amount_ownership.from_columns`.  A later edit that
    "simplified" it away -- mapping the validating class directly, which reads
    like the obvious thing to do -- would fail here and pass everything else,
    because the raise happens in machinery no test calls directly.  Measured on
    SQLAlchemy 2.0.49, where ``orm/descriptor_props.py`` builds
    ``composite_class(*deleted)`` for the pre-change side of the attribute.

    Two paths reach it and both are covered below: ``get_history`` on an
    EXPIRED row reassigned before its old values reload, and
    ``Session.is_modified`` on a pending row whose composite was never set.
    """

    def test_reading_the_composites_history_does_not_raise(
        self, app, db, seed_user, seed_periods,
    ):
        """Re-declare an expired row, then ask SQLAlchemy what changed."""
        with app.app_context():
            txn = _row(
                seed_user, seed_periods,
                AmountOwnership.own(Decimal("300.00")),
            )
            db.session.add(txn)
            db.session.flush()
            db.session.expire(txn)

            txn.amount_ownership = AmountOwnership.derived(
                ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
            )

            history = inspect(txn).attrs.amount_ownership.history
            assert history.has_changes()
            assert txn in db.session.dirty
            db.session.rollback()

    def test_is_modified_on_a_row_that_never_stated_its_ownership(
        self, app, db, seed_user, seed_periods,
    ):
        """The second path SQLAlchemy builds the empty pair on.

        ``transfer_service/_update`` gated the parent's optimistic-lock bump on
        ``Session.is_modified`` of a shadow until plan step
        ``balance:X-bi-6-4d-2``, where a spurious answer was a measured lost
        update on a money figure; the status seam's Transfer arm moves the
        transfer's counter since, and no ``app/`` code calls ``is_modified``
        now.  The path is still SQLAlchemy's own, so a validating class mapped
        directly would still raise on it.
        """
        with app.app_context():
            # The claim is that it ANSWERS rather than raises.  What it answers
            # is ``True`` -- the row's other columns were just set -- and that
            # is not what this control is about; mapping the validating class
            # directly makes this line a ``ValueError`` instead.
            assert isinstance(
                db.session.is_modified(_row(seed_user, seed_periods)), bool,
            )


class TestThePublicNamesAreReadOnly:
    """Every write spelling raises, including the two a census cannot see."""

    def test_a_direct_assignment_raises(self, app, db, seed_user, seed_periods):
        """``row.estimated_amount = x`` -- the spelling four services used."""
        with app.app_context():
            txn = _row(
                seed_user, seed_periods, AmountOwnership.own(Decimal("1.00")),
            )
            with pytest.raises(AttributeError):
                txn.estimated_amount = Decimal("2.00")

    def test_a_setattr_over_a_variable_name_raises(
        self, app, db, seed_user, seed_periods,
    ):
        """The SPLAT shape, and the reason the census was never enough.

        ``recurrence_engine/_maintain.py`` and
        ``routes/transactions/mutations.py`` write ``setattr(row, field,
        value)`` over a field name held in a variable.  No grep and no AST pass
        can see those two sites, so "the one writer" could never be maintained
        by counting -- it had to become a property of the mapping.
        """
        with app.app_context():
            txn = _row(
                seed_user, seed_periods, AmountOwnership.own(Decimal("1.00")),
            )
            for field in ("estimated_amount", "amount_source_id"):
                with pytest.raises(AttributeError):
                    setattr(txn, field, Decimal("2.00"))

    def test_the_declarative_constructor_raises(
        self, app, db, seed_user, seed_periods,
    ):
        """``Transaction(estimated_amount=...)`` raises at construction.

        SQLAlchemy's declarative ``__init__`` is a ``setattr`` loop, so the
        constructor is refused by the same descriptor -- which is what makes
        the pair unwritable-by-halves on a NEW row as well as an existing one.
        """
        with app.app_context():
            with pytest.raises(AttributeError):
                _row(seed_user, seed_periods, estimated_amount=Decimal("1.00"))

    def test_the_transfer_twin_is_read_only_too(self, app, db, seed_full_user_data):
        """``Transfer.amount`` is the same rule on the second table."""
        with app.app_context():
            xfer = Transfer(
                user_id=seed_full_user_data["user"].id,
                from_account_id=seed_full_user_data["account"].id,
                to_account_id=seed_full_user_data["savings_account"].id,
                pay_period_id=seed_full_user_data["periods"][0].id,
                scenario_id=seed_full_user_data["scenario"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Attribute control",
                amount_ownership=AmountOwnership.own(Decimal("5.00")),
            )
            with pytest.raises(AttributeError):
                xfer.amount = Decimal("6.00")


class TestTheReadsAndQueriesAreUnchanged:
    """The shims are why 414 call sites did not have to learn the pair."""

    def test_the_shims_project_both_states(
        self, app, db, seed_user, seed_periods,
    ):
        """An OWN row and a DERIVED row read back through the old names."""
        with app.app_context():
            own = _row(
                seed_user, seed_periods,
                AmountOwnership.own(Decimal("300.00")),
            )
            assert own.estimated_amount == Decimal("300.00")
            assert own.amount_source_id is None

            template = ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE)
            derived = _row(
                seed_user, seed_periods, AmountOwnership.derived(template),
            )
            assert derived.estimated_amount is None
            assert derived.amount_source_id == template

    def test_a_row_with_no_ownership_reads_none_rather_than_raising(
        self, app, db, seed_user, seed_periods,
    ):
        """The shims read the COLUMN, so a half-built row answers.

        Written this way deliberately: projecting the composite instead would
        make ``row.estimated_amount`` raise ``AttributeError`` on a row still
        being built, and ``owns_its_amount`` asks one of these names.
        """
        with app.app_context():
            bare = _row(seed_user, seed_periods)
            assert bare.estimated_amount is None
            assert bare.amount_source_id is None
            assert bare.amount_ownership is None

    def test_the_class_level_names_are_still_query_expressions(
        self, app, db, seed_user, seed_periods,
    ):
        """``filter``, ``order_by`` and ``is_()`` keep working unchanged.

        The property that let the shims be ``hybrid_property`` rather than
        ``property``: a plain property compares as a Python object and
        SQLAlchemy would refuse the resulting clause.
        """
        with app.app_context():
            txn = _row(
                seed_user, seed_periods,
                AmountOwnership.own(Decimal("321.00")),
            )
            db.session.add(txn)
            db.session.flush()

            found = db.session.scalars(
                select(Transaction)
                .where(Transaction.estimated_amount == Decimal("321.00"))
                .where(Transaction.amount_source_id.is_(None))
                .order_by(Transaction.estimated_amount)
            ).all()
            assert [r.id for r in found] == [txn.id]

    def test_a_composite_write_is_visible_before_the_flush(
        self, app, db, seed_user, seed_periods,
    ):
        """A door that states an amount can read it back in the same act.

        ``carry_forward_service`` and ``mutations`` both write the ownership
        and then run further logic against the row before anything flushes.
        """
        with app.app_context():
            txn = _row(
                seed_user, seed_periods,
                AmountOwnership.own(Decimal("10.00")),
            )
            txn.amount_ownership = AmountOwnership.derived(
                ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
            )
            assert txn.estimated_amount is None
            assert txn.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )
