"""
Shekel Budget App -- Transfer Service: bringing the POSTING LEDGER back in step.

WHICH edits move a transfer's posted double-entry effect
(:data:`_POSTING_RELEVANT_FIELDS`), and the reconcile that runs after one did
(:func:`_reconcile_postings_after_update`).  Split out of :mod:`._update` at
plan step **X-au-f**, which took that module past ``max-module-lines``.

**It is a module of its own for the reason ``._endpoints``, ``._status`` and
``._amount`` are**: ``._update`` ORCHESTRATES the doors, and every concern that
carries a rule of its own lives beside it rather than inside it.  This one
carries two -- which fields can move the ledger, and what has to be re-derived
when they do -- and both are argued at length, because both were measured
rather than reasoned.

**The alternative was shaving prose, and this project has already ruled against
it**: *"three lines of headroom is not a design, and the structural answer is a
package with one private leaf per verb"* (``transaction_service`` package
docstring).

Flask-isolated like the rest of the package: ORM rows and plain data in,
nothing out.  Reads and writes the posting ledger through
``posting_service`` / ``account_posting_service``; does not commit.
"""

from app.extensions import db
from app.models.ref import Status
from app.models.transfer import Transfer
from app.services import account_posting_service
from app.services import posting_service
from app.services.transfer_service._loan_posting import (
    _resync_vacated_loan,
    _sync_loan_postings_if_loan,
)


# The ``update_transfer`` kwargs whose change can alter a transfer's posted
# double-entry ledger effect or its attribution, so a change to any of them
# triggers a posting reconcile (Build-Order Step 2; see
# ``posting_service.sync_transfer_postings``).  ``status_id`` flips the
# settled/unsettled target; ``amount_ownership`` (what prices the transfer)
# and ``figure`` (a stated settled figure) together determine what a settled
# transfer moved (its one ``settled_figure``) -- the magnitude posted;
# ``pay_period_id`` moves the entry's period, so a settled period move
# reconciles R2-correctly (the per-(account, period) reconcile reverses the old
# period and posts the new) AND fires the effect-time self-heal for the Step-5
# account-anchor corrections (F1).
#
# ``due_date`` IS here, and its inclusion is load-bearing: on a LOAN payment the
# due date is a posting input.  The genesis write walk dates every payment by it
# (``loan_ledger.loan_event_stream``), orders on it
# (``loan_ledger.replay_loan_events``, which applies its strict
# ``anchor_date < due_date`` post-anchor boundary against it), and the
# installment interval it falls in names the charge the payment clears (ruling
# R-R89) -- so moving it changes which payments an anchor SUBSUMES, which
# installment's charge a payment clears, and therefore the POSTED balance.  Editing it
# without a reconcile would leave the posted ledger disagreeing with every live
# reader (the history rows, the payment
# table, the resolver's replay), silently, until an unrelated chokepoint happened
# to fire.  On a NON-loan transfer the cash reconcile is reconcile-to-target and
# writes nothing, so listing it costs one idempotent no-op round-trip.
#
# The remaining kwargs (``category_id`` / ``name`` / ``notes`` / ``is_override``)
# move none of these, so they raise no reconcile.  ``side_days`` is deliberately
# NOT here: it moves no leg AMOUNT, and an unsettled transfer has no postings to
# re-date, so the set stays the cheap always-on pre-filter.  A SETTLED
# settle-day edit IS posting-relevant since step E1a -- it moves the day every
# posting counts from (the ``entry_date``, step C2's one clock) -- and
# ``_reconcile_postings_after_update`` runs the full reconcile for that case
# explicitly (the per-(period, date) reconcile re-dates the entries, finding
# N-13) plus the two endpoint accounts' anchor-correction resync (F1).  The
# reconcile is idempotent, so listing a field that did not move the effect is a
# harmless no-op; this set is the cheap pre-filter that avoids a ledger
# round-trip on a pure metadata edit.
#
# ``from_account_id`` / ``to_account_id`` ARE here, and they are plan step
# R10-b's addition.  An endpoint move changes WHICH ledger accounts a settled
# transfer's two legs sit on, which is a change of the posted effect in exactly
# the sense this set names -- ``_posting_write.reconcile_periods`` takes the
# per-ledger-account delta over the UNION of what is posted and what is
# targeted, so the vacated accounts reverse to zero and the new ones post the
# effect, converging in one pass.  What that reconcile does NOT reach is the
# vacated accounts' own anchor corrections and, when the vacated destination was
# an amortizing loan, that loan's genesis ledger; both are re-derived
# explicitly in :func:`_reconcile_postings_after_update`.
_POSTING_RELEVANT_FIELDS = frozenset(
    {
        "status_id", "amount_ownership", "figure", "pay_period_id",
        "due_date",
        "from_account_id", "to_account_id",
    }
)


