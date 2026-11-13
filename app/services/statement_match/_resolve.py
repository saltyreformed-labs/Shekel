"""What a SUBMISSION names, resolved under the pass's own scope.

:mod:`._accept` writes a match; this decides what a match may be written
ABOUT.  The seam is that module's own, stated there in prose since plan step
``bank_import:X-f6a-3c-2`` -- *resolving and recording are two acts* -- and
made structural here at ``bank_import:X-f6d-3``, when the reconciliation
finding **N-336** asks for took the file past its line cap.  Nothing moved
across it: :func:`resolve_rows` called nothing in the write half and the write
half calls nothing here, so the split is the call graph's own shape.

**SEVEN refusals live here and they share one subject**: whether what a body
sent is what this pass could have offered.  Three are about the LINES -- a
line this account does not hold, one another match has claimed, and one the
owner has already SKIPPED (plan step ``bank_import:X-gj-4a``) -- and four
about the ROWS: a row this pass could not offer or can no longer price, one
subject named twice, a row that has MOVED since the screen described it, and
an ATTRIBUTION naming a row the submission does not carry (plan step
``bank_import:X-gj-3a``).
The refusals in :mod:`._accept` are about the submission's SHAPE instead -- an
empty side -- and about what the act's own writes did to a member's price, and
the ones in :mod:`._variance` are about the two sides DISAGREEING, which since
plan step ``bank_import:X-f6d-4`` includes the figure that is not the row's to
state.  *(:mod:`._accept` also refused a parent matched beside its own child
until plan step ``credit_card:CC-5-4a-5``'s leaf 5c-2c-1 deleted that refusal
as unreachable -- finding **CC-386**, ruling **R-CC144** -- because a row
holding a purchase is worth ``$0.00`` to the offer (ruling **R-BAL81**), so
this module refuses it as a row this pass could not offer or can no longer
price.)*

*(This module's count is stated because this arc has shipped a taxonomy that
did not add up before; if an eighth refusal is added here, this sentence is
what has to change with it.  It read SIX until plan step
``bank_import:X-gj-4a`` added the skip, which is the count moving with the
predicate rather than a reader being left to re-count.  No count is claimed
for the other module, which owns its own.)*

**The security property is the SCOPE** and it did not change: an id is looked
up in the pass's own offer set (:class:`~._scope.ReviewScope`), never queried
directly, so a row belonging to another user, another account, a
non-contributing row, a card purchase or a row another match has claimed is
not a candidate and cannot be reached by crafting a request.

**The row lock every DOOR of this package takes on a bank line lives here
too**, since plan step ``bank_import:X-gi-5``: its MODE is stated once in
:func:`locked_for_write` (ruling **bank_import:R-BI5**), its ORDER once in
the query :func:`load_lines` and :func:`lock_lines` share, so a door's own
locked read and the pass's up-front one cannot come to take the same rows in
two orders (finding **N-471**), and since plan step ``bank_import:X-gv`` its
REFRESH beside the mode, so a row a door reads under the lock is the row the
lock holds rather than the one the pass hydrated before it (finding
**BI-493**).  The writers OUTSIDE the package -- a re-import's UPDATE of a
recorded line, an import delete's cascade -- are named on :func:`_lines_on`
and :func:`lock_lines`, one composed with and one not.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
frozen dataclasses out, no Flask import.  It READS and never writes; a lock is
a read's claim on the row, not a write.
"""

from __future__ import annotations

from app.exceptions import ValidationError
from app.extensions import db
from app.models.statement_import import BankStatementLine

from ._candidates import MatchedSubjects, unmatched_rows
from ._subjects import CandidateRow, RowKind
from ._scope import ReviewScope
from ._submission import MatchSubmission, ReviewedRow
from ._undisposed import skipped_among
from ._valuation import repriced


