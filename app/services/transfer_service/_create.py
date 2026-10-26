"""
Shekel Budget App -- Transfer Service: the CREATE verb

The one path that brings a transfer into existence, and the
:class:`TransferSpec` value that describes one such request.

**A transfer is ONE row since plan step ``balance:X-bi-6-4d-2``** (ruling
**R-BAL166**).  This door also built two twin
:class:`~app.models.transaction.Transaction` rows per transfer, one on each
account, until then; since ``X-bi-6-4d-2`` nothing read a twin's status or
day, each side's payment hangs off the transfer by its side link
(``transaction_entries.expense_transfer_id`` / ``income_transfer_id``), and
that step's migration deleted the rows the door had built.

**This leaf is the ONE holder of the W9907 status allowlist entry.**  The
write it makes is a CONSTRUCTOR write -- ``Transfer(status_id=...)`` in
:func:`create_transfer` -- which the status fence exempts by naming this
module.  Every other status write in the package goes through
:func:`app.services.status_seam.apply_status_change`
(:mod:`app.services.transfer_service._status`), so the allowlist stops at
this file rather than covering the package.  Plan step X-aj2 makes the write
door structural and deletes the entry.

Flask-isolated like the rest of the package: plain data in, ORM rows out, no
``request`` / ``session`` imports.  Flushes; does NOT commit.
"""

import logging
from dataclasses import dataclass
from datetime import date

from app.exceptions import ValidationError
from app.extensions import db
from app.models.amount_ownership import AmountOwnership
from app.models.ref import Status
from app.models.transfer import Transfer
from app.services.transfer_service._loan_posting import (
    _reject_payment_before_origination,
)
from app.services.transfer_service._ownership import (
    _get_owned_account,
    _get_owned_category,
    _get_owned_period,
    _get_owned_scenario,
    _get_owned_transfer_template,
)
from app.services.transfer_service._validation import (
    _reject_unmodeled_source,
    _validate_positive_amount,
)
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSFER_CREATED,
    log_event,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransferSpec:  # pylint: disable=too-many-instance-attributes
    """The canonical inputs for creating a transfer.

    Bundles the thirteen fields :func:`create_transfer` needs into one
    cohesive value object so the sole transfer-creation path takes a
    single argument rather than a thirteen-field signature.  Every field
    is read by ``create_transfer`` and supplied together by every caller
    (the new-transfer route, the recurrence engine, the materialize path)
    -- this is one "transfer to create" request, mirroring the columns
    of the ``Transfer`` row it produces.

    Pylint: ``too-many-instance-attributes`` (13/7) -- these are the
    irreducible inputs of one creation request, read as a flat unit by
    the single consumer; there is NO cohesive sub-group to nest, so
    splitting would fragment one concept for no gain.  Mirrors the
    ``AmortizationRow`` / ``PayoffScenarios`` precedent.  Frozen so a
    constructed spec is an immutable record of one request.

    Attributes:
        user_id: Owner of the transfer.
        from_account_id: Account money leaves (expense side).
        to_account_id: Account money enters (income side).
        pay_period_id: Pay period for the transfer.
        scenario_id: Budget scenario.
        amount_ownership: WHAT PRICES this transfer, as ONE value (ruling
            **R-BAL11**, plan step X-au-f): an
            :class:`~app.models.amount_ownership.AmountOwnership` that either
            OWNS a positive figure -- an ad-hoc transfer, or one an operator
            priced -- or DECLARES
            :attr:`~app.enums.AmountSourceEnum.TEMPLATE`, meaning its
            definition's effective-dated series prices it as of its own due
            date.  It was a bare ``amount: Decimal`` until that step, which
            could express only the first: a create door that cannot say *this
            row is priced by its definition* has to write a figure and let a
            later writer take it back, and the row is OWN in between.
        status_id: Initial status, outside the settled band: every app
            door creates Projected.  A settled status is refused (ruling
            **R-BAL379**): a transfer becomes settled only through the settle
            verb, after it exists.
        category_id: Optional spending category.  May be None.
        notes: Optional notes on the transfer.
        transfer_template_id: Optional link to the generating transfer
            template (for recurrence).
        name: Optional display name.  If None, generated from the
            account names.
        due_date: Optional due date stored on the transfer.
        occurs_on: WHICH OCCURRENCE this transfer answers: the day its
            template's cadence named (the transfer recurrence engine), or its
            own due date for a ONE-TIME transfer (the one-time branch of
            ``routes/transfers/_instances``, since plan step
            ``balance:X-ci-1``, ruling **R-BAL94**); ``None`` only for an
            ad-hoc transfer, which no definition placed.
    """

    user_id: int
    from_account_id: int
    to_account_id: int
    pay_period_id: int
    scenario_id: int
    amount_ownership: AmountOwnership
    status_id: int
    category_id: int | None
    notes: str | None = None
    transfer_template_id: int | None = None
    name: str | None = None
    due_date: date | None = None
    occurs_on: date | None = None


