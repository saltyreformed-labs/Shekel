"""The sweep: no GET route may 5xx for an owner with no baseline scenario."""

from urllib.parse import urlsplit

import pytest

from werkzeug.exceptions import HTTPException

from ._helpers import (
    _MEASURED_PAGE_DOORS,
    _MEASURED_DOORS,
    _ACCOUNT_KINDS,
    _ACCOUNT_CONVERTER,
    _CONVERTER_TOKEN,
    _CONVERTER_ROWS,
    _BASELINE_LESS_OWNER,
    _get_rules,
    _sweep_cases,
    _fill_converters,
    _is_answered_by_its_own_row,
    _SWEEP_CASES,
    _SWEEP_IDS,
    _SWEPT_ENDPOINTS,
)


@pytest.mark.seeded_start_state(_BASELINE_LESS_OWNER)
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
        # members nobody censused, and the world hands out TWELVE keys that are
        # not account kinds (`user_id`, `period`, `rent_category`, the seven
        # rows X-be-3 added, salary:S11-b's pay stub and plan#2331's card terms
        # version), every one of which would have to be named in the
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