def locked_for_write(query):
    """Return *query* reading its bank lines under the lock a WRITER holds.

    **``FOR NO KEY UPDATE``, and the mode is stated here ONCE** (ruling
    **bank_import:R-BI5**, plan step ``bank_import:X-gi-5``).  Both halves of
    ruling **R-HP**'s *exactly one verb* are app-tier reads across two tables
    -- :func:`load_lines` asks whether a SKIP answers the line, and
    :func:`~._skipping.skip_line` asks whether a MATCH does -- and write
    transactions run at ``READ COMMITTED`` (:mod:`app.db_transaction`), so
    two tabs otherwise interleave into a line carrying BOTH answers with
    nothing raising.  No key can catch it, because the pair spans two tables.
    What both writers share is the bank line row itself, which both of their
    foreign keys reference, so each locks it BEFORE reading the other table
    and the two serialise on the one row they have in common.

    **Why this mode and not its neighbours.**  Not ``FOR KEY SHARE``: that is
    what an ordinary foreign-key insert already takes implicitly, and two of
    those are compatible with each other, so it would serialise nothing.  Not
    ``FOR UPDATE``: it also blocks the ``FOR KEY SHARE`` a foreign-key check
    takes, so it would hold up an unrelated writer referencing the same line
    for no benefit, and it is the strength
    :mod:`app.services.credit_workflow` argues would deadlock against exactly
    those checks.  ``FOR NO KEY UPDATE`` conflicts with itself, which is the
    whole of what the exclusivity needs.  Named by adversarial security
    review 2026-09-02.

    **The flag is ``key_share=True``, and stating it is the point of this
    function.**  SQLAlchemy renders ``with_for_update(key_share=True)`` as
    ``FOR NO KEY UPDATE`` and ``key_share=False`` -- the default, and what
    BOTH sites passed from plan step ``bank_import:X-gj-4a`` until this one --
    as the stronger ``FOR UPDATE``, so the two docstrings that argued the
    paragraph above each sat over a statement doing the other thing, and no
    test read the emitted text.  The compiled statement is what
    ``tests/test_services/test_statement_match/test_lock_order.py`` grades.
    One helper rather than a flag at each site is what stops the inversion
    coming back at the next site: :func:`load_lines`, :func:`lock_lines` and
    :func:`~._skipping._line_on` all take the lock through here, and nothing
    in this package spells ``with_for_update`` on a bank line itself.

    **It REFRESHES the instance it hands back, and that is the other half of
    what the lock is for** (finding **BI-493**, plan step ``bank_import:X-gv``).
    A press carrying creations runs :func:`~._reads.review_set` before
    :func:`~._batch.apply_reviewed`, in the one ``READ COMMITTED``
    transaction the request is, and that derivation reads every undisposed
    line into the session's identity map, unlocked.  The locked ``SELECT`` a
    door then runs fetches the row the lock holds -- and without
    ``populate_existing()`` the ORM handed back the instance it already held,
    unrefreshed, because it populates only the attributes an existing
    instance has NOT loaded.  The one concurrent writer of a recorded line
    is a re-import, which since plan step ``bank_import:X-f6b-1b`` writes
    ONE thing: a new SIGHTING row carrying what the later export stated --
    the merchant its word names and its transaction day among them (ruling
    **R-BI16**; through X-f6b-1 the merchant KEY was also filled on the line
    itself).  Both facts reach a door's decision: ``merchant_id`` -- the
    earliest naming sighting's -- is the rule lookup at the create and
    income doors, the deposit's category placement at the income door and
    the skip door's account-payment refusal (ruling **R-JI**);
    ``transaction_on`` -- the earliest day any sighting states -- is the day
    the create door files the purchase on and, through
    :meth:`~._offers.MatchDays.of`, the day the match door re-dates a
    purchase to (ruling **R-FW**).  *A first draft of this sentence named
    two doors; the neutral review counted four, and an enumeration a reader
    takes as complete has to be.*  ``populate_existing()`` overwrites every
    column and ``column_property`` from the locked row -- ``merchant_id``
    and ``merchant_name`` are the two subqueries
    ``BankStatementLine._stated_by_the_naming_sighting`` builds, loaded in
    the same statement -- and re-runs the joined ``sightings`` load, so the
    sighting-derived reads are the row's too; a change pending on the
    instance is flushed before the statement runs (the session
    autoflushes), so the row read back holds it.
    It composes HERE rather than at each site for the reason the mode does:
    a locked read written next year inherits it by calling this.  On
    :func:`lock_lines` it is vacuous by construction -- that read selects
    the id column alone and hydrates no instance, so there is nothing for it
    to hand back stale -- and the doors' own reads, which are what hand a
    row to a door, are where it acts.  The precedent one table over was
    ``credit_workflow.lock_source_transaction_for_payback``, for the same
    trap on ``status_id``, until plan step ``balance:X-bn`` deleted it with
    the transaction row locks it took.  Graded by
    ``tests/test_services/test_statement_match/test_locked_read_refresh.py``,
    which reproduced the stale read on the tree before this step: the skip
    landed on a line whose merchant now paid an account the owner holds, and
    a purchase was filed on its posting day over a stated transaction day.

    Args:
        query: A query whose FROM list holds
            :class:`~app.models.statement_import.BankStatementLine`.

    Returns:
        The same query, locking the bank line rows it returns for the rest of
        this transaction, and returning each as the locked row stands.
    """
    return query.populate_existing().with_for_update(
        of=BankStatementLine, key_share=True,
    )


