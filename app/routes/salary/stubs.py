"""
Shekel Budget App -- Salary route package: the pay stub ENTRY door (S11-b).

A real pay stub transcribed line by line (plan step **salary:S11-b**, ruling
**R-SAL42**).  Five routes, all owner-scoped (404 for "not found" and "not
yours" alike):

* ``GET  /salary/<profile_id>/stubs/new`` -- the entry flow (ruling
  **R-SAL50**, "Pick the payday first").  Without a ``payday`` it is step 1,
  the date alone; with one it refuses a date that cannot carry a stub
  (**R-SAL48**, **R-SAL49**), opens the stub a payday already holds for
  editing, and otherwise renders step 2, the form for that payday.
* ``POST /salary/<profile_id>/stubs`` -- record a NEW stub; a payday that
  already holds one is refused with a link to it (**R-SAL52**).
* ``GET  /salary/stubs/<stub_id>`` -- the stub: what it adds up to, each line
  beside the app's figure for that payday (fork 2), the base-pay gap (fork 5),
  and its form.
* ``POST /salary/stubs/<stub_id>/edit`` -- rewrite it (version-checked); the
  payday rules apply only to a date the edit changes (**R-SAL53**).
* ``POST /salary/stubs/<stub_id>/pricing`` -- its "Use for pricing" switch
  (fork 8a', **R-SAL51** (a)); never asks for the net again.

There is no delete door (fork 8a': "Nothing is ever deleted").  Every rule is
the service's (:mod:`app.services.pay_stub_service`); this module reads the
form, calls it and renders.

**Every write regenerates the profile's salary rows in its own transaction**
(plan step salary:S11-c-2c, its review's finding M3): since that step a
switched-on stub prices the taxes of the paychecks on or after its payday until
a later one takes over (rulings **R-SAL42**, **R-SAL54**), so recording,
editing or switching one can move today's paycheck and the rows the
regeneration rebuilds, as the calibration doors it replaced did (the salary
template stored today's paycheck until plan step X-bp).  The regeneration's
pass is built
AFTER the write, and the profile's ``pay_stubs`` -- a view-only collection
the write cannot append to -- is expired first (:func:`_reprice_after`).
"""

import logging
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from flask import abort, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import current_user
from marshmallow import Schema
from marshmallow import ValidationError as SchemaValidationError
from werkzeug.datastructures import MultiDict
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm.exc import StaleDataError

from app.exceptions import NotFoundError, PayStubRefused
from app.extensions import db
from app.models.pay_stub import PayStub
from app.models.salary_profile import SalaryProfile
from app.routes._commit_helpers import (
    DbErrorContext,
    StaleConflictContext,
    handle_db_error,
    handle_stale_conflict,
    handle_stale_form_conflict,
    regenerate_commit_or_report,
)
from app.routes._redirect_target import RedirectTarget
from app.routes.salary._bp import salary_bp
from app.routes.salary._helpers import _edit_page, _regenerate_salary_transactions
from app.schemas.validation import (
    PayStubFigureSchema,
    PayStubLineSchema,
    PayStubOneOffSchema,
    PayStubPaydaySchema,
    PayStubSchema,
)
from app.services import (
    paycheck_line_kinds, pay_stub_gross, pay_stub_service, withholding_kinds,
)
from app.services.balance_at import BalanceContext
from app.utils.auth_helpers import get_or_404, get_owned_via_parent, require_owner
from app.utils.db_errors import is_unique_violation
from app.utils.dates import display_today
from app.utils.digit_strings import parse_row_id

logger = logging.getLogger(__name__)

_payday_schema = PayStubPaydaySchema()
_stub_schema = PayStubSchema()
_figure_schema = PayStubFigureSchema()
_line_schema = PayStubLineSchema()
_one_off_schema = PayStubOneOffSchema()

#: How many empty one-off rows the form offers beyond the stub's own.  A stub
#: rarely prints a one-off; saving and re-opening offers two more.
_BLANK_ONE_OFF_ROWS = 2

