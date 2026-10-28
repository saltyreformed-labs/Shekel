"""
Shekel Budget App -- Domain-Specific Exceptions

Raised by the service layer, caught and translated to HTTP responses
by the route layer.  Keeps business logic free of Flask concerns.

**One of them is translated APPLICATION-wide instead of per route**:
:class:`BaselineMissingError` is answered by a single handler in
:mod:`app.error_handlers` (plan step X-v, ruling R-BW), because the condition
it reports -- this user has no baseline scenario, so no balance is computable
for them -- has exactly one right answer on every surface, and sixteen routes
deciding it separately is the defect that ruling exists to end.
"""

from datetime import date

from app.tax_law import name_states


class ShekelError(Exception):
    """Base exception for all Shekel domain errors."""


class NotFoundError(ShekelError):
    """Requested resource does not exist."""


class ValidationError(ShekelError):
    """Input data failed business-rule validation."""


class AuthError(ShekelError):
    """Authentication or authorisation failure."""


class ConflictError(ShekelError):
    """Operation would create a conflicting state (e.g. duplicate)."""


class UndatedSettleError(ShekelError, ValueError):
    """A settled transaction was read for its settle day and carries none.

    A row is settled if and only if it records the civil day its money moved.
    Both facts are written by one statement -- ``status_seam.apply_status_change``
    assigns ``status_id`` and ``settled_on`` together -- so a row that breaks the
    pairing was written around that seam (a bulk ``query.update()``, or a
    fixture constructing the row directly).

    Raised by ``app.utils.balance_predicates.settled_day``, the one refusal of
    a missing settle day (plan step X-f1, ruling R-EC) and the accessor the
    row readers ask the question through.  A settled TRANSFER's loan-side
    payment was its second holder, through
    ``app.services.loan_ledger.payment_visible_on`` (leaf balance:X-bi-6-4d-1;
    it named the shadow row the record hung off until then), until plan step
    balance:X-bi-6-5's third leaf: the status-band rule
    (:mod:`app.side_band_infrastructure`) refuses a settled transfer's
    un-dated record at commit.  It is a REFUSAL rather than a
    fallback on purpose: the
    day is a stored fact now, so inventing one would put real money on a day
    nothing recorded, and skipping the row would remove money from a balance
    without saying so.

    A ``ValueError`` as well as a ``ShekelError``, mirroring
    :class:`BaselineMissingError` below: the condition is a broken invariant in
    stored data rather than a user input error, and no route translates it --
    reaching a user as a 500 is the correct disposition, because the request
    cannot be answered correctly and answering it wrongly is worse.
    """


class RequiredRecordMissing(ShekelError, ValueError):
    """A record a live invariant says must exist does not.

    Raised where the record is READ, by the surface that cannot answer without
    it: an interest-bearing account with no ``budget.interest_params``, an
    appreciating one with no ``budget.asset_appreciation_params``, an owner
    with no ``auth.user_settings``.

    **Each of those was an AUTO-CREATE on a GET until plan step
    balance:X-i3.**  A render that repairs data is a write inside a read -- it
    cost those pages the one snapshot every figure on them is computed against
    (:mod:`app.db_transaction`) -- and it hid the doors that should have
    written the row: the params seeder had ONE caller while THREE doors set an
    account's kind, which is what
    :mod:`app.services.account_params` now states once for all of them.

    A ``ValueError`` as well as a ``ShekelError``, on
    :class:`UndatedSettleError`'s reasoning exactly: the condition is a broken
    invariant in stored data rather than a user input error, and no route
    translates it -- reaching a user as a 500 is the correct disposition,
    because a manufactured zero-rate row renders on screen exactly like a rate
    the owner configured.

    **Deliberately NOT given an application-wide handler**, which is where it
    differs from :class:`BaselineMissingError` beneath it: that one has a
    repair the owner can perform on a page the app can render, and this one
    names a state no door can produce.  A rendered answer for it would be the
    auto-create under another name.
    """