def _lines_on(account_id: int, line_ids: "frozenset[int]"):
    """Return the query for *line_ids* on *account_id*, in THE lock order.

    **One spelling of the order every locked read of bank lines takes**
    (plan step ``bank_import:X-gi-5``): ascending by ``id``.
    :func:`load_lines` reads through it per act and :func:`lock_lines` reads
    through it once per PASS, and sharing the query rather than the sentence
    is what makes the two orders one.  Two spellings that agree today are
    still two spellings (``CLAUDE.md`` rule 14).

    **``id`` and not ``(posted_on, id)``, which this read ordered by until
    that step.**  The reason it was chosen: the order had to compose with
    the other ORDERED writer of these rows, and only ``id`` did.  Through
    plan step ``bank_import:X-f6b-1`` a re-import filled the merchant KEY a
    recorded line lacked -- an ``UPDATE`` on the line -- and the ORM flushes
    a mapper's UPDATEs sorted by PRIMARY KEY
    (``sqlalchemy.orm.persistence._sort_states``), so that transaction took
    its row locks in id order.  Ids are not monotone in posted day across
    imports -- a fresher export inserts a finalized swipe into an earlier
    day's block -- so a pass ordered by day held a later-id line while
    wanting an earlier one that the re-import held: the cycle one order
    over.  Named by adversarial design review 2026-09-12.  **Since plan step
    ``bank_import:X-f6b-1b`` (ruling **R-BI16**) a re-import UPDATEs no
    line at all**: every fact it states is a new sighting row, which takes
    only ``FOR KEY SHARE`` on its line, and that lock conflicts with nothing
    a pass takes (``FOR NO KEY UPDATE``) -- so the cycle with a re-import is
    structurally gone, not ordered around.  The reason ``id`` STAYS: two
    passes over overlapping sets still need one order, ``id`` rests on
    nothing that can move (a day order held only while no writer ever
    changed ``posted_on``), and a writer that UPDATEs lines again would
    compose with it.  Nothing reads the returned list's order: every
    consumer sums it, takes its ``max`` or ``min``, or iterates to insert.

    **The ORDER BY is what orders the LOCKS, and that is PostgreSQL's
    documented behaviour rather than an assumption**: a locking ``SELECT``
    applies ``ORDER BY`` first and then takes each row's lock as the ordered
    rows are returned -- the plan is ``LockRows`` above the ordering node,
    re-measured by ``test_lock_order`` on every run -- so two transactions
    running it over overlapping sets wait on the first row they share and
    never cross.

    Args:
        account_id: The account whose lines may be reached.  A FILTER rather
            than a check on fetched rows: an id naming another account's line
            returns nothing, so a crafted body can neither read nor lock a
            row that is not this pass's to touch.
        line_ids: The ids to read.

    Returns:
        The unlocked query; callers that write wrap it in
        :func:`locked_for_write`.
    """
    return (
        db.session.query(BankStatementLine)
        .filter(
            BankStatementLine.account_id == account_id,
            BankStatementLine.id.in_(line_ids),
        )
        .order_by(BankStatementLine.id)
    )


