"""
Shekel Budget App -- Transaction flag-resolution property tests

Unit tests for ``Transaction.tracks_purchases`` and
``Transaction.visible_to_companion``.  Resolution rule: a row's two flags
are its DEFINITION's (the definition is the single source of truth for
every row it generates or places), and a row that names no definition
answers ``False`` (ruling **R-BAL73**).  That row is a CC payback: the
transfer shadow was the other until plan step ``balance:X-bi-6-4d-2``, where
the database began refusing a row that names a transfer, and the card
cutover (``credit_card:CC-7``, rulings **R-CC17** / **R-CC20**) deletes the
payback, so these two cases go with the ``template_id is None`` arms they
grade.
These properties are the load-bearing abstraction behind F2 (companion
visibility) and F3 (purchase tracking).

**The row carries no flag of its own** (plan step ``balance:X-bi-7d-2``,
ruling **R-BAL20**).  Until that cutover a LEGACY link-less row stated both
flags in two cells of its own, sealed at ``balance:X-bi-1`` / ``X-bi-1b`` so
the dead half on a generated row could not be read (keying on it read 4
envelopes where there are 238); the cutover minted every such row a
rule-less definition carrying its flags and dropped both columns, so the
public names ``is_envelope`` / ``companion_visible`` that read the accessor
and wrote the cell are gone from the row too.
:class:`TestTheRowCarriesNoFlag` pins what that means; the derived flags'
class-level names still refuse to key a query rather than silently matching
nothing.
"""
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from tests._test_helpers import (
    generate_row_of,
    make_expense_template,
    one_off_row_of,
    payback_row_of,
)


def _placed(seed_user, period, *, is_envelope, companion_visible):
    """Place and commit a ONE-OFF's row whose definition carries the flags."""
    txn = one_off_row_of(
        period,
        name="One-off",
        amount=Decimal("100.00"),
        user_id=period.user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        category_id=list(seed_user["categories"].values())[0].id,
        is_envelope=is_envelope,
        companion_visible=companion_visible,
    )
    db.session.commit()
    return txn


def _generated(seed_user, period, *, is_envelope, companion_visible):
    """Create a RECURRING definition with the flags plus the engine's row of it."""
    tpl = make_expense_template(
        db.session, seed_user, amount="100.00", name="Templated",
        is_envelope=is_envelope, companion_visible=companion_visible,
    )
    txn = generate_row_of(tpl, period)
    db.session.commit()
    return txn


def _payback(seed_user, period):
    """Return ``(envelope, payback)``: a card purchase's payback and the row it repays.

    The payback names no definition -- its one pricing link is the row it
    repays, here an envelope -- so it is the link-less shape these cases
    need.  The envelope's definition states BOTH flags True, so a link-less
    arm that answered through the payback's link to its envelope would read
    True where the rule says ``False``.
    """
    envelope = _placed(
        seed_user, period, is_envelope=True, companion_visible=True,
    )
    payback = payback_row_of(
        db.session, seed_user, envelope, Decimal("40.00"), period.start_date,
    )
    db.session.commit()
    assert payback.template_id is None
    return envelope, payback