class AmountUnresolvable(ShekelError, ValueError):
    """A row was asked what its amount is and no rule could answer.

    Raised by ``app.services.cash_ledger.resolve_transaction_amount`` and its
    transfer twin (plan step X-au-b, ruling **R-FI**): a row's amount is either
    its OWN or DERIVED, and where the rule that owns it cannot produce a figure
    -- an EMPTY series, no live net for the row's pay period, a loan whose
    basis will not resolve, a shadow with no parent -- this refusal is the
    answer.  (A linked row with no due date to resolve a series on was one
    until plan step X-bv-2 made it unstorable; see
    ``cash_ledger._definition_cash._stated_amount``.)

    **It is a refusal rather than a fallback, and the fallback is what the arc
    is deleting.**  Reading the stored column instead would publish exactly the
    stale derived figure R-FI exists to make unrepresentable, and once plan step
    X-au-c makes both amount columns NULLABLE that column holds ``None`` for a
    derived row, so the fallback would put a ``None`` into a money path.  The
    message names the row and what the rule needed, because every one of these
    conditions is repairable data rather than a transient.

    A ``ValueError`` as well as a ``ShekelError``, mirroring
    :class:`UndatedSettleError` above and for the same reason: the condition is
    a broken invariant in stored data rather than user input, and no route
    translates it -- reaching a user as a 500 is the correct disposition,
    because the request cannot be answered correctly and answering it wrongly is
    worse.
    """


class ForeignAccountError(ShekelError, ValueError):
    """A read pass was asked to derive state for an account it does not own.

    Raised by ``app.services.balance_at._memoize._memoize_once`` -- the ONE
    primitive that creates state keyed by ``account.id`` on a
    :class:`~app.services.balance_at.BalanceContext` -- when the account handed
    in belongs to some other owner than the pass's ``user_id`` (plan step
    balance:**X-i4**, finding **N-354**).

    **It is a PAIRING refusal, not a second ownership gate.**  Whether the
    requester may see the account is decided upstream, where an untrusted id
    becomes a row (``account_resolver``, ``auth_helpers.require_owner``,
    ``load_cash_account_or_404``), and a copy of that decision here would be a
    band-aid.  This answers a question no upstream gate can even ask: the seam
    takes the account and the pass as two arguments, the route validates the
    account against ``current_user`` while the context is built separately from
    ``current_user.id``, and a service-tier caller
    (``debt_strategy``, ``tax_report_service``, ``loan_recurrence_sync``, a CLI
    door) has no ``current_user`` at all.  Nothing above the seam knows a
    context exists, so nothing above it can catch the two being mis-paired.

    **What a mis-pairing would publish, which is why it raises rather than
    returning nothing.**  The transaction rows are scenario-scoped, so a foreign
    account folds NONE of its owner's rows -- but
    :func:`app.services.cash_ledger.cash_anchor_facts` is scoped by ACCOUNT
    alone, so the other owner's real balance ASSERTIONS replay and the fold
    answers a confident figure built from them.  A wrong number that renders
    like a right one, plus a cross-tenant disclosure.

    **Not translated by any route**, which is the same disposition
    :class:`UndatedSettleError` and :class:`RequiredRecordMissing` carry and for
    the same reason: it names a state no door can legitimately produce, so
    reaching a user as a 500 is correct.  Answering it as a 404 would make a
    code defect read as an ordinary missing page -- which is exactly how a
    permissive account resolver's silent fallback stayed invisible long enough
    to need the guard at ``app/routes/analytics.py``.

    A ``ValueError`` as well as a :class:`ShekelError`, on the same reasoning as
    its neighbours above.
    """