#: The one-off rows' parallel form lists.
_ONE_OFF_FIELDS = ("one_off_name", "one_off_kind", "one_off_amount")

#: How a one-off row's refused field is worded in its row's message.
_ONE_OFF_LABELS = {"name": "Name", "paycheck_line_kind_id": "Kind", "amount": "Amount"}

#: The unique key a second record of one payday meets (R-SAL52's race arm).
_STUB_PAYDAY_UNIQUE_CONSTRAINT = "uq_pay_stubs_profile_payday"

_STALE_MESSAGE = (
    "This pay stub was changed by another action.  Please review it and try again."
)


# ── Pages ──────────────────────────────────────────────────────────


@salary_bp.route("/salary/<int:profile_id>/stubs/new")
@require_owner
def new_stub(profile_id: int) -> ResponseReturnValue:
    """The entry flow: step 1 (the payday), or step 2 (the form) once chosen."""
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)
    if "payday" not in request.args:
        return render_template("salary/stub_payday.html", profile=profile, errors={})

    errors = _payday_schema.validate(request.args)
    if errors:
        return render_template(
            "salary/stub_payday.html", profile=profile,
            errors=_first_messages(errors), payday=request.args.get("payday"),
        ), 422
    payday = _payday_schema.load(request.args)["payday"]
    # A payday that holds a stub opens it, asked BEFORE the payday refusal: a
    # stub whose payday the record no longer holds is still the owner's to
    # open (R-SAL50) and to correct in place (R-SAL53).
    held = pay_stub_service.stub_on(profile, payday)
    if held is not None:
        flash(f"Your {payday.isoformat()} stub is already entered; here it is.", "info")
        return redirect(url_for("salary.view_stub", stub_id=held.id))
    ctx = BalanceContext.build(current_user.id)
    refusal = pay_stub_service.payday_refusal(ctx, payday, display_today())
    if refusal is not None:
        return render_template(
            "salary/stub_payday.html", profile=profile,
            errors={"payday": refusal}, payday=payday.isoformat(),
        ), 422
    return render_template(
        "salary/stub_form.html",
        **_form_page(profile, ctx, payday),
        **_entry_values({"payday": payday.isoformat()}),
    )


@salary_bp.route("/salary/stubs/<int:stub_id>")
@require_owner
def view_stub(stub_id: int) -> ResponseReturnValue:
    """A saved stub: its report beside the app's paycheck, and its form."""
    stub = _owned_stub(stub_id)
    profile = stub.salary_profile
    ctx = BalanceContext.build(current_user.id)
    return render_template(
        "salary/stub_form.html",
        **_form_page(profile, ctx, stub.payday, stub),
        **_entry_values(_values_of(pay_stub_service.figures_of(stub))),
    )


# ── Writes ─────────────────────────────────────────────────────────


@salary_bp.route("/salary/<int:profile_id>/stubs", methods=["POST"])
@require_owner
def record_stub(profile_id: int) -> ResponseReturnValue:
    """Record a NEW transcribed stub; a payday already holding one is refused (R-SAL52)."""
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)
    ctx = BalanceContext.build(current_user.id)
    figures, printed, errors = _read_form(profile)
    user_id = current_user.id
    if figures is not None:
        try:
            stub = pay_stub_service.record_stub(
                profile, figures, printed, ctx, display_today(),
            )
            _reprice_after(profile)
            db.session.commit()
        except PayStubRefused as refused:
            db.session.rollback()
            errors = refused.errors
        except NotFoundError:
            db.session.rollback()
            abort(404)
        except IntegrityError as exc:
            # Two records of one payday that both passed the service's check:
            # the unique key refuses the second, and it is R-SAL52's refusal.
            db.session.rollback()
            if not is_unique_violation(exc, _STUB_PAYDAY_UNIQUE_CONSTRAINT):
                return _record_failed(user_id, profile_id)
            errors = {"payday": pay_stub_service.held_payday_refusal(figures.payday)}
        except SQLAlchemyError:
            return _record_failed(user_id, profile_id)
        else:
            logger.info(
                "user_id=%d recorded pay stub %d (%s) for profile %d",
                user_id, stub.id, stub.payday.isoformat(), profile_id,
            )
            flash(f"Pay stub for {stub.payday.isoformat()} saved.", "success")
            return redirect(url_for("salary.view_stub", stub_id=stub.id))
    payday = _posted_payday(figures)
    return render_template(
        "salary/stub_form.html",
        **_form_page(profile, ctx, payday),
        **_entry_values(request.form, errors),
        # R-SAL52: a payday entered elsewhere while this form was open is
        # refused, and the refusal links to the stub that holds it.
        held_stub=pay_stub_service.stub_on(profile, payday) if payday else None,
    ), 422


