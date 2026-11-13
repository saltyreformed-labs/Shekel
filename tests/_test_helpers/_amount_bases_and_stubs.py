"""Amounts and pay stubs: a rendered transfer amount, a row's amount basis, matched bank lines, a
write past the amount seam, and paycheck lines and pay stubs.
"""

from decimal import Decimal

from ._salary_and_entries import make_salary_profile
from ._prices_and_rules import make_line_cadence_rule


def rendered_transfer_amount(xfer) -> str:
    """Return what the transfer FORM would have rendered in its amount box.

    **What a route test must post as ``amount_as_rendered``**, and the ONE
    spelling of it, because getting this wrong is how a route test stops
    exercising the door it names.  The three transfer fragments read
    ``budgets[xfer.id]`` off ``routes._render_helpers.transfer_budgets`` since
    plan step balance:X-au-f-1 -- the amount model's answer, not the
    ``budget.transfers.amount`` COLUMN -- so a payload built from that column
    stopped being what a browser sends the moment X-au-f emptied it for a
    generated transfer.

    Sixteen call sites posted ``str(xfer.amount)`` until X-au-f, which was the
    same string while every transfer owned its figure and became the literal
    ``"None"`` the day the cutover landed.  ``TransferUpdateSchema`` then
    refuses the field and the PATCH returns 422, so every one of those cases
    would have been asserting against a rejected request rather than against
    the authorship rule it was written for -- the shape
    ``feedback_a_route_test_must_post_what_the_template_emits`` names.

    Args:
        xfer: The transfer whose form is being simulated.

    Returns:
        The rendered figure as the form's string, ready to post.
    """
    from app.services.cash_ledger import (  # pylint: disable=import-outside-toplevel
        derived_amount_basis, resolve_transfer_amount,
    )

    return str(resolve_transfer_amount(
        xfer, derived_amount_basis(xfer.user_id, xfer.scenario_id),
    ))


def amount_basis_for(row):
    """Return the :class:`AmountBasis` that prices ONE row, for a test.

    **Named for the thing it builds, because ``basis_for`` was already taken**
    by the ``ProjectedBasis`` helper in ``_read_passes.py`` -- appending a
    second definition of that name silently shadowed it and took 63 tests down
    with a ``TypeError``.  Two different "bases" live in this suite and neither may
    answer to the bare word.

    **The single-row form of what a read pass holds** (plan step X-au-j).  Both
    ``settle_amount`` twins take their basis as a REQUIRED parameter, because
    their two live callers are PASSES and an optional one would leave the
    expensive shape as what a caller gets by saying nothing -- which is how
    findings **N-295** and **N-309** grew back one tier up after plan step
    X-au-c2b closed the same cost within a call.

    A test valuing one row is the caller that legitimately holds no pass, so it
    builds one for that row and says so.  Shared here rather than spelled at
    each of the sites that need it, which is this project's DRY rule; it stays
    EXPLICIT at every call (``settle_amount(txn, amount_basis_for(txn))``), so the
    derivation being built is still visible where it is paid for.

    Args:
        row: The :class:`~app.models.transaction.Transaction` being valued.
            Its ``account.user_id`` and ``scenario_id`` are what a basis is
            pinned to.

    Returns:
        The :class:`~app.services.cash_ledger.AmountBasis` for that row's owner
        and scenario.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app or ORM
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services.cash_ledger import derived_amount_basis

    return derived_amount_basis(row.account.user_id, row.scenario_id)


def count_amount_bases(monkeypatch):
    """Return a list that records every ``AmountBasis`` CONSTRUCTION.

    **It counts the producer, not the factory, and that difference is the
    whole instrument** (plan step X-au-j).  A first version of this control
    patched ``amount_basis`` on the modules that import it and PASSED against a
    planted defect: every module does ``from app.services.cash_ledger import
    amount_basis``, which binds the function by value at import time, so
    patching a name in one module says nothing about the copy another module
    already holds -- and the arm rebuilding its caller's basis was invisible.

    ``amount_basis`` calls ``income_service.salary_pricing`` unconditionally on
    every construction, through a module ATTRIBUTE resolved at call time, so a
    patch here is seen no matter which module reached the factory -- and
    ``derived_amount_basis``, the pass-less constructor since plan step
    salary:C12, reaches it through ``amount_basis``, so both doors count.  One
    entry per basis built, by anybody.  The owner is read off the pricer the
    basis is built over, which costs no derivation.

    Args:
        monkeypatch: The test's ``monkeypatch`` fixture.

    Returns:
        The list the counter appends ``(user_id, scenario_id)`` to.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app or ORM
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services import income_service

    built = []
    real = income_service.salary_pricing

    def _counted(scenario_id, paychecks):
        built.append((paychecks.user_id, scenario_id))
        return real(scenario_id, paychecks)

    monkeypatch.setattr(income_service, "salary_pricing", _counted)
    return built


