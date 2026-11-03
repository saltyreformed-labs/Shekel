"""Where a figure comes from: an amount's owner, a statement's source, a day's basis, evidence.

Split out of the flat ``app/enums.py`` at plan step **credit_card:CC-6** (see
:mod:`app.enums` for why).  Every class below stands exactly as it stood there,
and so does the evidence ladder beside the last.
"""

import enum


class AmountSourceEnum(enum.Enum):
    """WHICH RELATION states a row's amount, when the row does not state it itself.

    Ruling **R-FI**'s discriminator (plan step **X-au-c1**): a row's amount is
    either its OWN -- a human authored the figure, or the money moved -- or it
    is DERIVED, and a derived amount is not stored at all.  A row that owns its
    amount carries ``amount_source_id IS NULL`` and a figure; a derived row
    names one of these and carries no figure.  The pairing is the CHECK
    ``ck_transactions_amount_ownership`` / ``ck_transfers_amount_ownership``, so
    a stale derived figure is unrepresentable rather than merely unlikely.

        template -- the recurring DEFINITION that generated this row states
                    its price: ``Transaction.template_id`` ->
                    ``budget.transaction_templates``, or
                    ``Transfer.transfer_template_id`` ->
                    ``budget.transfer_templates``.

    ``parent_transfer`` named a transfer SHADOW's parent, the shadow worth
    what its transfer was (amount rule 5); plan step ``balance:X-bi-6-4d-2``
    deleted the shadows, and ``balance:X-bi-6-5`` the member, its reference
    row and the rule (migration ``c956c1d83108``).

    **These name the RELATION that prices the row, not the RULE that computes
    the figure, and the difference is a developer ruling (2026-08-12) amending
    R-FI's own enumeration.**  R-FI listed five values naming the five
    producers -- own / salary / template / loan payment / transfer -- and two of
    those are refinements a DEFINITION carries, not facts about a row: a
    template is salary-linked when an active
    :class:`~app.models.salary_profile.SalaryProfile` names it
    (``template_amount_service.is_salary_linked_template``), and a transfer
    template is a loan payment when it holds a
    :class:`~app.models.loan_payment_settings.LoanPaymentSettings` row
    (``recurring_transfer_query.loan_payment_config``).  Storing the refinement on
    every generated row copies a definition-level fact onto each of its
    instances, and two LIVE routes then falsify the copy:

    * ``routes/loan/payment_transfer.py`` ``track_payment`` -- the loan
      dashboard's one-click "auto-track the contract" sets
      ``derive_from_loan = True``.  A legacy manual payment has no settings row,
      so its shadows would have been stamped with the plain-transfer rule; after
      the flip the definition's price series is dormant
      (``template_amount_service.owns_its_amount`` is False) and every stamped
      shadow becomes unpriceable.
    * ``routes/salary/profiles.py`` ``delete_profile`` -- archiving a profile is
      the moment a template stops being salary-linked and starts owning its own
      amount, which X-au-a already handles by stating a price through the write
      door in the same unit of work (58 production rows).  Rows stamped with the
      salary rule would keep naming a producer that no longer answers.

    Both are repairable only by a writer that rewrites every affected row -- a
    second maintainer of a derived value, which is the shape this arc exists to
    delete.  Naming the relation instead leaves the refinement where it is a
    fact: the row says *my definition prices me*, and the definition says how.
    ``credit_card:CC4b``'s card payment needs no new value here -- it prices
    through its transfer template's satellite, the same shape as a loan payment
    -- and neither does ``CC5a``'s rewards accrual.  **``CC4c``'s projected
    finance charge DOES**, and a first draft of this paragraph claimed the card
    arc needed none: that row carries no pricing link at all and its producer is
    reached from the row's ACCOUNT, which is finding **N-264**.

    Application code resolves these via ``ref_cache.amount_source_id`` and
    compares against the integer ID -- never the string ``name`` -- matching the
    project-wide ``ref-table: IDs for logic, strings for display only``
    invariant.  The OWN state is deliberately NOT a member: it is the ABSENCE of
    a source, which is what lets the ownership CHECK be written as
    ``(amount_source_id IS NULL) = (estimated_amount IS NOT NULL)`` -- a
    constraint over two NULL-nesses, with no ref-table id literal frozen into
    the schema.
    """

    TEMPLATE = "template"


