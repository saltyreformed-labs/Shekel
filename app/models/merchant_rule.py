"""
Shekel Budget App -- Merchant Rule Model (budget schema)

WHERE the owner has said a merchant's spending goes.  One table, one subject
(plan step ``bank_import:X-f6a-3d``).

**It is a DECISION, and that is why it is stored rather than derived.**  The app
already records, permanently, which bank line became which purchase in which
budget line -- so *where has Amazon gone before?* is answerable with a join and
no table at all.  That was considered and rejected on 2026-08-19 (developer),
for three reasons and the third is the one that settles it:

  * where a merchant's money went last April is EVIDENCE; where it should go
    from now on is a decision only the owner can make, and the two are
    different facts.  A stored decision duplicates nothing -- it is derivable
    from no other row in the database -- so none of the normalization
    arguments against a stored derivation apply to it;
  * history changes underneath a derivation.  Delete an old purchase or
    release an old match and the app's forward-looking answer moves, without
    anyone deciding it should;
  * **history cannot express "never".**  A line the owner deliberately left
    alone leaves no trace at all, so a derivation is silent about exactly the
    merchant that matters most.  Measured on the developer's own statement:
    Capital One Credit Card is 9 of the 91 unexplained outflows and
    **`-$7,412.94` of the `-$11,336.36` in that list**, and every one of them
    must NEVER become a purchase -- the app already holds that money as CC
    Payback rows, so recording it would count it twice.  Without this table the
    screen re-asks those 9 questions on every pass and there is nowhere to
    write down the answer.

**The same shape ruling R-FP already gives the account identity**: recorded by
the user's own choice and then CHECKED, never inferred.  Nothing here writes
money and nothing here can: a rule is read to SUGGEST, and the only thing
that records a purchase is an explicit destination on one specific line
(developer ruling, 2026-08-19; see :class:`MerchantRule`).

**A rule is RESTATED freely and is never UN-stated** (ruling **R-GS**, plan step
``bank_import:X-gd-2``).  It is a statement about today's shape of the budget,
not a judgement: when the credit card arc gives Capital One its own account,
the Checking-side line stops being "not a purchase" and becomes a payment to
MATCH against the card's payback row, and the card account gets rules of its
own -- which is why the key carries the account.  What used to be a WITHDRAWAL
-- delete the row, return the merchant to *you have not said* -- is now the
fourth answer, *ask me every time*: the owner who wants no standing answer for
a merchant has DECIDED that, and a decision is a row.  There is no delete door.

**A cascade may still remove a rule, and that is not a contradiction.**  All
three ANSWER keys below are ``ON DELETE CASCADE``, so hard-deleting the
template, the envelope's category or the income category an answer names takes
the answer with it -- because an answer
naming a row that no longer exists is not an answer, which is the same reason
``RESTRICT`` was refused (finding **N-302**).  R-GS governs what the OWNER may
do at the control; referential integrity is a different subject.  What the
cascade must not do is happen SILENTLY behind a door that says the subject is
unused, which is why ``archive_helpers.category_has_usage`` counts this table.
"""

from app.extensions import db
from app.models.mixins import (
    AccountScopedMixin,
    TimestampMixin,
    UserScopedMixin,
)


