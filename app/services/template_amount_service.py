"""
Shekel Budget App -- A recurring definition's amount, as a dated series

The ONE place a transaction template's or transfer template's amount is stated,
read as-of a date, and corrected.  Plan step **X-au-a**, rule 1 of ruling
**R-FI**: every financial quantity that VARIES OVER TIME is a dated series
resolved as-of, and a template's ``default_amount`` was the last such quantity
in this app carried as a bare scalar.  The model is
:class:`~app.models.template_amount_version.TemplateAmountVersion`; its
docstring holds the supersession argument and this one holds the rules.

**Four rules, and each answers a question the scalar could not.**

1. **Which definitions have a series** (:func:`owns_its_amount`).  A version
   records an amount somebody STATED.  A salary-linked transaction template and
   a derive-mode loan-payment transfer template both carry a ``default_amount``
   that something else COMPUTED -- the paycheck calculator in one case, P&I plus
   escrow in the other -- so a series over either would manufacture a price
   history nobody ever set.  Neither gets one.

2. **What a definition is worth on a date** (:func:`amount_as_of`).  The version
   with the greatest ``effective_date`` at or before the date; before the
   earliest version the series HOLDS FLAT at that earliest amount.  Holding flat
   rather than answering ``None`` is ruling **R-I**'s shape, and it is what makes
   this resolver TOTAL: a template generates rows into historical pay periods as
   readily as future ones, so a partial resolver would refuse to price a row the
   app itself created.  ``None`` is reserved for the one honest gap -- a
   definition with no version at all.

3. **How an amount is stated** (:func:`set_amount`, the write door).  Setting an
   amount as of a date keeps the scalar and the series in step in one call.  It
   is a no-op on the series when the series ALREADY answers that amount on that
   date, so a rename or a cadence edit appends nothing, and a version already
   standing on that exact date is CORRECTED in place rather than joined by a
   second (the escrow model's same-day rule).  **A ONE-OFF's price is
   corrected in place through a second door** (:func:`restate_in_effect`,
   plan step ``balance:X-bi-7b``, ruling **R-BAL29**): its series holds one
   version and a typed figure corrects the version the row's date reads,
   so the series never grows past one.

4. **How a mis-dated version is withdrawn** (:func:`delete_amount_version`).  A
   restatement writes a version at the date it NAMES, so it cannot fix one
   recorded against the wrong date; that is what this exists for.  The EARLIEST
   version is refused, because rule 2 answers every date before the series
   begins from it -- withdrawing it would silently reprice all of pre-history,
   and it also happens to be the only version of a one-version series, so an
   empty series is unreachable by hand.  A mis-dated earliest is corrected the
   way the model already allows: state the amount at the right date, which
   becomes the new earliest, then withdraw the old one.

**Back-dating is allowed, and deliberately** (developer, 2026-08-11).  Stating
that a premium rose last April moves no money that has already moved: a settled
row owns the figure it was booked at, and this series only ever prices rows that
are still projected.  The forward-only guard the loan escrow screen applies
(:func:`app.services.escrow_calculator._after_forward_boundary`) does NOT
transfer, because an escrow version edit really does move a settled payment's
principal/interest split and a template amount does not.

**Additive as of this step.**  ``default_amount`` stays authoritative and no row
is priced from this table yet; plan step **X-au-b** builds the resolver that
reads it and **X-au-e** cuts generated rows over.  Until then a definition whose
series is empty is not silently wrong -- :func:`amount_as_of` answers ``None``
and X-au-b's resolver is specified to REFUSE rather than fall back, so a writer
that forgets this door fails loud instead of publishing a plausible figure.

Boundary discipline: no Flask import.  Inputs are ORM rows and plain data,
outputs are plain data (``CLAUDE.md::Architecture``).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.template_amount_version import TemplateAmountVersion
from app.models.transaction_template import TransactionTemplate


def is_salary_linked_template(template) -> bool:
    """Return True iff an ACTIVE salary profile drives this template's amounts.

    A salary-linked template's instance amounts are paycheck-calculated per
    period (``income_service.SalaryPricing``), so its
    ``default_amount`` is vestigial: editing it does not change a generated row.
    Two callers read this, and they are the same question asked for two
    purposes -- the update route skips the amount-change conflict chooser for
    such a template, and :func:`owns_its_amount` refuses it a price series.

    It lives HERE rather than in ``recurrence_engine`` (its original home, plan
    step R2e-1) because the dependency has to run this way round: plan step
    X-au-e makes the recurrence engine READ this module's series, and a
    predicate in the engine that this module imported would close that loop.

    **It reads the RELATIONSHIP, not a fresh SELECT, and an adversarial review
    is why.**  Archiving a salary profile is exactly the moment a template stops
    being salary-linked and starts owning its own amount, so ``delete_profile``
    states that amount through the write door in the same unit of work -- and
    that door suppresses autoflush to protect the optimistic-lock counter.  A
    predicate issuing its own query would therefore read the profile as still
    ACTIVE and record nothing, leaving an eligible template with an EMPTY series
    (measured: 58 rows on production's one salary template).  The collection is
    identity-mapped, so a pending ``is_active = False`` is already visible here.

    Args:
        template: The :class:`~app.models.transaction_template.TransactionTemplate`
            to test.

    Returns:
        ``True`` when an active :class:`~app.models.salary_profile.SalaryProfile`
        names this template.
    """
    return any(profile.is_active for profile in template.salary_profiles)


def owns_its_amount(template) -> bool:
    """Return True iff *template*'s amount is STATED rather than derived.

    The single eligibility predicate for the price series, applied identically
    by the write door, the backfill and every display.  Two kinds of recurring
    definition are refused a series, and both for the same reason -- their
    ``default_amount`` is a snapshot of a computation, so versioning it would
    record a history nobody stated:

    * a **salary-linked** transaction template, whose rows the paycheck
      calculator prices per period (:func:`is_salary_linked_template`);
    * a **derive-mode loan-payment** transfer template, whose ``default_amount``
      is the P&I plus escrow captured at its last write
      (``routes/loan/payment_transfer.py``) and whose projected cash is
      recomputed live by amount rule 4 (``cash_ledger._loan_pricing``).

    A MANUAL loan payment is NOT refused: there the operator owns the base cash
    (amount rule 4's MANUAL arm, which resolves the parent transfer's own
    figure since plan step X-au-g-2c-2), which is exactly a stated
    amount.  The distinction is read live off the settings row rather than
    remembered, so a template that is switched between the two modes gains or
    loses its series at that moment; the versions it already holds stay as the
    record of what was stated while it was manual.

    Args:
        template: A :class:`~app.models.transaction_template.TransactionTemplate`
            or :class:`~app.models.transfer_template.TransferTemplate`.

    Returns:
        ``True`` when the template may carry amount versions.
    """
    if isinstance(template, TransactionTemplate):
        return not is_salary_linked_template(template)
    settings = template.settings
    return settings is None or not settings.derive_from_loan


def amount_versions(template) -> list[TemplateAmountVersion]:
    """Return *template*'s amount versions, oldest first.

    Sorted here rather than by a relationship ``order_by`` so a version appended
    in this session -- which the collection would append at the tail regardless
    of its date -- still reads in date order to every caller.  ``id`` breaks a
    tie that the partial unique indexes make unreachable in the database but
    which two pending inserts in one session could still produce.

    Args:
        template: The transaction or transfer template to read.

    Returns:
        The template's versions ascending by ``(effective_date, id or 0)``;
        empty for a template that has none.
    """
    return sorted(
        template.amount_versions,
        key=lambda version: (version.effective_date, version.id or 0),
    )


def amount_as_of(template, on_date: date) -> Decimal | None:
    """Return the amount *template* states for ``on_date``, or ``None``.

    Supersession resolution: the version with the greatest ``effective_date`` at
    or before ``on_date``, which is the one no later version has superseded.
    **Before the earliest version the series holds FLAT** at that earliest
    amount rather than answering ``None`` -- ruling **R-I**'s shape, and the
    reason this resolver is TOTAL for any template that has a version at all.
    Generation writes rows into historical pay periods as readily as future
    ones, so answering "no amount" for a date before the first recorded price
    would refuse to price a row the app itself created; the honest answer there
    is the earliest price on record.

    ``None`` means the series is EMPTY, which is the one gap this function does
    not paper over: it is either a template whose amount is derived
    (:func:`owns_its_amount` is ``False``) or one whose creator did not state an
    amount through :func:`set_amount`.  Plan step X-au-b's resolver refuses
    rather than falling back, so that gap surfaces as a refusal instead of a
    plausible wrong figure.

    Args:
        template: The transaction or transfer template to resolve.
        on_date: The date to resolve the stated amount for.  For a generated
            row this is the row's own DUE date, never its pay period's bounds
            (developer, 2026-08-11; the rule ruling D5 already applies to a loan
            payment's escrow).

    Returns:
        The stated amount on ``on_date``, or ``None`` when the template holds no
        version.
    """
    resolved = _version_in_effect(template, on_date)
    if resolved is None:
        return None
    return Decimal(str(resolved.amount))


def _version_in_effect(template, on_date: date) -> TemplateAmountVersion | None:
    """Return the version that answers ``on_date``, or ``None`` for an empty series.

    **The ONE walk behind rule 2**, shared by :func:`amount_as_of` (which
    reads its amount) and :func:`restate_in_effect` (which corrects it in
    place, **R-BAL29**), so what a row is priced at and what a restatement
    corrects cannot be two different versions.  The version with the
    greatest ``effective_date`` at or before ``on_date``; before the earliest
    version, the earliest -- the series holds FLAT there, which is what makes
    the resolver total (see :func:`amount_as_of`).

    Args:
        template: The transaction or transfer template to resolve.
        on_date: The date to resolve for.

    Returns:
        The :class:`~app.models.template_amount_version.TemplateAmountVersion`
        in effect, or ``None`` when the template holds no version.
    """
    versions = amount_versions(template)
    if not versions:
        return None
    in_effect = [v for v in versions if v.effective_date <= on_date]
    return in_effect[-1] if in_effect else versions[0]


def _states_something_new(template, amount: Decimal, effective_on: date) -> bool:
    """Whether stating *amount* from ``effective_on`` records a fact the series lacks.

    Two ways it can, and the second was missing until an adversarial review
    walked the repair path this module documents:

    * the series answers a DIFFERENT amount on that date -- the ordinary price
      change; or
    * the date precedes the series' EARLIEST version.  A new earliest is always
      new information even at an unchanged amount, because it moves where
      pre-history is anchored: :func:`amount_as_of` holds flat before it, and
      :func:`delete_amount_version` refuses to withdraw it.  Without this arm the
      documented repair for a mis-dated earliest entry -- "state the amount at
      the date you want first, then withdraw the old one" -- wrote NOTHING, since
      back-projection made the restatement compare equal, leaving that entry
      permanently uncorrectable.

    Args:
        template: The transaction or transfer template being stated.
        amount: The stated amount.
        effective_on: The date it takes effect.

    Returns:
        ``True`` when a version should be appended.
    """
    versions = amount_versions(template)
    if versions and effective_on < versions[0].effective_date:
        return True
    return amount_as_of(template, effective_on) != amount


def _resync_scalar(template) -> None:
    """Put ``default_amount`` back on the NEWEST price the series states.

    The one rule keeping the scalar and the series in step, applied by both
    writers (:func:`set_amount` and :func:`delete_amount_version`) so neither
    can state it differently.  **The newest price rather than TODAY's**, because
    that is what the column MEANS, and the reason it means that has changed.
    Generation used to write the scalar onto every row an edit rebuilt from
    ``effective_from`` forward; since plan step balance:X-au-e a transaction's
    generation writes no figure at all, and the column's readers are the edit
    form's prefill, the Recurring list, ``obligations_aggregator`` and a
    transfer's generation.  A scheduled rise still has to reach the column the
    moment it is stated or the rows it was stated for would be
    rebuilt at the old figure.  Today's price is a different question, and the
    edit form asks it separately (:func:`current_amount`).

    A no-op for a template with no series, which is only ever one whose amount is
    derived -- there the column IS the statement and has already been set.

    Args:
        template: The transaction or transfer template to re-sync.
    """
    versions = amount_versions(template)
    if versions:
        template.default_amount = Decimal(str(versions[-1].amount))


def current_amount(template, on_date: date) -> Decimal:
    """Return what *template* costs on ``on_date``, for a form to prefill.

    :func:`amount_as_of` with the derived kinds' answer folded in: a salary or
    derive-mode loan template holds no series, so its column is its only
    statement.  Separate from the scalar because the two answer different
    questions and an adversarial review found the edit form asking the wrong
    one: it prefilled ``default_amount`` -- the NEWEST stated price -- beside a
    date input whose blank value means TODAY, so re-saving a template with a
    scheduled rise restated that rise as today's price.  The page said so out
    loud, offering an amount its own history panel labelled "Scheduled" while
    labelling a different one "Current".

    Args:
        template: The transaction or transfer template to read.
        on_date: The date to price it on -- today, for both edit forms.

    Returns:
        The amount in effect on ``on_date``.
    """
    resolved = amount_as_of(template, on_date)
    if resolved is None:
        return Decimal(str(template.default_amount))
    return resolved


def _version_on(template, effective_on: date) -> TemplateAmountVersion | None:
    """Return *template*'s version standing on exactly ``effective_on``, or None.

    The same-day lookup :func:`set_amount` and :func:`delete_amount_version`
    both need.  At most one can exist -- the partial unique indexes on
    ``(<template fk>, effective_date)`` say so -- which is what makes a same-day
    restatement a CORRECTION of one row rather than a second row racing it.

    Args:
        template: The transaction or transfer template to read.
        effective_on: The exact effective date to match.

    Returns:
        The version on that date, or ``None``.
    """
    for version in template.amount_versions:
        if version.effective_date == effective_on:
            return version
    return None


def set_amount(template, amount: Decimal, *, effective_on: date) -> None:
    """State *template*'s amount as ``amount``, in effect from ``effective_on``.

    **The one write door for a recurring definition's amount.**  It keeps the
    scalar (``default_amount``) and the dated series in step within one call, so
    no caller can move one without the other.  **The scalar stopped PRICING a
    transaction row at plan step balance:X-au-e** -- generation writes no figure
    and the series answers on each row's own due date -- but it did not go with
    that step, and it stays until plan step balance:X-bp deletes it: a generated
    TRANSFER still stores it (until X-au-f), ``salary_profile_service`` opens an
    archived profile's series AT it, the conflict chooser gates on it, and the
    cutover migration's downgrade restores from it.

    Every route that states a template's amount goes through this door: the
    two create forms, the two edit forms, the investment contribution and loan
    payment creators, and the salary paths (which it correctly records nothing
    for).

    Three outcomes on the series, and the order is the rule:

    1. A version already stands on ``effective_on`` -- its amount is CORRECTED
       in place.  A same-day restatement is a correction of one fact, not two
       competing facts, and the partial unique index makes the second
       unrepresentable anyway.
    2. Otherwise the series already answers ``amount`` on that date -- nothing
       is written.  This is what stops a rename, a cadence change or a re-saved
       form from littering the history with versions that state no change.
    3. Otherwise a version is appended through the relationship, so a template
       that has not been flushed yet still records its opening amount.

    A template that does NOT own its amount (:func:`owns_its_amount`) has the
    scalar set and the series left alone -- the salary and derive-mode loan
    paths reach here and must record nothing.

    **The scalar is then re-read off the series, never assigned the argument,
    and an adversarial review is why.**  ``default_amount`` means "the NEWEST
    stated price" -- which is the argument only when the argument IS the newest.
    *It meant that because generation wrote the scalar onto the rows an edit
    rebuilt from ``effective_from`` forward; since plan step balance:X-au-e a
    transaction's generation writes no figure at all, and the meaning now rests
    on the two readers below rather than on that write -- the edit form
    prefills from this column, and a transfer's generation still writes it.*
    Assigning it directly broke both other cases:
    stating a FUTURE price moved the scalar to it immediately, so the edit form
    (which prefills from that column) then offered the December figure with a
    blank date meaning today, and the next save -- a rename -- recorded the
    December rise as having happened today; and BACK-dating a price below an
    already-scheduled one silently cancelled the scheduled rise.
    :func:`_resync_scalar` states the rule once, and
    :func:`delete_amount_version` applies the same one.

    **It reads under ``no_autoflush``, and that is a correctness requirement
    rather than a tuning choice.**  Both templates carry an optimistic-lock
    ``version_id_col``, so EVERY separate UPDATE of the row bumps the counter --
    and the eligibility read below queries ``salary_profiles`` while the column
    assignment above is already dirty.  Left to autoflush, that query would
    flush the amount on its own and the caller's later field write would flush
    again, bumping the counter TWICE for one edit and breaking the guarantee the
    stale-form guard rests on (measured: ``version_id`` went from 1 to 3 on a
    single rename-plus-amount submit).  Suppressing the flush lets the caller's
    one flush carry every field of the edit, which is what it did before this
    door existed.

    Args:
        template: The transaction or transfer template whose amount is stated.
        amount: The stated amount.
        effective_on: The date the amount takes effect.  Back-dating is legal
            and deliberate: a settled row owns the figure it was booked at, so
            restating a past price moves no money that has already moved.
    """
    with db.session.no_autoflush:
        if not owns_its_amount(template):
            # No series to be the newest price OF; the column is the whole
            # statement, exactly as it was before this door existed.
            template.default_amount = amount
            return

        existing = _version_on(template, effective_on)
        if existing is not None:
            if Decimal(str(existing.amount)) != amount:
                existing.amount = amount
        elif _states_something_new(template, amount, effective_on):
            template.amount_versions.append(
                TemplateAmountVersion(
                    effective_date=effective_on, amount=amount,
                ),
            )
        _resync_scalar(template)


def restate_in_effect(template, amount: Decimal, *, on: date) -> None:
    """Correct, IN PLACE, the version of *template*'s series that ``on`` reads.

    **A ONE-OFF's price is restated in place, never appended** (rulings
    **R-BAL21** and **R-BAL29**, plan step ``balance:X-bi-7b``).  A one-off
    is a rule-less definition whose series holds ONE version dated on its
    row's due date, and the row is priced through amount rule 3 by that
    series as of its own due date.  A figure typed for such a row at the
    grid is a correction of the one price the definition states, so the
    version the row's date READS takes the figure and the series stays one
    version -- whatever the date has done since.  :func:`set_amount` cannot
    say this: with ``effective_on=today`` it APPENDS and leaves the row on
    the old figure (R-BAL21's rejected crossing); with
    ``effective_on=row.due_date`` it corrects in place only while a version
    stands on that EXACT date, so once the owner has moved the date from
    05-07 to 05-10 it appends ``{05-07: 162.25, 05-10: 170.00}`` and moving
    the date back silently re-reads ``$162.25``.  This door finds the
    version :func:`amount_as_of` answers for ``on`` -- the same walk,
    :func:`_version_in_effect` -- and corrects THAT one, so the series
    stays ``{05-07: 170.00}`` and one version stays one.

    A no-op when the version already states *amount*; the scalar is re-read
    off the series afterwards exactly as :func:`set_amount` does, and under
    the same ``no_autoflush`` for the same reason (the optimistic-lock
    counter must bump once per edit, not once per read).

    Not an arm inside :func:`set_amount`: exact-date semantics behind a flag
    is the override-parameter shape rule 1 forbids, and the two answer
    different questions -- one STATES a price from a date, this CORRECTS the
    price a date already reads.  Both are the series' own write doors,
    which is why this lives here and not in the one-off module (a door
    across modules would need this module's private helpers).

    Args:
        template: The :class:`~app.models.transaction_template.TransactionTemplate`
            whose series is corrected -- a rule-less definition's at the one
            door that calls this today.
        amount: The corrected figure.
        on: The date whose version is corrected -- the row's own due date,
            AFTER whatever this same edit did to it.

    Raises:
        ValueError: *template* holds no version, so no date reads anything.
            A definition reaches here only through the one-off producer or
            the template form, both of which open the series, so this names
            a state the app does not write rather than handling one.
    """
    with db.session.no_autoflush:
        in_effect = _version_in_effect(template, on)
        if in_effect is None:
            raise ValueError(
                f"Template {template.id} holds no amount version, so there is "
                f"nothing in effect on {on} to restate."
            )
        if Decimal(str(in_effect.amount)) != amount:
            in_effect.amount = amount
        _resync_scalar(template)


def _may_withdraw(versions, index: int) -> bool:
    """Return whether ``versions[index]`` may be withdrawn from the series.

    **THE one statement of the withdrawal rule**, so
    :func:`delete_amount_version` and :func:`build_amount_history`'s
    ``is_deletable`` cannot disagree -- the UI offering a Remove the service
    then refuses is two spellings of one rule, which is what
    :class:`AmountVersionRow`'s docstring already claimed was not happening
    (``CLAUDE.md`` rule 14).  It was ``index > 0`` in the builder against a
    newest-price test in the service, and after plan step balance:X-au-e the
    two disagreed about every middle version as well as the newest.

    A version may go only when the version BEFORE it states the same amount:
    every date it answered is then answered identically without it, so no row,
    no projection and no balance can move.  The earliest version is never
    withdrawable -- :func:`amount_as_of` holds flat before it, so removing it
    re-anchors the whole pre-history.

    Args:
        versions: The template's versions, ascending (:func:`amount_versions`).
        index: The position of the version being considered.

    Returns:
        ``True`` when withdrawing it changes what the series answers on no
        date at all.
    """
    if index == 0:
        return False
    return Decimal(str(versions[index - 1].amount)) == Decimal(
        str(versions[index].amount),
    )


def delete_amount_version(template, version_id: int) -> bool:
    """Withdraw one amount version from *template*'s series.

    The correction path for a version recorded against the wrong DATE, which
    restating the amount cannot fix (a restatement writes a version at the date
    it names and leaves the mis-dated one standing).  Scoped to ``template``'s
    own versions, so the caller's ownership check on the template is the whole
    authorisation: a ``version_id`` belonging to anyone else simply is not found.

    **The EARLIEST version is refused**, for the reason :func:`amount_as_of`
    holds flat before it: that version answers every date before the series
    begins, so withdrawing it reprices all of pre-history without saying so.
    It is also the only version of a one-version series, which makes an EMPTY
    series -- the gap :func:`amount_as_of` reports as ``None`` -- unreachable by
    hand.  A mis-dated earliest is still correctable: state the amount at the
    right date, which becomes the new earliest, then withdraw the old one.

    **A withdrawal that would change the PRICE on ANY DATE is refused too**,
    and that is what keeps this a correction of the RECORD rather than a
    money-moving act.  Unlike every other amount-changing path in the app, this
    one runs no regeneration and offers no conflict chooser, so a moved price
    here moves silently.

    **The predicate compared only the NEWEST price until plan step
    balance:X-au-e, and that was a live defect the moment rows started
    resolving.**  While generation copied ``default_amount`` onto each row, a
    MIDDLE version's withdrawal moved no stored figure and the newest-price
    test was sufficient.  A generated row resolves through
    :func:`amount_as_of` on its own due date now, so withdrawing a middle
    version re-prices every row inside that version's window.  Measured on a
    production clone 2026-09-03: Geico's series was
    ``[2026-04-01 $178.00, 2026-06-01 $178.32, 2026-09-01 $165.30]``, the
    2026-06-01 entry was WITHDRAWABLE, and taking it moved one row from
    ``$178.32`` to ``$178.00`` under a flash reading only "Amount history entry
    removed".

    **The rule that replaces it is one sentence and subsumes the old one**: a
    version may be withdrawn only when the version BEFORE it states the same
    amount, so the series answers identically on every date once it is gone.
    For the newest entry that is exactly the old test -- its predecessor
    becomes the newest -- so the special case disappears rather than being kept
    beside the general one.

    **The correction path stays complete**, because a restatement is what
    changes a price and a withdrawal is what tidies the record afterwards.  An
    entry dated wrongly is repaired by stating the same amount at the right date
    -- which appends (see :func:`_states_something_new`) and leaves the newest
    price untouched -- and then withdrawing the old entry, which is now neither
    earliest nor price-bearing.  Cancelling a SCHEDULED rise is a price change,
    so it belongs in the Amount field: restating the old figure on that date
    corrects the entry in place.

    Args:
        template: The transaction or transfer template that owns the version.
        version_id: The
            :class:`~app.models.template_amount_version.TemplateAmountVersion`
            primary key to withdraw.

    Returns:
        ``True`` when the version was removed; ``False`` when no such version
        belongs to this template, when it is the earliest one, or when
        withdrawing it would change what the series answers on any date.
    """
    versions = amount_versions(template)
    for index, version in enumerate(versions):
        if version.id != version_id:
            continue
        if not _may_withdraw(versions, index):
            return False
        template.amount_versions.remove(version)
        return True
    return False


@dataclass(frozen=True)
class AmountVersionRow:
    """One row of a recurring definition's price-history display.

    Every cell is precomputed here so the edit form renders without arithmetic
    and without comparing a status string ("templates display, never compute"),
    mirroring :class:`~app.services.escrow_calculator.EscrowVersionDisplay`.
    ``status_key`` is a CSS-modifier token and ``status_label`` its caption;
    ``is_deletable`` is :func:`delete_amount_version`'s own verdict for this row,
    read off the same rule rather than restated in the template.
    """

    id: int
    effective_date: date
    amount: Decimal
    status_key: str
    status_label: str
    is_deletable: bool


def _version_status(version, current_id: int | None, is_earliest: bool,
                    on_date: date) -> tuple[str, str]:
    """Classify one amount version for the history display: ``(key, label)``.

    A future-dated version is ``scheduled`` (a price change already queued); the
    version in effect on ``on_date`` is ``current``; the oldest version, when it
    is neither, is ``earliest`` -- named because it is the one every date before
    the series answers from, and the one :func:`delete_amount_version` refuses.
    Anything else is a superseded ``past`` version.

    Args:
        version: The :class:`~app.models.template_amount_version.TemplateAmountVersion`.
        current_id: The id of the version in effect on ``on_date``, or ``None``.
        is_earliest: Whether this is the oldest version of the series.
        on_date: Today (the cutoff the ``scheduled`` classification uses).

    Returns:
        ``(status_key, status_label)`` for the version.
    """
    if version.effective_date > on_date:
        return ("scheduled", "Scheduled")
    if version.id is not None and version.id == current_id:
        return ("current", "Current")
    if is_earliest:
        return ("earliest", "Earliest")
    return ("past", "Past")


def build_amount_history(template, on_date: date) -> list[AmountVersionRow]:
    """Build a recurring definition's price history for display, NEWEST first.

    The edit form's read-and-correct surface for the series this module writes
    (developer, 2026-08-11).  Until plan step X-au-e prices rows from it, the
    series is a fact the app records and nothing renders, and a fact nobody can
    see is a fact nobody can find wrong -- a price stamped against a mistyped
    date would surface only once it started moving money.

    Newest first because the current price is what a reader came for; the
    resolution order is ascending and stays inside :func:`amount_versions`.

    Args:
        template: The transaction or transfer template to display.
        on_date: Today -- the date the ``current`` / ``scheduled`` split and the
            in-effect lookup resolve against.

    Returns:
        One :class:`AmountVersionRow` per version, descending by effective date;
        empty for a template with no series, and empty for one whose amount is
        DERIVED even when it holds versions from an earlier mode.
    """
    if not owns_its_amount(template):
        # Eligibility is read LIVE, so a manual loan payment switched to derive
        # mode keeps the versions it recorded while it was manual -- correctly,
        # as the record of what was stated then.  Rendering them would state
        # something false: an adversarial review measured the transfer edit form
        # offering "Current $531.94" from that dormant series while the app was
        # using the $1,910.95 the loan derives, with a live Remove button beside
        # it.  A price nothing prices from is history, not a control.
        return []
    versions = amount_versions(template)
    if not versions:
        return []
    in_effect = [v for v in versions if v.effective_date <= on_date]
    current_id = in_effect[-1].id if in_effect else None
    rows = []
    for index, version in enumerate(versions):
        status_key, status_label = _version_status(
            version, current_id, index == 0, on_date,
        )
        rows.append(AmountVersionRow(
            id=version.id,
            effective_date=version.effective_date,
            amount=Decimal(str(version.amount)),
            status_key=status_key,
            status_label=status_label,
            is_deletable=_may_withdraw(versions, index),
        ))
    rows.reverse()
    return rows


__all__ = [
    "AmountVersionRow",
    "amount_as_of",
    "amount_versions",
    "current_amount",
    "build_amount_history",
    "delete_amount_version",
    "is_salary_linked_template",
    "owns_its_amount",
    "restate_in_effect",
    "set_amount",
]
