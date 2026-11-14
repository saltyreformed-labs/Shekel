"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_template_amount_history.py``, which ``tools/split`` moved here, and
the one builder of a definition with no price of its own (:func:`_bare_template`) and of a
salary profile's definition (:func:`_salary_linked_template`).  The reader of what an edit
form posts with its disabled controls left out moved to ``tests/_form_controls.py``
(``enabled_form_controls``) when a second package came to read it (task plan#4046).
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import template_amount_service as tas
from tests._test_helpers import (
    create_savings_account,
    make_every_period_rule,
    make_salary_profile,
)


def _bare_template(seed_user, txn_type, name):
    """Create a rule-less transaction template that states no price, flushed.

    Args:
        seed_user: The ``seed_user`` fixture's dict.
        txn_type: The template's ``TxnTypeEnum`` member.
        name: The template's name.

    Returns:
        The flushed ``TransactionTemplate``, with no amount version.
    """
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(txn_type),
        name=name,
    )
    db.session.add(template)
    db.session.flush()
    return template


def _template_with_history(seed_user, amounts, name="Geico"):
    """Create a rule-less expense template carrying a stated price history.

    ``amounts`` is ``[(effective_date, "amount"), ...]`` ascending, written
    through the real write door so the stored shape is the one production
    would hold.
    """
    template = _bare_template(seed_user, TxnTypeEnum.EXPENSE, name)
    for effective_on, amount in amounts:
        tas.set_amount(template, Decimal(amount), effective_on=effective_on)
    db.session.commit()
    return template


def _salary_linked_template(seed_user):
    """Create an income template an ACTIVE salary profile names, before any price is stated.

    The profile's paycheck prices its rows, so ``owns_its_amount`` refuses
    the template a series and it holds none.

    Args:
        seed_user: The ``seed_user`` fixture's dict.

    Returns:
        ``(template, profile)``, committed.
    """
    template = _bare_template(seed_user, TxnTypeEnum.INCOME, "Data Manager")
    profile = make_salary_profile(seed_user, db.session)
    profile.template_id = template.id
    profile.is_active = True
    db.session.commit()
    return template, profile


def _transfer_template(seed_user, amount="250.00"):
    """Create a recurring transfer template with an Every-Period rule.

    It moves money from the owner's checking account into a new $0.00
    savings account, "Money Market", which its ``to_account_id`` names.
    """
    savings_acct = create_savings_account(
        seed_user, db.session, "Money Market", Decimal("0.00"),
    )
    db.session.commit()
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
    make_every_period_rule(db.session, template)
    tas.set_amount(template, Decimal(amount), effective_on=date(2020, 4, 9))
    db.session.commit()
    return template
