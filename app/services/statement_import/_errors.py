"""The statement import's refusals: every exception the import door can raise.

**Moved here verbatim from** :mod:`app.exceptions` (plan step plan#2232,
ruling **R-IR**: pylint's 1,000-line module ceiling stays, and the session
that breaks a module is the one that splits it).  That module stood at 975
lines, and the refusal plan#2232 adds would have taken it past the ceiling;
the import's family is the one cut that is a subject of its own -- raised
only by this package, caught only by the statement routes through
:class:`StatementImportError` -- so it lives beside the door that raises it,
as :class:`~app.services.pay_calendar._eras.PayCalendarError` does beside
its own.  The classes moved byte-for-byte from :mod:`app.exceptions` in a
commit of their own, and only their import site changed: a module of this
package reads them from here, and every other importer from
:mod:`app.services.statement_import`.  The same plan step then changed
:class:`StatementLineIdMoved`'s advice and added
:class:`StatementLineIdsShareALine`, in its own later commit.

Services-boundary discipline (``CLAUDE.md`` Architecture): no ``flask``
import; a route renders the message.
"""

from app.exceptions import ShekelError


class StatementImportError(ShekelError):
    """A statement import was refused.  The base every refusal shares.

    Every subclass below REFUSES THE WHOLE IMPORT and writes nothing: the
    import door runs inside one unit of work, so a file that is wrong in one
    line leaves the database exactly as it was.  That is deliberate rather than
    convenient -- a half-recorded statement is a statement whose running-balance
    chain no longer closes, and the chain is the only self-check the app has
    over a record it did not author.

    Routes catch THIS and render the message; the subclasses exist so that a
    raise site says which refusal it is, and so a caller that wants to tell
    "not this account" from "the file contradicts itself" can.
    """


class StatementParseError(StatementImportError):
    """The uploaded file is not the shape its chosen adapter reads.

    A wrong file, a wrong source, or an export the institution has changed.
    Raised before anything is looked up, so it says nothing about the account.
    """


class StatementIntegrityError(StatementImportError):
    """The file contradicts ITSELF: its running-balance chain does not close.

    A source carrying a per-line running balance states the same account twice
    -- once as a sequence of amounts and once as a sequence of balances -- and
    the two must agree (``previous balance + this amount = this balance``).
    When they do not, one of three things is true: the export is missing lines,
    the adapter has ordered them wrongly, or the file has been edited.  All
    three make the record untrustworthy, and this is the one moment the app can
    tell.

    Attributes:
        break_count: How many consecutive pairs failed.
        first_break: A human-readable description of the earliest one.
    """

    def __init__(self, break_count, first_break):
        self.break_count = break_count
        self.first_break = first_break
        super().__init__(
            f"This statement does not add up: {break_count} place(s) where "
            f"the running balance does not follow from the line before it.  "
            f"The first is {first_break}.  Nothing was imported.  Re-export "
            f"the full span from your bank rather than a partial one."
        )


class StatementBalanceUnexplained(StatementImportError):
    """The file's own running-balance CHAIN reaches its header figure on no day.

    :class:`StatementIntegrityError`'s sibling, and separate from it because
    the two name different defects in the same chain and a shared message would
    state the wrong cause.  That one is a BREAK -- one line's balance does not
    follow from the line before it -- and points at the pair.  This one is a
    chain that is internally sound and still cannot produce the figure the
    file's own header states.

    A statement claims a balance and lists lines.  Where the file states a
    balance beside every line it states its own opening, so the claim is
    reconciled by the day ``d`` where ``stated - sum(lines up to d) == opening``
    (plan step ``bank_import:X-f6e-1``, ruling **R-GF**).  Real exports need
    that flexibility: the developer's 2026-08-16 file states 2026-08-13's
    closing over a list containing two 2026-08-14 lines, and it reconciles at
    08-13.  When NO day does, the file disagrees with itself, and a re-export
    is the answer.

    **It fires ONLY against the file's own chain, never against what the app
    has recorded**, and that bound is the whole of its honesty.  A mismatch
    with recorded history has innocent explanations the file cannot be blamed
    for -- a date-range export states the CURRENT balance, so the movements
    explaining it are simply not in the file -- and an earlier draft refused on
    that too.  An adversarial review reproduced it rejecting an honest export
    and telling the owner to delete an import, with a figure the bank's file
    had never asserted (2026-08-23).  An unplaceable claim is recorded
    unanchored instead.

    Attributes:
        stated: What the file's header claims the account held.
        implied: What its own chain puts the account at after its last line.
        detail: The sentence naming both, for the person who uploaded it.
    """

    def __init__(self, stated, implied, detail):
        self.stated = stated
        self.implied = implied
        self.detail = detail
        super().__init__(
            f"This statement disagrees with itself: {detail}.  Nothing was "
            f"imported.  Re-export the full span from your bank."
        )