def lock_lines(account_id: int, line_ids: "frozenset[int]") -> None:
    """Take every bank-line lock a PASS will need, in one order, up front.

    Plan step ``bank_import:X-gi-5``, finding **N-471**.  Each door locks the
    line it writes through :func:`load_lines` or
    :func:`~._skipping._line_on`, and each of those reads is ordered -- but
    :func:`~._batch.apply_reviewed` runs a door per ITEM across four arms, so
    a pass took its locks in the order the SUBMISSION listed them, one
    ordered read at a time.  Two concurrent presses naming the same lines in
    different arms took them in opposite orders, PostgreSQL detected the
    cycle and aborted one mid-batch: the loser's whole press rolled back and
    was answered with the generic *Something went wrong* sentence
    (:func:`~app.routes.accounts._statement_doors.run_statement_fragment_door`
    catches the database error) over an ERROR-level traceback.
    **Reproduced 2026-09-12** with a forced interleave: press A recording a
    LATE line then skipping an EARLY one, press B the reverse, each paused
    after its first lock -- A died ``DeadlockDetected`` and B landed both.

    **This reads every line the batch names ONCE, in :func:`_lines_on`'s
    order, before any arm runs.**  A row lock is held by the TRANSACTION --
    outside every item's savepoint, so a refused item's rollback releases
    the door's re-take and not this -- and a door's own locked read a moment
    later re-takes a lock it already holds and blocks on nothing; the order
    two presses take their line locks in is therefore this read's, which is
    the same for both, and they queue on the first line they share instead
    of crossing.  The doors keep their own locks: each is still safe called
    on its own, and a pass carrying a line this read could not lock -- one
    another account holds -- reaches the door's own refusal for it, one
    item, with the rest still landing.

    **It changes no item's order and no savepoint's** -- a solo press's
    receipt is byte-identical, which the ledger row's remedy sentence had
    wrong (*it changes which savepoint runs first*): that would be true of
    SORTING the items, which is not the remedy.  What moves is WHEN a press
    that must wait does so: before its first item rather than part-way
    through, so every line the batch names is held for the whole pass, and a
    pair that would have deadlocked on a SHARED LINE now ends as one press
    landing and the other's items on that line landing as repeats or
    refusing as already answered.

    **What it does not remove is every other lock a pass takes**, and the
    claim above is scoped to the lines on purpose.  An item that settles a
    row locks ``budget.transactions``, and until plan step ``balance:X-bn``
    it then took the per-user advisory lock through the posting sync -- so
    two presses of one owner sharing NO line, or a press against a
    concurrent settle, could cross on those (finding **N-193**'s class).
    Since that step the advisory lock is held from before the pass's first
    row lock (below), so those two serialise on it instead.  An import
    DELETE cascades through these rows in the referential trigger's own
    scan order, which no ``ORDER BY`` of ours composes with.  This read
    closes the cycle on the BANK LINES, which is the one this door created
    by locking per item.

    **It takes the lock under the account FILTER**, so ids the pass has no
    business with lock nothing, and it returns nothing: what a door needs to
    know about a line it reads for itself under the same lock -- and that
    read is the one :func:`locked_for_write` refreshes, since this one
    selects the id column alone and hydrates no instance it could hand back
    stale (plan step ``bank_import:X-gv``).  An empty set emits no statement
    at all, which is the ordinary untouched-form press.

    **The per-user advisory lock is not taken here, and it is already held
    ABOVE this read**: since plan step ``balance:X-bn`` the command
    transaction a pass runs in took it before reading any of the owner's
    data (:mod:`app.db_transaction`), so it is that transaction's FIRST lock
    and this is the pass's first ROW lock.  *Until that step this paragraph
    said the lock was still to come, and that taking it at one door first
    would put this door's order against every door not yet reached, the
    cycle finding **N-193** records.*

    Args:
        account_id: The pass's account, which is the ONE statement of whose
            lines may be locked.
        line_ids: Every bank line the batch names, across all of its arms
            (:attr:`~._batch.ReviewedBatch.line_ids`).
    """
    if not line_ids:
        return
    locked_for_write(
        _lines_on(account_id, line_ids).with_entities(BankStatementLine.id)
    ).all()


