"""
Shekel Budget App -- ``recurs``: the one accessor for "is there a cadence"

Plan step ``balance:X-bi-7a`` (ruling **R-BAL20**, every plan item has exactly
one definition).  ``TransactionTemplate.recurs`` states whether a definition
has a recurrence rule; ``Transaction.recurs`` delegates to it for the row.
Every site that read ``template_id IS NULL`` as *one-off* reads this instead,
so a RULE-LESS definition's row -- template-linked, no cadence -- behaves as
the one-off it is.  What is pinned here is the accessor itself: what it
answers on each shape, that it reads the DEFINITION and not the link, and
that neither class-level name can key a query (a plain property compares
``False`` to everything, so ``filter_by(recurs=True)`` would return no rows
with no error -- the silent shape ``_derived_flag`` exists to refuse).
"""
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from tests._test_helpers import (
    bare_expense_template,
    generate_row_of,
    make_every_period_rule,
    make_expense_template,
    one_off_row_of,
    payback_row_of,
)


def _row_fields(seed_user, period):
    """The kwargs the one-off builder below takes."""
    return {
        "name": "One-off",
        "amount": Decimal("100.00"),
        "user_id": period.user_id,
        "account_id": seed_user["account"].id,
        "scenario_id": seed_user["scenario"].id,
        "transaction_type_id": ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        "category_id": list(seed_user["categories"].values())[0].id,
    }


def _placed(seed_user, period):
    """Place and commit a one-off's row through the producer."""
    txn = one_off_row_of(period, **_row_fields(seed_user, period))
    db.session.commit()
    return txn


def _payback(seed_user, period):
    """Return ``(envelope, payback)``: a card purchase's payback and the row it repays.

    The payback names no definition -- its one pricing link is the row it
    repays, here an envelope -- the one link-less shape left since plan step
    ``balance:X-bi-6-4d-2`` refused the transfer shadow (the family's cutover,
    plan step balance:X-bi-7d-2, minted every bare row a definition).  The
    envelope is the engine's row of a RECURRING definition, so it recurs: a
    link-less arm that answered through the payback's link to its envelope
    would read ``True`` where the rule says ``False``.
    """
    envelope = generate_row_of(
        make_expense_template(db.session, seed_user, is_envelope=True), period,
    )
    payback = payback_row_of(
        db.session, seed_user, envelope, Decimal("40.00"), period.start_date,
    )
    db.session.commit()
    return envelope, payback


class TestTheDefinitionAnswers:
    """``TransactionTemplate.recurs`` is the rule's presence, nothing else."""

    def test_a_definition_with_a_rule_recurs(self, app, db, seed_user):
        """The every-paycheck builder's definition answers True."""
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            db.session.commit()
            assert template.recurrence_rule is not None
            assert template.recurs is True

    def test_a_definition_with_no_rule_does_not(self, app, db, seed_user):
        """A definition born without a cadence answers False."""
        with app.app_context():
            template = bare_expense_template(db.session, seed_user)
            db.session.commit()
            assert template.recurs is False

    def test_clearing_the_rule_flips_the_answer(self, app, db, seed_user):
        """The edit door's clear (dis-associate; delete-orphan) is read live.

        A cleared cadence is the one way a rule-less transaction definition
        holds rows today, so the answer must follow the rule as it stands and
        never a cached reading of it.
        """
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            db.session.commit()
            assert template.recurs is True
            template.recurrence_rule = None
            db.session.commit()
            db.session.expire_all()
            assert db.session.get(TransactionTemplate, template.id).recurs is False

    def test_authoring_a_rule_onto_a_bare_definition_flips_it_back(
        self, app, db, seed_user,
    ):
        """*Make this repeat* is the other direction, and it reads the same."""
        with app.app_context():
            template = bare_expense_template(db.session, seed_user)
            assert template.recurs is False
            make_every_period_rule(db.session, template)
            db.session.commit()
            assert template.recurs is True


class TestTheRowDelegates:
    """``Transaction.recurs`` reads the DEFINITION's answer, not the link."""

    def test_a_generated_row_recurs(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The engine's row of a recurring definition answers True."""
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            row = generate_row_of(template, seed_periods_today[0])
            db.session.commit()
            assert row.template_id == template.id
            assert row.recurs is True

    def test_a_rule_less_definitions_row_does_not(
        self, app, db, seed_user, seed_periods_today,
    ):
        """THE row this step exists for: linked, and no cadence.

        ``template_id`` is still set -- the link is not what the accessor
        reads -- and the answer is the definition's: no rule, so ``False``.
        This is the state that separates ``recurs`` from every
        ``template_id IS NULL`` test it replaced.
        """
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            row = generate_row_of(template, seed_periods_today[0])
            template.recurrence_rule = None
            db.session.commit()
            db.session.expire_all()
            row = db.session.get(Transaction, row.id)
            assert row.template_id == template.id
            assert row.recurs is False

    def test_a_link_less_row_does_not(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A card payback names no definition to recur by, though its envelope recurs.

        The ``template_id is None`` arm of the accessor, on the shape that
        still reaches it since the cutover minted every legacy one-off a
        definition; the live one-off arm is the rule-less definition's case
        above.  The envelope it repays recurs, so the payback's ``False`` is
        not a copy of its envelope's answer.  The card cutover
        (``credit_card:CC-7``, rulings **R-CC17** / **R-CC20**) deletes the
        payback, and this case goes with the arm.
        """
        with app.app_context():
            envelope, row = _payback(seed_user, seed_periods_today[0])
            assert envelope.recurs is True
            assert row.template_id is None and row.credit_payback_for_id is not None
            assert row.recurs is False

    def test_the_row_refuses_assignment(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A derivation with no setter; a write would shadow the descriptor."""
        with app.app_context():
            row = _placed(seed_user, seed_periods_today[0])
            with pytest.raises(AttributeError):
                row.recurs = True
            assert row.recurs is False


class TestNeitherNameKeysAQuery:
    """The class-level name refuses to BUILD a query, on both models.

    Five spellings each, the shape ``test_transaction_flag_resolution`` pins
    for the sealed flags: a plain property at class level would let every
    one of them build and match nothing.  The definition's is the one a
    reader would reach for first -- *the recurring definitions on this
    account* is exactly the account-delete refusal's question -- and it
    loads and asks instead.
    """

    @pytest.mark.parametrize("model", [Transaction, TransactionTemplate])
    def test_a_query_keyed_on_recurs_refuses_to_build(self, app, model):
        """Every spelling of a query over ``recurs`` raises at BUILD time."""
        with app.app_context():
            with pytest.raises(TypeError, match="cannot key a query"):
                db.session.query(model).filter_by(recurs=True)
            with pytest.raises(TypeError, match="cannot key a query"):
                model.recurs == True  # noqa: E712  # pylint: disable=singleton-comparison,expression-not-assigned
            with pytest.raises(TypeError, match="cannot key a query"):
                model.recurs.is_(True)
            with pytest.raises(TypeError, match="cannot key a query"):
                bool(model.recurs)
            with pytest.raises(TypeError, match="cannot key a query"):
                db.session.query(model).filter(model.recurs)
