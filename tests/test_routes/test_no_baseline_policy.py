"""The no-baseline policy, graded across EVERY route the app registers.

Plan step X-v1, rulings R-BW and R-CB.  Every balance the app produces is
scoped to a baseline scenario, so a user without one has no balance any surface
can answer.  The seam raises one named exception
(:class:`app.exceptions.BaselineMissingError`) and one application-level handler
answers it: the setup-recovery page for a full request, ``204 No Content`` for
an HTMX request, an ERROR log event either way.

**Why this suite enumerates ``url_map`` instead of listing routes.**  The
defect it exists to catch was found that way and could not have been found any
other way.  Finding N-112 counted the sites that STATE the no-baseline rule --
13 of them, in six modules.  The three surfaces that 500'd
(``routes/loan/_helpers._load_route_context``,
``home_equity_service.resolve_home_equity``,
``debt_strategy._load_debt_accounts``, reached by 8 endpoints between them)
state it nowhere at all, so no search for a predicate could reach them.  A
route added a year from now is graded here without its author knowing this rule
exists, which is the property the rejected ``@require_baseline`` decorator
could not have had.

**What it does NOT prove**, stated because a gate that reads as proving more
than it does is this arc's most expensive recurring lesson:

* it cannot see a route that answers with a **fabricated figure**.  A ``$0.00``
  hero over balances that are all ``None`` returns 200 and passes every arm
  here, and so did a balance sheet asserting ``in_balance`` over a ledger it
  could not read -- which is exactly how X-v1's first draft of this file
  graded that page green.  Both were deleted at plan step X-v2 rather than
  gated, because there is no mechanical predicate for "this number is
  invented";
* it grades **GET** routes.  Two of the eight endpoints the census measured are
  POSTs, and they stay outside the sweep (ruling R-CB); the mutating-request
  contract is pinned by :class:`TestTheHandlerItself` instead;
* it proves nothing about surfaces that resolve the baseline WITHOUT the
  balance seam.  Fifteen such sites remain in ``app/`` -- forms, template
  generation, posting sync -- and they are finding **N-117**, owned by plan
  step **X-y**.  One of them is inside the sweep and answers 400 with a bare
  text body rather than the policy's card (``carry_forward_preview``); the
  sweep is green on it because 400 is under 500, which is the limit of what it
  claims;
* it grades ONE row of each kind, in the state the world builds it.  Whether
  the STATE matters was measured rather than assumed (2026-08-30): the four
  transaction doors, the three transfer doors and the transaction-template
  door were re-run against a SETTLED transaction, a settled transfer and an
  envelope transaction carrying an entry, and all twelve answered 200 exactly
  as the projected rows do.  A door whose answer turns on a row's status is
  therefore not something this file has evidence of, and a future one would
  need its own axis the way ``_ACCOUNT_KINDS`` is one;
* it could not see a fill that names the WRONG KIND of row, **for eight of
  the eleven pairs the map held on 2026-08-30**, while the test database
  numbered every table from 1.  Measured that day: eight filled from the first
  row of their own table,
  id 1, so handing the ``/transfers`` edit door the transaction template's id
  emitted the byte-identical ``/transfers/1/edit`` -- a valid URL for a real
  row, 200, indistinguishable from the right one, and two mutations of that
  shape survived.  The three ``period_id`` pairs were the exception:
  ``period`` (``periods[4]``) was id 6, because ``build_seed_user`` writes an
  opening period first and ``build_periods_today`` then RESETS the schedule
  (through ``pay_period_admin.reset_pay_periods`` since plan step
  ``pay_calendar:C4-b-1``), so a swap involving one of those 404'd.  **Each
  table now numbers its ids from a range of its own** (ruling
  ``balance:R-BAL356``), so no two rows this world builds share an id: a
  wrong-kind fill requests an id no row of the door's table holds, and a door
  answering a missing row with 404 (the app's not-found rule) is refused by
  :func:`_is_answered_by_its_own_row`.  Measured 2026-10-22 on the template
  pair, in both directions (each mutation reddens its own door's case); the
  other pairs rest on the same ranges, which
  ``tests/test_scripts/test_no_two_kinds_of_id_coincide.py`` guards.
  :data:`_CONVERTER_ROWS` stays TOTAL over ``(blueprint, name)`` with no
  fallback, so the mistake still has to be WRITTEN DOWN rather than defaulted
  into.

**Every GET route is graded, and that is CHECKED** (plan step balance:X-be-3,
finding **N-388**).  Until it shipped, a rule whose converter the fixture could
not fill was skipped and pinned in a hand-maintained ``_UNREACHED_RULES``
allowlist -- seventeen of the app's 97 GET rules, 17.5%, graded by nobody
since the sweep was written, and the coverage test asserted the list rather
than closing it.  The world now holds a row of every kind a GET rule takes an
id for, there is no skip branch, and the coverage claim is an equality against
``url_map`` that no list can satisfy.

**None of the seventeen 5xx'd, and that is a claim about FIFTEEN of them.**
Two are the retired ``/salary/<id>/breakdown`` stubs, which redirect into the
cockpit on both request shapes and so reach no producer to crash in; the other
fifteen render.  An adversarial review caught the first draft of this sentence
counting all seventeen (2026-08-30).  Nothing goes ungraded by it -- the arm on
each case now resolves a redirect one hop through ``url_map`` and refuses one
landing outside the swept set -- but the answer N-388 asked for is fifteen
routes wide, not seventeen.
"""

