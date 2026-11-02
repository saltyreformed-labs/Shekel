"""
Shekel Budget App -- The Card's Terms (a dated series)

The ONE home of every read and write of ``budget.credit_card_params``
(:class:`~app.models.credit_card_params.CreditCardParams`, plan step
credit_card:CC-2): the terms door, the cash detail page that renders the
terms, and the card gate a configured-card feature takes (plan step CC-3).
Each spelled the query itself until CC-3's review counted three; the loan
side's :func:`~app.services.loan_loaders.load_loan_params` exists for the
same reason.

**The terms are a DATED SERIES** (plan#2331, developer ruling **plan#2333**,
2026-11-02): each row is the whole term set in force from its
``effective_date`` until the next row's, so a change governs from its day and
every statement and reward before it stands.  Three rules, each answering a
question the one rewritten row could not:

1. **Which version governs a day** (:func:`terms_in_effect`): the row with
   the greatest ``effective_date`` on or before the day; before the earliest
   row, the earliest -- the series holds flat backwards, ruling **R-I**'s
   shape for a stated figure, which a recurring definition's price series
   already takes (:func:`app.services.template_amount_service.amount_as_of`).
   So a card with one version derives exactly as the undated row did, and the
   date the owner first saves terms can never make a statement before it
   vanish: the cycle open on the day terms are first saved began at the
   close before it, and its statement is cut by those terms.  The APR
   series answers ``None`` before its first row instead
   (:func:`app.services.card_apr.apr_in_effect`): that is one rate a finance
   charge multiplies, and ``None`` refuses to price interest at a rate
   nobody stated for those days.
2. **How terms are stated** (:func:`set_terms`): FOR a date, creating the
   version or rewriting the one already on that date -- developer ruling
   **R-CC27**'s set-by-date shape, the APR's, so a same-date save is the
   correction and a double submit cannot reach the key.
3. **Which reader takes which version**: a statement reads the version in
   force at its CLOSE, a reward the one in force on its purchase's day (the
   card's spec, plan#2331).  :mod:`app.services.card_statement` takes the
   series and picks for itself.

An EMPTY series is the DORMANT state, not an error (design 3.4: no
auto-create): a card whose owner has not stated its terms is a plain
liability and every card feature stays off.  A reader gates on
:func:`~app.services.account_projection.is_revolving` FIRST -- the rows
outlive a re-type, so their existence alone never means "this is a card".

Flask-isolated; the write flushes and never commits (the door owns the
transaction).
"""

from datetime import date

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.extensions import db
from app.models.credit_card_params import (
    CREDIT_CARD_TERMS_UNIQUE_CONSTRAINT,
    CreditCardParams,
)
from app.utils.effective_dated import in_effect_or_earliest

def load_card_term_versions(account_id: int) -> list[CreditCardParams]:
    """Return the account's terms versions, earliest effective date first.

    Args:
        account_id: The revolving account (the caller has decided the kind).

    Returns:
        Its :class:`~app.models.credit_card_params.CreditCardParams` rows in
        ascending ``effective_date`` order; empty while the card is dormant.
    """
    return (
        db.session.query(CreditCardParams)
        .filter_by(account_id=account_id)
        .order_by(CreditCardParams.effective_date)
        .all()
    )


def terms_in_effect(
    versions: list[CreditCardParams], day: date,
) -> CreditCardParams | None:
    """Return the version of the card's terms that governs *day*.

    The ONE walk (:func:`app.utils.effective_dated.in_effect_or_earliest`):
    the version with the greatest ``effective_date <= day``, and the earliest
    version for a day before every version (rule 1 in the module docstring).

    Args:
        versions: The card's versions (:func:`load_card_term_versions`), in
            any order.
        day: The day to resolve.

    Returns:
        The governing version; ``None`` only for an empty series (a dormant
        card).
    """
    return in_effect_or_earliest(versions, day)


def set_terms(
    account_id: int, user_id: int, effective_date: date, terms: dict,
) -> None:
    """State the card's terms in force from *effective_date*: create or correct.

    ONE statement against ``uq_credit_card_params_account_effective_date``:
    a version for the date is inserted, or the version already on that date
    has the terms *terms* names rewritten in place (its id and ``created_at``
    kept).  The door hands the whole form, every term every time
    (:class:`~app.schemas.validation.CreditCardTermsSchema` loads a blank
    nullable term as ``None``), so a correction is the whole version.
    Because the choice is the database's, two submits in flight for one date
    both succeed and the later one's terms stand, where a query-then-insert
    would raise for one of them -- :func:`app.services.card_apr.set_apr`'s
    idiom.  ``updated_at`` is set by hand on the update arm: an
    ``ON CONFLICT DO UPDATE`` runs no ORM ``onupdate`` hook.

    Args:
        account_id: The revolving account (the door has proved it is the
            owner's and a card).
        user_id: The ACCOUNT's owner, which ``fk_credit_card_params_owner``
            holds the row to.
        effective_date: The first day these terms govern.
        terms: The seven terms by column name, in the storage domain (the
            schema has already divided the two percents).
    """
    table = CreditCardParams.__table__
    db.session.execute(
        pg_insert(table)
        .values(
            account_id=account_id, user_id=user_id,
            effective_date=effective_date, **terms,
        )
        .on_conflict_do_update(
            constraint=CREDIT_CARD_TERMS_UNIQUE_CONSTRAINT,
            set_={**terms, "updated_at": db.func.now()},
        ),
    )