def load_lines(
    account_id: int, line_ids: "frozenset[int]", matched: MatchedSubjects,
    *, for_write: bool,
) -> "list[BankStatementLine]":
    """Return the submitted bank lines, refusing any this account cannot match.

    **A line ALREADY in a match is refused here, symmetrically with the row
    side**, and the asymmetry was a real defect rather than an omission.
    ``uq_statement_match_members_line`` refuses the second act either way, so
    nothing could be corrupted -- but it arrives as an ``IntegrityError`` AFTER
    ``_apply_day`` has moved a settle day, which reaches the user as
    "Something went wrong" and logs a full traceback at ERROR for an ordinary
    stale page.  The hand-build form makes it easy to reach: its checkboxes
    render ``review.unmatched``, so one tab submitting a line another tab has
    just matched is two clicks.  Found by adversarial security review
    2026-08-17.

    **PUBLIC within the package since plan step X-f6a-3c-2**, because
    :mod:`._create` needs exactly this refusal for the one line it records and
    had grown its own copy of it.  Two implementations of "is this line on this
    account, and has something already claimed it" is two places for the
    refusal to stop firing.

    **A line the owner has SKIPPED is refused for the same reason one match
    holds** (plan step ``bank_import:X-gj-4a``, ruling **bank_import:R-HP**):
    a bank line ends on exactly ONE of the four verbs, and
    :func:`~._skipping.skip_line` refuses the mirror -- a line a live match
    answers may not also be skipped.  **Without this half the exclusivity is
    one-directional**, and the state it admits is silent: the line carries a
    match AND a skip, so it renders a card on the Explained tab and another on
    the Skipped tab, is absent from the inbox for two independent reasons, and
    nothing raises.  No key can hold it -- the rule spans two tables -- which
    is the position ``accept_match``'s balance refusal is already in.

    *The two-cards half of that sentence was written at ``X-gj-4a`` and was
    FALSE until plan step ``bank_import:X-gj-4c-2``*: the Skipped tab then held
    the lines a standing *never a purchase* answer barred rather than recorded
    skips, so such a line rendered on Explained and nowhere else.  It is true
    now, and it is MEASURED rather than asserted --
    ``test_reconcile.test_the_cards_and_skips.TestADoublyAnsweredLineIsTHECOSTTwoRefusalsBUY``
    builds the match through the real door at a moment when no skip exists, then
    inserts the SKIP row at the ORM tier -- so only the second answer is
    planted, which is the half this refusal owns.  *An earlier draft said
    "past both doors"; only one is bypassed, and the resulting STATE is the
    thing neither door would allow.*  Recorded
    because a justification written ahead of the surface it describes reads,
    to the next reader, exactly like one that was checked.

    **Asked HERE and not in the three doors**, because this function is
    already the one statement of *is this line on this account, and has
    anything claimed it*: the paragraph above says two implementations of that
    question is two places for it to stop firing, and a third door written next
    year inherits this one by calling it.

    Args:
        account_id: The account the match is for.
        line_ids: The submitted ids.
        matched: What this account's matches have already claimed, read by the
            ACT rather than queried here -- so a batch's fourth item sees the
            lines its third item claimed.
        for_write: Whether the caller is about to WRITE a match, which decides
            whether the lines are read under a row lock.  **Keyword-only and
            with NO DEFAULT**, because the value that reads as safe is the
            wrong one in both directions: defaulting to ``True`` makes a
            PREVIEW fail, and defaulting to ``False`` makes a DOOR race.  The
            three write doors pass ``True``;
            :func:`~._preview.preview_hand_build` passes ``False``, and it is
            the only caller that may -- it exists to run this door's reads and
            refusals WITHOUT its writes.
            **A preview must not lock, and that is measured rather than
            stylistic**: a query request runs inside a
            ``REPEATABLE READ, READ ONLY`` transaction
            (:mod:`app.db_transaction`), where PostgreSQL refuses every row-lock
            strength -- ``FOR NO KEY UPDATE`` included -- at executor start,
            whether or not the query matches a row.  **The caller that reaches
            it that way is the Reconcile page's own render**: ``?open=<line>``
            builds the card's MATCH pane through
            :func:`~._opened.opened_match`, which is an ordinary GET, so a lock
            taken unconditionally here is a 500 on that page.  Named by
            adversarial design review 2026-09-02, which found it in this step's
            own first draft, on the workbench's ``GET
            .../statements/match?line=N``; plan step ``bank_import:X-gi-2``
            deleted that page and the constraint is unchanged, which is why the
            caller is NAMED here rather than left as a page a reader can no
            longer open.

    Returns:
        The lines, ascending by id (:func:`_lines_on`); for a writing caller,
        each as the locked row stands rather than as the pass first hydrated
        it (:func:`locked_for_write`).

    Raises:
        ValidationError: When an id names no line on this account, names one
            another match already explains, or names one the owner has already
            skipped.  A REFUSAL rather than silently dropping the member,
            unlike the reconcile panel's bulk tick: that door narrows a
            set the user swept, and this one names specific rows on purpose, so
            dropping a member would change what the match MEANS while
            reporting success.  *(It read "rather than a silent skip" until
            plan step ``bank_import:X-gj-4a`` made SKIP a verb of this
            package's own, at which point the sentence read as being about the
            refusal one line above it.)*
    """
    if line_ids & matched.lines:
        raise ValidationError(
            "A statement line you picked is already matched to something "
            "else.  Undo that match first if it is wrong.  Nothing was "
            "changed."
        )
    # **A WRITING CALLER READS THE LINES LOCKED, AND BEFORE THE SKIP TEST
    # BELOW.**  Which lock, and why it has to precede that test, is
    # :func:`locked_for_write`'s docstring: this door asks whether a skip
    # answers the line and :func:`~._skipping.skip_line` asks whether a match
    # does, and the row lock is what keeps two tabs from interleaving into a
    # line carrying both answers.
    #
    # **The ORDER is :func:`_lines_on`'s, shared with the read the PASS takes
    # before its arms.**  Until plan step ``bank_import:X-gi-5`` the order
    # here bounded the deadlock risk WITHIN one call only:
    # :func:`~._batch.apply_reviewed` calls a door per item, so a bulk apply
    # took N separately-ordered reads in SUBMISSION order and two concurrent
    # presses naming the same lines in opposite order deadlocked (finding
    # **N-471**, named by adversarial design review 2026-09-02).
    # :func:`lock_lines` now takes every one of a pass's locks first, through
    # the same query, so this read re-takes locks its transaction already
    # holds and the order across a batch is that read's.
    query = _lines_on(account_id, line_ids)
    if for_write:
        query = locked_for_write(query)
    lines = query.all()
    if len(lines) != len(line_ids):
        raise ValidationError(
            "A statement line you picked is no longer on this account.  "
            "Reload the page and try again -- nothing was changed."
        )
    if skipped_among(line_ids, account_id):
        raise ValidationError(
            "A statement line you picked is one you have already skipped, so "
            "it is not waiting to be explained.  Undo that skip first if you "
            "meant to explain it.  Nothing was changed."
        )
    return lines


