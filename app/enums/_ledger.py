"""The posting ledger's lookups: an account's class and kind, a posting's kind and source.

Split out of the flat ``app/enums.py`` at plan step **credit_card:CC-6** (see
:mod:`app.enums` for why).  Every class below stands exactly as it stood there.
"""

import enum


class LedgerAccountClassEnum(enum.Enum):
    """Ledger account class values for the double-entry posting ledger.

    The five fundamental accounting classes (Build-Order Step 2) plus the
    reporting class ruling **R-FO** added (plan step X-f3d).  Values match
    ``ref.ledger_account_classes.name``.  Asset and Expense are debit-normal;
    Liability, Income, Equity and Unrealized are credit-normal -- the
    natural-balance side is stored as the ``is_debit_normal`` boolean on
    each row and read via ``ref_cache.ledger_class_is_debit_normal``,
    never inferred from these member names.

    ``UNREALIZED`` is OTHER COMPREHENSIVE INCOME: a price movement the owner
    has not sold into cash.  It is a class of its own rather than more Income
    because ``net_income = income - expense``, so a ``$40,000`` house
    revaluation booked as Income would read as ``$40,000`` earned.  The income
    statement reports it BELOW the net-income line and the balance sheet folds
    it into Equity as one derived accumulated line, exactly as Income and
    Expense are folded into Retained Earnings -- so the trial balance closes
    with it, not despite it.  Its accounts are per-account
    (``LedgerAccountKindEnum.UNREALIZED_CHANGE``) and are booked by an
    ``INVESTMENT`` / ``APPRECIATING`` account's balance-assertion true-up.
    """

    ASSET = "Asset"
    LIABILITY = "Liability"
    INCOME = "Income"
    EXPENSE = "Expense"
    EQUITY = "Equity"
    # ``String(12)`` on the ref column, which is why the member value is the
    # bare adjective rather than "Change in Value": the class NAME is an
    # identifier (matched to the enum by ``ref_cache.init``), and the reader's
    # section headings carry the prose.
    UNREALIZED = "Unrealized"


class PostingKindEnum(enum.Enum):
    """Posting-leg kind values for ``budget.account_postings``.

    ``transfer`` is a transfer's two balanced legs (Build-Order Step 2);
    ``income`` / ``expense`` are an ordinary settled transaction's cash and
    category legs (Build-Order Step 3); ``principal`` / ``interest`` /
    ``escrow`` / ``refund`` are the four legs of a confirmed loan payment's
    real-split correction (Build-Order Step 4) -- the loan principal
    adjustment, the accrued interest expense, the configured escrow expense,
    and the payoff-overpayment refund receivable.  ``opening`` / ``trueup``
    are the two-leg kinds the loan read switch adds (Build-Order Step 4,
    second half): ``opening`` books a loan's origination balance once as a
    balanced opening-equity entry so the ledger is authoritative for the
    confirmed balance, and ``trueup`` books an append-only dated correction
    that drives the ledger balance to a user-verified value without rewriting
    the prior payment postings.  Later Build-Order steps add further kinds
    via data migrations.  Values match ``ref.posting_kinds.name``.
    """

    TRANSFER = "transfer"
    INCOME = "income"
    EXPENSE = "expense"
    PRINCIPAL = "principal"
    INTEREST = "interest"
    ESCROW = "escrow"
    REFUND = "refund"
    OPENING = "opening"
    TRUEUP = "trueup"


class PostingSourceEnum(enum.Enum):
    """Journal-entry source-event values for ``budget.journal_entries``.

    ``loan_payment`` is the real-split correction appended to a confirmed
    loan-payment transfer (Build-Order Step 4).  ``loan_opening`` /
    ``loan_trueup`` are the source events the loan read switch adds
    (Build-Order Step 4, second half): ``loan_opening`` tags the once-per-loan
    opening-equity entry booked at origination, and ``loan_trueup`` tags an
    append-only loan-balance correction entry.  ``account_opening`` /
    ``account_trueup`` are their NON-loan siblings (Build-Order Step 5):
    ``account_opening`` tags the once-per-account entry booking a non-loan
    account's earliest anchor assertion, and ``account_trueup`` tags the
    balanced correction appended for each later anchor true-up -- together
    they make every non-loan linked ledger sum to an ABSOLUTE balance, closing
    the app-wide trial balance.  None of the five corrections links a row (the
    source FK is nullable); the source kind is what disambiguates them.
    ``purchase`` is a single PURCHASE recorded against an envelope whose bank
    posting day the owner has recorded (plan step X-f3b, ruling **R-FM**): it
    links ``transaction_entry_id``, and it books its own cash leg on its own
    day so its envelope's close books only the remainder.
    ``transfer_movement`` is ONE side of a settled transfer -- a side's
    covering movement, booked on its own bank day against the owner's
    Transfers-in-transit clearing account (plan step ``balance:X-bi-6-3``,
    rulings **R-BAL45** and **R-BAL101**): it links ``transaction_entry_id``
    exactly as ``purchase`` does.  Two older values are gone: ``transfer``,
    the one-entry-per-transfer shape (Build-Order Step 2), and
    ``transaction``, a settled row's own cash leg (Step 3).  After plan steps
    ``balance:X-bi-6-3`` and ``balance:X-bi-4a`` each was written only to
    reverse what it had posted, the deploy resync bringing every entry of
    both to zero, and plan step
    ``balance:X-bi-6-5``'s second leaf (card plan#1845) deleted the reversed
    pairs, both kinds and both links (ruling **plan#1867**).  Later steps add
    ``paycheck`` and ``credit_payback`` via data migrations.  Values match
    ``ref.posting_sources.name``.
    """

    PURCHASE = "purchase"
    TRANSFER_MOVEMENT = "transfer_movement"
    LOAN_PAYMENT = "loan_payment"
    LOAN_OPENING = "loan_opening"
    LOAN_TRUEUP = "loan_trueup"
    ACCOUNT_OPENING = "account_opening"
    ACCOUNT_TRUEUP = "account_trueup"


