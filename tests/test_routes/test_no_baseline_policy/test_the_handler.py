"""The handler's own contract, and the handler inert for a healthy owner."""

import pytest

from app.models.transfer_template import TransferTemplate
from tests._test_helpers import cadence_payload

from ._helpers import _BASELINE_LESS_OWNER


@pytest.mark.seeded_start_state(_BASELINE_LESS_OWNER)
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
            "amount": "150.00",
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