class TestTracksPurchasesResolution:
    """Resolution of Transaction.tracks_purchases."""

    def test_a_placed_row_reads_its_definition(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A one-off's row tracks purchases exactly when its definition does."""
        with app.app_context():
            tracking = _placed(
                seed_user, seed_periods_today[0],
                is_envelope=True, companion_visible=False,
            )
            plain = _placed(
                seed_user, seed_periods_today[1],
                is_envelope=False, companion_visible=False,
            )
            assert tracking.tracks_purchases is True
            assert plain.tracks_purchases is False

    def test_a_generated_row_reads_its_definition(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A recurring definition's row reads the definition's flag."""
        with app.app_context():
            txn_on = _generated(
                seed_user, seed_periods_today[0],
                is_envelope=True, companion_visible=False,
            )
            txn_off = _generated(
                seed_user, seed_periods_today[1],
                is_envelope=False, companion_visible=False,
            )
            assert txn_on.tracks_purchases is True
            assert txn_off.tracks_purchases is False

    def test_a_row_with_no_definition_answers_false(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A card payback tracks no purchases, though its envelope does (ruling R-BAL73).

        There is no definition to ask and no cell of the row's own, so the
        answer is stated without either.  The envelope it repays tracks
        purchases, so the payback's ``False`` is not a copy of its envelope's
        answer.
        """
        with app.app_context():
            envelope, payback = _payback(seed_user, seed_periods_today[0])
            assert envelope.tracks_purchases is True
            assert payback.tracks_purchases is False


class TestVisibleToCompanionResolution:
    """Resolution of Transaction.visible_to_companion."""

    def test_a_placed_row_reads_its_definition(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A one-off's row is visible exactly when its definition says so."""
        with app.app_context():
            visible = _placed(
                seed_user, seed_periods_today[0],
                is_envelope=False, companion_visible=True,
            )
            private = _placed(
                seed_user, seed_periods_today[1],
                is_envelope=False, companion_visible=False,
            )
            assert visible.visible_to_companion is True
            assert private.visible_to_companion is False

    def test_a_generated_row_reads_its_definition(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A recurring definition's row reads the definition's flag."""
        with app.app_context():
            txn_on = _generated(
                seed_user, seed_periods_today[0],
                is_envelope=False, companion_visible=True,
            )
            txn_off = _generated(
                seed_user, seed_periods_today[1],
                is_envelope=False, companion_visible=False,
            )
            assert txn_on.visible_to_companion is True
            assert txn_off.visible_to_companion is False

    def test_a_row_with_no_definition_answers_false(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A card payback is not companion-visible, though its envelope is (ruling R-BAL73).

        A SECURITY predicate answered ``False`` where nothing states
        otherwise: no definition, no cell.  The envelope it repays IS
        visible, so the payback's ``False`` is not a copy of its envelope's
        answer.
        """
        with app.app_context():
            envelope, payback = _payback(seed_user, seed_periods_today[0])
            assert envelope.visible_to_companion is True
            assert payback.visible_to_companion is False


class TestTheRowCarriesNoFlag:
    """Plan step ``balance:X-bi-7d-2``: a row has no flag of its own.

    Four claims.  The two column names are gone from the row -- from the
    mapped class, from its constructor and from the table -- so nothing can
    state a flag on a row; the class-level name of each derived flag refuses
    to key a query (the silent-empty-result trap measured 2026-09-11); and
    the two derived flags refuse assignment.
    """

    @pytest.mark.parametrize("name", ["is_envelope", "companion_visible"])
    def test_the_public_name_is_gone_from_the_row(self, app, name):
        """``Transaction.<name>`` is no attribute, no constructor kwarg, no column.

        Reaching the name on the class raises rather than answering a
        descriptor; the declarative constructor refuses the kwarg every
        pre-cutover door once passed; and the table has no such column, so
        a Core writer cannot reach a cell either.
        """
        with app.app_context():
            assert not hasattr(Transaction, name)
            with pytest.raises(TypeError):
                Transaction(**{name: True})
            assert name not in Transaction.__table__.c

    @pytest.mark.parametrize("flag", ["tracks_purchases", "visible_to_companion"])
    def test_a_query_keyed_on_a_derived_flag_refuses_to_build(
        self, app, flag,
    ):
        """Every spelling of a query over the flag raises at BUILD time.

        A plain ``property`` at class level compares ``False`` to
        everything, so ``filter_by(flag=True)`` and ``Transaction.flag ==
        True`` returned NO ROWS with no error (measured 2026-09-11 on the
        first cut of X-bi-1) -- a silent wrong answer, worse than the
        wrong column it replaced.  Five spellings, one refusal each.
        """
        with app.app_context():
            with pytest.raises(TypeError, match="cannot key a query"):
                db.session.query(Transaction).filter_by(**{flag: True})
            with pytest.raises(TypeError, match="cannot key a query"):
                getattr(Transaction, flag) == True  # noqa: E712  # pylint: disable=singleton-comparison,expression-not-assigned
            with pytest.raises(TypeError, match="cannot key a query"):
                getattr(Transaction, flag).is_(True)
            # The truth-test arm on its own: the three above reach the
            # refusal through ``__eq__`` and ``__getattr__``, so deleting
            # ``__bool__`` would leave them green while
            # ``if Transaction.flag:`` silently read True.
            with pytest.raises(TypeError, match="cannot key a query"):
                bool(getattr(Transaction, flag))
            with pytest.raises(TypeError, match="cannot key a query"):
                db.session.query(Transaction).filter(getattr(Transaction, flag))

    def test_the_derived_flags_refuse_assignment(
        self, app, db, seed_user, seed_periods_today,
    ):
        """``tracks_purchases`` and ``visible_to_companion`` have no setter.

        A derivation that accepted a write would bind a plain instance
        attribute shadowing the descriptor for that one row, and the row
        would answer something its definition never said.
        """
        with app.app_context():
            txn = _placed(
                seed_user, seed_periods_today[0],
                is_envelope=False, companion_visible=False,
            )
            with pytest.raises(AttributeError):
                txn.tracks_purchases = True
            with pytest.raises(AttributeError):
                txn.visible_to_companion = True
            assert txn.tracks_purchases is False
            assert txn.visible_to_companion is False
