"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_transaction_posting_lifecycle.py``, moved verbatim by
``tools/split``.
"""

from __future__ import annotations

from decimal import Decimal

from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.services.ledger_lines import category_key
from tests._ledger_reads import (
    account_net,
    family_movement_ids,
    ledger_net,
    period_nets_of_movements,
    scenario_lines,
)
from tests._records_oracle import posted_purchase_effect, settled_transfer_effect
from tests._test_helpers import figure_source_columns
from app.services import cash_ledger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _category_chart_key(seed_user, category_name, ledger_class):
    """Return the chart key of a seed category's account."""
    return category_key(seed_user["categories"][category_name].id, ledger_class)


def _ledger_total(seed_user, key):
    """Return the worked-out net of chart account *key* in the owner's baseline."""
    return ledger_net(db.session, key, seed_user["scenario"].id)


def _family_nets(transaction_id, scenario_id):
    """Return ``{period id: {chart key: net}}`` over the lines of the row's movements."""
    return period_nets_of_movements(family_movement_ids(transaction_id), scenario_id)


def _purchase_lines(entry_id, scenario_id):
    """Return the worked-out lines of one purchase."""
    return [
        line for line in scenario_lines(db.session, scenario_id)
        if line.movement_id == entry_id
    ]


def _add_purchase(seed_user, txn, amount, *, is_credit=False):
    """Attach one purchase entry (debit or credit) to *txn* directly and flush.

    Bypasses ``entry_service`` so the envelope's pre-settle state can be built
    without triggering the entry-level payback workflow; the tests that exercise
    the entry-mutation hook call ``entry_service`` explicitly instead.
    """
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
        user_id=seed_user["user"].id,
        amount=Decimal(amount),
        description="purchase",
        purchased_on=txn.pay_period.start_date,
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def _assert_reconciles(scenario_id, *accounts):
    """Assert ledger == opening + settled-source effect (the absolute oracle).

    The per-account reconciliation invariant in its Step-5 ABSOLUTE form: the
    net of an account's ledger lines equals its opening anchor correction (its
    opening equity, equal to the origination row's balance, the one assertion
    here) plus the net effect of its settled, non-deleted transfers AND
    ordinary transactions.  Every settle in this suite
    is stamped at server-now, after the assertion instant, so the effects
    ride on top of the opening.  Asserted from independent producers so any
    divergence between the ledger and the source rows fails loudly.

    **"Rides on top of the opening" is a PRECONDITION, not a claim**, and since
    plan step X-f3b one test breaks it deliberately: a purchase's cash leg is
    dated the day the BANK took the money, which may be on or before an
    assertion, in which case that assertion's correction absorbs it and the
    ledger lands on the asserted balance rather than on opening + effect.  That
    test asserts the figure directly and says so at the call site.

    **TWO terms since plan step ``balance:X-bi-4a``** (ruling **R-BAL80**): a
    transfer's settled effect (off the income shadow's record then, off the
    side's record and the transfer's one figure since plan step
    ``balance:X-bi-6-4d-2``), and every DATED
    movement of a contributing non-transfer parent (``posted_purchase_effect``,
    keyed on the movement's own account).  A plan row posts nothing of its
    own, so the transaction source's term (``settled_transaction_effect``,
    whose ``effective - credit`` sum was what the parent's leg and its
    purchases' legs added up to) went with the leg.
    """
    for account in accounts:
        posted = account_net(db.session, account.id, scenario_id)
        effect = (
            settled_transfer_effect(account.id, scenario_id)
            + posted_purchase_effect(account.id, scenario_id)
        )
        opening = Decimal(str(cash_ledger.resolve_anchor(account).balance))
        assert posted == opening + effect, (
            f"account {account.id}: ledger {posted} != opening {opening} "
            f"+ source effect {effect}"
        )