@salary_bp.route("/salary/stubs/<int:stub_id>/edit", methods=["POST"])
@require_owner
def edit_stub(stub_id: int) -> ResponseReturnValue:
    """Rewrite a saved stub's figures, its payday included (version-checked)."""
    stub = _owned_stub(stub_id)
    profile = stub.salary_profile
    # The version is read FIRST, before any field: a stale form is turned away
    # even when its fields are invalid, so a 422 re-render can never hand back
    # a form re-pinned to a version its figures were not typed against.  The
    # re-render carries the SUBMITTED version, which is this one.
    submitted_version = parse_row_id(request.form.get("version_id"))
    if submitted_version is None:
        abort(400)
    stale_ctx = _stale_ctx("edit_stub", stub_id)
    if submitted_version != stub.version_id:
        return handle_stale_form_conflict(
            stale_ctx, submitted=submitted_version, current=stub.version_id,
        )
    ctx = BalanceContext.build(current_user.id)
    figures, printed, errors = _read_form(profile)
    user_id = current_user.id
    if figures is not None:
        try:
            pay_stub_service.edit_stub(
                stub, figures, printed, ctx, display_today(),
            )
            _reprice_after(profile)
            db.session.commit()
        except PayStubRefused as refused:
            db.session.rollback()
            errors = refused.errors
        except NotFoundError:
            db.session.rollback()
            abort(404)
        except StaleDataError:
            return handle_stale_conflict(stale_ctx)
        except SQLAlchemyError:
            return handle_db_error(DbErrorContext(
                logger=logger,
                log_message="user_id=%d failed to edit pay stub %d",
                log_args=(user_id, stub_id),
                flash_message="Failed to save the pay stub. Please try again.",
                redirect=RedirectTarget("salary.view_stub", {"stub_id": stub_id}),
            ))
        else:
            logger.info("user_id=%d edited pay stub %d", user_id, stub_id)
            flash(f"Pay stub for {stub.payday.isoformat()} saved.", "success")
            return redirect(url_for("salary.view_stub", stub_id=stub_id))
    return render_template(
        "salary/stub_form.html",
        **_form_page(profile, ctx, stub.payday, stub),
        **_entry_values(request.form, errors),
        # An edit refused for moving onto a held payday links to that stub.
        held_stub=(
            pay_stub_service.stub_on(profile, figures.payday)
            if figures is not None and figures.payday != stub.payday else None
        ),
    ), 422


@salary_bp.route("/salary/stubs/<int:stub_id>/pricing", methods=["POST"])
@require_owner
def set_stub_pricing(stub_id: int) -> ResponseReturnValue:
    """Turn a stub's "Use for pricing" switch on or off (fork 8a', R-SAL51 (a))."""
    stub = _owned_stub(stub_id)
    profile = stub.salary_profile
    profile_id = profile.id
    wanted = request.form.get("use_for_pricing")
    submitted = parse_row_id(request.form.get("version_id"))
    if wanted not in ("on", "off") or submitted is None:
        abort(400)
    stale_ctx = _stale_ctx("set_stub_pricing", stub_id, to_profile=profile_id)
    if submitted != stub.version_id:
        return handle_stale_form_conflict(
            stale_ctx, submitted=submitted, current=stub.version_id,
        )
    user_id = current_user.id
    if pay_stub_service.set_use_for_pricing(stub, wanted == "on"):
        failed = regenerate_commit_or_report(
            lambda: _reprice_after(profile),
            stale_ctx=stale_ctx,
            error_ctx=DbErrorContext(
                logger=logger,
                log_message="user_id=%d failed to switch pay stub %d's pricing",
                log_args=(user_id, stub_id),
                flash_message="Failed to switch the pay stub. Please try again.",
                redirect=_edit_page(profile_id),
            ),
        )
        if failed is not None:
            return failed
        logger.info(
            "user_id=%d turned pay stub %d pricing %s", user_id, stub_id, wanted,
        )
    state = "is used for pricing" if wanted == "on" else "is kept but not used for pricing"
    flash(f"Your {stub.payday.isoformat()} stub {state}.", "info")
    return _edit_page(profile_id).to_response()