class StatementSourceEnum(enum.Enum):
    """WHERE a recorded statement line came from -- ruling **R-FP**'s adapter.

    A statement importer is a SOURCE ADAPTER over one normalized line shape
    (plan step ``bank_import:X-f6a``), so matching, review and fact-writing stay
    source-independent: a new way for a statement to arrive is a new member here
    and a new parser, never a second path through the importer.

        secu_checking_csv -- State Employees' Credit Union's own transaction
                             export, as CSV.

    **A member names a FORMAT at an INSTITUTION, not an institution**, because
    one bank publishes one statement several ways and the ways do not carry the
    same facts.  Measured on the developer's own exports 2026-08-16: SECU's OFX
    -- and its QFX and QBO twins, which are the same statement content plus two
    Intuit routing tags -- carries a ``FITID`` and truncates the description to
    the OFX ``NAME`` limit, 326 of 361 lines landing at exactly 32 characters;
    the CSV carries the merchant, the bank's own category and a per-line running
    balance, and carries no ``FITID`` at all.  The CSV description STARTS WITH
    the OFX name on 306 of 306 shared lines, so the two really are one statement
    with one of them cut short.

    **Losing ``FITID`` costs nothing, and that is measured rather than
    assumed.**  A line's identity is
    ``(account, posted_on, amount, sequence within that group)``
    (:func:`app.services.statement_import.line_identity`), which every source
    can compute including one carrying no id of its own.  Compared across two
    SECU exports twelve days apart, that key reproduced the ``FITID`` key
    exactly: 0 keys present in only one export, 0 lines whose ``FITID``
    disagreed, over 342 shared lines.  So an external id is stored as a
    CORROBORATING fact (``bank_statement_lines.external_id``) rather than as the
    thing identity rests on, and the importer has ONE identity rule instead of
    one per format -- which is what lets ``X-f6b``'s automated source join
    without re-opening the question.

    Application code resolves these via ``ref_cache.statement_source_id`` and
    compares against the integer ID -- never the string ``name`` -- matching the
    project-wide ``ref-table: IDs for logic, strings for display only``
    invariant.
    """

    SECU_CHECKING_CSV = "secu_checking_csv"


class SettledDayBasisEnum(enum.Enum):
    """HOW a settled row's settle DAY is known (plan step **X-az**).

    :class:`MovementFigureSourceEnum`'s twin for the DAY: that one says who
    wrote the FIGURE a settled row's covering movement carries; this one says
    how the day itself is known.  (Its twin was ``SettlementBasisEnum`` on
    the row's own ``settled_basis_id`` until plan step ``balance:X-bi-4b-2``
    deleted that column, migration ``45f10b870c8b``.)  Three writers put three
    different kinds of fact into ``settled_on`` and nothing said which
    (finding **N-332**):

        observed  -- a bank statement showed the money posting on this day.  It
                     is a POINT: the bank named the day, and an observation
                     beats a belief.
        asserted  -- the owner asserted a BALANCE for this day and this money
                     was inside it, so the money moved on or before it.  It is
                     an UPPER BOUND and not a point; the true posting day may
                     be days earlier.
        entered   -- the owner's own word with no bank document behind it:
                     the owner typed the day, or a settle door stamped the day
                     the act happened on a plain row.  A POINT, on the owner's
                     word.
        borrowed  -- NO evidence of its own: one side of a TRANSFER whose day
                     is its other side's, or the day Paid was pressed when
                     neither side has one (plan step ``balance:X-bi-6-4c-3``,
                     rulings **R-BAL142** and **R-BAL143**).  A guess, which
                     follows its lender when the lender's day is corrected.

    **The partition is over EVIDENCE, which is what makes it exhaustive.**  A
    settle day is backed by a bank line, by a balance assertion, by the owner's
    own word, or by nothing of its own; there is no fifth kind, so every writer
    lands in exactly one member.  The first three ARE evidence and ``borrowed``
    is the one member that is not -- the question
    ``settle_day.is_evidence`` answers, and the reason a
    transfer's two sides can hold two different days.  What separates
    ``entered`` from ``observed`` is not confidence but provenance -- both are
    points, and a reader that wants to rank them can, because the column now
    says which is which.  Only a transfer side is ever ``borrowed``: a plain
    row has no other side to borrow from.

    **Its whole reason for existing is that the difference decides a WINDOW.**
    ``statement_match._subjects.CandidateRow.expected_window`` bounds a purchase
    by ``(purchased_on, settled_on)`` when the day is a bound and pins it to a
    point when it is not, and it used to tell the two apart by testing whether
    ``reconciled_by_id`` was populated.  That inference was exact over the three
    writers of the day it happened to meet and blind to the third of them: a day
    the owner typed read as a day the bank had shown.  Reading the bound as an
    observation had already cost **50 duplicate purchases worth `$3,590.00`** on
    the developer's dev database before ``f633d46a``, and inferring a fact from
    another column being populated is the shape finding **N-241** deleted one
    column over -- the row's ``settled_basis_id`` existed (X-au-c3 to
    X-bi-4b-2) precisely so that *"which one a figure is stands in
    ``settled_basis_id`` rather than being inferred from a column being
    populated"*, a question the movement's ``figure_source_id`` answers now.

    **It does NOT replace ``reconciled_by_id``, and the two are not the same
    question.**  That column names WHICH statement was seen to show this money;
    this one says what kind of day the row records.  A row whose asserted day
    the bank later CONFIRMS keeps its link and becomes ``observed`` -- one fact
    changed, the other did not -- which the coupled reading could not express at
    all.

    Application code resolves these via ``ref_cache.settled_day_basis_id`` and
    compares against the integer ID -- never the string ``name`` -- matching the
    project-wide ``ref-table: IDs for logic, strings for display only``
    invariant.  There is deliberately no member meaning *not settled*: a row
    that carries no day carries no basis either, and the pairing CHECK is a
    BICONDITIONAL over the two NULL-nesses, so no ref id is frozen into the
    schema (the reason :class:`AmountSourceEnum` has no ``own`` member).
    """

    OBSERVED = "observed"
    ASSERTED = "asserted"
    ENTERED = "entered"
    BORROWED = "borrowed"


