"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_template_amount_history.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.ref import TransactionType
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import template_amount_service as tas
from tests._test_helpers import make_every_period_rule


def _template_with_history(seed_user, amounts, name="Geico"):
    """Create a rule-less expense template carrying a stated price history.

    ``amounts`` is ``[(effective_date, "amount"), ...]`` ascending, written
    through the real write door so the stored shape is the one production
    would hold.
    """
    expense = db.session.query(TransactionType).filter_by(name="Expense").one()
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=expense.id,
        name=name,
    )
    db.session.add(template)
    db.session.flush()
    for effective_on, amount in amounts:
        tas.set_amount(template, Decimal(amount), effective_on=effective_on)
    db.session.commit()
    return template


def _transfer_template(seed_user, savings_acct, amount="250.00"):
    """Create a recurring transfer template with an Every-Period rule."""
    # Authored through the write door (plan step R7c-b): the two-axis columns
    # are NOT NULL, so a rule naming only a pattern cannot be stored.
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings_acct.id,
        name="Money Market Contribution",
    )
    db.session.add(template)
    db.session.flush()
    # The definition first, then the cadence onto it (plan step R-F6).
    rule = make_every_period_rule(db.session, template)
    tas.set_amount(template, Decimal(amount), effective_on=date(2020, 4, 9))
    db.session.commit()
    return template
