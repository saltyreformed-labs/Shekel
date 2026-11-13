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
  could not read -- which is exactly how X-v1's first draft of these tests
  graded that page green.  Both were deleted at plan step X-v2 rather than
  gated, because there is no mechanical predicate for "this number is
  invented";
* it grades **GET** routes.  Two of the eight endpoints the census measured are
  POSTs, and they stay outside the sweep (ruling R-CB); the mutating-request
  contract is pinned by :class:`TestTheHandlerItself` instead;
* it proves nothing about surfaces that resolve the baseline WITHOUT the
  balance seam.  Fifteen such sites were counted in ``app/`` (forms, template
  generation, a posting sync deleted since) as finding **N-117**, owned by plan
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
  therefore not something these tests have evidence of, and a future one would
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