#: One import, two bank lines it sighted (plan step ``bank_import:X-f6b-1``:
#: a line is held by its sightings, and the wording is the sighting's) and
#: one match naming BOTH, in the shape ``statement_match._accept.record_match``
#: leaves: the group's EARLIEST line posts before the row that explains it
#: settles.
#:
#: **Raw SQL, and ONE copy of it.**  It lived in three test modules
#: byte-identically until an adversarial test-quality review counted them --
#: and the query has to agree with
#: :data:`app.opening_infrastructure.MATCHED_LINE_DAYS_SQL`'s row set, so
#: three copies were three places for that to drift silently.  That is this
#: arc's own stated root cause, in its own test suite.
#:
#: It is raw because what the cases using it grade IS a database trigger and a
#: raw-SQL reader; a case grading a DOOR should build its match through
#: ``accept_match`` instead -- what the DOOR leaves is not what this
#: builds, and a case grading a door has never seen the row shape a
#: door produces if it uses this.
_A_MATCHED_GROUP = """
    WITH import_row AS (
        INSERT INTO budget.statement_imports
               (account_id, user_id, source_id, file_name, file_digest,
                declared_start, declared_end)
        SELECT :a, :u,
               (SELECT id FROM ref.statement_sources ORDER BY id LIMIT 1),
               'books-boundary-probe.csv', :digest, :early, :late
        RETURNING id
    ), line_rows AS (
        INSERT INTO budget.bank_statement_lines
               (account_id, posted_on, amount, sequence_in_group)
        SELECT :a, day.posted_on, -15.96, 0
          FROM (VALUES (:early), (:late)) AS day(posted_on)
        RETURNING id
    ), sighting_rows AS (
        INSERT INTO budget.statement_line_sightings
               (account_id, line_id, import_id, description)
        SELECT :a, line_rows.id, import_row.id, 'PROBE'
          FROM import_row, line_rows
    ), match_row AS (
        INSERT INTO budget.statement_matches
               (account_id, user_id, applied_by_rule)
        VALUES (:a, :u, false)
        RETURNING id
    )
    INSERT INTO budget.statement_match_members
           (match_id, account_id, bank_statement_line_id)
    SELECT match_row.id, :a, line_rows.id FROM match_row, line_rows
"""