class MerchantRule(AccountScopedMixin, UserScopedMixin, TimestampMixin,
                   db.Model):
    """Where this owner has said one merchant's money goes on one account.

    **FIVE ANSWERS, and they are the complete set rather than a menu** (rulings
    **R-GI** and **R-HT(a)**).  A budget line either has a period-independent
    identity or it does not: a recurring one is generated from a
    ``budget.transaction_templates`` row and that template IS its identity
    across every period, while an ad-hoc one exists in a single period and
    nowhere else.  So *where does this merchant go* can be answered in exactly
    five ways:

    1. **a TEMPLATE** -- file into whatever row that template generated in the
       line's OWN pay period;
    2. **a NEW ENVELOPE** -- create one for the line's period, with this name
       and this category;
    3. **never a purchase** -- no container, :attr:`never_a_purchase` true;
    4. **ask me every time** -- no container, :attr:`never_a_purchase` false;
    5. **an INCOME CATEGORY** -- a DEPOSIT from this signature is income under
       that category (plan step ``bank_import:X-gj-2a``).

    **The fifth answers a different DIRECTION, and that is why it fits on this
    row rather than on a table of its own.**  Every other answer here says
    where money LEAVING goes; this one says what money ARRIVING is.  A merchant
    could in principle want both -- and on the developer's own 378 recorded
    lines none does, because ruling **R-HT(a)** makes a merchant CREDIT the
    inverse of that merchant's spending answer rather than a second stored
    fact: an Amazon refund files back into the Amazon envelope, so a merchant
    with answer (1) or (2) already has its inflow answer and cannot need (5).
    What (5) is for is a signature that only ever deposits -- ``Dividend
    Earned``, ``IRS Tax Refund``, ``Member Deposit`` -- which is 4 of the 6
    deposit signatures on that account.  One answer per merchant therefore
    stays TRUE rather than merely convenient, and the exclusion is the CHECK's
    rather than a reader's.

    A FIFTH state is the absence of a row: the owner has not said.  It is
    distinct from (4) -- *I have not decided* against *I have decided not to
    have a standing answer* -- and the difference is what a rule-covered import
    reads: (4) stops the screen asking the owner to state a rule, and no row at
    all does not.

    **The five are told apart by the COLUMNS plus one boolean** (ruling
    **R-GS**).  A stored discriminator was refused on two grounds.  It would be
    a second statement of what the columns already say, which is
    ``statement_match._rules.RuleAnswer.of``'s own argument; and a CHECK cannot
    reference a ``ref`` row's id, so a ref-keyed answer could sit on a row
    whose columns contradicted it -- readable only under the sole-writer trust
    contract ``ledger_accounts.kind_id`` accepts and documents.  The boolean is
    also the shape that SURVIVES plan step ``bank_import:X-f6c`` collapsing (2)
    into a template id, where a ref table would need a data migration.

    **The boolean asserts the CONSEQUENTIAL answer, not the harmless one**, and
    that direction is the point: a row whose optional columns are all empty and
    whose flag is false is *ask me every time*, which files nothing and bars
    nothing.  Pointing it the other way would have made the same row mean
    *never a purchase* -- the answer that stops a bank line ever becoming a
    purchase -- so any future path that failed to state the flag would fail
    into a money decision the owner never made.  The column carries NO default
    for the same reason: a writer that omits it gets a NOT NULL violation
    rather than an answer chosen by the schema.

    **Why not the envelope, and why not its NAME.**  Measured on the
    developer's own data 2026-08-19: the 24 unexplained Amazon lines fall in
    **10 different pay periods**, so there is no single ``transactions`` row to
    remember -- an envelope belongs to one period.  And the name is not stable
    either: template 22 generated a row called ``Kayla`` in one period and
    ``Kayla's Spending Money`` in the other 60, so a rule matching on the name
    would silently miss that period where one naming the template does not.
    IDs for logic, strings for display, one more time.

    Columns:
        account_id -- the account whose statements this governs
            (:class:`~app.models.mixins.AccountScopedMixin`).  **Per account
            and not per owner**, because a destination is: a Checking rule
            naming a Checking template is meaningless on a card statement, and
            the same merchant legitimately goes somewhere else there.
        user_id -- its owner (:class:`~app.models.mixins.UserScopedMixin`),
            held equal to the account's by ``fk_merchant_rules_owner``
            so it is a co-located key rather than a copy.  The same
            construction ``fk_account_external_identities_owner`` uses.
        merchant_id -- the :class:`~app.models.merchant.Merchant` this answer
            is about, held to this row's own account by
            ``fk_merchant_rules_merchant_account``.  It held the bank's
            STRING until plan step ``bank_import:X-gd-1``, matched to
            ``bank_statement_lines.merchant`` by equality between two
            independently-declared 100-character columns; the merchant is a row
            now, so the two tables agree by id and *which merchants may be
            asked about* is that table rather than a union of two derivations.
        template_id -- answer (1), else NULL.
        envelope_name / category_id -- answer (2), else both NULL.
        income_category_id -- answer (5), else NULL.  **What a DEPOSIT from
            this merchant is**, not where its spending goes, which is why it
            is a column of its own beside ``category_id`` rather than a second
            reading of it.  Categories carry no income/expense class of their
            own -- ``ledger_lines.purchase_target`` takes the class from the
            parent's transaction TYPE -- so nothing here can refuse a category the owner
            usually spends under, and nothing should: what makes the row income
            is that the bank line is money arriving.
        never_a_purchase -- answer (3) when true.  It is the DISCRIMINATOR for
            the two answers that name no container, so on answers (1) and (2)
            it is pinned false by ``ck_merchant_rules_one_answer`` rather than
            merely expected to be.  **A bar and not a suggestion**: a merchant
            answered this way has no create-a-purchase arm anywhere -- not on
            the screen, not in the sweep and not at the door
            (:class:`~app.services.statement_match._bars.CreationBar`, ruling
            **R-GJ**) -- which is why the answers that BAR and the answers that
            SUGGEST are told apart structurally and not by a caption.
        created_at / updated_at -- when it was first stated and last restated
            (:class:`~app.models.mixins.TimestampMixin`).  A rule is EDITED
            in place rather than superseded by a new row, because it answers
            one question and the answer is whatever the owner last said; the
            history of what they said before is
            ``system.audit_log``'s, which this table has a trigger for.

    **All three ANSWER keys CASCADE, and the consequence is deliberate.**
    Deleting a template or either category takes the rule with it, leaving the
    merchant unanswered -- which is the truth, because an answer naming a row that no
    longer exists is not an answer.  ``RESTRICT`` would refuse an ordinary
    delete because of a PREFERENCE the user cannot see from the thing they are
    deleting, which is the dead end finding **N-302** records one arc over.
    **The MERCHANT key does NOT cascade** -- it declares no ``ON DELETE``, so
    deleting a merchant a rule answers for is REFUSED rather than silently
    taking the answer with it.  The difference is which of the three is the
    rule's SUBJECT and which are its ANSWERS.

    **What the owner may no longer do is un-state a rule** (ruling **R-GS**),
    so a cascade is the only remaining way a row can leave -- which is why both
    permanent-delete doors ask about this table before they run
    (``archive_helpers.category_has_usage`` and
    ``archive_helpers.template_has_standing_rule``).
    """

    __tablename__ = "merchant_rules"
    __table_args__ = (
        # ONE answer per merchant per account.  The key is what makes
        # "restating a rule" an UPDATE rather than a second row that would
        # leave two answers to one question.  **``user_id`` is not a term of
        # it**: ``fk_merchant_rules_owner`` holds it equal to the
        # account's, so it was functionally dependent on ``account_id`` and
        # narrowed nothing.
        db.UniqueConstraint(
            "account_id", "merchant_id",
            name="uq_merchant_rules_account_merchant",
        ),
        # THE FIVE SHAPES, spelled as four shapes because the last two share
        # their columns and differ only in the flag.  A count-the-NULLs form
        # (``ck_statement_match_members_one_subject``'s) cannot say this:
        # answer (2) sets TWO columns and answers (3) and (4) set none, so what
        # has to be constrained is which COMBINATIONS are legal rather than how
        # many columns are filled.
        #
        # **Every pre-existing arm gained an ``income_category_id IS NULL``
        # term, and leaving them alone would have been the defect** (plan step
        # ``bank_import:X-gj-2a``): a CHECK that names only the NEW column's own
        # arm still admits a row carrying a template AND an income category,
        # which is two answers to one question -- and ``RuleAnswer.of`` reads
        # the container first, so such a row would file SPENDING under a rule
        # the owner stated about deposits.
        #
        # **The flag is pinned FALSE on both container answers**, which is what
        # keeps ``RuleAnswer.of``'s reading total: without it a row could name a
        # template AND claim never-a-purchase, and the two readers that ask the
        # question in different orders -- ``of`` reads the container first,
        # ``CreationBars`` reads the flag -- would answer differently about the
        # same row.  One is a suggestion and the other bars a purchase, so that
        # disagreement is a money disagreement.
        db.CheckConstraint(
            "(template_id IS NOT NULL AND envelope_name IS NULL "
            "AND category_id IS NULL AND income_category_id IS NULL "
            "AND NOT never_a_purchase) "
            "OR (template_id IS NULL AND envelope_name IS NOT NULL "
            "AND category_id IS NOT NULL AND income_category_id IS NULL "
            "AND NOT never_a_purchase) "
            "OR (template_id IS NULL AND envelope_name IS NULL "
            "AND category_id IS NULL AND income_category_id IS NOT NULL "
            "AND NOT never_a_purchase) "
            "OR (template_id IS NULL AND envelope_name IS NULL "
            "AND category_id IS NULL AND income_category_id IS NULL)",
            name="ck_merchant_rules_one_answer",
        ),
        # The merchant this answer is about is one of THIS ACCOUNT's,
        # structurally (plan step ``bank_import:X-gd-1``).  It is the same
        # composite construction the two subject keys below use, and it is what
        # retires the scope CHECK: ``_stating._refuse_unknown_merchants``
        # compared a submitted string against a DISTINCT over every recorded
        # line, and was the only thing between a crafted body and a stored row
        # keyed on a merchant this account has never seen.  That refusal
        # survives as a SENTENCE for a stale page -- the shape
        # ``_rules._checked_template`` already has -- and no longer as what
        # makes the row correct.  The blank-name rule it also carried now lives
        # once, on ``ck_merchants_name_not_blank``.
        db.ForeignKeyConstraint(
            ["merchant_id", "account_id"],
            ["budget.merchants.id", "budget.merchants.account_id"],
            name="fk_merchant_rules_merchant_account",
        ),
        # An envelope NAME is a name too, for the same reason
        # ``transactions.name`` is NOT NULL: answer (2) creates a budget line
        # with it.
        db.CheckConstraint(
            "envelope_name IS NULL OR btrim(envelope_name) <> ''",
            name="ck_merchant_rules_envelope_name_not_blank",
        ),
        # This row's owner IS its account's, guaranteed rather than maintained
        # -- keyed onto ``uq_accounts_id_user``, the construction
        # ``fk_account_external_identities_owner`` uses one table over.
        db.ForeignKeyConstraint(
            ["account_id", "user_id"],
            ["budget.accounts.id", "budget.accounts.user_id"],
            name="fk_merchant_rules_owner",
            ondelete="CASCADE",
        ),
        # ...and the TEMPLATE it names is on that same account, structurally.
        # A statement is one bank's record of ONE account, so a rule pointing
        # at another account's recurring envelope is not a destination at all.
        # Composite rather than a bare ``template_id`` FK for the reason
        # ``fk_statement_match_members_entry_account`` is composite:
        # otherwise "is this template on this account" is a reader's check that
        # can be forgotten, and the row it protects is one a crafted request
        # reaches.  ``MATCH SIMPLE`` (PostgreSQL's default) is what lets it sit
        # beside the nullable arms -- a row whose ``template_id`` is NULL
        # satisfies it whatever ``account_id`` says.
        db.ForeignKeyConstraint(
            ["template_id", "account_id"],
            ["budget.transaction_templates.id",
             "budget.transaction_templates.account_id"],
            name="fk_merchant_rules_template_account",
            ondelete="CASCADE",
        ),
        # ...and the CATEGORY it names is this owner's.  Categories carry only
        # a ``user_id``, so that is the whole of their ownership fact -- and a
        # foreign ``category_id`` satisfies a bare FK perfectly well, which is
        # the IDOR every create door in this project probes for by hand.  Here
        # it is unwritable instead.
        db.ForeignKeyConstraint(
            ["category_id", "user_id"],
            ["budget.categories.id", "budget.categories.user_id"],
            name="fk_merchant_rules_category_owner",
            ondelete="CASCADE",
        ),
        # ...and the INCOME category answer (5) names is this owner's too,
        # by the same construction and for the same reason: a bare
        # ``income_category_id`` FK is satisfied perfectly well by another
        # owner's category, which is the IDOR every create door in this
        # project probes for by hand and this one cannot be handed.
        db.ForeignKeyConstraint(
            ["income_category_id", "user_id"],
            ["budget.categories.id", "budget.categories.user_id"],
            name="fk_merchant_rules_income_category_owner",
            ondelete="CASCADE",
        ),
        # The review screen reads a whole account's rules at once.
        db.Index("idx_merchant_rules_account", "account_id"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    # No direct single-column key: the merchant is reached through a composite
    # that also holds the account equal, exactly as the two answer arms below
    # are.
    merchant_id = db.Column(db.Integer, nullable=False)
    # No direct single-column keys on either arm: both are reached through a
    # composite key that also holds the owner or the account equal.  Same
    # shape, same reason, as ``statement_match_members``' two subject keys.
    template_id = db.Column(db.Integer)
    envelope_name = db.Column(db.String(200))
    category_id = db.Column(db.Integer)
    # Answer (5), reached through a composite key that holds the owner equal,
    # exactly as ``category_id`` above is.  **A SEPARATE column rather than a
    # second meaning for ``category_id``**, which would have needed no
    # migration at all: sharing it would make answer (2) and answer (5)
    # differ only by whether ``envelope_name`` is set, so a writer that
    # omitted the NAME would store a valid INCOME rule instead of raising --
    # which is the exact failure mode ``never_a_purchase``'s own note refuses
    # one column down, and it fails into a money decision the owner never
    # made.
    income_category_id = db.Column(db.Integer)
    # **No default, server-side or Python-side, and that is deliberate.**  It
    # is the one column here whose absence would be silently answerable: the
    # three above are the answer's own subject and a row missing all of them is
    # a legal answer, so nothing raises on their behalf.  With no default a
    # writer that does not state this one gets a NOT NULL violation at flush,
    # which is loud, rather than *ask me every time* -- or, had the flag been
    # named the other way round, *never a purchase*.
    never_a_purchase = db.Column(db.Boolean, nullable=False)

    # **No relationships, deliberately, and the reason is the GRAIN rather
    # than the key.**  Both controls that render these rows render EVERY rule
    # on the account at once and need only a NAME for each subject, so
    # ``statement_match._rules`` reads the templates and the categories in one
    # statement each and indexes them -- where a relationship would be loaded
    # per rule unless every caller remembered an eager option.
    #
    # **The composite key is NOT the reason**, and this comment said it was
    # until plan step ``bank_import:X-gf-2``: a composite-key relationship is
    # perfectly expressible and its sibling
    # :class:`~app.models.statement_match.StatementMatchMember` carries two
    # of them, joined on ``(subject_id, account_id)`` and loaded whole
    # with the act.  What that step ALSO did was delete ``_reads._by_id``,
    # which this comment cited as the shape it followed -- so the argument was
    # refuted and its citation resolved to nothing in the same commit.  A
    # citation that resolves to nothing is how a second spelling survives
    # (:func:`~app.services.statement_match._accepted_view._still_holds`).

    def __repr__(self):
        answer = (
            f"template={self.template_id}"
            if self.template_id is not None
            else f"new={self.envelope_name!r}"
            if self.envelope_name is not None
            else "never" if self.never_a_purchase
            else "ask"
        )
        return (
            f"<MerchantRule account={self.account_id} "
            f"merchant={self.merchant_id} -> {answer}>"
        )