class MovementFigureSourceEnum(enum.Enum):
    """WHO WROTE a movement's FIGURE (plan step **X-bi-3a**, ruling **R-BAL39**).

    A movement is a ``budget.transaction_entries`` row: a purchase recorded
    against an envelope, or -- since X-bi-3a -- the COVERING MOVEMENT a settle
    writes for a bill (and since X-bi-3b for a paycheck), the payment row that
    records the parent's money the way a purchase already records an
    envelope's.  Three writers put a figure on one,
    and this catalogue says which, partitioned by the SOURCE of the figure --
    the argument :class:`SettledDayBasisEnum` makes for the DAY one column
    over, applied to the amount beside it:

        resolved  -- the settle priced it from the PLAN at the moment of the
                     settle (the definition's price series, the salary
                     profile, the loan's schedule).  Not repeatable, which is
                     why it is RECORDED: a re-settle re-prices it, because the
                     app's own inference about a moment that has passed is
                     worth less than a fresh one.
        typed     -- a person stated it: a hand-typed purchase, or a figure
                     read off a statement and typed over the plan's.  A fact
                     somebody stands behind; a re-settle HONOURS it, exactly as
                     ``status_seam.Settlement.from_settle`` honours a retained
                     ``corrected`` record.
        observed  -- the bank's own line stated it: a purchase born from a
                     statement line, or a figure the statement matcher took
                     from a line onto a matched row.  A match that confirms a
                     DAY alone writes no figure and so changes nothing about
                     who wrote it: the label follows the WRITER of the figure,
                     never the evidence beside it (ruling **R-BAL61**, which
                     measured the older "a confirmation raises a typed figure
                     to observed" rule false on every row kind -- every
                     bank-observed day ever written onto a settled row was a
                     day-only confirmation).

    **A reader needs it, which is why it is a column and not a label.**
    ``Settlement.from_settle`` re-prices a ``resolved`` record and honours a
    stated one (``Settlement.stated``); a bill's figure lives on its covering
    movement and nowhere else since plan step ``balance:X-bi-4b-2`` deleted
    ``transactions.settled_basis_id`` (migration ``45f10b870c8b``), so that
    distinction has no other home.  The catalogue was NEW rather than a
    reuse of that column's ``SettlementBasisEnum`` (``derived`` /
    ``corrected`` / ``purchases``, retired with it) because its ``purchases``
    member meant nothing on a movement (a movement IS a purchase) and its
    ``corrected`` member was defined as *a human typed it*, which a bank-born
    purchase would have to claim falsely.

    **It records who last WROTE the figure, and a withdrawal does not
    launder it**: releasing a match leaves a purchase's ``observed`` day in
    place (finding **N-333**, *the days an accepted match wrote are the APP's
    own record*), and the figure's source stays with it.  A day-only edit
    leaves the source alone; the source changes only when someone WRITES the
    figure -- the human PATCH makes it ``typed``, a bank line ``observed``, a
    settle ``resolved``, ``typed`` or (through the matcher's transaction arm)
    ``observed``.  Every writer STATES it with the figure, as one value
    (:class:`app.services.stated_figure.StatedFigure`; plan step
    **X-bi-3e-1**, ruling **R-BAL69**); nothing infers it from the day.

    Application code resolves these via ``ref_cache.movement_figure_source_id``
    and compares against the integer ID -- never the string ``name`` -- matching
    the project-wide ``ref-table: IDs for logic, strings for display only``
    invariant.  NOT NULL and no default: both writers of a movement state it,
    and a stored guess would be the shape ruling **R-IY** deletes.
    """

    RESOLVED = "resolved"
    TYPED = "typed"
    OBSERVED = "observed"