def resolve_rows(
    submission: MatchSubmission,
    scope: ReviewScope,
    matched: MatchedSubjects,
) -> "list[CandidateRow]":
    """Return the submitted app rows as priced candidates, refusing the rest.

    **Looked up in the pass's own offer set rather than queried directly**, so
    the set this door may act on is exactly the set the screen may offer.  One
    scope, shared by the reader and the writer, is the security property
    ``reconcile_service`` is built on: an id belonging to another user, another
    account, a non-contributing row, a card purchase or a row already spoken
    for by another match is not a candidate and cannot be matched by crafting a
    request.

    **The scope is a PARAMETER, the claims are re-read per act, and the FIGURE
    is re-derived per act** (plan step X-f6a-3c-2).  This function derived the
    whole account itself until that step, at 3.593 s a call on the developer's
    own data, which is 12.88 minutes to work one statement's 215 acts.  What
    made the derivation shareable is that its parts move at different rates:

    * WHICH rows exist and may be offered cannot change while a pass runs, so
      that is derived once and arrives on *scope*.  It is also the expensive
      half -- an 827-row scan -- and the security-bearing one;
    * WHICH of them are already spoken for changes with every item, so that is
      the *matched* argument, re-read by every act;
    * WHAT one is WORTH can be moved by a SIBLING act, so it is re-derived here
      through :func:`~._valuation.repriced`.

    **That third bullet replaces an argument adversarial financial review
    measured FALSE on 2026-08-19.**  The claim was that only a parent/child
    pairing can move a figure another item names, and that the accept door's
    guard against an envelope named beside its own purchase refuses it.  But
    settling a matched purchase runs ``entry_service.update_entry``, which
    re-derives the envelope's CC Payback through ``sync_entry_payback`` and
    WRITES its ``estimated_amount`` -- and that payback is a candidate on the
    same account, a SIBLING of the purchase rather than its parent, invisible
    to that guard.  Measured: a `$60.00` payback dropping to `$50.00`
    mid-pass, with the second match accepted against the stale `$60.00` and
    the ledger booking `$50.00` for a `-$60.00` bank line.  Re-pricing is
    total where an enumeration of sibling writers is one writer from being
    wrong again.  *(The guard is deleted since plan step
    ``credit_card:CC-5-4a-5``, leaf 5c-2c-1 -- finding **CC-386**, ruling
    **R-CC144** -- because this function refuses the pairing first: a row
    holding a purchase re-prices to nothing, ruling **R-BAL81**.)*

    Args:
        submission: What the owner accepted.
        scope: The pass's derived offer set.
        matched: What this account's matches have already claimed, as of this
            act.

    Returns:
        The candidates the submission names, transactions first, priced as they
        stand NOW.

    Raises:
        ValidationError: When an id names nothing the screen could have
            offered, names a row another match has since claimed, or names one
            that can no longer be priced at all.
    """
    reviewed = submission.subjects
    if len(reviewed) != len(submission.rows):
        # **A body naming one subject twice, refused BY NAME rather than
        # collapsed** (plan step ``bank_import:X-f6d-3``).  The screen renders
        # exactly one input per row, so this cannot arrive from it; and
        # ``subjects`` is a mapping, so two entries with one subject and
        # different reviewed figures would silently keep whichever the set
        # iterated last -- letting the SENDER choose which state the staleness
        # guard checks against, on the door that re-prices rows.  A first draft
        # left this to the count below and a docstring claimed it was caught
        # there; it was not, because that count is taken over the collapsed
        # mapping and 2 rows over 1 subject compares 1 against 1.
        raise ValidationError(
            "This match names the same row more than once.  Reload the page "
            "and try again; nothing was changed."
        )
    landed_on = (
        None if submission.consent is None else submission.consent.on_row
    )
    if landed_on is not None and landed_on not in submission.rows:
        # **The consent's row is a POINTER into the rows and this is what
        # makes it one** (plan step ``bank_import:X-gj-3a``; one value with
        # the figure since ``X-gp``).  It is refused HERE, beside the
        # duplicate-subject refusal above, because both are facts about the
        # SUBMISSION as a set of rows rather than about any row's state -- and
        # because refusing it before the offer set is read means a body naming
        # a row it does not carry never reaches the arithmetic that would
        # decide the remedy.
        #
        # **Compared as a WHOLE reviewed value rather than by subject.**  The
        # pane writes the option's value with the row's own token inside it,
        # so the pointer and the row are one string in any browser; a body
        # whose consent carries a different figure or revision from the row it
        # points at is describing two states of one row, which is finding
        # **N-336**'s shape with the halves inside one submission.
        raise ValidationError(
            "This match says its difference belongs to a row it does not "
            "include.  Reload the page and try again; nothing was changed."
        )
    offered = [
        row for row in unmatched_rows(scope.candidates, matched)
        if (row.kind, row.row_id) in reviewed
    ]
    found = [
        fresh for fresh in (
            repriced(row, scope.calendar, scope.basis, scope.account_id)
            for row in offered
        )
        if fresh is not None
    ]
    if len(found) != len(reviewed):
        raise ValidationError(
            "One of the rows in this match is no longer available to match -- "
            "it may have been deleted, cancelled, or matched to another "
            "statement line.  Reload the page and try again; nothing was "
            "changed."
        )
    _reject_moved_since_review(found, reviewed)
    return found