def _reconcile_postings_after_update(
    xfer: Transfer,
    updates: dict[str, object],
    vacated: "tuple[int, ...]" = (),
    vacated_destination_id: "int | None" = None,
) -> None:
    """Bring the posting ledger back in step after an ``update_transfer`` edit.

    Extracted from :func:`update_transfer` (which was at its branch/statement
    budget) so the reconcile tail is one cohesive step.  Runs after every kwarg
    is applied and the session is flushed:

    * **Step-2 cash reconcile** when a magnitude / settled-sense / period field
      changed (``_POSTING_RELEVANT_FIELDS``).  Placed here -- NOT inside
      ``apply_status_to_all_three`` -- because the period and due-date arms
      are applied AFTER the status arm, and the reconcile reads the
      transfer's settled figure and each side's dated record as they stand,
      so it must run once everything is in place or it would post the
      pre-edit state.  Since plan step ``balance:X-bi-6-3`` (ruling
      **R-BAL101**) it is told no settled sense: a movement posts iff it is
      dated under a contributing parent, and the seam has already dated (a
      settle), un-dated (a revert) or taken off (a ``$0.00`` close) each
      side's record by the time this runs.  Idempotent reconcile-to-target:
      a settle posts the per-movement entries, a revert reverses them to
      zero (a cancel, reached only from Projected, has none to reverse), an
      unchanged effect writes nothing.
    * **Loan-payment genesis reconcile** right after the cash reconcile (a
      no-op for a non-loan transfer):
      a settle / revert / amount / actual / period edit of a loan payment
      re-reconciles that loan's confirmed-payment splits (coupled on the running
      balance) and its opening / true-up anchor corrections.
    * **Full reconcile on a settled ``settled_on`` edit too (E1a / N-13)**: a
      ``settled_on`` change moves the day every posting counts from (its
      ``entry_date``, step C2's one clock) without changing any leg amount, so
      the per-period reconcile used to write nothing and the entries kept
      their stale dates.  The reconcile is per-(period, DATE) now: the
      old-dated entries reverse at their own date, the effect re-posts at the
      new settle date, and the loan sync's checked-projection assert verifies
      the result -- so the fold and the ledger cannot disagree about WHEN.
    * **Step-5 account-anchor resync on a settled ``settled_on`` edit (F1)**:
      resync the two endpoint accounts' anchor corrections so a settled
      ``settled_on`` move cannot strand a stale anchor correction (their
      reconcile is anchor-walk-derived, not delta-keyed off this transfer).
      Only for a SETTLED transfer (a projected one posts nothing); a no-op for
      a loan endpoint (the account walk skips amortizing accounts).
      ``pay_period_id`` needs no such branch -- it is in
      ``_POSTING_RELEVANT_FIELDS``, so a period move reconciles R2-correctly
      and self-heals via the cash reconcile above.  Fires on ANY settled
      ``settled_on`` edit, not only a pure one: on the common settle path (status
      + ``settled_on`` together) the reconcile's tail self-heal already covers
      both endpoints, so these two idempotent walks are redundant there -- an
      accepted, safe cost.  It is deliberately NOT narrowed to
      ``not needs_reconcile``, because a future COMBINED edit (e.g. ``amount``
      + ``settled_on``) could move the attribution in a way the delta-keyed
      self-heal does not cover; an always-correct resync is the point of this
      seam.

    * **The accounts an ENDPOINT MOVE left behind (plan step R10-b), LAST**: the cash
      reconcile above heals the LEGS by itself -- ``reconcile_periods`` takes
      the per-ledger-account delta over the union of posted and target, so a
      vacated ledger account reverses to zero in the same pass the new one
      posts -- but two things it emits are scoped to the transfer's CURRENT
      endpoints and reach no further.  ``sync_transfer_postings``' own
      Step-5 self-heal names ``(from_account_id, to_account_id)``, so a vacated
      account's opening / true-up corrections are re-derived here instead; and
      when the vacated destination was an amortizing LOAN, that loan's genesis
      ledger and its recurring payment's window both still count a payment it
      no longer has (:func:`._loan_posting._resync_vacated_loan`).  Both walks
      are idempotent and neither is gated on the transfer being settled: a
      PROJECTED payment posts no cash but is still inside the payoff projection
      the loan's window is bounded by, so a projected payment moving off a loan
      moves that loan's payoff.

    Args:
        xfer: The updated, flushed :class:`Transfer`.
        updates: The ``update_transfer`` kwargs that were applied.
        vacated: The account IDs this update moved the transfer OFF
            (:attr:`_Endpoints.vacated`); empty for every update that names no
            account, which is every caller outside the recurrence engine and the
            non-repeating propagation.
        vacated_destination_id: Which of those was the DESTINATION, or ``None``.
            Only that one can have been a loan whose payment set counted this
            transfer; see the comment at the call.
    """
    # **The vacated walks below need no disjunct of their own**, and three
    # adversarial reviews of this step each flagged the one that stood here.
    # *vacated* is non-empty only when ``from_account_id`` or ``to_account_id``
    # is in *updates*, and both are members of
    # :data:`_POSTING_RELEVANT_FIELDS` -- so ``vacated`` implies
    # ``needs_reconcile`` by MEMBERSHIP in that set, and ``or vacated`` could
    # never be the term that admitted a call.  A guard no input can exercise is
    # the shape this step deleted from both retention predicates; the
    # implication is stated here instead, where the set it rests on is three
    # definitions up and visible.
    needs_reconcile = bool(_POSTING_RELEVANT_FIELDS & updates.keys())
    # A stated side's day, by the key that states one since plan step
    # ``balance:X-bi-6-4c-3`` (it was ``settle_day``, the pair's one day).  A
    # side's day moves the postings of BOTH sides when the other borrows it,
    # which the whole-transfer sync below already covers.
    settle_day_edited = bool(updates.get("side_days"))
    if not (needs_reconcile or settle_day_edited):
        return
    current_status = db.session.get(Status, xfer.status_id)
    # A settled ``settled_on`` edit moves the day the event counts from (step
    # C2's one clock), which since step E1a IS a posting-relevant change: the
    # per-(period, date) reconcile reverses the stale-dated entry and re-posts
    # at the new settle date (finding N-13), and the loan sync's
    # checked-projection assert then verifies the ledger against the walk.
    if needs_reconcile or (settle_day_edited and current_status.is_settled):
        # The pair's door reads each movement's own state (dated under a
        # contributing parent, or not) since plan step ``balance:X-bi-6-3``
        # (ruling **R-BAL101**), so the settled sense is no longer passed:
        # the seam has already dated or un-dated the movements above.
        posting_service.sync_transfer_postings(xfer)
        _sync_loan_postings_if_loan(xfer)
    if settle_day_edited and current_status.is_settled:
        account_posting_service.sync_account_anchor_postings(
            xfer.from_account_id, xfer.scenario_id,
        )
        account_posting_service.sync_account_anchor_postings(
            xfer.to_account_id, xfer.scenario_id,
        )
    # LAST, and the position is load-bearing rather than tidy: both walks below
    # read the vacated account's ledger, and until ``sync_transfer_postings``
    # above has reversed this transfer's legs off it that ledger still holds a
    # net for a transfer with no leg there.  Run first instead, the account
    # walk raised ``PostingError`` -- *"Ledger account 8 holds a nonzero net for
    # transfer ids [409] but no active shadow on account 1 resolves them;
    # Transfer Invariant 1 is broken"* -- which was the invariant correctly
    # reporting a ledger this function had not finished moving.  Measured on a
    # production clone before the order was fixed.
    for account_id in vacated:
        account_posting_service.sync_account_anchor_postings(
            account_id, xfer.scenario_id,
        )
    # The LOAN half is the vacated DESTINATION's alone, and that narrowing is a
    # measurement rather than an economy.  A loan reached as a transfer's SOURCE
    # carries that transfer's EXPENSE leg, and a loan's payment set is
    # ``loan_loaders.income_legs`` -- transfers INTO the loan only -- so such
    # a transfer was never one of the loan's payments and there is no split to
    # re-derive when it leaves.  Its raw cash leg is reversed by
    # ``sync_transfer_postings`` above, which takes the per-ledger-account delta
    # over the union of posted and target.  Verified by removing the call: the
    # legacy-loan-source case stays green either way, where the destination
    # cases fail.
    if vacated_destination_id is not None:
        _resync_vacated_loan(vacated_destination_id, xfer.scenario_id)