# ── Helpers ────────────────────────────────────────────────────────


def _record_failed(user_id: int, profile_id: int) -> ResponseReturnValue:
    """The record door's generic database failure: roll back, log, flash, redirect."""
    return handle_db_error(DbErrorContext(
        logger=logger,
        log_message="user_id=%d failed to record a pay stub for profile %d",
        log_args=(user_id, profile_id),
        flash_message="Failed to save the pay stub. Please try again.",
        redirect=_edit_page(profile_id),
    ))


def _reprice_after(profile: SalaryProfile) -> None:
    """Regenerate *profile*'s salary rows after a stub write, in its transaction.

    A switched-on stub prices the taxes of the paychecks on or after its
    payday until a later one takes over (plan step salary:S11-c-2c; rulings
    R-SAL42, R-SAL54), so each of this module's writes can change what the
    profile's paychecks are priced at.  ``pay_stubs`` is a
    view-only collection: a recorded stub is not appended to it and an edited
    payday does not re-sort it, so it is expired here and the pricer reloads
    it.  The regeneration's read pass is built here, after the write, rather
    than taken from the door, whose pass priced the profile before it.  The
    regeneration prices nothing since plan step X-bp, so whether it may take
    the door's pass is finding plan#1589's question.

    Args:
        profile: The stub's :class:`SalaryProfile`, its write staged.
    """
    db.session.expire(profile, ["pay_stubs"])
    _regenerate_salary_transactions(BalanceContext.build(current_user.id), profile)


def _owned_stub(stub_id: int) -> PayStub:
    """Return the current user's stub, or 404 (not found and not yours alike)."""
    stub = get_owned_via_parent(PayStub, stub_id, "salary_profile")
    if stub is None:
        abort(404)
    return stub


def _stale_ctx(
    label: str, stub_id: int, *, to_profile: int | None = None,
) -> StaleConflictContext:
    """The stale-conflict report: back to the stub, or to the profile's card."""
    redirect_to = (
        _edit_page(to_profile)
        if to_profile is not None
        else RedirectTarget("salary.view_stub", {"stub_id": stub_id})
    )
    return StaleConflictContext(
        logger=logger, log_label=label, log_id=stub_id,
        flash_message=_STALE_MESSAGE, redirect=redirect_to,
    )


def _first_messages(errors: Mapping[str, list[str]]) -> dict[str, str]:
    """Flatten a marshmallow error map to one message per field."""
    return {field: messages[0] for field, messages in errors.items()}


def _read_form(
    profile: SalaryProfile,
) -> tuple[
    pay_stub_service.StubFigures | None,
    pay_stub_service.PrintedTotals | None,
    dict[str, str],
]:
    """Read the entry form into the service's values, or into field errors.

    The line and tax inputs are read BY THE PROFILE'S OWN LINES AND THE FOUR
    TAXES, never by the keys the form happened to post: an input named for
    another profile's line is simply not read.  A blank input means the stub
    does not print that line or tax; that every tax is present is the
    service's refusal, not this reader's.

    Returns:
        ``(figures, printed, errors)`` -- ``figures`` is a
        :class:`~app.services.pay_stub_service.StubFigures` and ``printed``
        the :class:`~app.services.pay_stub_service.PrintedTotals` the stub's
        gross and net are checked against when every field loaded, else both
        are ``None`` with ``errors`` naming each bad field.
    """
    form = request.form
    errors = {}
    scalars = _load(_stub_schema, form, errors)
    line_amounts = _read_lines(form, profile, errors)
    withholdings = _read_taxes(form, errors)
    one_offs = _read_one_offs(form, errors)
    if errors or scalars is None:
        return None, None, errors
    figures = pay_stub_service.StubFigures(
        payday=scalars["payday"], base_pay=scalars["base_pay"],
        line_amounts=line_amounts, withholdings=withholdings,
        one_offs=one_offs, notes=scalars.get("notes"),
    )
    printed = pay_stub_service.PrintedTotals(
        gross=scalars["printed_gross"], net=scalars["printed_net"],
    )
    return figures, printed, errors