def match_two_lines(db_session, account, owner_id, early, late):
    """Match two bank lines on *account*, posted *early* and *late*.

    The state plan step **balance:X-f3c-2b-2b**'s matched-line bound is about:
    a group whose earliest line posts strictly before the row explaining it
    settles, which is every multi-day match.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` to match on.
        owner_id: Its owner.
        early: The earlier line's posting day.
        late: The later one's.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep avoidance
    # as the loan helpers in ``_loans.py``.
    import sqlalchemy as _sa

    db_session.execute(_sa.text(_A_MATCHED_GROUP), {
        "a": account.id,
        "u": owner_id,
        "digest": f"probe-{account.id}-{early}",
        "early": early,
        "late": late,
    })
    db_session.commit()


def write_past_the_amount_seam(row, value):
    """Write *row*'s figure column DIRECTLY, bypassing ``amount_ownership``.

    **The only supported way for a test to construct a state the mapping
    refuses** (plan step **X-au-k**), and it exists because the obvious
    spelling is a trap.  ``Transaction.__estimated_amount`` and
    ``Transfer.__amount`` are double-underscored, so Python mangles them and a
    hand-written ``row._estimated_amount = x`` binds a plain instance attribute
    that reaches no column -- a SILENT no-op, which is the right failure for a
    typo in application code and the wrong one for a control that means to
    write.

    Its callers (read 2026-10-20): in ``test_amount_source.py``'s
    ``TestTheRulesDoNotReadTheColumnTheyReplace``, the two cases that prove a
    derived answer is invariant under a rival figure (a definition's row
    under its own column, a generated transfer's leg under its parent's) and
    the one that proves the database refuses a declared row carrying one; in
    ``test_a_transfers_amount_is_declared/test_the_declaration.py``,
    ``test_a_leg_cannot_hold_a_figure_while_it_declares_a_parent``, which
    proves the database refuses a transfer's twin holding a figure; and the
    hand-run ``tests/manual/verify_amount_resolver.py``.  Each is constructing
    the shape :class:`~app.models.amount_ownership.AmountOwnership` has no
    member for, so none of them can go through the seam.

    Args:
        row: A :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer`.
        value: The figure to write, or ``None`` to empty the column.

    Raises:
        AttributeError: When *row*'s model maps no such private column, which
            is what stops this helper from silently binding a new attribute if
            a later step renames one.
    """
    attr = f"_{type(row).__name__}__{_figure_column_of(row)}"
    if attr not in type(row).__mapper__.attrs:
        raise AttributeError(
            f"{type(row).__name__} maps no {attr!r}; the amount seam's private "
            "column was renamed and this helper would have bound a plain "
            "attribute that writes nothing"
        )
    setattr(row, attr, value)


def _figure_column_of(row) -> str:
    """Return the column *row*'s table stores an owned figure in.

    Args:
        row: A ``Transaction`` or ``Transfer``.

    Returns:
        ``"estimated_amount"`` or ``"amount"``.

    Raises:
        AttributeError: When *row* is neither.
    """
    for column in ("estimated_amount", "amount"):
        if column in type(row).__table__.c:
            return column
    raise AttributeError(f"{type(row).__name__} carries no figure column")


def make_flat_paycheck_line(profile, name, amount, kind):
    """Build, add and flush one FLAT paycheck line of *kind* on *profile*.

    Args:
        profile: The owning :class:`~app.models.salary_profile.SalaryProfile`.
        name: The line's name.
        amount: Its per-paycheck amount, as a string.
        kind: A :class:`~app.enums.PaycheckLineKindEnum` member.

    Returns:
        The flushed :class:`~app.models.paycheck_line.PaycheckLine`.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers in ``_loans.py``.
    from app import ref_cache
    from app.enums import CalcMethodEnum
    from app.extensions import db
    from app.models.paycheck_line import PaycheckLine

    line = PaycheckLine(
        salary_profile=profile,
        paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(kind),
        calc_method_id=ref_cache.calc_method_id(CalcMethodEnum.FLAT),
        name=name,
        amount=Decimal(amount),
    )
    db.session.add(line)
    db.session.flush()
    return line


def build_pay_stub_world(owner):
    """The pay stub entry door's worked example (plan step salary:S11-b), committed.

    A ``$75,000.00`` profile of *owner* (base pay ``$2,884.62`` at 26 a year)
    with five flat lines: Health Insurance ``$280.00`` and Vision ``$12.00``
    (pre-tax, every paycheck), Dental ``$35.00`` (pre-tax, 12 a year: a
    month's first paycheck), Roth IRA ``$100.00`` (post-tax) and Phone
    Allowance ``$60.00`` (taxable earning).  Both of the door's suites
    (``test_pay_stub_service.py``, ``test_salary_stubs.py``) price their
    worked example over it; the owner needs pay periods for the Dental rule.

    Args:
        owner: The ``seed_user``-shaped fixture dict.

    Returns:
        ``(profile, {"health", "vision", "dental", "roth", "phone": line})``.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers in ``_loans.py``.
    from app.enums import PaycheckLineKindEnum
    from app.extensions import db

    profile = make_salary_profile(owner, db.session, name="Day Job")
    db.session.flush()
    pre_tax = PaycheckLineKindEnum.PRE_TAX_DEDUCTION
    lines = {
        "health": make_flat_paycheck_line(profile, "Health Insurance", "280.00", pre_tax),
        "vision": make_flat_paycheck_line(profile, "Vision", "12.00", pre_tax),
        "dental": make_flat_paycheck_line(profile, "Dental", "35.00", pre_tax),
        "roth": make_flat_paycheck_line(
            profile, "Roth IRA", "100.00", PaycheckLineKindEnum.POST_TAX_DEDUCTION,
        ),
        "phone": make_flat_paycheck_line(
            profile, "Phone Allowance", "60.00", PaycheckLineKindEnum.TAXABLE_EARNING,
        ),
    }
    make_line_cadence_rule(db.session, lines["dental"], 12)
    db.session.commit()
    return profile, lines


def add_test_pay_stub(profile, payday, base_pay, taxes, lines=()):
    """Seed and flush one transcribed pay stub of *profile* (plan step salary:S11-c-2c).

    The paycheck engine prices a paycheck's four taxes from a switched-on stub
    on or before it (``paycheck_calculator._stubs``); this writes the rows that
    pricing reads, around the entry door, so a case can seed a stub the door
    would refuse (one missing a tax) and states every figure itself.  A case
    wanting a one-off or the switch off sets it on the returned stub.

    Args:
        profile: The owning :class:`~app.models.salary_profile.SalaryProfile`.
        payday: The stub's date.
        base_pay: Its base pay, as a string.
        taxes: ``{WithholdingKindEnum member: amount string}``; a member left
            out records no figure for that tax.
        lines: ``(paycheck_line, amount string)`` pairs, each recorded under
            the line's own kind, or ``(paycheck_line, amount string, kind
            member)`` to record it under another (ruling **R-SAL58**).

    Returns:
        The flushed :class:`~app.models.pay_stub.PayStub`, switched on.  The
        profile's ``pay_stubs`` is expired, so the next read sees it.
    """
    # Pylint: ``import-outside-toplevel`` -- the circular-dependency avoidance
    # the loan helpers in ``_loans.py`` state: this module loads before the
    # app's models.
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.extensions import db
    from app.models.pay_stub import PayStub, PayStubWithholding

    stub = PayStub(
        salary_profile_id=profile.id, payday=payday, base_pay=Decimal(base_pay),
        withholdings=[
            PayStubWithholding(
                withholding_kind_id=ref_cache.withholding_kind_id(member),
                amount=Decimal(amount),
            )
            for member, amount in taxes.items()
        ],
        line_amounts=[_stub_line_amount(entry) for entry in lines],
    )
    db.session.add(stub)
    db.session.flush()
    db.session.expire(profile, ["pay_stubs"])
    return stub


def _stub_line_amount(entry):
    """One :func:`add_test_pay_stub` line entry as its ``PayStubLineAmount`` row."""
    # Pylint: ``import-outside-toplevel`` -- as :func:`add_test_pay_stub`.
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.models.pay_stub import PayStubLineAmount

    line, amount = entry[0], entry[1]
    return PayStubLineAmount(
        paycheck_line_id=line.id,
        paycheck_line_kind_id=(
            ref_cache.paycheck_line_kind_id(entry[2]) if len(entry) > 2
            else line.paycheck_line_kind_id
        ),
        amount=Decimal(amount),
    )