def create_transfer(spec: TransferSpec) -> Transfer:
    """Create a transfer that has not settled.

    This is the ONLY code path that should create rows in
    budget.transfers.  **It creates no settled transfer** (ruling
    **R-BAL379**, developer 2026-10-23, "Delete paid-at-birth door"): a
    transfer becomes settled only through the settle verb
    (``settle_transfer``, or ``update_transfer`` moving it to Done),
    which prices its figure, dates each side and records both sides'
    payments in one act.  So a create writes the transfer row alone, and
    neither side holds a payment record.

    Args:
        spec: The :class:`TransferSpec` carrying the owner, endpoints,
            placement (period/scenario), amount, status, category, and
            optional metadata (notes/name/template link/due date) for
            the transfer to create.

    Returns:
        The created, flushed :class:`Transfer`.

    Raises:
        ValidationError: If a stated amount is non-positive, if the
            accounts are the same, if the status is a settled one, or if any
            business rule is violated.
        NotFoundError: If any referenced entity does not exist or
            does not belong to user_id.
    """
    # ── Validate inputs ────────────────────────────────────────────
    # A stated figure is still refused when it is not positive; a spec that
    # states a RELATION instead has no figure to grade, and its price is its
    # definition's (ruling **R-BAL11**, plan step X-au-f).
    amount = (
        None if spec.amount_ownership.figure is None
        else _validate_positive_amount(spec.amount_ownership.figure)
    )
    # **The COERCED ownership is what the row gets.**
    # ``_validate_positive_amount`` returns ``Decimal(str(amount))``, and a
    # first revision kept that value for the settlement record and the log
    # while writing the caller's RAW ownership to the column -- one input, two
    # values, in one function.
    ownership = (
        spec.amount_ownership if amount is None
        else AmountOwnership.own(amount)
    )

    if spec.from_account_id == spec.to_account_id:
        raise ValidationError(
            "Source and destination accounts must be different."
        )

    from_account = _get_owned_account(
        spec.from_account_id, spec.user_id, label="Source account"
    )
    to_account = _get_owned_account(
        spec.to_account_id, spec.user_id, label="Destination account"
    )
    # The ONE set of source refusals -- a loan (a disbursement) and, since
    # plan step credit_card:CC-10, a card (a cash advance or balance transfer)
    # -- composed in ``_validation`` so this door and the endpoint move read
    # one home rather than each spelling the set.
    _reject_unmodeled_source(from_account)
    # R-C: a loan cannot receive a payment before it originates -- the fold
    # would erase it while the cash side still debits the funding account.
    #
    # The ownership answer and the installment's fallback basis are ONE
    # resolution (plan step ``pay_calendar:C13-b``).  This was a bare
    # ``_get_owned_period`` on the line above followed by a comment explaining
    # that the guard ran second DELIBERATELY -- because it read the period's
    # ``start_date``, so running it first would answer a cross-user id with a
    # 400 carrying a date off that row where the rule requires an
    # indistinguishable 404.  Passing the resolved period makes that ordering
    # a data dependency instead of a paragraph, and deletes the guard's own
    # second read of the same row.
    _reject_payment_before_origination(
        to_account,
        _get_owned_period(spec.pay_period_id, spec.user_id),
        spec.due_date,
    )
    _get_owned_scenario(spec.scenario_id, spec.user_id)
    _get_owned_category(spec.category_id, spec.user_id)
    _get_owned_transfer_template(spec.transfer_template_id, spec.user_id)
    created_status = db.session.get(Status, spec.status_id)
    # **A settled status is refused, before any row exists** (ruling
    # **R-BAL379**): there is one way to a settled transfer, the settle verb,
    # and a create that accepted one was a second create-and-settle path with
    # its own figure and its own day writer, which no app door reached (each
    # creates Projected) and only tests did.  Asked here, with every other
    # refusal, so no row is ever written for a refused request.
    #
    # ``is not None`` because ``db.session.get`` answers ``None`` for a status
    # id no row carries, and the FK is what refuses that at the flush; reading
    # the attribute unguarded would turn a bad id into an ``AttributeError``
    # here instead of the integrity error the caller gets.
    if created_status is not None and created_status.is_settled:
        raise ValidationError(
            f"A transfer cannot be created already {created_status.name}: "
            "create it Projected, then settle it."
        )

    # ── Determine the name ─────────────────────────────────────────
    transfer_name = spec.name or f"{from_account.name} to {to_account.name}"

    # ── Create transfer record ─────────────────────────────────────
    xfer = Transfer(
        user_id=spec.user_id,
        from_account_id=spec.from_account_id,
        to_account_id=spec.to_account_id,
        pay_period_id=spec.pay_period_id,
        scenario_id=spec.scenario_id,
        status_id=spec.status_id,
        transfer_template_id=spec.transfer_template_id,
        name=transfer_name,
        amount_ownership=ownership,
        category_id=spec.category_id,
        notes=spec.notes,
        due_date=spec.due_date,
        occurs_on=spec.occurs_on,
        is_override=False,
        is_deleted=False,
    )
    db.session.add(xfer)
    # Flush to get transfer.id, which the event below names.
    db.session.flush()

    log_event(
        logger, logging.INFO, EVT_TRANSFER_CREATED, BUSINESS,
        "Transfer created",
        user_id=spec.user_id,
        transfer_id=xfer.id,
        from_account_id=spec.from_account_id,
        to_account_id=spec.to_account_id,
        pay_period_id=spec.pay_period_id,
        scenario_id=spec.scenario_id,
        amount=str(amount) if amount is not None else None,
        status_id=spec.status_id,
        category_id=spec.category_id,
        transfer_template_id=spec.transfer_template_id,
    )
    return xfer