def _reject_moved_since_review(
    rows: "list[CandidateRow]",
    reviewed: "dict[tuple[RowKind, int], ReviewedRow]",
) -> None:
    """Refuse an item whose row is no longer what the screen described.

    **Finding N-336, and it is the one refusal here that is about the SCREEN
    rather than about the row.**  Every guard beside it asks whether an act is
    legal; this asks whether it is the act the owner reviewed.  Ruling
    **R-FP** -- *a match is a PROPOSAL, never a silent apply* -- is only true
    of the shipped app if something compares the two moments, and until this
    step nothing did: the screen offered *from ``-178.32`` to ``-178.29``*, the
    row was edited to ``500.00`` in another tab, and this door wrote a
    **``$321.71``** correction under that caption.

    **It runs AFTER the re-pricing rather than instead of it.**  The two answer
    different questions and both are needed: :func:`~._valuation.repriced`
    makes the write correct against the database as it stands NOW (finding
    **N-309**), and this makes the write one the owner agreed to.  A door with
    only the first writes a correct number nobody saw; a door with only the
    second writes a reviewed number that is stale.

    **It fails CLOSED, which is what the exact tier used to do by accident.**
    An equal match whose price moved became UNEQUAL and was refused by
    :func:`~._variance.reject_unrecordable`'s predecessor (**R-FV**);
    ``X-f6d-2`` made
    an unequal one-to-one recordable and that accident stopped protecting
    anything.  So this refuses on ANY movement, in either direction, on either
    coordinate -- not only where a correction would be written.  A match whose
    row silently grew a card purchase between render and Apply is exactly as
    unreviewed as one whose figure was retyped.

    Args:
        rows: The submitted rows as they stand now, re-priced.
        reviewed: What the screen showed for each, by subject
            (:attr:`~._submission.MatchSubmission.subjects`).

    Raises:
        ValidationError: Naming the row and both figures, on the first
            disagreement.  ONE sentence rather than a list: the batch quotes it
            per item (**R-FZ(a)**) and a reviewer acts on a stale page by
            reloading it, which fixes every row at once.
    """
    for row in rows:
        moved = reviewed[(row.kind, row.row_id)].disagrees_with(row)
        if moved is not None:
            raise ValidationError(
                f"This match was reviewed against different figures -- {moved}."
                "  Nothing was changed.  Reload the page to review it against "
                "what your records hold now."
            )