class StatementAccountMismatch(StatementImportError):
    """The file names an account other than the one it is being imported into.

    Ruling **R-FP** makes the source-account mapping a FACT rather than a
    guess: the first import for an account records what its source calls that
    account, and every import after it is checked against the record.  This is
    that check failing -- importing the card's export into Checking, or one
    person's export into another's account.

    **The message names the repair, because the mapping itself can be the
    thing that is wrong** (plan step ``bank_import:X-f6a-4``, finding
    **N-302**).  It is learned from a FIRST import and was, until that step,
    unremovable -- so an owner who chose the wrong Shekel account once could
    never import that account's statements again.  Deleting every import an
    account holds from a source now clears the mapping with the last of them,
    and the next import learns it afresh.

    Attributes:
        recorded: What the source called this account when the mapping was
            first recorded.
        submitted: What the uploaded file calls its account.
    """

    def __init__(self, recorded, submitted, *, claimed_elsewhere=False):
        self.recorded = recorded
        self.submitted = submitted
        self.claimed_elsewhere = claimed_elsewhere
        # **The repair is on whichever account HOLDS the pairing, and the two
        # arms hold it in different places** -- so the sentence naming it is
        # per arm rather than appended to both.  A first version appended one
        # sentence pointing at "this account's statements page", which is right
        # for the arm below and WRONG for the one that fires when another of
        # the owner's accounts claims the file: there this account has no
        # pairing at all, and deleting its imports clears nothing.  That is the
        # arm the repair door exists for -- import into the wrong account, then
        # try the right one -- so it was the arm the sentence had to serve.
        # Found by adversarial security review 2026-08-20.
        repair = (
            "The pairing is on that other account, so that is where to clear "
            "it: delete its imports on its own statements page and the last "
            "one takes the pairing with it."
            if claimed_elsewhere else
            "If the recorded pairing is itself wrong, delete this account's "
            "imports on its statements page: the last one takes the pairing "
            "with it."
        )
        super().__init__(
            f"This file is for account '{submitted}', but this Shekel account "
            f"has been imported from '{recorded}' before.  Nothing was "
            f"imported.  Choose the matching account, or check that you "
            f"exported the right one.  {repair}"
        )


#: What frees a held line id, said once for both refusals that name one
#: (:class:`StatementLineIdMoved`, :class:`StatementLineIdsShareALine`).
#: **An id is held for as long as its LINE is recorded** (ruling
#: **plan#2233**): ``budget.line_source_ids`` goes with its line and at no
#: other time, and a line goes when the last import showing it is deleted.
#: So deleting the import that first showed the id is not enough while
#: another import -- another source's, or a later one of this source's --
#: still shows the line.  *Until plan step plan#2232 the sentence said "delete
#: the import that recorded it", which was true while an id was held only by
#: its own source's sightings.*
_HELD_ID_REMEDY = (
    "The app keeps a line id for as long as its line is recorded, even after "
    "the import that first showed it is deleted.  If the recorded line is the "
    "wrong one, delete every import that shows it on the statements page, "
    "then import this file again."
)


class StatementLineIdMoved(StatementImportError):
    """A source's own id for a line now names a different day or amount.

    Plan step ``bank_import:X-f6b-1``, ruling **R-BI10**.  A source that
    carries its own line ids (an OFX ``FITID``, the feed's ``id``) has ONE
    line per id, and the reconciliation pairs an incoming line to the line
    this source holds that id on before it compares any wording.  That
    pairing is within a ``(day, amount)`` group, so a file stating a held id
    on a DIFFERENT day or amount is the one shape it cannot pair and must not
    record: the file means a second line under an id this source holds on
    one.  The door refuses it here, before the pairing
    (``_plan._refuse_moved_ids``); ``uq_line_source_ids_account_source_external``
    (plan step plan#2232) refuses the same state to a writer outside the
    door and to two imports racing through it.

    Attributes:
        external_id: The id the source reused.
        recorded: ``(posted_on, amount)`` -- the line this source holds the id
            on.  The reconciliation's own group key
            (:func:`app.services.statement_import.group_key`), because that
            is exactly the pair the id failed to agree with.
        stated: ``(posted_on, amount)`` this file states for it.
    """

    def __init__(self, external_id, recorded, stated):
        self.external_id = external_id
        self.recorded = recorded
        self.stated = stated
        super().__init__(
            f"This file states line id '{external_id}' on {stated[0]} for "
            f"{stated[1]}, while this source has already shown that id on "
            f"{recorded[0]} for {recorded[1]}.  Nothing was imported.  A "
            f"source names one line per id, so the app will not record a "
            f"second under it.  {_HELD_ID_REMEDY}"
        )


