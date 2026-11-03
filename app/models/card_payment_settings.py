"""
Shekel Budget App -- Card Payment Settings Model (budget schema)

WHICH AMOUNT a card works out for its payment (plan step
**credit_card:CC-6**, design ``docs/design/credit_card_from_scratch.md`` 3.5;
rulings plan#2666, plan#2667 and plan#2671).

A recurring transfer into a credit card is the card's worked-out payment when
it carries one of these rows, and an ordinary transfer -- a fixed payment, an
extra one -- when it does not.  The row says HOW MUCH, as one of the three
amounts :class:`app.enums.CardPaymentModeEnum` names; the transfer's own repeat
says WHEN (plan#2671), so nothing here names a day.  **No figure is stored**
(plan#2667): the amount is worked out fresh on every read, so the row has no
column a stale price could live in.

**The card is named ONCE.**  ``card_account_id`` sits beside the definition's
``to_account_id`` because the at-most-one-per-card key needs a column on THIS
table, and ``fk_card_payment_settings_payment`` holds the two equal: a
composite key onto ``uq_transfer_templates_id_to_account``, the definition's
``(id, to_account_id)`` superkey.  The settings row can therefore never name a
card its definition does not pay, and a definition's destination cannot move
while a settings row names it (``ON UPDATE NO ACTION``).
``uq_card_payment_settings_card_account_id`` keeps one worked-out payment per
card: two on one day would each subtract the other.

**Its owner is GUARANTEED rather than maintained**, the key
``pay_calendar:C13-c`` gives the owner-scoped tables:
``fk_card_payment_settings_owner`` holds ``user_id`` equal to the card's owner
by a composite key onto ``uq_accounts_id_user``, the construction
:class:`~app.models.credit_card_params.CreditCardParams` uses.

**Only a credit card can be the card.**  The doors refuse first, each with its
own sentence: an account re-type that would leave a payment on a non-card
(:func:`app.utils.account_validation._validate_account_type_change`) and a
transfer edit moving a card payment's destination
(:func:`app.routes.transfers._helpers._first_template_fk_refusal`).  The
database is the backstop for every other writer:
:mod:`app.card_payment_infrastructure`'s triggers, which read the
card's type through its account, so the type is never copied here.
"""

from app.extensions import db
from app.models.mixins import TimestampMixin, UserScopedMixin


class CardPaymentSettings(UserScopedMixin, TimestampMixin, db.Model):
    """The worked-out amount of one card payment (1:1 with its definition).

    Columns:

      ``transfer_template_id`` -- the recurring transfer this row prices; at
          most one row per definition
          (``uq_card_payment_settings_transfer_template_id``), and the row
          dies with it (``fk_card_payment_settings_payment``, CASCADE).
      ``card_account_id``      -- the card that definition pays, held equal to
          its ``to_account_id`` by the same key, and unique: one worked-out
          payment per card.
      ``mode_id``              -- which amount the card works out
          (``ref.card_payment_modes``, RESTRICT).
      ``user_id``              -- :class:`~app.models.mixins.UserScopedMixin`'s,
          held equal to the card's owner by ``fk_card_payment_settings_owner``.

    No ORM relationship to the card is declared, for the reason
    :class:`~app.models.credit_card_params.CreditCardParams` gives: the column
    sits under two composite keys and a joined backref on ``Account`` would add
    a join to every account load for a table most owners hold no row in.
    """

    __tablename__ = "card_payment_settings"
    __table_args__ = (
        db.UniqueConstraint(
            "transfer_template_id",
            name="uq_card_payment_settings_transfer_template_id",
        ),
        db.UniqueConstraint(
            "card_account_id",
            name="uq_card_payment_settings_card_account_id",
        ),
        # The definition, and the card it pays, in one key: onto the
        # definition's ``(id, to_account_id)`` superkey, so the card named
        # here is always the one the definition pays.
        db.ForeignKeyConstraint(
            ["transfer_template_id", "card_account_id"],
            [
                "budget.transfer_templates.id",
                "budget.transfer_templates.to_account_id",
            ],
            name="fk_card_payment_settings_payment",
            ondelete="CASCADE",
        ),
        # This row's owner IS its card's, keyed onto ``uq_accounts_id_user``.
        db.ForeignKeyConstraint(
            ["card_account_id", "user_id"],
            ["budget.accounts.id", "budget.accounts.user_id"],
            name="fk_card_payment_settings_owner",
            ondelete="CASCADE",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    transfer_template_id = db.Column(db.Integer, nullable=False)
    card_account_id = db.Column(db.Integer, nullable=False)
    mode_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.card_payment_modes.id",
            name="fk_card_payment_settings_mode_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    # Joined on BOTH columns of the payment key, so assigning a definition
    # here also states the card: SQLAlchemy copies ``to_account_id`` into
    # ``card_account_id`` with the id.
    template = db.relationship(
        "TransferTemplate", back_populates="card_payment_settings",
    )

    def __repr__(self):
        return (
            f"<CardPaymentSettings template_id={self.transfer_template_id} "
            f"card={self.card_account_id} mode={self.mode_id}>"
        )
