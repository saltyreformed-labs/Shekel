"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_no_baseline_policy.py``, moved verbatim by ``tools/split``.
"""

import re
from datetime import timedelta
from decimal import Decimal

from flask import Flask

import app as shekel_app_package
from app import ref_cache
from app.enums import WithholdingKindEnum
from app.models.account import Account
from app.models.credit_card_params import CreditCardParams
from app.models.pay_stub import PayStub, PayStubWithholding
from app.models.pension_profile import PensionProfile
from app.models.ref import AccountType
from app.models.savings_goal import SavingsGoal
from app.models.scenario import Scenario
from app.models.transfer_template import TransferTemplate
from app.services import account_service
from app.url_converters import register_url_converters
from app.utils.dates import display_today
from tests._test_helpers import (
    add_txn,
    bare_expense_template,
    create_hysa_account,
    create_loan_account,
    create_transfer,
    make_appreciating_account,
    make_investment_account,
    make_salary_profile,
    state_template_price,
)
from tests._seed_owner_fixtures import build_periods_today, build_seed_user
from tests._seeded_start_states import register_seeded_state


#: The PAGE endpoints measured to raise before plan step X-v1 shipped its
#: handler, with the door each reaches the seam through.  Named individually
#: because the sweep below proves only "nothing 5xx"; these prove the ANSWER is
#: the repair card, which is the part a future refactor could quietly turn back
#: into a 404 or an empty page while the sweep stayed green.
_MEASURED_PAGE_DOORS = [
    # routes/loan/_helpers._load_route_context -> _require_figures -> loan_figures
    ("loan dashboard", "/accounts/{loan}/loan"),
    ("mortgage dashboard", "/accounts/{mortgage}/loan"),
    # home_equity_service.resolve_home_equity -> loan_figures
    ("property detail", "/accounts/{property}/property"),
    # debt_strategy._load_debt_accounts -> balance_at
    ("debt strategy", "/debt-strategy"),
]


#: The two FRAGMENT endpoints on the same loan door.  They are listed apart
#: because a plain GET never reaches the seam through them at all: both redirect
#: a non-HTMX request to the loan page before resolving anything
#: (``dashboard.py:540-541`` and ``:568-569``), which is why they take the
#: 204 arm only.  The first
#: draft of this gate asserted the card for all six and FAILED on exactly these
#: two -- kept as a comment rather than deleted, because it is the reason the
#: split exists.
_MEASURED_FRAGMENT_DOORS = [
    ("loan anchor form", "/accounts/{loan}/loan/anchor-form"),
    ("loan balance hero", "/accounts/{loan}/loan/balance-hero"),
]


#: Every measured door, for the arm that grades the HTMX answer -- which is the
#: same for a page endpoint and a fragment endpoint.
_MEASURED_DOORS = _MEASURED_PAGE_DOORS + _MEASURED_FRAGMENT_DOORS


#: The account kinds every ``<int:account_id>`` route is swept against.  The
#: kind decides which PRODUCER runs, which is why one id would not do: the loan
#: doors are unreachable with a checking id and the property door needs the
#: Property.
_ACCOUNT_KINDS = (
    "checking", "loan", "mortgage", "property", "hysa", "card", "invest",
)


#: The converter that fans a rule OUT rather than filling it once: an account
#: id is one case per kind, never one row.
_ACCOUNT_CONVERTER = "account_id"


#: One ``<converter:name>``, ``<converter(arg=1):name>`` or ``<name>`` token in
#: a rule string, capturing the VARIABLE name.  Werkzeug puts the converter
#: before the colon and the name after it.
#:
#: **It does not match every token Werkzeug accepts**, and that is why
#: ``"<" not in url`` is asserted beside ``unfilled`` in the case below rather
#: than trusted to be implied by it: ``werkzeug.routing.rules._part_re`` admits
#: a converter ARGUMENT containing a colon (``<any("a:b"):x>``), which this
#: refuses to parse.  Such a token is left in the path, and that assertion is
#: what catches it -- loudly -- instead of a 404 passing a 5xx sweep.
_CONVERTER_TOKEN = re.compile(r"<(?:[^<>:]+:)?([^<>:]+)>")


#: What fills every converter but the account axis: ``(blueprint, name)`` ->
#: the key into the world dict holding a row of exactly that kind.  **It is
#: TOTAL over the GET route table and graded BOTH WAYS against it**, which is
#: what replaced the ``_UNREACHED_RULES`` allowlist this file carried until
#: plan step balance:X-be-3 (finding **N-388**).  Seventeen GET rules -- 17.5%
#: of the app's 97 -- were pinned in that list and graded by NOBODY: a rule
#: whose converter the fixture could not fill was skipped, and the sweep's own
#: coverage test asserted the skip list rather than closing it, so the gap was
#: pinned rather than measured away.
#:
#: **There is no skip branch now.**  A rule carrying a pair absent from this
#: map turns the coverage test red and its own case red, so the only repair
#: available is to build the row, never to widen a list of exclusions.
#:
#: **A URL variable's identity is ``(blueprint, name)``, and this key is that
#: pair with no fallback to the bare name.**  Flask scopes a variable to its
#: view and the app uses that scope: censused 2026-08-30 over all 218 rules,
#: FIVE names are bound under more than one blueprint, and two of the five name
#: more than one TABLE -- ``template_id`` is a transaction template under
#: ``templates`` and a transfer template under ``transfers``, and
#: ``version_id`` is an escrow component version under ``loan`` where it is a
#: template amount version under the other two.  ``version_id``'s four rules
#: are POST-only today, so it is invisible here; a bare-name key with a
#: fallback would have filled the first GET among them from whichever table
#: was written down first, and while eight of the eleven rows it handed out
#: on 2026-08-30 carried id 1 that fill produced a VALID URL for the wrong row
#: and passed.  A total map cannot express that mistake without an author writing
#: the wrong pair down.
#:
#: **That a filled VALUE is the right KIND is checked by the ids, not by this
#: map.**  Since ruling ``balance:R-BAL356`` no two of the rows share an id, so
#: filling ``("transfers", "template_id")`` from the transaction template
#: requests an id no transfer template holds, the door answers 404, and
#: :func:`_is_answered_by_its_own_row` refuses it (measured 2026-10-22, both
#: directions of that pair).  Measured 2026-08-30, the eight pairs whose row
#: was the first of its table emitted byte-identical URLs
#: (``/transfers/1/edit``), and only a swap involving a ``period_id`` pair
#: (``period`` was id 6) was caught.
#: The two doors reading two different tables is ALSO graded on the doors, in each
#: blueprint's own ``TestTemplateUpdate::test_edit_template_form`` --
#: ``tests/test_routes/test_templates/test_the_edit_form_the_chooser_and_renames.py``
#: and ``test_transfers/test_templates.py``.  **The
#: two are not equally strong and the difference was measured** (2026-08-30):
#: the transfers one asserts ``b"Monthly Savings"``, which only its own row
#: supplies, while the templates one asserts ``b"Rent"``, which its fixture
#: uses for BOTH the template's name and its category -- and the category
#: ``<option>`` renders that string whatever the template is called.  What the
#: templates side really pins is that the door resolves a row in
#: ``transaction_templates`` at all: repointing it at ``transfer_templates``
#: 404s there, since that test creates none.
_CONVERTER_ROWS = {
    ("card", "row_id"): "card_terms",
    ("companion", "period_id"): "period",
    ("entries", "txn_id"): "transaction",
    ("retirement", "pension_id"): "pension",
    ("salary", "period_id"): "period",
    ("salary", "profile_id"): "salary_profile",
    ("salary", "stub_id"): "pay_stub",
    ("savings", "goal_id"): "goal",
    ("templates", "template_id"): "transaction_template",
    ("transactions", "period_id"): "period",
    ("transactions", "txn_id"): "transaction",
    ("transfers", "template_id"): "transfer_template",
    ("transfers", "xfer_id"): "transfer",
}


def _build_baseline_less_owner(db):
    """An OWNER holding every account kind, with the baseline removed.

    **Built ONCE per xdist worker and frozen into a snapshot database** (plan
    step balance:X-be-2, finding **N-387**); every test that declares this
    world still gets its own private clone of that snapshot, so per-test
    isolation is exactly what it was.  Once per WORKER, not once per run: the
    253 cases scatter across ``-n 12``, so the world is built up to twelve
    times a session rather than 253 times a worker.

    As a per-test fixture this cost 302 ms of setup for 22 ms of requests,
    236 times over; the file went 82.26 s -> 18.73 s serially.  The full split,
    and what of it this does NOT remove, is stated once in the seeded-start-
    state block comment in ``tests/_seeded_start_states.py``.

    Every kind is present deliberately: the door that 500'd the property page
    is reachable only through a Property that SECURES a configured loan, and
    X-t5 already paid for a fixture that could not tell -- its Property fixture
    set ``secured_by_account_id``, which is not a field, so SQLAlchemy accepted
    the assignment in silence and the control exercised nothing.  The link here
    is ``collateral_account_id``, and the sweep asserts the property page's
    answer rather than only its status.

    It returns IDS and no ORM objects, which the seam requires: the dict is
    computed once, at build time, and handed to every test that declares the
    world, so an object in it would be bound to a session that closed with the
    build.

    Args:
        db: The Flask-SQLAlchemy extension to write the world through.

    Returns:
        A dict of the row ids each parametrised URL and assertion needs.
    """
    seed_user = build_seed_user(db)
    periods = build_periods_today(db, seed_user)
    user = seed_user["user"]
    anchor = periods[0]

    loan = create_loan_account(
        seed_user, db.session, name="Policy Loan",
        principal=Decimal("20000.00"), term=60,
        origination_date=display_today() - timedelta(days=400),
    )
    mortgage = create_loan_account(
        seed_user, db.session, name="Policy Mortgage",
        principal=Decimal("300000.00"), term=360,
        origination_date=display_today() - timedelta(days=800),
    )
    prop = make_appreciating_account(
        seed_user, db.session, anchor, Decimal("400000.00"),
        Decimal("0.03000"),
    )
    # ``collateral_account_id`` is the real column; assigning any other
    # name would leave the loan unsecured and the property door unexercised.
    db.session.get(Account, mortgage.id).collateral_account_id = prop.id

    hysa = create_hysa_account(
        seed_user, db.session, anchor, Decimal("5000.00"),
    )
    card_type = (
        db.session.query(AccountType).filter_by(name="Credit Card").one()
    )
    card = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id, account_type_id=card_type.id,
            name="Policy Card", anchor_balance=Decimal("-500.00"),
        ),
    )
    invest = make_investment_account(
        seed_user, db.session, anchor, Decimal("30000.00"),
        name="Policy 401k",
    )

    # **One row of every kind a GET rule takes an id for** (plan step
    # balance:X-be-3, finding **N-388**).  Until these existed, seventeen GET
    # rules -- every ``<int:profile_id>``, ``<int:txn_id>``, ``<int:xfer_id>``,
    # ``<int:template_id>``, ``<int:goal_id>`` and ``<int:pension_id>`` route
    # in the app -- could not be requested at all, so the sweep pinned them in
    # an allowlist and graded none of them.
    #
    # FOUR go through the factory the ordinary fixtures use -- the salary
    # profile, the transaction template, the transaction and the transfer --
    # so the world describes the same rows the rest of the suite does rather
    # than a second definition of them.  THREE do not: `PensionProfile`,
    # `SavingsGoal` and `TransferTemplate` are constructed here, which is what
    # every other suite that needs one does (12 files and 7 files
    # respectively).  `make_transfer_template` exists and is bypassed on
    # purpose: it fixes the name and attaches an every-period rule, and this
    # world wants neither.  Extracting the other two into factories is a
    # suite-wide change and is not this step's.
    #
    # Every one is written BEFORE the baseline is taken away, because that is
    # the order production reaches this state in: rows exist, and then the
    # scenario stops being the baseline.  Building them after would exercise
    # the writers under a state no owner has, which is a different subject.
    salary_profile = make_salary_profile(
        seed_user, db.session, name="Policy Salary",
    )
    db.session.flush()
    pension = PensionProfile(
        user_id=user.id,
        salary_profile_id=salary_profile.id,
        name="Policy Pension",
        benefit_multiplier=Decimal("0.01850"),
        consecutive_high_years=4,
        hire_date=display_today() - timedelta(days=3650),
        planned_retirement_date=display_today() + timedelta(days=7300),
    )
    db.session.add(pension)
    goal = SavingsGoal(
        user_id=user.id,
        account_id=hysa.id,
        name="Policy Goal",
        target_amount=Decimal("10000.00"),
    )
    db.session.add(goal)
    # The two ``<int:template_id>`` rows, which are two different tables: a
    # TRANSACTION template under ``/templates`` and a TRANSFER template under
    # ``/transfers``.  Both are here because both doors are graded, and
    # `_CONVERTER_ROWS` keys them apart by blueprint.
    txn_template = bare_expense_template(
        db.session, seed_user, name="Policy Expense Definition",
    )
    xfer_template = TransferTemplate(
        user_id=user.id,
        from_account_id=seed_user["account"].id,
        to_account_id=hysa.id,
        name="Policy Transfer Definition",
    )
    db.session.add(xfer_template)
    # A transcribed pay stub (plan step salary:S11-b), for
    # ``/salary/stubs/<int:stub_id>``: dated on the world's period-4 payday and
    # constructed here as the pension and the goal are -- the entry door's
    # payday and net refusals are its own suites' subject, not this sweep's.
    # It records the four taxes the door requires of every stub: since plan
    # step salary:S11-c-2c a switched-on stub PRICES its profile's paychecks
    # (ruling R-SAL42), and the engine refuses one missing a tax rather than
    # price it, so the stubless shape this sweep seeded until then would 500
    # every page that prices the profile.  Made-up figures.
    pay_stub = PayStub(
        salary_profile_id=salary_profile.id, payday=periods[4].start_date,
        base_pay=Decimal("2884.62"),
        withholdings=[
            PayStubWithholding(
                withholding_kind_id=ref_cache.withholding_kind_id(member),
                amount=Decimal(amount),
            )
            for member, amount in (
                (WithholdingKindEnum.FEDERAL_INCOME, "250.00"),
                (WithholdingKindEnum.STATE_INCOME, "110.00"),
                (WithholdingKindEnum.SOCIAL_SECURITY, "178.85"),
                (WithholdingKindEnum.MEDICARE, "41.83"),
            )
        ],
    )
    db.session.add(pay_stub)
    # A version of the card's terms (plan#2331), for
    # ``/accounts/<int:account_id>/card/terms/<int:row_id>/edit``: constructed
    # here as the pension and the goal are.  It makes the world's card a
    # configured one, so the ``card`` kind reaches the card-gated doors'
    # producers where a dormant card's 404 would measure none.  Any date in the
    # calendar window serves: a card with one version derives the same terms on
    # every day.  Made-up terms.
    card_terms = CreditCardParams(
        account_id=card.id, user_id=user.id, effective_date=anchor.start_date,
        statement_close_day=20, payment_due_day=15,
        min_payment_percent=Decimal("0.0200"),
        min_payment_floor=Decimal("25.00"), cashback_rate=Decimal("0.0150"),
    )
    db.session.add(card_terms)
    # A PROJECTED instance of each movement kind.  Projected rather than
    # settled deliberately: a settled row carries a settle day, and the edit
    # doors this reaches (`/transactions/<id>/full-edit`,
    # `/transfers/<id>/full-edit`) render the correction controls a projection
    # does not have -- so a settled row would grade a wider surface while
    # putting the world's cash where no arm here asserts it.  The wider surface
    # is worth grading and is not this step's subject.
    transaction = add_txn(
        db.session, seed_user, anchor, "Policy Transaction", Decimal("75.00"),
        category_key="Groceries",
    )
    transfer = create_transfer(
        seed_user, db.session, seed_user["account"], hysa, anchor,
        Decimal("50.00"), name="Policy Transfer",
    )
    db.session.flush()
    # The transfer definition's price, through the one write door as its
    # create route states it: ``/templates`` prices a definition by its series
    # since plan step X-bp, and rule 3 needs no baseline to do it.
    state_template_price(xfer_template, Decimal("200.00"))

    scenario = db.session.get(Scenario, seed_user["scenario"].id)
    scenario.is_baseline = False
    db.session.commit()

    return {
        "user_id": user.id,
        "checking": seed_user["account"].id, "loan": loan.id,
        "mortgage": mortgage.id, "property": prop.id, "hysa": hysa.id,
        "card": card.id, "invest": invest.id,
        "period": periods[4].id,
        "salary_profile": salary_profile.id,
        "pay_stub": pay_stub.id,
        "card_terms": card_terms.id,
        "pension": pension.id,
        "goal": goal.id,
        "transaction_template": txn_template.id,
        "transfer_template": xfer_template.id,
        "transaction": transaction.id,
        "transfer": transfer.id,
        # The transfer-create arm below files against a real category, and a
        # world hands out ids rather than the ORM rows ``seed_user`` returned.
        "rent_category": seed_user["categories"]["Rent"].id,
    }


#: The world's name, which every marker below reads rather than spelling: a
#: test carrying the marker then imports this registration wherever it sits.
_BASELINE_LESS_OWNER = register_seeded_state(
    "baseline_less_owner", _build_baseline_less_owner,
)


def _get_rules(app):
    """Every GET rule in *app*'s route table, in rule order.

    The sweep's subject, as one expression, so the enumeration below and the
    coverage claim above it read the SAME definition of "a GET route" rather
    than each spelling their own.

    Args:
        app: any application whose ``url_map`` is the one to grade.

    **Flask's own static route is excluded BY ENDPOINT, not by path prefix.**
    ``startswith("/static")`` also swallows a future ``/statistics`` or
    ``/static-report``, which would leave a real route ungraded while this file
    claimed to grade every one -- the same silent-shrink defect N-388 was.
    Censused 2026-08-30: exactly one rule's path starts with ``/static``, and
    its endpoint is ``static``, so the two predicates agree today and only the
    endpoint one keeps agreeing.

    Returns:
        The :class:`werkzeug.routing.Rule` objects, sorted by rule string.
    """
    with app.app_context():
        rules = sorted(app.url_map.iter_rules(), key=lambda r: r.rule)
    return [r for r in rules
            if "GET" in r.methods and r.endpoint != "static"]


def _sweep_cases(app):
    """Every ``(endpoint, kind, rule)`` this sweep grades: EVERY GET rule.

    A route taking ``<int:account_id>`` becomes one case per account kind,
    because the kind decides which producer runs -- the loan doors are
    unreachable with a checking id, and the property door needs the Property.
    Every OTHER rule becomes exactly one case, whatever converters it carries.

    **There is no skip branch**, which is the whole of plan step
    balance:X-be-3.  Until it shipped, a rule whose converter the fixture could
    not fill was dropped here and pinned in an allowlist, and seventeen GET
    rules -- 17.5% of the app's 97 -- were graded by nobody (finding
    **N-388**).  A converter the world cannot fill is now caught in
    :func:`_fill_converters`, as a case that FAILS naming the missing row.

    It returns the RULE rather than a finished URL because a case has to exist
    before any fixture does -- see :data:`_SWEEP_CASES`.  The owner's real ids
    arrive later.

    Args:
        app: any application whose ``url_map`` is the one to grade.

    Returns:
        The cases, in ``url_map`` order.
    """
    cases = []
    for rule in _get_rules(app):
        if _ACCOUNT_CONVERTER in _CONVERTER_TOKEN.findall(rule.rule):
            for kind in _ACCOUNT_KINDS:
                cases.append((rule.endpoint, kind, rule.rule))
        else:
            cases.append((rule.endpoint, "", rule.rule))
    return cases


def _fill_converters(case, ids):
    """The concrete URL one case requests, and the converters nothing filled.

    Every ``<...>`` token in the rule is resolved through
    :data:`_CONVERTER_ROWS` on the pair ``(blueprint, name)`` and on nothing
    else: there is no fallback to the bare name, because a name alone is not a
    URL variable's identity in Flask and two of this app's are bound to two
    tables each.  ``account_id`` is resolved from the case's own kind instead,
    which is the axis :func:`_sweep_cases` fanned it out on.

    Args:
        case: One ``(endpoint, kind, rule)`` triple from :func:`_sweep_cases`.
        ids: The world's dict of row ids.

    Returns:
        ``(url, unfilled)`` -- the rule with every converter this world can
        fill replaced by a real id, and the sorted converter names it could
        not.  A non-empty ``unfilled`` has TWO causes and the coverage test
        tells them apart: the ``(blueprint, name)`` pair is absent from
        :data:`_CONVERTER_ROWS`, or its value names a row
        :func:`_build_baseline_less_owner` does not build.  Neither repair is
        ever to exclude the route.
    """
    endpoint, kind, rule = case
    # The endpoint PREFIX, which equals ``Blueprint.name`` only while no
    # blueprint is nested; a nested one would key as ``"parent.child"``.
    # Censused 2026-08-30: every endpoint in this app carries exactly one dot
    # except Flask's own ``static``, which `_get_rules` drops.  A nested
    # blueprint would fail LOUD through the pair arm rather than silently,
    # because its pairs would not be in the map.
    blueprint = endpoint.rpartition(".")[0]
    unfilled = []

    def _one(match):
        name = match.group(1)
        if name == _ACCOUNT_CONVERTER:
            # ``_sweep_cases`` emits a kind for exactly these rules, and the
            # coverage test pins every kind against a row in the world.
            return str(ids[kind])
        key = _CONVERTER_ROWS.get((blueprint, name))
        if key is None or key not in ids:
            unfilled.append(name)
            return match.group(0)
        return str(ids[key])

    return _CONVERTER_TOKEN.sub(_one, rule), sorted(unfilled)


def _is_answered_by_its_own_row(rule):
    """Whether a 404 for *rule* means the sweep requested nothing real.

    **The claim is "this case reached a producer", not "this row exists".**
    404 is under 500, so a case naming a row that is not there passes every
    other arm while measuring nothing -- which is N-388's own defect, one layer
    down.  A 404 from the URL map and a 404 a handler chose are
    indistinguishable, so this cannot say WHICH; the failure message offers
    both branches and the reader picks.

    Proved by mutation 2026-08-30, and it is the only arm that catches either:
    dropping the transfer from the world reddened its three doors, and adding
    100000 to the transaction's id reddened its four, both still green with
    this arm alone removed.  **It catches a fill naming the WRONG KIND of row
    since ruling ``balance:R-BAL356``**: no two rows this world builds share
    an id, so such a fill requests an id no row of the door's table holds and
    the door answers 404 (measured 2026-10-22 on the template pair, in both
    directions, each reddening its own door's case).  Measured 2026-08-30,
    over the eleven pairs the map held then, eight filled from the first row
    of their own table, id 1, and a swap between two of them emitted a
    byte-identical URL no arm here could catch; ``period`` was id 6 and the
    exception.

    TWO exclusions, both measured rather than assumed (2026-08-30, over the
    whole sweep):

    * an ``account_id`` rule, because a 404 is its DESIGN -- the kind axis
      deliberately requests the loan doors with a checking id, and 67 of the
      182 account cases answer 404 on one shape or the other.  Deleting this
      clause reddens all 67, which is the mutation that keeps it from being
      decorative;
    * a rule carrying NO converter, because such a route is addressed by its
      QUERY STRING and this predicate cannot see one.  The three that 404 today
      are the empty-cell family (``/transactions/empty-cell``, ``/new/full``,
      ``/new/quick``), whose coordinate is four ``request.args`` ids
      (``forms._resolve_grid_cell``); with none supplied the 404 is the correct
      answer, not a missing row.  The 19 rules left are exactly the ones this
      world addresses by id, and none answers 404 on either shape.

    Args:
        rule: The rule string, converters and all.

    Returns:
        True when a 404 would mean the request never reached a producer.
    """
    names = set(_CONVERTER_TOKEN.findall(rule))
    return bool(names) and _ACCOUNT_CONVERTER not in names


def _route_table_app():
    """A Flask app carrying the route table and NOTHING else.

    **Why not ``create_app``.**  The table this sweep needs is settled by
    blueprint registration, but ``create_app`` under the testing config also
    ensures schemas, seeds ``ref.*`` and populates ``ref_cache``
    (its dev/test arm and its `ref_cache.init`, `app/__init__.py`) -- 151 SQL
    statements over 3 connections including 5 ``CREATE SCHEMA IF NOT EXISTS``,
    measured 2026-08-29.  That work is orthogonal to ``url_map`` and it would
    run at COLLECTION here, where a database failure is a collection ERROR:
    under xdist a collection that DIFFERS BETWEEN WORKERS aborts the whole run
    rather than failing one test.  The two are not the same event and the
    difference matters: a uniform outage errors this file and lets the rest of
    the suite finish, while an INTERMITTENT one -- the shape two concurrent
    runs produce here -- aborts everything.  Seen once, 2026-08-29, against a
    DRAFT of this design that did build through ``create_app``:
    ``Different tests were collected between gw8 and gw3``, no test run.  It
    also reconfigures logging process-wide.

    **What this design costs in exchange, stated because it is not free.**  At
    HEAD the parametrize list was a constant tuple, so collection COULD NOT
    differ between workers whatever happened.  It is now derived, once per
    worker, from an app built at import: nothing in `_register_blueprints` is
    non-deterministic today, but the failure mode is no longer structurally
    impossible, and its blast radius is the suite rather than this file.

    ``register_url_converters`` is called FIRST because `create_app` documents
    that order as required (its URL converters comment): a rule naming a converter
    this app has not registered raises ``LookupError`` at import, which is that
    same collection error arriving by another door.

    Measured 2026-08-29, this table and ``create_app("testing")``'s are
    IDENTICAL -- 218 rules, equal on endpoint, rule and methods.  `create_app`
    adds no route of its own (no ``add_url_rule``, no ``@app.route``).

    **The identity is asserted for the GET routes this sweep grades, not
    assumed**: the coverage test compares this table against the one the
    FIXTURE's app serves.  A non-GET route registered outside a blueprint would
    not be covered by that comparison.

    Registering the blueprints here also marks each one registered-once in this
    process before any test runs.  Nothing in `app/` uses ``record_once`` today,
    so this is inert; a blueprint that grew one would have it consumed here.

    Returns:
        A Flask app whose ``url_map`` is the application's.
    """
    app = Flask(shekel_app_package.__name__)
    register_url_converters(app)
    # Pylint: ``protected-access`` -- ``_register_blueprints`` is the app
    # package's own single decider of what the route table holds, and reaching
    # for it is what keeps this enumeration from becoming a second list of
    # blueprints that could drift from it.  A public seam is worth minting;
    # that is a change to ``app/`` and is raised with the developer, not
    # assumed here.
    # pylint: disable=protected-access
    shekel_app_package._register_blueprints(app)
    return app


#: **Every route is its OWN test.**  This is the whole of the wall-clock fix,
#: and it replaces a hand-cut partition rather than resizing one.
#:
#: As ONE test the sweep made 388 requests (194 concrete URLs, each in both
#: request shapes) inside pytest-timeout's 30 s, which covers setup, call and
#: teardown together.  On 2026-08-16 it crossed: `test_no_get_route_returns_5xx`
#: timed out on four of nine CI runs.  Removing the cluster cost that made CI
#: slow (PR #106) bought headroom and did not remove the shape -- the measured
#: call was still 24.68 s against 30 s.
#:
#: **Splitting it into arms did not remove the shape either, whatever the arms
#: were named.**  One test issuing every request is O(the route table) under a
#: budget that is O(1) per test, and an arm only divides the constant: by
#: account KIND (`910065a9`) the largest arm still grew, and re-measured
#: 2026-08-29, eleven days later, the account-less arm was still exactly 54 URLs
#: while every one of the 42 URLs the table had gained sat in the KIND arms,
#: which grew 20 -> 26 each.  A kind axis cannot bound that by construction: one
#: new account route adds a URL to ALL SEVEN kind arms at once.  Finding
#: **N-364** reads the growth the other way round.  (An earlier draft cited a
#: 35 -> 41 census of ``<int:account_id>`` decorators as corroboration.  That
#: was an ARTIFACT: the grep only counted occurrences sharing a line with
#: ``.route(``, and 22 of today's 63 sit on continuation lines -- the true
#: count is 48 -> 63.  The +6 that matters is the GET rules per kind, 20 -> 26,
#: measured directly off ``url_map``.)
#:
#: One case per test makes the per-test cost O(1) in the route table: two
#: requests, whatever the app grows into.  Nothing here has a size to keep in
#: step, and pytest-timeout's cap goes back to being the hang detector
#: `pytest.ini` calibrated it as.  Measured CI-shaped (4 cores, ``-n 12``,
#: 2026-08-29): the largest single arm read 2.16 s split by account kind, and
#: no case's CALL reaches a `--durations` report ahead of this file's own
#: setup entries -- a case's two requests are a 0.020 s median.
#:
#: **TWO STANDING CONSTRAINTS ON WHOEVER EDITS THIS FILE NEXT, stated here
#: because the code is the only home that survives a planning document being
#: archived.**  Both were `X-bd`'s and `X-be-2`'s, whose README entries were
#: condensed to pointers on 2026-08-30 under conventions rule 5; an obligation
#: that outlives the step which discovered it may not live only in a record
#: that governs nothing.
#:
#: * **A later step may not reintroduce ONE test issuing every request**, by
#:   any name, and the routes must stay enumerable with NO database -- see
#:   :func:`_route_table_app` for why collection may not touch one.
#: * **The per-test drop and re-clone STAYS, because the clone is the
#:   isolation.**  A seeded start state changes what a test starts WITH, never
#:   whether it gets its own database; the same sentence is in
#:   ``tests/_seeded_start_states.py``'s seeded-start-state block, which is
#:   where the mechanism lives.
#:
#: **What it costs, and where that cost actually lives.**  Measured serially
#: 2026-08-29, 252 items in 81.16 s: per-item SETUP is **87%** of that (236
#: sweep items, 0.270 s median, 70.49 s) and the requests the sweep exists to
#: make are **7%** (0.020 s median, 5.39 s).  **The item count has since moved
#: to 253** -- `balance:X-be-3` widened the world and the 17 rules it could not
#: reach became cases -- so the ratios above are the ones to carry forward and
#: the absolute seconds are not.
#:
#: That 0.270 s splits, on matched serial probes of 30 items each the same day:
#: **0.160 s (59%) is this module's own seven-account fixture** and 0.110 s
#: (41%) is the per-test chain it inherits, whose floor is the autouse ``db``
#: fixture dropping and re-cloning the per-worker database.  Two earlier
#: readings of this file blamed the clone for the whole of it; both were
#: confounded -- one divided an xdist wall clock by an item count, charging the
#: fixed worker bootstrap to the items, and one compared against items that
#: turned out to use the same fixture.
#:
#: **The sweep is READ-ONLY -- every request here is a GET -- so it buys none
#: of that isolation.**  Paying once instead of 236 times is finding
#: **N-387**, owned by **X-be-2**, and the split above is what that step is
#: sized against: sharing a database alone recovers the 41%, so the shared
#: state has to carry the seven accounts too.  It is not done here, and the
#: obvious remedy is measured WRONG: a module-scoped clone is re-cloned out
#: from under this module by the next test from ANY other module landing on the
#: same worker, observed on three workers under ``-n 12`` (2026-08-29).
#:
#: Building an app at import is what a per-route test id costs -- ids are fixed
#: before any fixture runs -- but it costs no DATABASE: see
#: :func:`_route_table_app`, which registers the blueprints onto a bare Flask
#: app rather than calling ``create_app``.
_SWEEP_CASES = _sweep_cases(_route_table_app())


#: Readable ids, so a CI failure names the route it found rather than an
#: index.  Keyed on the RULE and not the endpoint: `analytics.retired_tab`
#: carries three rules and `dashboard.page` two, so an endpoint key left
#: pytest to disambiguate five of the cases by position -- which is the index
#: this line exists to avoid, and it re-points when a route is added.
_SWEEP_IDS = [f"{rule}[{kind}]" if kind else rule
              for _, kind, rule in _SWEEP_CASES]


#: The endpoints this sweep grades, for the arm that follows a REDIRECT one
#: hop.  Read off the cases rather than off ``url_map`` a third time, because
#: the claim it serves is "the hop lands somewhere this sweep also grades" and
#: the cases are what "this sweep" means.
_SWEPT_ENDPOINTS = frozenset(endpoint for endpoint, _, _ in _SWEEP_CASES)