class LedgerAccountKindEnum(enum.Enum):
    """Row-kind discriminator for ``budget.ledger_accounts`` (Build-Order Step 4).

    The explicit, positive discriminator that replaces inferring a ledger
    account's kind from the NULL-pattern of its ``account_id`` /
    ``category_id`` / ``is_owner_bucket`` columns (see
    :class:`app.models.ledger_account.LedgerAccount`).  Every row carries a
    ``kind_id`` FK to one of these values; readers branch on the integer ID,
    never on which FKs happen to be NULL.

    The first four enumerate the kinds Steps 2-3 already create:

        linked    -- one per real ``budget.accounts`` row (Asset/Liability).
        category  -- one per budget category per Income/Expense class.
        fallback  -- the per-(owner, class) Uncategorized bucket, an
                     OWNER-BUCKET kind (``is_owner_bucket``; see ``transit``).
        orphan    -- a former category row whose category was deleted.

    The next three are the per-loan ledger accounts Step 4's loan-payment
    correction books into:

        loan_interest -- the loan's accrued-interest Expense account.
        loan_escrow   -- the loan's configured-escrow Expense account.
        loan_refund   -- the loan's payoff-overpayment refund Asset account.

    The loan read switch (Build-Order Step 4, second half) adds one more
    per-loan account:

        equity_opening -- the loan's opening-balance Equity account, the
                          credit counter-leg of the once-per-loan
                          opening-equity entry that books the origination
                          balance so the ledger is authoritative for the
                          loan's confirmed balance.

    Build-Order Step 5 adds the non-loan sibling:

        anchor_equity -- a non-loan account's opening/true-up Equity account
                         (``account_id`` set, like ``linked``), the counter-leg
                         of its ``account_opening`` / ``account_trueup``
                         corrections.  One per real account, coexisting with
                         the linked row under the ``(account_id, kind_id)``
                         partial unique that Step 5's chart migration re-keys
                         ``uq_ledger_accounts_account`` into.

    Ruling **R-FO** (plan step X-f3d) adds the two kinds that say what a
    TRUE-UP's difference WAS, dispatched over the account's projection kind:

        interest_income  -- an ``INTEREST`` account's per-account Interest
                            Income account (Income class).
        unrealized_change  -- an ``INVESTMENT`` or ``APPRECIATING`` account's
                            per-account Change in Value account
                            (``LedgerAccountClassEnum.UNREALIZED``).

    Both carry ``account_id`` and share the ``anchor_equity`` shape and its
    ``(account_id, kind_id)`` unique exactly, so no new index exists for them.
    An account's OPENING keeps booking to ``anchor_equity`` whatever its kind:
    an opening is capital brought onto the books, not something earned (a
    Property's ``$350,000.00`` opening is not a gain).

    Plan step ``balance:X-bi-6-3`` (rulings **R-BAL45**, **R-BAL99**) adds
    the second OWNER-BUCKET kind beside ``fallback``:

        transit -- the owner's Transfers-in-transit clearing account (Asset
                   class): the counter leg of every settled transfer's TWO
                   per-movement entries, so each side posts on its own bank
                   day and the account nets to zero once both have cleared.
                   No ``account_id`` / ``category_id`` / ``loan_account_id``;
                   held to one per owner by the owner-bucket key
                   ``(user_id, class_id, kind_id) WHERE is_owner_bucket``,
                   the same key that holds the ``fallback`` rows.

    Application code resolves these via ``ref_cache.ledger_account_kind_id``
    and compares against the integer ID -- never the string ``name`` --
    matching the project-wide ``ref-table: IDs for logic, strings for display
    only`` invariant.
    """

    LINKED = "linked"
    CATEGORY = "category"
    FALLBACK = "fallback"
    ORPHAN = "orphan"
    LOAN_INTEREST = "loan_interest"
    LOAN_ESCROW = "loan_escrow"
    LOAN_REFUND = "loan_refund"
    EQUITY_OPENING = "equity_opening"
    ANCHOR_EQUITY = "anchor_equity"
    INTEREST_INCOME = "interest_income"
    UNREALIZED_CHANGE = "unrealized_change"
    TRANSIT = "transit"