class BaselineMissingError(ShekelError, ValueError):
    """The balance seam was asked for a figure by a user with no baseline scenario.

    Every balance the app produces is scoped to a baseline scenario, so a user
    without one has no balance the app can answer -- not a zero balance, an
    UNANSWERABLE one.  :func:`app.services.balance_at.require_scenario` raises
    this at the seam's door, :func:`app.services.scenario_resolver.require_baseline_scenario`
    raises it one tier down, and ONE handler
    (:func:`app.error_handlers.register_error_handlers`'s ``baseline_missing``)
    turns it into the setup-recovery page for a full request and ``204 No
    Content`` for a safe-method HTMX one -- so the answer is decided in ONE
    place instead of at every surface that reads a balance (plan step X-v,
    ruling R-BW).

    Carries :attr:`user_id`, the user the raise was RESOLVED for.  The handler
    logs it beside the requesting user's id, and they differ only when a caller
    built a context for the wrong user -- the one failure this exception's
    ERROR event exists to diagnose, and the one the requester's id alone cannot
    show (plan step X-v2's adversarial design review).

    **It is a state the application cannot produce**, measured 2026-07-28:
    ``registration_service.register_user`` writes a baseline for every owner, nothing in
    ``app/`` or ``scripts/`` deletes a scenario or clears ``is_baseline``, no
    path promotes a companion to owner, and ``scripts/integrity_check`` asserts
    it as critical check DC-08.  The handler exists because "cannot be produced
    by code" is not "cannot exist in the data", and because a user who somehow
    reaches this state needs the repair button rather than a stack trace.

    **It subclasses ``ValueError`` as well as :class:`ShekelError`** so that the
    seam's long-standing documented contract ("raises ``ValueError`` when the
    context has no baseline") stays literally true for every caller and test
    that relies on it, while the handler can catch this condition and nothing
    else.  Catching bare ``ValueError`` at the application tier would swallow
    every unrelated conversion failure in the request.

    Args:
        message: What happened, naming the repair.
        user_id: The user the raise was resolved for, or ``None`` when the
            raiser does not know it (the seam's context always does).

    Attributes:
        user_id: As above.
    """

    def __init__(self, message: str, user_id: int | None = None) -> None:
        """Store the resolved user id alongside the message."""
        super().__init__(message)
        self.user_id = user_id


class UnsupportedStateError(ShekelError, ValueError):
    """A salary profile was priced in a state the tax law does not list.

    Raised by :func:`app.services.tax_config_service.profile_tax_series`, the
    one read that slices the law for a profile, so the paycheck engine and the
    Taxes tab refuse the profile alike (plan step salary:X-at-3: "the engine
    refuses a state the law lacks"; ruling **salary:R-SAL78**).  Until
    that step the engine priced such a state's tax at ``$0.00`` -- or, for a
    paycheck a pay stub prices, carried the stub's printed state tax unmoved by
    any later pay change -- and said nothing (finding **salary:SAL-575**).

    **It is a state no door can produce**: both profile doors refuse a state
    the law does not list, and no profile held one when X-at-3 shipped (the
    developer, 2026-10-08).  What remains is a row older than a release that
    dropped a state, or one written around the doors.  The request cannot be
    answered correctly, and answering ``$0.00`` is the defect, so it is
    refused (ruling **salary:R-SAL130**).  **Unlike**
    :class:`RequiredRecordMissing` it has a repair the owner can make on a
    page: the profile's edit page prices nothing, so it opens, and its state
    list offers only the states the law lists.  So ONE handler answers it
    with a page naming the profile and linking there
    (:func:`app.error_handlers.register_error_handlers`'s
    ``salary_state_unsupported``; the developer, 2026-10-08, "One fix-it
    page", amending R-SAL130's generic error page), and the log names the
    profile and its state.

    A ``ValueError`` as well as a :class:`ShekelError`, mirroring its
    neighbours above.

    Args:
        profile_id: The profile's id, so the log names one row.
        profile_name: The profile's name, as its owner knows it.
        state_code: The state the profile holds, exactly as stored.
        supported: The states the law lists, sorted.

    Attributes:
        profile_id, profile_name, state_code, supported: As above.
    """

    def __init__(
        self, profile_id: int, profile_name: str, state_code: str,
        supported: tuple[str, ...],
    ) -> None:
        """Name the profile, its state and the states the law lists."""
        super().__init__(
            f'Salary profile "{profile_name}" (id {profile_id}) is in '
            f"{state_code!r}, which Shekel's tax law does not list (it lists "
            f"{name_states(supported)}).  Choose a state the law lists on the "
            f"profile's edit page."
        )
        self.profile_id = profile_id
        self.profile_name = profile_name
        self.state_code = state_code
        self.supported = supported