def _read_lines(
    form: MultiDict, profile: SalaryProfile, errors: dict[str, str],
) -> dict[int, pay_stub_service.LineFigure]:
    """``{line id: LineFigure}`` for each of the profile's lines the form filled.

    Each filled line's amount is read with the kind beside it (ruling
    **R-SAL58**): the form pre-sets the kind, so a submission missing it is a
    malformed one and is refused on that field rather than given a kind the
    owner never saw.
    """
    line_amounts = {}
    for line in profile.lines:
        field = f"line-{line.id}"
        raw = form.get(field, "").strip()
        if not raw:
            continue
        kind_field = f"line-kind-{line.id}"
        try:
            loaded = _line_schema.load(
                {"amount": raw, "paycheck_line_kind_id": form.get(kind_field, "")},
            )
        except SchemaValidationError as exc:
            messages = _first_messages(exc.messages)
            if "amount" in messages:
                errors[field] = messages["amount"]
            if "paycheck_line_kind_id" in messages:
                errors[kind_field] = messages["paycheck_line_kind_id"]
            continue
        line_amounts[line.id] = pay_stub_service.LineFigure(
            loaded["paycheck_line_kind_id"], loaded["amount"],
        )
    return line_amounts


def _read_taxes(form: MultiDict, errors: dict[str, str]) -> dict[int, Decimal]:
    """``{withholding kind id: amount}`` for each tax the form filled."""
    withholdings = {}
    for kind_id, _ in withholding_kinds.kind_options():
        field = f"tax-{kind_id}"
        raw = form.get(field, "").strip()
        if not raw:
            continue
        amount = _load_figure(field, raw, errors)
        if amount is not None:
            withholdings[kind_id] = amount
    return withholdings


def _read_one_offs(
    form: MultiDict, errors: dict[str, str],
) -> tuple[pay_stub_service.OneOffFigure, ...]:
    """The FILLED one-off rows, loaded; a row's error files as ``one_off:<index>``.

    The index counts filled rows only -- the numbering the service's refusals
    use (its index into ``StubFigures.one_offs``) and the one the re-rendered
    form lists them in -- so an error lands on the row it is about.
    """
    one_offs = []
    for index, row in enumerate(_filled_one_off_rows(form)):
        loaded = _load(
            _one_off_schema,
            {"name": row["one_off_name"], "paycheck_line_kind_id": row["one_off_kind"],
             "amount": row["one_off_amount"].strip()},
            errors, key=f"one_off:{index}",
        )
        if loaded is not None:
            one_offs.append(pay_stub_service.OneOffFigure(
                loaded["name"], loaded["paycheck_line_kind_id"], loaded["amount"],
            ))
    return tuple(one_offs)


def _load(
    schema: Schema, data: Mapping[str, Any], errors: dict[str, str], *,
    key: str | None = None,
) -> dict[str, Any] | None:
    """Load *data* through *schema*; on failure record its messages and return ``None``.

    Args:
        schema: The schema instance.
        data: The mapping to load.
        errors: The field-error map to add to.
        key: One key to file every message under (a one-off ROW), or ``None``
            to file each under its own field.
    """
    try:
        return schema.load(data)
    except SchemaValidationError as exc:
        messages = _first_messages(exc.messages)
        if key is None:
            errors.update(messages)
        else:
            errors[key] = "; ".join(
                f"{_ONE_OFF_LABELS.get(field, field)}: {text}"
                for field, text in messages.items()
            )
        return None


