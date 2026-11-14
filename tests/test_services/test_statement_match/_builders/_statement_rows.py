"""The statement's side of a match: an import, its bank lines and their sightings.

With them, the merchants a line names, the merchant rules a test stages or submits, and an
account whose opening equity already accounts for a line.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatementSourceEnum
from app.extensions import db
from app.models.merchant import Merchant
from app.models.merchant_rule import MerchantRule
from app.models.statement_import import BankStatementLine, StatementImport, StatementLineSighting
from app.services.statement_match import RuleSubmission


def an_import(
    seed_user, account=None, *, created_at=None,
    declared=(date(2026, 1, 1), date(2026, 12, 31)),
):
    """Stage and return one statement import for an account.

    Args:
        seed_user: The seeded user bundle.
        account: The account; the seeded checking one by default.
        created_at: The instant the import ran, or ``None`` to take the
            column's own ``now()`` default.  Stated only by a case whose
            subject is WHICH import is newest or WHICH day one displays on --
            and it has to be stated for the first of those, because ``now()``
            is the TRANSACTION's start time in PostgreSQL, so two imports
            written in one test carry the identical instant.
        declared: The window the import DECLARES it answers for, as
            ``(first, last)`` (ruling **R-BAL71**).  Wide by default so a
            line staged on any seeded day falls inside it.

    **It states no count** since plan step ``bank_import:X-f6b-1``: what a
    file held and what it added are derived from the sightings its lines
    carry (:func:`a_bank_line`, :func:`a_sighting`), so a fixture states the
    sightings it means and the counts follow.

    Returns:
        The staged :class:`~app.models.statement_import.StatementImport`.
    """
    statement = StatementImport(
        account_id=(account or seed_user["account"]).id,
        user_id=seed_user["user"].id,
        source_id=ref_cache.statement_source_id(
            StatementSourceEnum.SECU_CHECKING_CSV
        ),
        file_name="statement.csv",
        file_digest="c" * 64,
        declared_start=declared[0],
        declared_end=declared[1],
    )
    if created_at is not None:
        statement.created_at = created_at
    db.session.add(statement)
    db.session.flush()
    return statement


def a_merchant(seed_user, name, *, account=None, account_id=None):
    """Stage and return the merchant row *name* names, creating it once.

    **Built through the ORM, like everything else here**, so a broken
    ``statement_import._merchants.resolve_merchants`` cannot also build the
    fixture a reader test would have caught it with -- the rule
    :func:`a_rule` already states.

    **Get-or-create, because a merchant is per ACCOUNT and a test states its
    name several times.**  Two lines from one merchant are two lines and ONE
    merchant, and a builder that inserted twice would fail
    ``uq_merchants_account_name`` -- which is the fixture reproducing the
    production identity rather than working around it.

    Args:
        seed_user: The seeded user bundle.
        name: What the source calls the merchant.
        account: The account whose statements name it; the seeded checking one
            by default.
        account_id: Its id, where the caller holds that instead -- a bank line
            carries its import's ``account_id`` and no account object.

    Returns:
        The staged :class:`~app.models.merchant.Merchant`.
    """
    if account_id is None:
        account_id = (account or seed_user["account"]).id
    found = (
        db.session.query(Merchant)
        .filter(Merchant.account_id == account_id, Merchant.name == name)
        .one_or_none()
    )
    if found is not None:
        return found
    row = Merchant(account_id=account_id, name=name)
    db.session.add(row)
    db.session.flush()
    return row


def a_bank_line(
    seed_user, statement, *, amount="-180.00", posted_on=None,
    description="ACH DEBIT DUKEENERGY", sequence_in_group=0,
    transaction_on=None, merchant=None, source_category=None,
):
    """Stage and return one recorded bank line, sighted by *statement*.

    The line is the bank's fact and *statement*'s sighting of it is what
    that source said (plan step ``bank_import:X-f6b-1``, ruling **R-BI10**):
    the line carries the day, the amount and the ordinal; the sighting
    carries the wording, the merchant KEY (plan step ``bank_import:X-f6b-1b``,
    ruling **R-BI16**), the stated day and the category.  A second import
    that shows the same line adds a sighting through :func:`a_sighting`.

    Args:
        seed_user: The seeded user bundle.
        statement: The import that showed it.
        amount: Signed, positive INTO the account.
        posted_on: The day the bank posted it.
        description: What the bank called it.
        sequence_in_group: The ordinal completing its identity.
        transaction_on: The day the bank STATED the transaction happened, or
            ``None`` for a source stating none -- which is the DEFAULT here
            because it is the majority case on the developer's own statement
            (179 of 361 lines) and because a fixture that always states one
            would never exercise
            :attr:`~app.services.statement_match._offers.BankLine.happened_on`'s
            fallback.
        merchant: What the source NAMES the merchant, as a string, or ``None``
            for a source naming none -- which is the DEFAULT, and it is a
            fixture decision worth stating.  The string is resolved to a
            :class:`~app.models.merchant.Merchant` row
            (:func:`a_merchant`, through :func:`a_sighting`) for the
            sighting's KEY, so a test states the name it means and the
            fixture reproduces the production identity; the line's own
            ``merchant_id`` is then the read over its sightings the app
            makes.  **It is NOT derived from *description* here.**  A builder that re-ran the
            adapter's own parse would move with it, so a change to that
            parse would shift the fixture and the assertion together and
            grade nothing; the parse is graded where it belongs, against
            real CSV bytes, in
            ``tests/test_services/test_statement_import/test_secu_csv.py``.
            What these builders state is the recorded FACT.  ``None`` is the
            default because it is the state every guard here has to survive: a
            line with no merchant joins no destination rule, and the label
            readers fall back to *description*.
        source_category: The BANK's own category string, or ``None`` for a
            source stating none -- which is the DEFAULT, because it is
            provenance and almost nothing reads it.  Ruling **R-GJ** is the one
            thing that does: a merchant whose lines the source files under a
            card-payment category has no create arm until the owner answers for
            it (:class:`~app.services.statement_match.CreationBars`).  Stated
            here VERBATIM as the source spells it, never mapped, for the reason
            *merchant* is not derived from *description*: a builder that
            re-ran a translation would move with it and grade nothing.

    Returns:
        The staged
        :class:`~app.models.statement_import.BankStatementLine`, its
        sighting loaded.
    """
    day = posted_on or seed_user["bootstrap_period"].start_date
    line = BankStatementLine(
        account_id=statement.account_id,
        posted_on=day,
        amount=Decimal(amount),
        sequence_in_group=sequence_in_group,
    )
    db.session.add(line)
    db.session.flush()
    a_sighting(
        seed_user, statement, line, description=description,
        merchant=merchant, transaction_on=transaction_on,
        source_category=source_category,
    )
    return line


def a_sighting(
    seed_user, statement, line, *, description="ACH DEBIT DUKEENERGY",
    merchant=None, transaction_on=None, source_category=None,
    running_balance=None,
):
    """Stage and return *statement*'s sighting of *line*.

    What ONE import said about ONE line (plan step ``bank_import:X-f6b-1``).
    :func:`a_bank_line` writes the first; a case whose subject is a line two
    imports showed writes the second here.  **The line is expired
    afterwards** -- its eager ``sightings`` collection and its two merchant
    projections, the one set the record door expires
    (``BankStatementLine.READS_OVER_SIGHTINGS``) -- so the next read reports
    the row and not the instance as it stood before this sighting existed.

    Args:
        seed_user: The seeded user bundle, for :func:`a_merchant`.
        statement: The import that showed the line.
        line: The line it showed.
        description: What this source called it.
        merchant: What this source NAMES the merchant, as a string, or
            ``None`` for a source naming none.  Resolved to the account's
            :class:`~app.models.merchant.Merchant` row here for this
            sighting's KEY (plan step ``bank_import:X-f6b-1b``, ruling
            **R-BI16**), which is the fact the app records.
        transaction_on: The day this source states it was made, or ``None``.
        source_category: This source's category string, or ``None``.
        running_balance: The balance this source stated after it, or ``None``.

    Returns:
        The staged
        :class:`~app.models.statement_import.StatementLineSighting`.
    """
    sighting = StatementLineSighting(
        account_id=line.account_id,
        line_id=line.id,
        import_id=statement.id,
        description=description,
        merchant_id=(
            None if merchant is None
            else a_merchant(seed_user, merchant, account_id=line.account_id).id
        ),
        transaction_on=transaction_on,
        source_category=source_category,
        running_balance=(
            None if running_balance is None else Decimal(running_balance)
        ),
    )
    db.session.add(sighting)
    db.session.flush()
    db.session.expire(line, BankStatementLine.READS_OVER_SIGHTINGS)
    return sighting


def the_merchant_id(seed_user, name, *, account=None):
    """Return the id of the merchant *name* names, refusing to create one.

    **:func:`a_merchant` is get-or-create and it FLUSHES**, so calling it
    inside an assertion writes to the database from a comparison -- and, worse,
    quietly supplies the row whose absence the case may be about.  Both
    adversarial reviews of 2026-08-25 flagged the shape.  This is the read-only
    half: a case ARRANGES with :func:`a_merchant` and ASSERTS with this one, so
    an assertion can only ever observe.

    Args:
        seed_user: The seeded user bundle.
        name: What the source calls the merchant.
        account: The account whose merchants to look in; the seeded checking
            one by default.

    Returns:
        Its :class:`~app.models.merchant.Merchant` id.

    Raises:
        AssertionError: When this account holds no such merchant -- which is
            the failure the case wanted to see, named rather than papered over
            by a fixture minting one.
    """
    account_id = (account or seed_user["account"]).id
    found = (
        db.session.query(Merchant.id)
        .filter(Merchant.account_id == account_id, Merchant.name == name)
        .one_or_none()
    )
    assert found is not None, (
        f"no merchant named {name!r} on account {account_id} -- the code "
        f"under test did not create it"
    )
    return found[0]


def a_statement(seed_user, merchant, answer, *, account=None, **fields):
    """Return the submission a test makes ABOUT *merchant*, by name.

    **The door takes a merchant ROW ID** (plan step ``bank_import:X-gd-1``), and
    a case that spelled one inline would be asserting against a number instead
    of against a merchant.  This resolves the name the same way the screen
    does -- to the row -- so a case still reads as *say where Amazon goes*.

    **It CREATES the merchant where the case has staged no line naming it**
    (:func:`a_merchant`), which is what makes it wrong for the two cases that
    grade the scope refusal: those need an id this account does NOT have, and
    they state one directly.

    Args:
        seed_user: The seeded user bundle.
        merchant: What the source calls the merchant.
        answer: The :class:`~app.services.statement_match.RuleAnswer`.
            **Required, and it used to default to ``None`` meaning WITHDRAW**
            (ruling **R-GS**, plan step ``bank_import:X-gd-2``): there is no
            withdrawal, a submission always states one of the four answers, and
            the screen's *I have not said* never reaches this door at all
            because the route drops it.
        account: The account it governs; the seeded checking one by default.
        **fields: ``template_id`` / ``envelope_name`` / ``category_id``, as the
            answer needs them.

    Returns:
        The :class:`~app.services.statement_match.RuleSubmission`.
    """
    return RuleSubmission(
        merchant_id=a_merchant(seed_user, merchant, account=account).id,
        answer=answer, **fields,
    )


def a_rule(
    seed_user, merchant, *, template_id=None, envelope_name=None,
    category_id=None, income_category_id=None, always_ask=False, account=None,
):
    """Stage and return one stated merchant rule.

    Built through the ORM like everything else here, so a broken
    ``state_rules`` cannot also break the fixture a reader test would have
    caught it with -- and the four columns are spelled out rather than taken
    from ``_rules._columns_of``, so a mistake in that mapping cannot arrive
    here and agree with itself.

    Args:
        seed_user: The seeded user bundle.
        merchant: What the source calls the merchant.  Resolved to the
            :class:`~app.models.merchant.Merchant` row that IS the key
            (:func:`a_merchant`), creating it where this test has staged
            no line naming it.
        template_id: The recurring definition to file into, for the TEMPLATE
            answer.
        envelope_name: What to call the envelope to create, for the
            NEW ENVELOPE answer.
        category_id: The category to create it under, likewise.
        income_category_id: What a DEPOSIT from this merchant is, for the
            INCOME CATEGORY answer (ruling **R-HT(a)**, plan step
            ``bank_import:X-gj-2a``).  **Its own parameter and not a second use
            of ``category_id``**, for the reason the COLUMN is its own: the two
            answer different questions, and a fixture that shared one field
            could stage a row the CHECK refuses while looking correct.
        always_ask: Which of the two CONTAINER-LESS answers this is
            (ruling **R-GS**) -- ``False`` for *never a purchase*, ``True`` for
            *ask me every time*.  **Read only when no container arm is given**,
            which is the same discipline ``_rules._columns_of`` follows: a
            field belongs to one answer, so naming it beside another answer's
            arm states nothing.
        account: The account it governs; the seeded checking one by default.

    Returns:
        The staged :class:`~app.models.merchant_rule.MerchantRule`.  With no
        arm given at all it is the NEVER answer, which is what every caller
        written before ``always_ask`` existed meant by it.

        **The five columns are spelled out here rather than taken from
        ``_stating._columns_of``**, which is this builder's founding rule and
        matters more now there are five: a mistake in that mapping cannot
        arrive here and agree with itself.  What DOES grade the pair is the
        round trip over ``RuleAnswer.of``.
    """
    names_something = (
        template_id is not None
        or envelope_name is not None
        or income_category_id is not None
    )
    row = MerchantRule(
        user_id=seed_user["user"].id,
        account_id=(account or seed_user["account"]).id,
        merchant_id=a_merchant(seed_user, merchant, account=account).id,
        template_id=template_id,
        envelope_name=envelope_name,
        category_id=category_id,
        income_category_id=income_category_id,
        never_a_purchase=not names_something and not always_ask,
    )
    db.session.add(row)
    db.session.flush()
    return row


def an_account_whose_books_hide_a_line(db, seed_user, seed_periods):
    """Return an account whose opening equity already accounts for one line.

    **Built through ``create_account``, not by restating the seeded account.**
    These route fixtures open their pay calendar at ``seed_periods[0]``, years
    after the seeded account's own origination assertion -- so restating THAT
    account's books onto the calendar's first day would move them past an
    assertion, which the restatement door refuses.  A fixture may not assert
    against a state no door can produce, which is the objection an adversarial
    test-quality review raised against exactly this shape.

    An account CREATED with its ``observed_on`` on the calendar's first day
    opens its books there legally, and that is the developer's own Checking
    shape: books opening on the pay calendar's own first day, so a line posted
    that day is inside the calendar and inside the opening equity at once.

    **It lives here because it was BYTE-IDENTICAL in three route modules** --
    the review queue, the reconcile page and the workbench -- and it belongs
    beside :func:`a_bank_line` and :func:`an_import`, which it calls.  Same
    count and same remedy as ``_A_MATCHED_GROUP`` in ``tests/_test_helpers``,
    found by the same adversarial review one pass later.  ``pylint``'s
    ``duplicate-code`` never reads ``tests/``, so nothing but a reader was
    going to find it.

    Args:
        db: The test ``db`` fixture.
        seed_user: The seeded user bundle.
        seed_periods: The seeded pay periods.

    Returns:
        ``(account, day)`` -- the account and the day both its books and the
        line fall on.
    """
    # Pylint: ``import-outside-toplevel`` -- ``account_service`` reaches back
    # into this package's subject at import time; the three modules that used
    # to hold their own copy of this fixture deferred it for the same reason.
    # pylint: disable=import-outside-toplevel
    from app.services import account_service as _accounts

    day = seed_periods[0].start_date
    account = _accounts.create_account(
        _accounts.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=seed_user["account"].account_type_id,
            name="Books Hide A Line",
            anchor_balance=Decimal("689.16"),
            observed_on=day,
        ),
    )
    db.session.flush()
    a_bank_line(
        seed_user, an_import(seed_user, account=account), posted_on=day,
    )
    db.session.commit()
    return account, day
