"""
Shekel Budget App -- Credit Card Parameters Model (budget schema)

The card's TERMS, as the issuer states them: when the statement closes, when
its payment is due, how the minimum is computed, what the card pays back, and
the limit it is measured against (plan step **credit_card:CC-2**, design
``docs/design/credit_card_from_scratch.md`` 3.4, rulings **R-CC2** and
**R-CC4**).

**The terms are a DATED SERIES: one row per card per effective date** (plan#2331,
developer ruling **plan#2333**, 2026-11-02; ruling **R-FI**'s rule 1, "every
financial quantity that VARIES OVER TIME is a dated series resolved as-of").
Each row is the WHOLE term set in force from its ``effective_date`` until the
next row's -- the ``budget.rate_history`` shape the card's APR already has --
so a change of terms governs from its day and every statement and reward
derived before it stands.  CC-2 built one row per card that the form rewrote
in place, and because every statement and reward is DERIVED from these terms
(never stored), that rewrite would have re-cut every past statement and
re-priced every past reward once a reader existed.  Before the earliest row
the earliest governs (:func:`app.services.card_terms.terms_in_effect`), so a
card with one row derives exactly as the undated row did.

**The series is OPTIONAL, and nothing writes it but the owner** (design 3.4:
"no auto-create").  A Credit Card account with no row here is a dormant plain
liability -- its balance is the cash fold every non-loan account rides
(**R-CC14**) and no card feature is offered -- and every card feature gates on
a row's existence.  That is why the type carries ``has_parameters = FALSE``
under ``ck_account_types_revolving_is_plain`` (CC-1): that column means a
``*Params`` row that MUST exist beside the account, and this one need not.  So
:func:`app.services.account_params.ensure_type_params` has no arm for a card,
deliberately.

**What this table does NOT hold.**  The card's APR rides ``budget.rate_history``
unchanged, effective-dated and account-scoped, through a card-gated loader and
write door of its own (plan step CC-3); the statement itself is DERIVED from
these terms and the fold at the close instant, never stored (**R-CC2**, CC-3's
``card_statement.py``); the payment definition is CC-6's; rewards ACCRUE as a
derived figure and only a REDEMPTION is a recorded event (**R-CC4**, CC-9).
These rows are the terms those derivations read, each the version in force on
the day it governs: a statement's at its close, a reward's at its purchase.

**Every rate is stored as a FRACTION** (``0.0200`` for 2%), the E-28 / HIGH-06
convention every rate column in this schema holds: the form takes a percent,
the schema's ``@pre_load`` divides by 100, and the CHECK pins storage to
``[0, 1]`` so a writer that forgets the conversion is refused by the database
rather than storing a 200-fold minimum.

**Its owner is GUARANTEED rather than maintained.**  Beside the account key
each row carries ``user_id``, held equal to the account's by
``fk_credit_card_params_owner`` -- the composite key onto
``uq_accounts_id_user`` that ``fk_account_external_identities_owner`` and
``fk_merchant_destinations_owner`` use, and the shape plan step
``pay_calendar:C13-c`` (ruling **R-PC48**) gives six older tables.  A row for
one owner's card can therefore never name another owner, whatever a writer
passes, and the ownership gate on every door reads a column the database keeps
true instead of a join a reader could skip.
"""

from decimal import Decimal

from app.extensions import db
from app.models.mixins import (
    AccountScopedMixin,
    TimestampMixin,
    UserScopedMixin,
)

#: The one-version-per-effective-date key, named ONCE where the table declares
#: it: the terms door names it as the ``ON CONFLICT`` target of its set-by-date
#: write (:func:`app.services.card_terms.set_terms`), the
#: ``RATE_HISTORY_UNIQUE_CONSTRAINT`` construction.
CREDIT_CARD_TERMS_UNIQUE_CONSTRAINT = "uq_credit_card_params_account_effective_date"


