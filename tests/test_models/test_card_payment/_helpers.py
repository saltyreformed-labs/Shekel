"""The two builders both card-payment test modules share."""

from app.extensions import db
from app.models.transfer_template import TransferTemplate
from tests._test_helpers import create_account_of_type


def account_of(seed_user, type_name, name):
    """An account of *seed_user*'s of the built-in type *type_name*."""
    return create_account_of_type(seed_user, db.session, type_name, name)


def payment_into(seed_user, account, name="Pay the card"):
    """A flushed transfer definition from *seed_user*'s checking into *account*.

    No repeat and no price: the schema and its rule read neither.
    """
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=account.id,
        name=name,
    )
    db.session.add(template)
    db.session.flush()
    return template