def _load_figure(field: str, raw: str, errors: dict[str, str]) -> Decimal | None:
    """Load one stub figure; on failure file its message under *field*."""
    try:
        return _figure_schema.load({"amount": raw})["amount"]
    except SchemaValidationError as exc:
        errors[field] = _first_messages(exc.messages)["amount"]
        return None


def _filled_one_off_rows(form: MultiDict) -> list[dict[str, str]]:
    """The one-off rows as posted, blank rows dropped: one dict per row."""
    columns = [form.getlist(name) for name in _ONE_OFF_FIELDS]
    count = max((len(column) for column in columns), default=0)
    rows = [
        {name: (column[index] if index < len(column) else "")
         for name, column in zip(_ONE_OFF_FIELDS, columns)}
        for index in range(count)
    ]
    return [row for row in rows if any(value.strip() for value in row.values())]


def _posted_payday(figures: pay_stub_service.StubFigures | None) -> date | None:
    """The payday a refused form was FOR: the loaded one, else the posted text."""
    if figures is not None:
        return figures.payday
    try:
        return _payday_schema.load(request.form)["payday"]
    except SchemaValidationError:
        return None


def _values_of(figures: pay_stub_service.StubFigures) -> dict[str, Any]:
    """A saved stub's figures as the form's field values (strings)."""
    values = {
        "payday": figures.payday.isoformat(),
        "base_pay": f"{figures.base_pay:.2f}",
        "notes": figures.notes or "",
    }
    for line_id, line in figures.line_amounts.items():
        values[f"line-{line_id}"] = f"{line.amount:.2f}"
        values[f"line-kind-{line_id}"] = str(line.paycheck_line_kind_id)
    for kind_id, amount in figures.withholdings.items():
        values[f"tax-{kind_id}"] = f"{amount:.2f}"
    values["one_offs"] = [
        {"one_off_name": one_off.name, "one_off_kind": str(one_off.paycheck_line_kind_id),
         "one_off_amount": f"{one_off.amount:.2f}"}
        for one_off in figures.one_offs
    ]
    return values


def _form_page(
    profile: SalaryProfile, ctx: BalanceContext, payday: date | None,
    stub: PayStub | None = None,
) -> dict[str, Any]:
    """The stub form's page context: the line split, the report, the options.

    ``gross_counts`` words which earnings the printed-gross check adds on this
    job (ruling **R-SAL102**), the phrase the check's own refusals use.

    Args:
        profile: The owned profile.
        ctx: The request's :class:`~app.services.balance_at.BalanceContext`.
        payday: The payday the form's line split is for, or ``None`` when a
            refused form's date did not load (every line is then "not taken").
        stub: The saved stub being edited, or ``None`` for a new one.
    """
    lines = (
        pay_stub_service.form_lines(profile, ctx, payday) if payday is not None
        else pay_stub_service.FormLines(taken=(), not_taken=tuple(profile.lines))
    )
    return {
        "profile": profile,
        "stub": stub,
        "report": (
            pay_stub_service.stub_report(profile, stub, ctx) if stub is not None else None
        ),
        "lines": lines,
        "kind_options": paycheck_line_kinds.kind_options(),
        "tax_options": withholding_kinds.kind_options(),
        "gross_counts": pay_stub_gross.gross_counts(
            profile.stub_gross_includes_after_tax,
        ),
    }


def _entry_values(
    values: Mapping[str, Any], errors: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The stub form's field values, its one-off rows and its errors.

    Args:
        values: A saved stub's values (:func:`_values_of`), the posted form, or
            a new stub's payday alone.
        errors: ``{field or "one_off:<i>": message}``.
    """
    if hasattr(values, "getlist"):
        rows = _filled_one_off_rows(values)
    else:
        rows = list(values.get("one_offs", []))
    rows += [{name: "" for name in _ONE_OFF_FIELDS} for _ in range(_BLANK_ONE_OFF_ROWS)]
    return {"values": values, "one_off_rows": rows, "errors": errors or {}}
