"""Which of a SOURCE's own category words mean the money left the budget.

Plan step ``bank_import:X-ga``, ruling **R-GJ**.  A bank line's
``source_category`` is one adapter's private vocabulary (ruling **R-FP**'s
seam), and translating it into a fact the rest of the app can act on is the
adapter layer's knowledge.  One fact is translated here and no other: whether a
line PAID AN ACCOUNT THE OWNER HOLDS rather than bought something.

**Two modules ask it and neither may import the other**, which is why it is a
leaf rather than a member of either.  :mod:`._bars` asks it of a LINE, to
decide whether a create-a-purchase arm exists; :mod:`._stating` asks it of a
SUBMISSION, to refuse an answer that would file such a merchant's money as
spending.  A second spelling of *which words mean this* across those two is the
duplication a money rule may least afford.

**It would live beside the adapter if it could.**  ``statement_import`` is
where a source's own words are read, and that package imports this one
(``_reads`` takes ``removals_by_match``), so the edge back would be a cycle.
The layering is inverted there rather than here; recorded as an improvement
worth making, not worked around by a private import.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozenset out, no Flask import, no clock read.  It READS and never writes.
"""

from __future__ import annotations

from app import ref_cache
from app.enums import StatementSourceEnum
from app.extensions import db
from app.models.statement_import import (
    BankStatementLine,
    StatementImport,
    StatementLineSighting,
)


#: Which of each SOURCE's own category strings name a payment to an account the
#: owner holds rather than spending.
#:
#: **The category is a card-payment one and the assertion is wider, and that is
#: the correction rather than sloppiness.**  A first version read this set as
#: *this is a credit card payment*, which is false of 7 of the 22 lines SECU
#: files under it -- they are the `$531.94` Van Loan car payment. What is true
#: of all 22 is that the money went to an account the owner holds, and that is
#: the only fact this bar rests on: neither a card payment nor a loan payment
#: is spending, so the refusal is right about every one of them and only the
#: WORDING was ever wrong.
#:
#: Keyed by the adapter rather than by the string alone, because a category
#: string is a source's private vocabulary: two banks may spell one meaning
#: differently and one spelling differently, and a set unioned across sources
#: would quietly apply one bank's meaning to another's words.  Two entries: the
#: one upload adapter ``statement_import._adapters`` registers, and the feed
#: source (below), whose reader the sync leaf of ``bank_import:X-f6b-2`` adds.
#:
#: SECU's own value, verbatim, measured on the developer's 2026-08-16 export:
#: 22 of the 378 recorded lines carry it.
#:
#: **It must name EVERY member of the enum, including with an empty set**, and
#: the reason is a fail-open an adversarial review 2026-08-24 measured: a
#: source absent from this map contributes no clause, so no line it recorded is
#: ever barred and no test fails.  ``statement_import._adapters`` -- which this
#: mirrors -- fails LOUD in the same position ("a member that gains no entry
#: here fails at the lookup below with a message naming it"), and a registry
#: that fails open beside one that fails loud is the asymmetry that lets
#: ``X-f6b``'s SimpleFIN feed silently retire the second bar.
#: ``test_bars.TestTheSourcesLabelOnlyASKS`` asserts the totality, so adding a
#: member without deciding its vocabulary breaks the suite rather than the
#: guard.
#:
#: **The SimpleFIN feed's entry is ruling R-BI22's** (developer, 2026-09-18:
#: a recorded feed line's MX category is the sighting's source category,
#: "R-GJ's vocabulary for this source: Financial Services/Credit Card Payment
#: and /Transfers"), and the two strings are MEASURED on the developer's two
#: 2026-09-18 dumps (183 lines): ``Financial Services/Credit Card Payment``
#: on 9 (the Capital One payment), ``Financial Services/Transfers`` on 2 (the
#: ``FUNDS TRANSFER TO ******6190`` mortgage payment -- money to an account
#: the owner holds, the fact this bar rests on).  Two more ``Financial
#: Services/`` spellings occur once each and are deliberately NOT here:
#: ``/ATM`` (cash out, which is spending's raw material) and ``/Investing``
#: (a Fidelity transfer, which the ruling did not name; a candidate for the
#: sync leaf's review, stated rather than decided by this entry).  Entered
#: at leaf (3c) of ``bank_import:X-f6b-2``, before any feed line is
#: recorded, because the totality test below refuses the member without it.
ACCOUNT_PAYMENT_CATEGORIES = {
    StatementSourceEnum.SECU_CHECKING_CSV: frozenset({
        "Financial Services/Credit Card Payment",
    }),
    StatementSourceEnum.SIMPLEFIN: frozenset({
        "Financial Services/Credit Card Payment",
        "Financial Services/Transfers",
    }),
}