class RecurrenceWindowError(ShekelError):
    """A generate pass was handed a write window its owner's schedule lacks.

    A broken invariant rather than user input.
    :class:`~app.services.generation_schedule.GenerationSchedule` takes the
    owner's pay CALENDAR and the ids of the periods a pass may write into, and
    refuses any id the calendar does not hold.  **ONE way in since pay-calendar
    plan step C2-f3c**:

    * the caller paired one user's template with another user's pay period, or
      named a period that no longer exists -- every path already
      ownership-checks before reaching generation, so this is a route-layer
      hole or a probe.  An UNSAVED period arrives here too, as an id of
      ``None``: it is not one of the owner's materialised ids, so the same
      refusal answers it.

    **Two other arms lost their SUBJECT at C2-f3c and this docstring is the
    third revision of that list.**  The value used to LOAD the schedule itself,
    twice -- once as ORM rows and once as a calendar -- so it refused a
    disagreement between the two reads (a concurrent schedule write between
    them, or a stored ``period_index`` out of payday order) and refused an
    unsaved period by name.  There is one read now and it is the caller's, its
    order is payday order by construction, and a window is a set of integers,
    so neither state is expressible rather than being refused.  An earlier
    revision said "exactly two ways" and was left stale by plan step C2-b2; an
    adversarial review caught that one, and an adversarial review of C2-f3c
    caught this one going stale the same way.

    Raised rather than skipped because both alternatives are silent: a window
    period the schedule does not contain simply matches nothing, and the pass
    would report "generated 0 rows" for a template whose rule fires every
    paycheck.  A recurring bill that quietly stops being generated is exactly
    the failure this arc exists to make loud.
    """


class RecurrenceConflict(ShekelError):
    """Recurrence regeneration found rows it must not change unasked.

    Three kinds, because the owner owns three different things about a
    generated row: its AMOUNT, its EXISTENCE, and the RECORDS kept against it.

    Attributes:
        overridden:  List of row IDs with is_override = True -- the owner set
                     this row's AMOUNT by hand, so re-pricing it from the
                     template would discard that.
        deleted:     List of row IDs with is_deleted = True -- the owner removed
                     this row, so recreating it would resurrect it.
        retained:    List of row IDs the pass LEFT ALONE because the owner has
                     records against them that applying the definition change
                     would have destroyed or re-attributed.  Added at plan step
                     R10-a with finding **N-292**, in two shapes: the new rule
                     no longer fires in this row's period (the old behaviour
                     deleted the row, and ``transaction_entries`` CASCADED
                     until plan step ``credit_card:CC-5-4a-4``, so the
                     purchases went with it), or the template's ACCOUNT
                     moved, which drags every purchase onto the new account and
                     invalidates the statement link that cleared it.  A THIRD
                     shape joined at finding **REC-516**: the row answers no
                     occurrence at all (``occurs_on`` is NULL), so no rule ever
                     named it, none dropped it, and the definition has moved
                     past a row it cannot place.  **Unlike the other two, this
                     list names rows the pass did not touch at all**, so
                     abandoning the prompt is always the safe outcome.
    """

    def __init__(self, overridden=None, deleted=None, retained=None):
        self.overridden = overridden or []
        self.deleted = deleted or []
        self.retained = retained or []
        super().__init__(
            f"Recurrence conflict: {len(self.overridden)} overridden, "
            f"{len(self.deleted)} deleted, {len(self.retained)} retained."
        )


