"""Templates: every-period expense, income and transfer definitions, a projected envelope expense,
an entry-tracked expense, and a read pass populated over them.
"""

from decimal import Decimal

from ._rows import generate_row_of
from ._prices_and_rules import state_template_price, make_every_period_rule


def create_envelope_txn(seed_user, db_session, period, name, estimated):
    """Create an entry-tracked (is_envelope) projected expense (flushed).

    Builds a minimal Every-Period envelope template and lets the engine
    generate its Projected row in ``period`` (:func:`generate_row_of`), so the
    stereotyped template + instance construction is not copied per suite (a
    duplicate-code finding) and the row is the one the application makes
    rather than a hand-built copy of it (plan step balance:X-cf).  The
    envelope's budget is the template's stated price, which the derived row
    reads; the caller attaches entries via :func:`add_entry` and commits.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        period: The :class:`~app.models.pay_period.PayPeriod` to place the
            instance in.
        name: The template / transaction name.
        estimated: The envelope's estimated (budget) amount (Decimal).

    Returns:
        The generated :class:`~app.models.transaction.Transaction` (flushed).
    """
    template = make_expense_template(
        db_session, seed_user, amount=str(estimated),
        name=name, category_key="Groceries", is_envelope=True,
    )
    return generate_row_of(template, period)