def account_payment_merchants(account_id: int) -> "frozenset[int]":
    """Return the merchants this account's sources file as card payments.

    ONE statement over the account's whole recorded history rather than over
    this pass's leftovers, and the reason is the reason
    :func:`~._rules.account_merchants` spans the whole account too: a merchant
    whose every line is already matched is still one the owner may want to
    answer for, and the next statement will bring more of it.

    The SOURCE is joined rather than assumed, because a category is one
    source's private vocabulary
    (:attr:`~app.models.statement_import.StatementLineSighting.source_category`)
    and this account may hold lines from several (ruling **R-FP**'s adapter
    seam).  **A line is filed under a category by a SIGHTING of it** since
    plan step ``bank_import:X-f6b-1``: the read walks line -> sighting ->
    import, and a line qualifies when ANY import of a source filed it under
    that source's card-payment words.  **The merchant it answers with is the
    LINE's** -- the earliest surviving sighting's, the read
    :attr:`~app.models.statement_import.BankStatementLine.merchant_id` is
    (ruling **R-BI16**) -- and not the filing sighting's, because the bar
    this set feeds is asked of a line's merchant
    (:meth:`~._bars.CreationBars.bar_for`); the two are one sighting's
    except where two sources named a known line two words, and the line's
    answer is the one the bar is asked about.  **What narrows the read to this
    account is the explicit filter on the LINE**, not the joins: an
    adversarial review 2026-08-24 measured a first version of this sentence
    claiming a composite key made the narrowing structural, and reducing the
    join to the bare id left the account test green.  The composite halves
    are still stated, because a join that could pair a line with another
    account's sighting or import would be wrong even where a filter saves it
    -- but the filter is the guard, and
    ``test_ANOTHER_accounts_lines_do_not_reach_this_accounts_bars`` is what
    grades it.

    Args:
        account_id: The account being reviewed.

    Returns:
        The merchant ROW IDS, as a set (plan step ``bank_import:X-gd-1``).  A
        line naming none contributes nothing -- a rule cannot be keyed on it,
        so neither can a bar, and :meth:`~._bars.CreationBars.bar_for` is total
        over ``None`` BECAUSE of this filter rather than beside it.
    """
    filed_as = [
        db.and_(
            StatementImport.source_id == ref_cache.statement_source_id(source),
            StatementLineSighting.source_category.in_(sorted(categories)),
        )
        for source, categories in ACCOUNT_PAYMENT_CATEGORIES.items()
    ]
    rows = (
        db.session.query(BankStatementLine.merchant_id)
        .join(StatementLineSighting, StatementLineSighting.of_its_line())
        .join(StatementImport, StatementLineSighting.of_its_import())
        .filter(
            BankStatementLine.account_id == account_id,
            # Pylint: ``no-member`` -- a false positive on a hybrid: pylint
            # infers the getter function, where at class level this is the
            # sealed ``column_property``'s own expression (R-BI16).
            BankStatementLine.merchant_id.isnot(None),  # pylint: disable=no-member
            db.or_(*filed_as),
        )
        .distinct()
        .all()
    )
    return frozenset(row[0] for row in rows)