import re
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlsplit

import pytest

from flask import Flask

from werkzeug.exceptions import HTTPException

import app as shekel_app_package
from app import ref_cache
from app.enums import WithholdingKindEnum
from app.models.account import Account
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
    cadence_payload,
    create_hysa_account,
    create_loan_account,
    create_transfer,
    make_appreciating_account,
    make_investment_account,
    make_salary_profile,
)
# The world builders and the registry live in ``tests/conftest.py`` beside the
# worker-database plumbing they drive.  Importing them from there is safe and
# is the same module object pytest loaded -- ``tests`` is a package, so pytest
# imports its conftest as ``tests.conftest`` and this resolves out of
# ``sys.modules`` rather than executing it a second time (verified: a second
# execution would re-run ``_bootstrap_worker_database`` and create a second
# database).  Whether the seam should instead be its own module is a placement
# question raised with the developer, not settled here.
from tests.conftest import (
    build_periods_today,
    build_seed_user,
    register_seeded_state,
)


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
#: ``tests/test_routes/test_templates.py`` and ``test_transfers/test_templates.py``.  **The
#: two are not equally strong and the difference was measured** (2026-08-30):
#: the transfers one asserts ``b"Monthly Savings"``, which only its own row
#: supplies, while the templates one asserts ``b"Rent"``, which its fixture
#: uses for BOTH the template's name and its category -- and the category
#: ``<option>`` renders that string whatever the template is called.  What the
#: templates side really pins is that the door resolves a row in
#: ``transaction_templates`` at all: repointing it at ``transfer_templates``
#: 404s there, since that test creates none.
_CONVERTER_ROWS = {
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
    state block comment in ``tests/conftest.py``.

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
        default_amount=Decimal("200.00"),
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


register_seeded_state("baseline_less_owner", _build_baseline_less_owner)


@pytest.fixture()
def baseline_less_owner(seeded_world):
    """The ids of the world this file's tests declare.

    A thin alias for :data:`seeded_world` so the tests keep naming the state
    they are in rather than the mechanism that delivers it.

    Returns:
        A dict of the row ids each parametrised URL and assertion needs.
    """
    return seeded_world




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
    (`app/__init__.py:159-162` and the `ref_cache.init` at `:207`) -- 151 SQL
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
    that order as required (`app/__init__.py:136`): a rule naming a converter
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
#:   ``tests/conftest.py``'s seeded-start-state block, which is where the
#:   mechanism lives.
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


@pytest.mark.seeded_start_state("baseline_less_owner")
class TestNoRouteCrashesWithoutABaseline:
    """The sweep: no GET route may 5xx for an owner with no baseline."""

    def test_the_sweep_still_covers_the_whole_url_map(self, app,
                                                      baseline_less_owner):
        """The COVERAGE claim, graded apart from the requests.

        Assertions that are properties of ``url_map`` and the world and of
        nothing else, so they belong in one test rather than being restated in
        every case -- where they would also be one chance per case to rot into
        disagreement.

        **The claim is now CHECKED rather than pinned** (plan step
        balance:X-be-3, finding **N-388**).  Until it shipped this test
        asserted a hand-maintained list of the routes the sweep did NOT reach:
        seventeen of them, and the assertion held them there rather than
        closing them.  The two arms below say the thing the file's title claims
        instead -- every GET rule in ``url_map`` is a case, and every case
        resolves to a real URL -- and neither can be satisfied by adding a
        route to a list.
        """
        cases = _sweep_cases(app)

        # **Every GET rule is a case, and the right-hand side is spelled out
        # HERE rather than borrowed from `_get_rules`.**  The first draft of
        # this arm read `{r.rule for r in _get_rules(app)}`, which
        # `_sweep_cases` also iterates -- so it was an IDENTITY, true by
        # construction, and it is the exact shape the `_ACCOUNT_KINDS` comment
        # below refuses: two sides off one constant, shrinking together.
        # Measured by an adversarial review 2026-08-30 -- adding one filter to
        # `_get_rules` deleted `/debt-strategy`, which is one of the three
        # surfaces N-112 caught 500ing, and every arm here stayed GREEN.
        #
        # So the predicate is DUPLICATED on purpose, which is the same
        # argument that makes the axis below a literal: a pin is the one form
        # that cannot shrink along with what it grades.  A filter added to
        # `_get_rules` now makes the two disagree and turns this red.
        with app.app_context():
            table = {rule.rule for rule in app.url_map.iter_rules()
                     if "GET" in rule.methods and rule.endpoint != "static"}
        uncovered = table - {rule for _, _, rule in cases}
        assert sorted({rule for _, _, rule in cases}) == sorted(table), (
            "a GET rule in url_map is covered by no case, so some route is "
            "graded by nobody.\n"
            f"  uncovered: {sorted(uncovered)}"
        )
        assert len(cases) > 100, (
            f"the sweep collapsed to {len(cases)} cases -- it is meant to "
            f"cover the whole url_map, so this means the enumeration stopped, "
            f"not that the app shrank"
        )

        # **The AXIS control, graded against the FIXTURE and not against
        # `_ACCOUNT_KINDS`.**  Nothing else here reads the kind -- it selects no
        # test and gates no branch -- so a kind dropped from `_ACCOUNT_KINDS`
        # deletes every case for it, and the surfaces N-112 measured 500ing are
        # reached through the loan, mortgage and property kinds.  Comparing the
        # emitted kinds against `_ACCOUNT_KINDS` CANNOT catch that: both sides
        # are built from that tuple, so both shrink together and the assertion
        # stays green.  Measured 2026-08-29 and recomputed 2026-08-30 --
        # cutting the tuple to two kinds deletes 130 cases, five per account
        # rule, and every assertion in an earlier draft of this test passed.
        # The deletion is 130 whatever the table's size, so the FLOOR is what
        # decays: it was 130 of 236 (55%) clearing `len(cases) > 100` by six,
        # and after X-be-3 it is 130 of 253 (51%) clearing it by 23.  A floor
        # whose margin grows with every route added is the reason the pin
        # below is a literal and not a threshold.
        #
        # So the axis is PINNED as a literal -- a pin is the one form that
        # cannot shrink along with what it grades.  It is NOT keyed on the
        # world's dict minus a stop-list: a set defined by SUBTRACTION claims
        # members nobody censused, and the world hands out TEN keys that are
        # not account kinds (`user_id`, `period`, `rent_category` and the seven
        # rows X-be-3 added), every one of which would have to be named in the
        # stop-list. An earlier draft of this sentence said seven, which is
        # what X-be-3 added rather than what the world holds. The containment
        # arm below keeps the pin honest against the world without inheriting
        # the subtraction shape.
        assert _ACCOUNT_KINDS == (
            "checking", "loan", "mortgage", "property", "hysa", "card",
            "invest",
        ), (
            f"an account kind left the sweep's axis: {_ACCOUNT_KINDS}. Deleting "
            f"one deletes every case for it. If that is deliberate, say so "
            f"here and in the fixture; if it is not, this is 26 producers "
            f"going ungraded."
        )
        assert set(_ACCOUNT_KINDS) <= set(baseline_less_owner), (
            f"the sweep names a kind the fixture creates no account for: "
            f"{sorted(set(_ACCOUNT_KINDS) - set(baseline_less_owner))}"
        )
        assert {kind for _, kind, _ in cases} - {""} == set(_ACCOUNT_KINDS), (
            "the fan-out stopped emitting a case for every kind on the axis, "
            "so a family of producers is graded by nobody.\n"
            f"  emitted: {sorted({kind for _, kind, _ in cases} - {''})}\n"
            f"  axis:    {sorted(_ACCOUNT_KINDS)}"
        )

        # **The fill map is TOTAL over the route table, and graded BOTH WAYS.**
        # This is what makes deleting the bare-name fallback safe rather than
        # merely stricter: a rule carrying a pair nobody wrote down fails HERE,
        # naming the pair, instead of being filled by a same-named key meaning
        # a different table. A dead key fails too, so the map cannot keep an
        # entry for a route that no longer exists and quietly stop describing
        # the app. `account_id` is excluded on both sides -- it is the fan-out
        # axis, filled from the kind rather than from one row.
        table_pairs = {
            (rule.endpoint.rpartition(".")[0], name)
            for rule in _get_rules(app)
            for name in _CONVERTER_TOKEN.findall(rule.rule)
            if name != _ACCOUNT_CONVERTER
        }
        assert set(_CONVERTER_ROWS) == table_pairs, (
            "the fill map and the GET route table disagree about which "
            "(blueprint, converter) pairs exist. An unmapped pair is a route "
            "requested with the converter still in its path -- a 404, which "
            "passes a sweep that only grades 5xx, and is finding N-388.\n"
            f"  unmapped:    {sorted(table_pairs - set(_CONVERTER_ROWS))}\n"
            f"  not a route: {sorted(set(_CONVERTER_ROWS) - table_pairs)}"
        )
        rows = set(_CONVERTER_ROWS.values())
        assert rows <= set(baseline_less_owner), (
            f"the fill map names a row the world does not build: "
            f"{sorted(rows - set(baseline_less_owner))}"
        )

        # **And every case resolves to a real URL.**  Graded AFTER the two arms
        # above, which name the cause; this names the consequence for every
        # rule at once. A converter the world holds no row for leaves a literal
        # ``<int:...>`` in the path, that 404s, and 404 is under 500 -- so the
        # case would PASS while grading nothing, which is the exact shape of
        # the seventeen routes N-388 measured. Deleting the skip branch alone
        # would have turned every one of them into a green case requesting a
        # URL that does not exist.
        unfilled = {}
        for case in cases:
            _url, missing = _fill_converters(case, baseline_less_owner)
            if missing:
                unfilled[case[2]] = missing
        assert not unfilled, (
            "the world holds no row for a converter these rules take, so each "
            "would be requested with the converter still in the path -- a "
            "404, which passes a sweep that only grades 5xx. Build the row in "
            "_build_baseline_less_owner and name it in _CONVERTER_ROWS.\n"
            + "\n".join(f"  {rule}: {names}"
                        for rule, names in sorted(unfilled.items()))
        )

        # The import-time table against the one the requests run on.  The cases
        # are pytest IDS, fixed at collection from an app built before any
        # fixture; if that table and this one ever disagree, some route is
        # graded by no case at all and every case still passes.
        assert cases == _SWEEP_CASES, (
            "the route table read at collection is not the one the requests "
            "run against, so the sweep is grading a stale set of routes.\n"
            f"  at collection: {len(_SWEEP_CASES)} cases\n"
            f"  at run time:   {len(cases)} cases"
        )

    @pytest.mark.parametrize("case", _SWEEP_CASES, ids=_SWEEP_IDS)
    def test_no_get_route_returns_5xx(self, app, owner_client,
                                      baseline_less_owner, case):
        """ONE GET route answers a baseline-less owner without crashing.

        Both request shapes, because the handler branches on them and a sweep
        of one shape proves nothing about the other: a plain request must get
        the recovery page, an HTMX request must get 204 and leave the DOM
        alone.  Before the X-v1 handler this reported 8 endpoints raising
        ``ValueError`` from ``require_scenario``, 6 of them GETs.

        **Both are issued before either is graded.**  Asserting inside the loop
        would mean a route that 5xx's on the plain GET never has its fragment
        shape exercised at all, so the run would say nothing about the contract
        the 204 branch exists to keep -- on exactly the routes most likely to
        be breaking it.

        **One case per test, which is what bounds this file.**  See
        :data:`_SWEEP_CASES`: as one test, or as a hand-cut arm of one, the
        wall clock grew with the route table under a budget that does not.
        Here it cannot -- a case is two requests however large the app gets --
        and a failure names the single route that failed instead of counting
        them.
        """
        endpoint, kind, rule = case
        url, missing = _fill_converters(case, baseline_less_owner)
        # A URL still holding a converter 404s, and 404 is under 500, so it
        # would pass while grading nothing.  The coverage test refuses the
        # whole set at once; this refuses THIS case, so a run that skipped the
        # coverage test still cannot report a green route it never requested.
        # ``"<" not in url`` is asserted as well as ``missing``: the two agree
        # only while `_CONVERTER_TOKEN` matches every token Flask accepts, and
        # a token it failed to match would leave the path unfilled with nothing
        # to report.
        assert not missing and "<" not in url, (
            f"{rule} reached the sweep with a converter unfilled ({url}, "
            f"{missing}). The baseline-less world holds no row of that kind: "
            f"build one in _build_baseline_less_owner and name it in "
            f"_CONVERTER_ROWS. A route this sweep cannot request is a route "
            f"graded by nobody, which is finding N-388."
        )

        seen = []
        for headers in ({}, {"HX-Request": "true"}):
            resp = owner_client.get(url, headers=headers)
            seen.append((bool(headers), resp.status_code,
                         b"Setup Incomplete" in resp.data,
                         resp.headers.get("Location")))

        crashed = [shape for shape in seen if shape[1] >= 500]
        assert not crashed, (
            f"{url} ({endpoint}, kind={kind or 'none'}) returned "
            f"{[s[1] for s in crashed]} for an owner with no baseline "
            f"scenario (htmx={[s[0] for s in crashed]})"
        )
        # **And the request REACHED a producer.**  404 is under 500, so a case
        # that names a row the world does not hold passes every arm above
        # while grading nothing -- and a 404 from the URL map is
        # indistinguishable from a 404 a handler chose.  For a rule filled
        # from the world's one row of each kind there is nothing legitimate to
        # refuse, so a 404 is that failure and no other; see
        # :func:`_is_answered_by_its_own_row` for why the account kinds are
        # not.
        assert not (_is_answered_by_its_own_row(rule)
                    and [s for s in seen if s[1] == 404]), (
            f"{url} ({endpoint}) was answered 404, so this case requested a "
            f"row that does not exist and measured no producer at all. "
            f"Either _CONVERTER_ROWS fills one of "
            f"{sorted(set(_CONVERTER_TOKEN.findall(rule)))} with the "
            f"wrong KIND of row, or the door needs a row "
            f"_build_baseline_less_owner does not build."
        )
        # **A REDIRECT measured no producer either, so the hop is followed
        # one step -- to a rule, not to a body.**  A route answering 3xx never
        # ran the code this sweep exists to grade, exactly as a 404 did not.
        # Refusing a 3xx outright would be wrong, because a redirect stub is a
        # legitimate answer; a redirect OUT of the graded set would be a hole
        # with no arm on it, which is N-388's shape again. So the target is
        # resolved through the app's own `url_map` and must be an endpoint
        # this sweep also grades.
        #
        # It applies to EVERY case's 3xx, not only the rules
        # `_is_answered_by_its_own_row` selects -- there is no reason to
        # exempt an account-kind hop, and no predicate is needed to include
        # it. Measured 2026-08-30, 3xx answers come from two families and
        # every one lands inside the swept set: the account fragments that
        # bounce a non-HTMX request to their page, and THREE own-row rules
        # that redirect on BOTH shapes -- `/companion/period/<id>` to `/grid`
        # and the two retired `/salary/<id>/breakdown` stubs to `/salary`.
        # Those three are why the module docstring's "none of the seventeen
        # 5xx'd" is a claim about fifteen routes that could have and two that
        # never reach a producer at all.
        for _htmx, status, _card, location in seen:
            if not 300 <= status < 400:
                continue
            assert location is not None, (
                f"{url} ({endpoint}) answered {status} with no Location, so "
                f"the request reached no producer and named no successor"
            )
            split = urlsplit(location)
            assert not split.netloc, (
                f"{url} ({endpoint}) redirected OFF this application, to "
                f"{location!r}; the sweep grades no route there"
            )
            try:
                landed, _args = app.url_map.bind("localhost").match(
                    split.path, method="GET",
                )
            except HTTPException as exc:
                # ``RequestRedirect`` arrives here too, and deliberately: it
                # means the target needs a trailing-slash normalisation before
                # it resolves, so the app emitted a Location it would itself
                # bounce again. A normalising branch was written, MEASURED
                # DEAD against every redirect the sweep produces (2026-08-30)
                # and deleted rather than kept as untested handling; the name
                # is in this message so the next occurrence is diagnosable.
                raise AssertionError(
                    f"{url} ({endpoint}) redirected to {location!r}, which "
                    f"this app's url_map does not resolve to a GET rule "
                    f"({exc!r}). A RequestRedirect here means the Location "
                    f"needs a trailing slash; anything else means the case "
                    f"measured no producer and its successor is graded by "
                    f"nobody."
                ) from exc
            assert landed in _SWEPT_ENDPOINTS, (
                f"{url} ({endpoint}) redirected to {location!r} -> "
                f"{landed}, which this sweep does not grade. A route whose "
                f"only answer is a hop out of the swept set is a route no "
                f"case measures, which is finding N-388's shape."
            )
        # A fragment must never receive the full-page card: htmx would swap a
        # setup page into a balance cell.  The sweep used to grade only the
        # 5xx, so THIS regression -- the one the 204 branch exists to prevent
        # -- would have passed it (X-v2's adversarial design review).
        assert not [s for s in seen if s[0] and s[2]], (
            f"{url} ({endpoint}) answered an HTMX request with the full-page "
            f"recovery card instead of 204"
        )

    @pytest.mark.parametrize("label,template", _MEASURED_PAGE_DOORS)
    def test_a_measured_door_answers_with_the_repair(
        self, app, owner_client, baseline_less_owner, label, template,
    ):
        """Each PAGE door that used to 500 now renders the repair card.

        The status code alone would pass against a blank 200, so this asserts
        the card's REPAIR is present -- the create-baseline form action, which
        is the only route back out of this state.
        """
        url = template.format(**baseline_less_owner)
        resp = owner_client.get(url)

        assert resp.status_code == 200, f"{label} ({url})"
        body = resp.data.decode()
        assert "Setup Incomplete" in body, f"{label} did not render the card"
        assert "/create-baseline" in body, (
            f"{label} rendered a card with no repair: the create-baseline form "
            f"is the only way out of this state"
        )

    @pytest.mark.parametrize("label,template", _MEASURED_DOORS)
    def test_a_measured_door_answers_an_htmx_request_with_204(
        self, app, owner_client, baseline_less_owner, label, template,
    ):
        """An HTMX request gets 204 and an EMPTY body, never the card.

        The grid partials' shipped contract, generalised: a fragment poll must
        leave the live DOM untouched.  Swapping a full-page setup card into a
        balance cell would be worse than the 500 it replaced.
        """
        url = template.format(**baseline_less_owner)
        resp = owner_client.get(url, headers={"HX-Request": "true"})

        assert resp.status_code == 204, f"{label} ({url})"
        assert resp.data == b"", (
            f"{label} returned a body with its 204; htmx swaps nothing on 204 "
            f"only because the body is empty"
        )


@pytest.mark.seeded_start_state("baseline_less_owner")
class TestTheHandlerItself:
    """The handler's own contract, exercised directly rather than inferred.

    Every test here needs the baseline-less owner, so the class declares that
    world.  The handler's NEGATIVE control -- the owner who HAS a baseline --
    is a different start state and therefore a different class,
    :class:`TestTheHandlerIsInertForAHealthyOwner` below.  A world is declared
    per class because a marker cannot be taken back off one test.
    """

    def test_it_logs_an_error_event(self, app, owner_client,
                                    baseline_less_owner, caplog):
        """The quiet screen is a LOUD log line.

        No code path produces a baseline-less owner (registration writes one,
        nothing deletes or un-baselines one, no path promotes a companion), so
        an occurrence is either data changed outside the app or a caller
        resolving the wrong user.  Both want an alert; a handler that degraded
        in silence would hide exactly the bug it is standing in front of.
        """
        url = f"/accounts/{baseline_less_owner['loan']}/loan"
        with caplog.at_level("ERROR"):
            owner_client.get(url)

        events = [r for r in caplog.records
                  if getattr(r, "event", None) == "baseline_missing"]
        assert events, (
            "the no-baseline handler answered without emitting its event; "
            f"records seen: {[r.message for r in caplog.records]}"
        )
        assert events[0].levelname == "ERROR"
        assert events[0].category == "error"

    def test_a_mutating_htmx_request_is_answered_not_silenced(
        self, app, owner_client, baseline_less_owner,
    ):
        """An HTMX POST gets the card, never 204.

        204 is right for an idempotent fragment poll and WRONG for a button:
        measured before the fix, ``POST /debt-strategy/calculate`` answered
        ``204`` with an empty body, so a user pressed Calculate and nothing
        happened -- silently, and every time.  Before X-v1 it 500'd, which at
        least said something.  Found by X-v2's adversarial design review and
        confirmed by executing it.
        """
        resp = owner_client.post(
            "/debt-strategy/calculate",
            data={"strategy": "avalanche", "extra_monthly": "0"},
            headers={"HX-Request": "true"},
        )

        assert resp.status_code == 200
        body = resp.data.decode()
        assert "Setup Incomplete" in body, (
            "a mutating HTMX request was answered with nothing; the user "
            "pressed a button and the app said neither what happened nor why"
        )
        assert "/create-baseline" in body

    def test_the_event_carries_the_user_the_raise_was_resolved_for(
        self, app, owner_client, baseline_less_owner, caplog,
    ):
        """The ERROR event logs the CONTEXT's user, not only the requester.

        The two ids differ only when a caller built a context for the wrong
        user -- which is one of the two reasons this handler exists, and the
        one the requesting id alone cannot show.  Logging only the requester
        would leave the event blind in exactly the failure it is meant to
        diagnose (X-v2's adversarial design review).
        """
        url = f"/accounts/{baseline_less_owner['loan']}/loan"
        with caplog.at_level("ERROR"):
            owner_client.get(url)

        events = [r for r in caplog.records
                  if getattr(r, "event", None) == "baseline_missing"]
        assert events
        assert events[0].context_user_id == baseline_less_owner["user_id"]

    @pytest.mark.parametrize("door,payload", [
        ("/accounts/{checking}/statements/reconcile", {"tab": "to_explain"}),
        ("/accounts/{checking}/statements/reconcile/merchants",
         {"tab": "to_explain"}),
        ("/accounts/{checking}/statements/reconcile/line/1/match", {}),
    ])
    def test_a_reconcile_door_answers_rather_than_falling_silent(
        self, app, owner_client, baseline_less_owner, door, payload,
    ):
        """The Reconcile page's three POSTs, which the sweep cannot reach.

        **All three are outside the GET sweep by construction** (ruling
        **R-CB**): they are POSTs, and the sweep grades ``"GET" in
        rule.methods``.  So the only thing standing between them and the
        silent 204 is this case.

        **The failure to look for is the SILENCE, not the 500.**  Every one of
        these is driven by htmx, and htmx swaps only 2xx -- so a 204 here is a
        button that does nothing, forever and without a word, which is exactly
        what ``POST /debt-strategy/calculate`` did before X-v2 measured it.
        The status is asserted as 200 AND the body as the repair card, because
        204 satisfies "not a 5xx" on its own.

        The raise comes from ``ReviewScope.build``.  Every door proves the
        account first (``load_cash_account_or_404``) and the two ``/reconcile``
        POSTs read their tab before it, either of which can 404 -- what
        matters here is that the MATCH pane builds the scope BEFORE it looks
        the line up, which is why its case may name a line the world does not
        hold.  If that order ever inverted this would fail loudly with a 404
        rather than passing for a new reason.
        """
        resp = owner_client.post(
            door.format(**{"checking": baseline_less_owner["checking"]}),
            data=payload,
            headers={"HX-Request": "true"},
        )

        assert resp.status_code == 200, (
            f"{door} answered {resp.status_code}; htmx swaps only 2xx, and a "
            f"204 in particular is a press that reports nothing at all"
        )
        body = resp.data.decode()
        assert "Setup Incomplete" in body, (
            f"{door} answered with a body that says neither what happened "
            f"nor why: {body[:200]!r}"
        )
        assert "/create-baseline" in body

    def test_a_transfer_create_refuses_instead_of_reporting_success(
        self, app, owner_client, db, baseline_less_owner,
    ):
        """A CREATE that materialises nothing may not say it created something.

        Ledger row **F-9**, closed by routing both transfer create paths onto
        ``require_baseline_scenario``.  They resolved the NULLABLE form and
        treated absence as a defined answer -- ``_materialize_one_time_transfer``
        returned ``None``, which is its caller's signal for "created, go ahead
        and commit", and ``generate_transfers_for_all_periods`` simply did not
        generate.  Either way the template was COMMITTED and the user was told a
        transfer existed that did not.

        Both branches are exercised because ``rule is None`` is what tells them
        apart: a one-time transfer takes the first path, a cadence the second.
        The assertion is on the DATABASE as well as the page, because the page
        alone passed before the fix on the recurring branch (it redirected to a
        cheerful "created" flash).
        """
        ids = baseline_less_owner
        base = {
            "default_amount": "150.00",
            "from_account_id": ids["checking"],
            "to_account_id": ids["hysa"],
            "category_id": str(ids["rent_category"]),
        }
        one_time = {**base, "name": "F9 One Time",
                    "start_period_id": ids["period"]}
        recurring = {**base, "name": "F9 Recurring", **cadence_payload()}

        for payload in (one_time, recurring):
            resp = owner_client.post(
                "/transfers", data=payload, follow_redirects=True,
            )

            assert resp.status_code == 200
            assert "Setup Incomplete" in resp.data.decode(), (
                f"the create door answered {payload['name']!r} with something "
                f"other than the repair page"
            )
            with app.app_context():
                assert db.session.query(TransferTemplate).filter_by(
                    user_id=ids["user_id"], name=payload["name"],
                ).count() == 0, (
                    f"{payload['name']!r} was committed for an owner with no "
                    f"baseline scenario, so a definition exists that "
                    f"generated nothing"
                )


class TestTheHandlerIsInertForAHealthyOwner:
    """The negative half of the control, on the state every real user is in.

    Its own class because it is the one test in this file that must NOT start
    in the baseline-less world -- the owner it needs is the ordinary seeded
    one, with the baseline intact.  Declaring the world per class is what keeps
    that difference visible instead of hiding it in a fixture list.
    """

    def test_a_user_with_a_baseline_is_untouched(self, app, auth_client,
                                                 seed_user, seed_periods_today):
        """The handler is inert for the state every real user is in.

        A gate that fired for everyone would pass every arm above while
        breaking the application.
        """
        resp = auth_client.get("/grid")

        assert resp.status_code == 200
        assert "Setup Incomplete" not in resp.data.decode()