def make_expense_template(
    db_session, seed_user, amount="1200.00", is_active=True, *,
    name="Rent", category_key="Rent", is_envelope=False,
    companion_visible=False, account=None, category=None,
):
    """Create and flush an every-period expense template on the seed account.

    Shared by the pay-period CRUD test suites so the
    ``RecurrenceRule`` + ``TransactionTemplate`` construction block is
    defined once.  The caller commits.  **It states the definition's price
    and its cadence**, so what it returns is a definition the engine can
    generate from -- the shape every real one has (:func:`state_template_price`
    says why a bare template is not) -- and :func:`generate_row_of` is the
    next line for a fixture that wants the definition's row.
    :func:`make_income_template` is the same builder for an income
    definition; the two share one body.

    Args:
        db_session: The test session.
        seed_user: The seed user fixture dict (or the second user's).
        amount: The stated price, as a string.
        is_active: Whether the definition is live.
        name: The definition's name.  Keyword-only, with the name every
            existing caller relied on, so widening this helper for the shared
            fixtures (plan step balance:X-cf) changed no caller.
        category_key: A key into ``seed_user["categories"]``.
        is_envelope: Whether the definition's rows track purchases.
        companion_visible: Whether a companion of the owner may see its rows.
            The second of the two flags a generated row RESOLVES through its
            definition (``Transaction.visible_to_companion``), widened here
            for the same reason as ``is_envelope`` and defaulting to the
            column's own default.
        account: The :class:`~app.models.account.Account` the definition
            moves money through; the seed user's checking account when
            omitted.  Widened at plan step balance:X-cf-3 for the fixtures
            whose row lives on an HYSA or a 401(k): the engine puts a row on
            its DEFINITION's account (``DerivedRowFields.account_id``), so
            that is the only place a fixture can say where the row goes.
        category: The :class:`~app.models.category.Category` the definition
            files under, when it is not one of the seed's -- a test that
            authors its own category passes the row here and *category_key*
            is not read.  Widened at plan step balance:X-cf-4 for the same
            reason as *account*: the engine files a row under its
            DEFINITION's category (``DerivedRowFields.category_id``), so the
            definition is the only place a fixture can say so.

    Returns:
        The flushed :class:`~app.models.transaction_template.TransactionTemplate`,
        priced and carrying an every-paycheck rule.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.enums import TxnTypeEnum

    return _priced_repeating_template(
        db_session, seed_user, TxnTypeEnum.EXPENSE, amount, is_active,
        name=name, category_key=category_key, is_envelope=is_envelope,
        companion_visible=companion_visible, account=account,
        category=category,
    )


def make_income_template(
    db_session, seed_user, amount="2000.00", is_active=True, *,
    name="Paycheck", category_key="Salary", is_envelope=False,
    companion_visible=False, account=None, category=None,
):
    """Create and flush an every-period INCOME template on the seed account.

    :func:`make_expense_template`'s twin for a definition that pays the owner
    rather than bills them, sharing that helper's body so a fixture wanting an
    income definition's row (plan step balance:X-cf) does not restate the
    template stanza with one column changed.  Same contract: priced, carrying
    an every-paycheck rule, and :func:`generate_row_of` is the next line.

    Args:
        db_session: The test session.
        seed_user: The seed user fixture dict (or the second user's).
        amount: The stated price, as a string.
        is_active: Whether the definition is live.
        name: The definition's name.
        category_key: A key into ``seed_user["categories"]``.
        is_envelope: Whether the definition's rows track purchases.
        companion_visible: Whether a companion of the owner may see its rows.
        account: The account the definition pays into; the seed user's
            checking account when omitted.  See :func:`make_expense_template`.
        category: The category the definition files under, when it is not
            one of the seed's.  See :func:`make_expense_template`.

    Returns:
        The flushed :class:`~app.models.transaction_template.TransactionTemplate`,
        priced and carrying an every-paycheck rule.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.enums import TxnTypeEnum

    return _priced_repeating_template(
        db_session, seed_user, TxnTypeEnum.INCOME, amount, is_active,
        name=name, category_key=category_key, is_envelope=is_envelope,
        companion_visible=companion_visible, account=account,
        category=category,
    )


def make_projected_envelope_expense(
    db_session, *, seed_user, pay_period, estimated, account=None,
    name="Groceries",
):
    """Create a Projected envelope expense + its definition in ``pay_period``.

    The engine's own row of a priced, every-paycheck ``is_envelope=True``
    definition (:func:`make_expense_template` then :func:`generate_row_of`,
    plan step balance:X-cf), which is what entries attach to.  Uses the seed
    user's Groceries category so the row matches the symptom #1 / #5 worked
    example.  ``account`` defaults to the seed user's checking account; pass
    the account when the row should live elsewhere -- the engine puts a row
    on its DEFINITION's account, so that is where the choice is made.

    **One definition** (plan ledger row BAL-490, closed at balance:X-ch): the
    accounts route suite and the savings dashboard suite each carried a
    private copy, and by the time they were folded here the copies had
    already drifted in signature (one required ``account``, one defaulted
    it), which is the drift a fixture spelled twice invites.

    Args:
        db_session: The test session.
        seed_user: The seed user fixture dict.
        pay_period: The period the row lands in.
        estimated: The definition's stated price, as a string.
        account: The account the definition (and so the row) lives on;
            ``None`` means the seed user's checking account.
        name: The definition's name.

    Returns:
        The generated Transaction row, flushed.
    """
    template = make_expense_template(
        db_session, seed_user, amount=estimated,
        name=name, category_key="Groceries", is_envelope=True,
        account=account,
    )
    return generate_row_of(template, pay_period)


def _priced_repeating_template(
    db_session, seed_user, txn_type, amount, is_active, *,
    name, category_key, is_envelope, companion_visible, account, category,
):
    """The one body behind :func:`make_expense_template` and its income twin.

    Args:
        db_session: The test session.
        seed_user: The seed user fixture dict (or the second user's).
        txn_type: The :class:`~app.enums.TxnTypeEnum` member the definition
            is of.
        amount: The stated price, as a string.
        is_active: Whether the definition is live.
        name: The definition's name.
        category_key: A key into ``seed_user["categories"]``.
        is_envelope: Whether the definition's rows track purchases.
        companion_visible: Whether a companion of the owner may see its rows.
        account: The account the definition is on, or ``None`` for
            ``seed_user["account"]``.
        category: The category the definition files under, or ``None`` for
            ``seed_user["categories"][category_key]``.

    Returns:
        The flushed :class:`~app.models.transaction_template.TransactionTemplate`,
        priced and carrying an every-paycheck rule.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.models.transaction_template import TransactionTemplate

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=(seed_user["account"] if account is None else account).id,
        category_id=(
            seed_user["categories"][category_key] if category is None
            else category
        ).id,
        transaction_type_id=ref_cache.txn_type_id(txn_type),
        name=name,
        is_active=is_active,
        is_envelope=is_envelope,
        companion_visible=companion_visible,
    )
    db_session.add(template)
    db_session.flush()
    state_template_price(template, Decimal(amount))
    # The definition first, then the cadence onto it (plan step R-F6).
    make_every_period_rule(db_session, template)
    return template


def populate_in_a_fresh_pass(user_id, period_ids):
    """Open a read pass over the CURRENT state and populate *period_ids*.

    The producer takes a :class:`~app.services.balance_at.BalanceContext` and
    builds none (ruling **R-R38**), so every caller owes it a pass -- and owes
    it AFTER the write that created the periods, because a pass resolved
    earlier holds a calendar that does not contain them and
    ``GenerationSchedule.__post_init__`` refuses the window.  Route code says
    that once, in ``app.routes._period_population.populate_new_periods``, which
    takes the PayPeriod rows a door returned; the suite says it here, because
    what a test usually holds is a set of ids.

    Args:
        user_id: The owning user's id.
        period_ids: The ``budget.pay_periods.id`` values to populate.

    Returns:
        The number of template-linked records created.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services.balance_at import BalanceContext
    from app.services.period_population import (
        populate_periods_from_active_templates,
    )

    return populate_periods_from_active_templates(
        BalanceContext.build(user_id), period_ids,
    )


def make_transfer_template(db_session, seed_user, to_account, amount="200.00"):
    """Create and flush an every-period transfer template (checking -> to).

    Shared by the pay-period CRUD test suites so the
    ``RecurrenceRule`` + ``TransferTemplate`` construction block is
    defined once.  The caller commits.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.models.transfer_template import TransferTemplate

    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=to_account.id,
        name="To Savings",
    )
    db_session.add(template)
    db_session.flush()
    # A definition STATES its price, exactly as ``routes/transfers/templates``
    # does on every create.  Since plan step X-au-f a generated TRANSFER stores
    # no figure either, so a bare-constructed transfer template generates rows
    # nothing can price -- the transaction twin's obligation, one table over.
    state_template_price(template, Decimal(amount))
    # The definition first, then the cadence onto it (plan step R-F6).
    make_every_period_rule(db_session, template)
    return template