class PayPeriodLocked(ShekelError):
    """A pay-period operation was refused: a target period is hard-locked.

    Raised by truncate / regenerate when the window they would delete or
    rebuild contains a period that may never be removed -- it is
    historical, holds a settled, non-deleted row or transfer, holds a row or
    transfer holding a payment or purchase, or holds posted ledger entries
    (:class:`~app.services.pay_period_locks.PeriodLockReason`).  A hard lock
    is NOT overridable (unlike the discard gate); the operation deletes
    nothing.

    **The message is the caller's** (plan step ``pay_calendar:C22``):
    ``pay_period_locks.locked_refusal`` words it and
    ``pay_period_gates.gate_deletable_tail`` raises it -- per paycheck locked
    for holding a movement, every payment and purchase it holds with its
    amount and its row (ruling **R-PC116**: "The 2026-11-16 paycheck holds 2
    payments or purchases you entered (Kroger, $87.43, in Groceries; Rent's
    payment, $1,200.00). Remove or move them first."), then a count of the
    rest.  The words are built beside the reader that finds the movements and
    the one spelling of a paycheck the pay-period refusals share, which this
    module cannot import.  Until C22 this class built the sentence itself
    from the periods' start dates alone -- ruling **R-CC66**'s "Pay period
    9/10 holds a recorded payment or purchase; delete it from its row
    first.", which R-PC116 replaced -- and before plan step
    ``credit_card:CC-5-4a-4`` its generic sentence listed "an account
    anchor, or a recurrence anchor": two reasons deleted at plan steps
    X-f1c3c and R7b-4, which it went on naming.

    Attributes:
        blocking: A dict mapping each blocking pay-period id to its
            :class:`~app.services.pay_period_locks.PeriodLockReason`.
    """

    def __init__(self, blocking, message):
        self.blocking = blocking
        super().__init__(message)


class PayPeriodDiscardRequired(ShekelError):
    """A destructive pay-period op would discard unrecoverable rows.

    Raised by truncate / regenerate when the affected window holds rows
    regeneration cannot reproduce -- hand-entered (no template), manual
    overrides, or deliberately Credit/Cancelled rows -- and the caller has
    not passed ``confirm_discard=True``.  Unlike :class:`PayPeriodLocked`,
    this gate is overridable: the user may confirm and proceed.  The
    operation deletes nothing until confirmed.

    Attributes:
        count: The number of rows that would be discarded.
    """

    def __init__(self, count):
        self.count = count
        super().__init__(
            f"This will permanently discard {count} hand-entered or changed "
            f"item(s) that cannot be regenerated. Confirm to proceed."
        )


class PayPeriodUnresolved(ShekelError):
    """A submitted pay-period id names no period the requesting owner has.

    Raised by ``pay_period_admin.truncate_pay_periods`` and
    ``remove_earlier_pay_periods`` (plan step ``pay_calendar:C21``) when the
    id the form posted resolves to none of the caller's own periods -- it
    was never theirs, it never existed, or a concurrent removal deleted it
    after the page rendered (for truncate, also between the discard-confirm
    422 and the confirmation post).  The operation deletes nothing.

    **One class for all three cases, and that is the security property**
    (plan step C3-a, finding **P13**).  The house rule is that "not found" and
    "not yours" are answered identically so no door becomes an existence
    oracle; here that is structural rather than remembered, because there is
    only one exception to raise and one message on it.  Which case it was IS
    distinguished -- in the ACCESS log, where an analyst can see it and a
    prober cannot (``pay_period_gates.log_unresolved_period``).

    Its own class rather than a bare
    :class:`ValidationError`, because the truncate route has to catch it: a
    catch on the generic base would flash "reload the settings page and choose
    the period again" for any future business-rule refusal raised anywhere
    below it, turning a real defect into advice about a dropdown.

    Args:
        period_id: The submitted id that resolved to nothing.
        choice: What the owner picks again, in the words of the form that
            posted it -- "the period to keep through" for truncate, "the
            paycheck to start from" for remove-earlier.

    Attributes:
        period_id: The submitted id that resolved to nothing.
    """

    def __init__(self, period_id, choice):
        """Keep the id for the log and name the choice to make again."""
        self.period_id = period_id
        # The id stays on the exception and in the ACCESS log, never in the
        # sentence: app text shows a person no system id (the developer,
        # 2026-09-23), and a stale page's owner could not act on one anyway.
        super().__init__(
            f"That pay period is not one of yours, or no longer exists. "
            f"Reload the pay-periods settings page and choose {choice} from "
            f"the current list."
        )


