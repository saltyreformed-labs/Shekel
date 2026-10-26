"""A reopened envelope whose lump payment a match still names takes purchases.

Finding **CC-385** (owner ``credit_card:CC-5-4a-5``, leaf 5c-2b) and ruling
**R-CC141** (developer 2026-10-04): *"The user should be allowed to add
purchases from various sources to an envelope. The envelope is typically the
sum of the purchases."*  Groceries is planned at `$120.00` on Checking, closed
as ONE `$120.00` payment, matched to the bank's `-$120.00` line, and set back
to Projected.  The app keeps that payment UN-DATED (ruling **R-BAL61**), so it
counts nothing on any balance, and the match still names it -- the register
shows it as no longer adding up, with its Undo.  A `$30.00` purchase under
Groceries is then money no other line explains: filing a bank line into the
envelope, or matching a purchase it already holds, counts that `$30.00` once.

Both states the review's probe measured, because the owner-wide claims scan
refused them through two different accounts: the lump paid from the VISA and
matched on the Visa's screen, and the lump paid from CHECKING and matched on
Checking's own.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.statement_match import StatementMatchMember
from app.services import balance_at, entry_service
from app.services.balance_at import BalanceContext
from app.services.cash_ledger import settled_cash_facts
from app.services.statement_match import accept_match
from app.services.transaction_service import settle_transaction
from app.utils.dates import display_today
from tests._test_helpers import generate_row_of, make_expense_template, typed
from tests.test_services.test_cc5_4a1_settlement_subject._helpers import (
    _accept,
    _card,
    _entered,
    _first_day,
    _movement,
    _revert,
)
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    a_submission,
    an_import,
    filed_by,
)


def _reopened_lump(seed_user, tender):
    """Groceries closed as one `$120.00` lump from *tender*, matched there, reopened.

    Returns:
        ``(envelope, match_id)``: the Projected envelope, and the act on
        *tender*'s screen that still names its kept, un-dated payment.
    """
    bank_day = _first_day(seed_user)
    envelope = generate_row_of(
        make_expense_template(
            db.session, seed_user, amount="120.00", name="Groceries",
            category_key="Groceries", is_envelope=True,
        ),
        seed_user["bootstrap_period"],
    )
    db.session.commit()
    settle_transaction(
        envelope, submitted=typed(Decimal("120.00")),
        settle_day=_entered(bank_day), tender_account_id=tender.id,
    )
    db.session.commit()
    lump_line = a_bank_line(
        seed_user, an_import(seed_user, tender), amount="-120.00",
        posted_on=bank_day, description="KROGER LUMP",
    )
    db.session.commit()
    accepted = _accept(seed_user, tender, [lump_line], [envelope])
    _revert(envelope)
    kept = _movement(envelope)
    assert kept.settled_on is None, "a reopened row keeps its payment UN-DATED"
    assert kept.account_id == tender.id
    return envelope, accepted.match_id


def _settled_cash(seed_user, account):
    """Return what *account*'s dated movements sum to: its actual cash, less its opening."""
    return sum(
        (
            fact.delta for fact in settled_cash_facts(
                account.id, seed_user["scenario"].id,
            )
        ),
        Decimal("0"),
    )


def _projected(seed_user):
    """Return Checking's balance a week from today, every planned row landed.

    Read as of today, so the envelope -- filed in a past period, overdue --
    lands the day after (ruling R-G) and its unspent remainder is inside the
    figure.
    """
    today = display_today()
    ctx = BalanceContext(
        user_id=seed_user["user"].id, scenario=seed_user["scenario"],
        as_of=today,
    )
    return balance_at.balance_at(
        seed_user["account"], ctx, today + timedelta(days=7),
    )


def _before(seed_user, tender):
    """Return what :func:`_assert_counted_once` compares against, read before the act."""
    return (
        _settled_cash(seed_user, seed_user["account"]),
        _settled_cash(seed_user, tender),
        _projected(seed_user),
    )