class StatementBalanceEvidenceEnum(enum.Enum):
    """How strongly an imported statement's balance is EVIDENCED (**X-f6e-1**).

    A statement states a balance (``Balance as of 08/22/2026,2459.600000``) and
    a list of lines.  What the app needs from it is an ANCHOR -- the account
    held this much at the end of this day -- and what this says is how much
    that anchor can be trusted.  Ruling **R-GF**.

        file_chain     -- the file states a balance beside EVERY line, so it
                          proves itself and needs nothing outside.  The only
                          level that rests on this file alone.
        corroborated   -- the figure agrees with a balance the app already
                          holds which is ITSELF evidenced, so two independent
                          statements say the same thing.
        uncorroborated -- nothing confirms it.  The figure is taken at face
                          value, which is what a FIRST import is and what any
                          anchor rooted in one remains.

    **It is the WEAKEST LINK in the chain behind the figure, and that is the
    whole design rather than a caution.**  An anchor's day is often SOLVED
    rather than stated -- ``stated - sum(lines up to d) == opening`` picks the
    day out of the file's own lines -- and it is tempting to record that the
    day was worked out and call the result corroborated.  It is not: a solved
    day is only as good as the opening it was solved against, so an anchor
    solved against an uncorroborated opening is uncorroborated too.  Recording
    the minimum makes that true BY CONSTRUCTION.

    **What it removes is a defect an adversarial review reproduced in two
    clicks** (2026-08-23): re-uploading the identical file made the app walk
    back from its own assumption, find that the file agreed with it, and record
    the result as corroborated -- the assumption checking itself, with the
    receipt turning from a warning to a green tick.  Under a weakest link no
    re-upload can strengthen anything, because the chain still contains the
    assumption.

    **The partition is over EVIDENCE, which is what makes it exhaustive.**  A
    figure is evidenced by its own file, by other files, or by nothing; there
    is no fourth kind, so every level lands in exactly one member -- and since
    plan step ``balance:X-bj-1`` that includes the OWNER's own true-ups, which
    sit in the same relation (``budget.account_anchor_history.evidence_id``)
    at ``uncorroborated``, because a typed figure is confirmed by nothing in
    the app.  A fourth member naming the owner was ruled out 2026-09-16: it
    would be a SOURCE, and the source is the level's ``statement_import_id``.

    **There is deliberately no member meaning "this file states no balance"**,
    for the reason :class:`SettledDayBasisEnum` has none meaning *not settled*:
    that is the ABSENCE of a member rather than one of them.  An import whose
    figure was placed on no day owns no level row, so it carries no evidence
    at all rather than a member saying so, and no ref id is frozen into the
    schema.

    Application code resolves these via ``ref_cache.statement_balance_evidence_id``
    and ``ref_cache.statement_balance_evidence_member`` and compares against the
    integer ID -- never the string ``name`` -- matching the project-wide
    ``ref-table: IDs for logic, strings for display only`` invariant.
    """

    FILE_CHAIN = "file_chain"
    CORROBORATED = "corroborated"
    UNCORROBORATED = "uncorroborated"

    @property
    def strength(self) -> int:
        """Return where this member sits on the evidence ladder, 0 the weakest.

        **Declared once, here, because the ORDER is the meaning.**  A caller
        that compared members by writing its own mapping would be a second
        statement of the ladder, and the two would drift; the weakest-link rule
        (:func:`app.services.statement_import.weaker_of`) is the only consumer
        and it reads this.
        """
        return _EVIDENCE_STRENGTH[self]


#: The evidence ladder, weakest first.  A module-level map rather than a body
#: inside :attr:`StatementBalanceEvidenceEnum.strength` so the ORDER is
#: readable as a list at a glance, which is what a ladder is.
_EVIDENCE_STRENGTH = {
    StatementBalanceEvidenceEnum.UNCORROBORATED: 0,
    StatementBalanceEvidenceEnum.CORROBORATED: 1,
    StatementBalanceEvidenceEnum.FILE_CHAIN: 2,
}