class CreditCardParams(AccountScopedMixin, UserScopedMixin,
                       TimestampMixin, db.Model):
    """One version of the terms of a revolving credit account.

    Columns, and what each is FOR:

      ``effective_date``    -- the first day this version's terms govern; they
          govern until the next version's day (supersession, no end date, so
          two versions can never overlap -- the escrow versions' argument).
          One version per card per day
          (``uq_credit_card_params_account_effective_date``): a same-day save
          CORRECTS that version (developer ruling **R-CC27**'s set-by-date
          shape, the APR's), a later day appends one.
      ``statement_close_day`` -- the day of the month the issuer closes a
          statement (1-31; a 31 means the month's last day in a shorter month,
          clamped by :func:`app.utils.dates.clamped_day` where CC-3 derives the
          cycle).  A purchase posted AT the close belongs to the NEXT cycle
          (closed-open window; CC-3).
      ``payment_due_day``   -- the day of the month the closed statement's
          payment is due (1-31, same clamp).  The due DATE is the first
          occurrence of this day strictly after the close date
          (:func:`app.services.card_statement.due_date_for`, developer ruling
          **R-CC26**): the same month when its clamped date falls after the
          close date (closes the 5th, due the 28th), the next month
          otherwise (closes the 20th, due the 15th) -- so no ordering
          between the two days is a rule here.
      ``min_payment_percent`` -- the fraction of the statement balance the
          issuer requires (``0.0200`` for 2%); the minimum is
          ``max(min_payment_floor, round_money(pct x balance))`` clamped to
          the balance and never below zero
          (:func:`app.services.card_statement.minimum_payment`, **R-CC29**).
      ``min_payment_floor``   -- the dollar floor of that minimum.
      ``cashback_rate``       -- the flat reward fraction on purchases
          (**R-CC4**); ``0`` is a value, not missing (E-12), and the default
          is in the SAFE direction: a card whose owner never stated a rate
          accrues nothing.
      ``auto_redeem_threshold`` -- the accrued balance at which rewards are
          redeemed automatically (**R-CC4**: "auto-redeem at
          ``auto_redeem_threshold`` (nullable, e.g. $25)"); NULL means manual
          redemption only, so a stored value is always a real threshold, above
          zero.
      ``credit_limit``        -- the issuer's limit, read for utilization only
          (CC-11); NULL means not tracked, so a stored value is above zero.

    ``account_id`` (NOT NULL, CASCADE) is
    :class:`~app.models.mixins.AccountScopedMixin`'s -- not UNIQUE, since a
    card holds one row per effective date; ``user_id`` is
    :class:`~app.models.mixins.UserScopedMixin`'s and is bound to the
    account's owner by ``fk_credit_card_params_owner`` below.  No ORM
    relationship to the account is declared: the tables carrying this two-key
    construction (``account_external_identities``, ``merchant_rules``,
    ``statement_line_skips``) declare none to ``Account`` either, and the
    doors read the rows by ``account_id`` -- an ``account`` relationship would
    have to name which of the two foreign-key paths it follows, and a joined
    backref on :class:`~app.models.account.Account` would add a join to every
    account load in the application for a table most owners hold zero rows
    in.

    **The rows outlive a re-type.**  Changing the account's type away from
    the Credit Card kind deletes nothing here, exactly as an
    :class:`~app.models.interest_params.InterestParams` row survives its
    account's re-class (``account_params.ensure_type_params``: "nothing is
    REMOVED when a kind changes away").  So a reader must gate on
    :func:`~app.services.account_projection.is_revolving` FIRST and read
    these rows second; their existence alone never means "this is a card".
    """

    __tablename__ = "credit_card_params"
    __table_args__ = (
        # One version per card per day; the as-of read's index too (its
        # leading column is the card).
        db.UniqueConstraint(
            "account_id", "effective_date",
            name=CREDIT_CARD_TERMS_UNIQUE_CONSTRAINT,
        ),
        # The day-of-month domain, stated as the loan's ``payment_day`` is.
        db.CheckConstraint(
            "statement_close_day >= 1 AND statement_close_day <= 31",
            name="ck_credit_card_params_statement_close_day",
        ),
        db.CheckConstraint(
            "payment_due_day >= 1 AND payment_due_day <= 31",
            name="ck_credit_card_params_payment_due_day",
        ),
        # Both rates are FRACTIONS pinned to ``[0, 1]`` at the storage tier,
        # the counterpart of the schema's ``Range`` in the same domain (E-28):
        # a raw-SQL writer that stores the percent is refused here.
        db.CheckConstraint(
            "min_payment_percent >= 0 AND min_payment_percent <= 1",
            name="ck_credit_card_params_min_payment_percent",
        ),
        db.CheckConstraint(
            "cashback_rate >= 0 AND cashback_rate <= 1",
            name="ck_credit_card_params_cashback_rate",
        ),
        db.CheckConstraint(
            "min_payment_floor >= 0",
            name="ck_credit_card_params_min_payment_floor",
        ),
        # NULL is the "not set" state for both, so a stored value is always a
        # positive amount (``CHECK`` treats NULL as unknown and admits it).
        db.CheckConstraint(
            "auto_redeem_threshold IS NULL OR auto_redeem_threshold > 0",
            name="ck_credit_card_params_auto_redeem_threshold",
        ),
        db.CheckConstraint(
            "credit_limit IS NULL OR credit_limit > 0",
            name="ck_credit_card_params_credit_limit",
        ),
        # This row's owner IS its account's, guaranteed rather than
        # maintained -- keyed onto ``uq_accounts_id_user``, the construction
        # ``fk_account_external_identities_owner`` uses.  Without it
        # ``user_id`` would be a copy some writer has to keep in step.
        db.ForeignKeyConstraint(
            ["account_id", "user_id"],
            ["budget.accounts.id", "budget.accounts.user_id"],
            name="fk_credit_card_params_owner",
            ondelete="CASCADE",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    effective_date = db.Column(db.Date, nullable=False)
    statement_close_day = db.Column(db.SmallInteger, nullable=False)
    payment_due_day = db.Column(db.SmallInteger, nullable=False)
    min_payment_percent = db.Column(db.Numeric(5, 4), nullable=False)
    min_payment_floor = db.Column(db.Numeric(12, 2), nullable=False)
    # Default 0 on BOTH tiers, the ``loan_payment_settings.extra_principal``
    # idiom: the Python-side default serves the ORM constructor, the
    # ``server_default`` serves a raw INSERT, and the two agree so
    # ``compare_server_default`` reports no drift.
    cashback_rate = db.Column(
        db.Numeric(5, 4), nullable=False, default=Decimal("0"),
        server_default=db.text("0"),
    )
    auto_redeem_threshold = db.Column(db.Numeric(12, 2), nullable=True)
    credit_limit = db.Column(db.Numeric(12, 2), nullable=True)

    def __repr__(self):
        return (
            f"<CreditCardParams account_id={self.account_id} "
            f"from={self.effective_date} "
            f"close={self.statement_close_day} due={self.payment_due_day}>"
        )