def _assert_counted_once(seed_user, tender, before):
    """The `$30.00` counts once: as cash moved, and inside Groceries' `$120.00` plan.

    Ruling R-CC143's "Checking goes down $30.00" is Checking's ACTUAL cash
    (its dated movements); the lump's account moves by nothing, its kept
    payment being un-dated.  And the balance once every planned row has
    landed does not move at all: the purchase is spent out of the
    envelope's `$120.00`, so the projection holds `$90.00` where it held
    `$120.00` -- neither the `$30.00` again beside the plan, nor the plan
    dropped.

    Args:
        seed_user: The seeded user bundle.
        tender: The account the lump was paid from.
        before: :func:`_before`, read before the act.
    """
    checking = seed_user["account"]
    assert _settled_cash(seed_user, checking) - before[0] == Decimal("-30.00")
    if tender.id != checking.id:
        assert _settled_cash(seed_user, tender) - before[1] == Decimal("0")
    assert _projected(seed_user) == before[2]


def _names_the_kept_payment(envelope, match_id):
    """Return whether the act still names the envelope's kept payment."""
    return db.session.query(StatementMatchMember).filter_by(
        match_id=match_id, transaction_entry_id=_movement(envelope).id,
    ).one_or_none() is not None


@pytest.fixture(name="tender")
def _tender(request, seed_user):
    """The account the lump was paid from and matched on: the Visa, or Checking."""
    if request.param == "visa":
        card = _card(seed_user)
        db.session.commit()
        return card
    return seed_user["account"]


@pytest.mark.parametrize("tender", ["visa", "checking"], indirect=True)
def test_a_bank_line_files_into_the_reopened_envelope(app, seed_user, tender):
    """WALMART `-$30.00` files into Groceries on Checking's screen, once.

    The envelope is offered as a place the line can go, the filing lands as
    a `$30.00` purchase dated the bank's day, Checking's actual cash falls
    `$30.00` (the lump's account's by nothing), and the act naming the kept
    `$120.00` payment stands untouched: closing Groceries from its purchases
    later is what takes that payment, and its match, off the books.
    """
    with app.app_context():
        envelope, match_id = _reopened_lump(seed_user, tender)
        before = _before(seed_user, tender)
        walmart = a_bank_line(
            seed_user, an_import(seed_user), amount="-30.00",
            posted_on=_first_day(seed_user), description="WALMART",
        )
        db.session.commit()
        scope = a_scope(seed_user)
        assert envelope.id in {
            destination.transaction_id for destination in scope.destinations
        }
        filed_by(seed_user, walmart, envelope, by_rule=False, scope=scope)
        db.session.commit()
        db.session.expire(envelope)
        (purchase,) = envelope.purchases
        assert purchase.amount == Decimal("30.00")
        assert purchase.settled_on == walmart.posted_on
        assert _movement(envelope).settled_on is None
        assert _names_the_kept_payment(envelope, match_id)
        _assert_counted_once(seed_user, tender, before)


@pytest.mark.parametrize("tender", ["visa", "checking"], indirect=True)
def test_a_purchase_under_the_reopened_envelope_matches(app, seed_user, tender):
    """A `$30.00` Kroger purchase already under Groceries matches KROGER `-$30.00`.

    The kept `$120.00` payment counts nothing while it is un-dated, so the
    purchase's line is the only line explaining the purchase's money: it
    matches at its own figure and takes the bank's day, Checking's actual
    cash falls `$30.00` (the lump's account's by nothing), and the act naming
    the kept payment stands untouched.
    """
    with app.app_context():
        envelope, match_id = _reopened_lump(seed_user, tender)
        # Read before the purchase exists, so the projection is graded across
        # adding it AND matching it: a `$30.00` counted beside the plan would
        # already be inside a reading taken after the add.
        before = _before(seed_user, tender)
        entry_service.create_entry(
            envelope.id, seed_user["user"].id,
            entry_service.EntryDetails(
                figure=typed(Decimal("30.00")), description="Kroger",
                purchased_on=_first_day(seed_user),
            ),
        )
        db.session.commit()
        (purchase,) = envelope.purchases
        kroger = a_bank_line(
            seed_user, an_import(seed_user), amount="-30.00",
            posted_on=_first_day(seed_user), description="KROGER",
        )
        db.session.commit()
        scope = a_scope(seed_user)
        accept_match(
            a_submission(scope, lines=[kroger], entries=[purchase]), scope,
        )
        db.session.commit()
        db.session.expire(purchase)
        assert purchase.settled_on == kroger.posted_on
        assert _movement(envelope).settled_on is None
        assert _names_the_kept_payment(envelope, match_id)
        _assert_counted_once(seed_user, tender, before)