class PayPeriodRemovalRefused(ValidationError):
    """ "Remove earlier paychecks" was refused: a ruled reason, and its sentence.

    Plan step ``pay_calendar:C21``.  Raised by the door's gate
    (``pay_period_gates.gate_removable_head``: a row the owner made, a pay
    stub, money dated inside the removed paychecks -- ruling **R-PC109**),
    by the era rule (``pay_era_write.era_to_move``, **R-PC110**) and by the
    ledger post-condition (``pay_period_gates.reject_moved_ledger``,
    **R-PC114**).  The operation leaves nothing behind: the gate's refusals
    come before the door writes anything, and R-PC110's and R-PC114's
    inside a savepoint the door rolls back before it re-raises.

    **Its own class, for the reason** :class:`PayPeriodUnresolved` **gives**:
    the route must catch the door's refusals and nothing else.  The door
    runs both ledger re-syncs below that catch, and a catch on the generic
    :class:`ValidationError` would flash any refusal they raise as advice
    about paychecks instead of surfacing it (review 2 of C21).  A subclass
    of :class:`ValidationError`, so a caller asking the broader question
    still hears it.
    """


class PayPeriodResetBlocked(ShekelError):
    """A full pay-period reset was refused: the user has settled history.

    Raised by ``pay_period_admin.reset_pay_periods`` when the user has at
    least one settled (Paid or Received), non-deleted
    row or transfer.  Reset rebuilds the WHOLE schedule -- including the
    account anchor period -- and is a first-time-setup correction only; a
    user whose paychecks have begun settling must use Regenerate (which
    rebuilds only the unlocked future tail) so settled money is never
    rewritten under a new schedule.  The operation changes nothing.

    **Or when a row or transfer holds a payment or a purchase**, whatever its
    status (plan step ``credit_card:CC-5-4a-4``, ruling **R-CC65**): the
    reset deletes every row and transfer, and a movement is money that moved.
    Its sentence names what is held (ruling **R-CC66**).  Asked only once no
    settled row or transfer blocks, so exactly one of the two counts is
    non-zero.

    Attributes:
        settled_count: The settled rows and transfers blocking the reset,
            a transfer once (**R-BAL126**); 0 when a held movement blocks.
        holding_count: The rows and transfers holding a payment or purchase
            blocking the reset, a transfer once; 0 when a settled one blocks.
    """

    def __init__(self, settled_count=0, holding_count=0):
        self.settled_count = settled_count
        self.holding_count = holding_count
        if settled_count:
            message = (
                f"Cannot reset the schedule: you have {settled_count} settled "
                f"transaction(s).  Reset rebuilds your entire schedule and is "
                f"only for first-time setup before any paychecks have "
                f"settled.  Use Regenerate to rebuild your future schedule "
                f"instead."
            )
        else:
            each = "it" if holding_count == 1 else "each"
            message = (
                f"Cannot reset the schedule: {holding_count} row(s) hold a "
                f"recorded payment or purchase; delete {each} from its row "
                f"first.  Reset rebuilds your entire schedule and would "
                f"delete every row with it."
            )
        super().__init__(message)


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