class StatementLineIdsShareALine(StatementImportError):
    """A file states two ids as two lines that this source holds on ONE line.

    Plan step plan#2232.  A recorded line can hold two ids from one source:
    the reconciliation pairs by this source's id first and by its wording
    after (ruling **R-BI10**), so a file whose line carried a NEW id and the
    wording of a held line was paired to that line, and the line gained the
    second id.  A later file that states BOTH ids, as two lines of one
    ``(day, amount)`` group, says the two are separate transactions where the
    app holds one.  The id step pairs both to that one line, which would be
    two sightings of it from one import
    (``uq_statement_line_sightings_line_import``), and recording either on
    another line would put a held id on a second line
    (``uq_line_source_ids_account_source_external``), so the door refuses the
    file first (``_plan._refuse_ids_sharing_a_line``).  *Until that key
    existed the id step paired only the first, the second fell to the steps
    after it, and the door recorded it on another line silently -- the state
    the key exists to refuse.*

    **It refuses rather than moving an id to a new line**, because the app
    cannot tell which earlier pairing was the mistake, and an observation
    quietly rewritten is what ruling **R-FL** exists to prevent.

    Attributes:
        external_ids: The two ids, in the order the file states them.
        recorded: ``(posted_on, amount)`` -- the one line this source holds
            both on.
    """

    def __init__(self, external_ids, recorded):
        self.external_ids = external_ids
        self.recorded = recorded
        first, second = external_ids
        super().__init__(
            f"This file states line ids '{first}' and '{second}' as two lines "
            f"on {recorded[0]} for {recorded[1]}, while this source has "
            f"already shown both ids on one recorded line.  Nothing was "
            f"imported.  A source names one line per id, so the app will not "
            f"record a second line under either.  {_HELD_ID_REMEDY}"
        )


class StatementLineConflict(StatementImportError):
    """A line already recorded now states something DIFFERENT.

    The app holds a line the file no longer states, and the file states a line
    at the same day and amount that the app cannot account for.  The bank has
    RESTATED a line the app recorded as an observation -- and an observation
    quietly rewritten is exactly what ruling **R-FL** exists to prevent.

    **That is a claim about a GROUP, not about a slot** (plan step
    ``bank_import:X-f6a-4``).  A line's stored identity ends in an ordinal this
    app mints, and comparing an incoming line against whatever sits at its
    ordinal fired on two events the bank had not restated at all: two same-day
    same-amount lines listed in the other order, and a genuinely new line the
    bank inserted ahead of a recorded one.  Both refused a whole file.  The
    reconciliation now pairs on the wording the bank itself wrote
    (:func:`app.services.statement_import.pair_by_statement`), so what reaches
    here is a contradiction rather than a re-ordering.

    **It refuses rather than overwriting, and refuses rather than ignoring**,
    which is a deliberate choice between three bad options on an event that has
    never been observed: across the developer's 2026-07-19, 2026-08-16 and
    2026-08-18 exports -- 1,041 lines, 0 groups holding more than one member --
    there were 0 restatements.  Overwriting would destroy the original
    observation with no record; ignoring would leave the app holding a line the
    bank no longer states.  Refusing puts a human in front of the only case
    where those differ.

    **The two wordings it carries are EXAMPLES rather than a pairing.**  The
    reconciliation declined to pair them, so where several stand unaccounted
    for on each side there is no correspondence to name, and a message
    asserting one would be a true sentence about the wrong problem.

    Attributes:
        posted_on: The day the conflicting lines posted.
        amount: Their signed amount.
        recorded: One wording the app holds that the file does not state.
        submitted: One the file states that the app does not hold.
    """

    def __init__(self, posted_on, amount, recorded, submitted):
        self.posted_on = posted_on
        self.amount = amount
        self.recorded = recorded
        self.submitted = submitted
        super().__init__(
            f"On {posted_on} this file states '{submitted}' for {amount}, "
            f"which this account does not hold -- while it holds '{recorded}' "
            f"for that day and amount, which this file does not state.  "
            f"Nothing was imported.  A statement line is a record of what your "
            f"bank showed, so the app will not overwrite one.  If the recorded "
            f"line is the wrong one, delete the import that recorded it on the "
            f"statements page and import this file again."
        )