class StatementLineIdMoved(StatementImportError):
    """A source's own id for a line now names a different day or amount.

    Plan step ``bank_import:X-f6b-1``, ruling **R-BI10**.  A source that
    carries its own line ids (an OFX ``FITID``, the feed's ``id``) has ONE
    line per id, and the reconciliation pairs an incoming line to the line
    this source's earlier sighting carries that id on before it compares any
    wording.  That pairing is within a ``(day, amount)`` group, so a file
    stating a held id on a DIFFERENT day or amount is the one shape it cannot
    pair and must not record: recording it would give this source two lines
    under one id, which is the state ``uq_bank_statement_lines_external_id``
    refused as a database error while the id lived on the line.  The
    refusal is the door's now, with a sentence.

    Attributes:
        external_id: The id the source reused.
        recorded: ``(posted_on, amount)`` -- where this source's earlier
            sighting holds the id.  The reconciliation's own group key
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
            f"second under it.  If the recorded line is the wrong one, delete "
            f"the import that recorded it on the statements page and import "
            f"this file again."
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


class PayStubRefused(ValidationError):
    """A transcribed pay stub the entry door will not record (plan step salary:S11-b).

    Carries every refusal the SERVICE finds in one submission, keyed by what
    it is about (``"payday"``, ``"tax-<kind id>"``, ``"printed_gross"``,
    ``"printed_net"``, ``"one_off:<index>"``, and
    ``"stub_gross_includes_after_tax"``, the profile's setting, named beside
    a printed-gross refusal by rulings R-SAL104 and R-SAL106), so the entry
    form marks each of them at once.  A form the route could not read at all
    (a malformed figure) is answered first and never reaches the service, so
    its refusals follow on the next submit.  What each refusal is, and whose
    ruling, is :mod:`app.services.pay_stub_service`'s module docstring.

    Attributes:
        errors: ``{key: message}``, never empty.
    """

    def __init__(self, errors: "dict[str, str]") -> None:
        self.errors = dict(errors)
        super().__init__("; ".join(self.errors.values()))


class TrackingStartRefused(ValidationError):
    """A tracking start was refused: a payment into the loan moved money on or before it.

    Plan step ``recurrence:R16-c-2``: rulings **R-R114** (the dashboard's
    tracking-start door), **R-R115** (the setup door, by the same rule) and
    **R-BAL155** (a payment counts from the day its money moved, so a
    ``$0.00`` close counts from none).  A ``tracking_start`` states "the app's
    record of this loan starts HERE", and the loan's recorded start reads
    nothing else
    (:attr:`app.services.balance_at._resolution.ResolvedLoan.recorded_start`).
    A payment whose money moved on or before that day contradicts the
    statement: the record plainly started earlier.  Raised by
    ``loan_anchor_service._stage_loan_anchor``, the one constructor of a loan
    anchor row, before anything is staged.

    **The message states the fact and no remedy, because the remedy is the
    door's.**  The dashboard's door offers an earlier date or its Record
    balance control (a ``user_trueup``, which corrects and starts nothing);
    the setup form has no such control and asks for a day after the
    origination and before the payment's, or for the origination day itself
    where no such day exists.  Each
    route appends its own sentence, and a caller asking the broader
    :class:`ValidationError` question still hears a true one.

    **Its own class, for the reason** :class:`PayPeriodRemovalRefused`
    **gives**: a route must catch this refusal and nothing else.

    Args:
        asked: The date the submitted tracking start asserts for.
        moved_on: The day the loan's earliest payment moved money, on or
            before *asked*.

    Attributes:
        asked: As above.
        moved_on: As above.
    """

    def __init__(self, asked: date, moved_on: date) -> None:
        """Keep both dates and state what they contradict."""
        self.asked = asked
        self.moved_on = moved_on
        super().__init__(
            f"A payment into this loan already moved money on "
            f"{moved_on.strftime('%b %-d, %Y')}, on or before "
            f"{asked.strftime('%b %-d, %Y')}, so the app's record of this loan "
            f"starts earlier than that date."
        )


class PageOutOfDate(ValidationError):
    """A press was refused: its page named other things than the press would take.

    Plan step ``credit_card:CC-5-4a-5``, rulings **R-CC81** and **R-CC127**
    (developer 2026-09-23 / 2026-09-30): *"Each warning also sends back the
    bank lines it named, and the function compares them with what it would
    undo. At 10:10 they differ, so nothing is saved"*.  Raised by
    ``match_press.Press`` -- per call of the match step of the one act
    that takes a movement off the books, and of the seam's re-point, before
    that call writes anything, and at the close of the save over everything
    it freed (:meth:`over_lines`, ruling **R-CC135**); the door's rollback
    undoes whatever the save staged.  Two causes reach it: a page drawn
    before a match was made or undone in another tab, and a door that forgot
    its caption.  **And by the row delete over the PURCHASES its dialog
    named** (ruling **R-CC131**, developer 2026-10-04, "Refuse and redraw",
    fulfilling ruling **R-CC96**'s clause): a purchase added under the row in
    another tab after the dialog was drawn is not deleted unnamed
    (``transaction_service._delete``).  A third --
    one popover Save typing a $0.00 estimate beside the status Paid, which
    the Paid caption reading the STORED estimate could not name, so the
    redrawn card was refused again at every try -- is closed by the caption
    under the Estimated box (ruling **R-CC129**, "Warn in both places").

    **The message states the fact and the reload a plain surface needs; a
    popover answers with its own.**  Ruling **R-CC128** (developer
    2026-10-04, "Redraw all"): a press refused from a full-edit popover
    redraws the whole popover from current state, so it shows
    :attr:`facts` above the current caption and tells the owner to press
    again rather than to reload.  Every other door hears the full sentence
    through its ordinary ``ValidationError`` arm, which is why this is a
    subclass: a route that does not know this refusal still renders a true
    one.

    Args:
        facts: What was refused and why, with no remedy -- the raiser's,
            because only it knows what the page named.

    Attributes:
        facts: As given.
    """

    def __init__(self, facts: str) -> None:
        """Keep the facts, then add the reload a plain surface needs."""
        self.facts = facts
        super().__init__(
            f"{facts} Reload the page to see what this press does now, then "
            f"press again."
        )

    @classmethod
    def over_lines(cls, freed: int, named: int) -> "PageOutOfDate":
        """Return the refusal of a SAVE that freed other bank lines than its page named.

        Worded for any press -- a Save, Paid / Received, a Delete -- because
        every one of them reaches it (review finding L3: it said "Saving now"
        on a Delete).  It opens with ruling **R-CC128**'s banner words
        ("nothing was saved because the page was out of date"), which every
        out-of-date refusal shares, the delete's purchases one included.
        Raised at the save's CLOSE (``match_press.Press``), which knows both
        counts whole; a call refused AT ONCE knows neither, and says
        :meth:`over_an_unnamed_line` instead.

        Args:
            freed: How many bank lines the press would leave unexplained again.
            named: How many lines its page named (plan step
                ``credit_card:CC-5-4a-5``, ruling **R-CC135**: the whole
                save's, compared whole).

        Returns:
            The exception, to raise.
        """
        return cls(
            f"Nothing was saved: this page was out of date. As things are now, "
            f"this press leaves {freed} bank line{'' if freed == 1 else 's'} "
            f"unexplained again on your statement screen, and the page named "
            f"{named}."
        )

    @classmethod
    def over_an_unnamed_line(cls) -> "PageOutOfDate":
        """Return the refusal of ONE call of a save freeing a bank line its page did not name.

        Raised AT ONCE, before the call writes anything (ruling **R-CC135**:
        *"undoing one the page did not name stops it at once"*), so it knows
        only the lines freed so far and never the whole save's -- it states
        the fact and no count.  It counted until plan step
        ``credit_card:CC-5-4a-5`` (leaf 5c-2c-2, its review's L1): two
        envelopes each freeing a line under a page that named none read
        "leaves 1 bank line", over a redraw naming two.  Opens with ruling
        **R-CC128**'s banner words, as :meth:`over_lines` does.

        Returns:
            The exception, to raise.
        """
        return cls(
            "Nothing was saved: this page was out of date. As things are now, "
            "this press would leave a bank line unexplained that this page did "
            "not mention."
        )
