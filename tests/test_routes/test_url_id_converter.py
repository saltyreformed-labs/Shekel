"""The path layer answers "what row does this digit string name" once.

Plan step X-ae / finding N-140.  Werkzeug's stock ``<int:>`` converter has
``regex = r"\\d+"`` compiled without ``re.ASCII``, and through Werkzeug 3.1.8
its ``to_python`` was a bare ``int()``, so before :mod:`app.url_converters`
the route tree's 123 path parameters carried both halves of the defect
finding N-136 closed for form bodies: one row id under many spellings, and an
unhandled ``ValueError`` on a long enough digit run.

**The 500 arm was the more serious of the two and these tests are the only
place it is graded**, because it raised inside ``url_adapter.match()`` --
ahead of the view, ahead of the login gate (``app/login_gate.py``), ahead of
any session -- so no route test could reach it and no authentication was
needed to trigger it.  Werkzeug 3.1.9 closed that arm in its own converter
(pallets/werkzeug#3237) and left the spelling arm open; a premise test pins
each stock answer, so the day Werkzeug changes either one a test here says
so.
"""

import pytest
from werkzeug.exceptions import MethodNotAllowed, NotFound
from werkzeug.routing import IntegerConverter, Map, Rule

from app.url_converters import RowIdConverter


# The longest ASCII digit run CPython will convert, plus one.  Read from the
# interpreter rather than hard-coded at 4,300: the ceiling is configurable at
# runtime (``sys.set_int_max_str_digits``), so a literal here would silently
# stop testing the boundary it names.
def _oversized_digits():
    """Return a digit run one longer than CPython will convert."""
    import sys  # pylint: disable=import-outside-toplevel

    return "1" * (sys.get_int_max_str_digits() + 1)


class TestTheConverterIsInstalled:
    """The override reaches the real application's real rules."""

    def test_the_app_uses_the_row_id_converter_for_int(self, app):
        """``int`` resolves to ours, not Werkzeug's.

        Asserted directly because every behavioural test below would pass
        vacuously against a stock converter that merely happened to agree on
        the input chosen -- and because the registration ORDER is the subtle
        part: Werkzeug binds a rule's converter when the rule is added, so a
        registration after ``_register_blueprints`` would leave every existing
        rule lax while this attribute still looked correct.
        """
        assert app.url_map.converters["int"] is RowIdConverter

    def test_every_int_rule_in_the_app_carries_it(self, app):
        """No rule escaped the override -- checked over the whole map.

        The failure this guards is a partial application: a blueprint
        registered before the converter, or a rule built on a second map,
        would keep the stock converter and stay lax with nothing to say so.
        """
        int_converters = [
            converter
            for rule in app.url_map.iter_rules()
            for converter in rule._converters.values()  # pylint: disable=protected-access
            if isinstance(converter, RowIdConverter)
        ]
        # Premise: the map really does carry the ~123 id parameters, so a
        # regression that emptied this list could not pass as "all clean".
        assert len(int_converters) > 100, (
            f"only {len(int_converters)} row-id path parameters found; the "
            "override may have run after the blueprints"
        )
        stock = [
            (rule.rule, name)
            for rule in app.url_map.iter_rules()
            for name, converter in rule._converters.items()  # pylint: disable=protected-access
            if type(converter).__name__ == "IntegerConverter"
        ]
        assert stock == [], f"rules still on the stock lax converter: {stock}"


class TestTheOversizedPathSegment:
    """The digit runs that were 500s, and the stock answer each rests on."""

    def test_an_oversized_digit_run_does_not_raise_out_of_routing(self, app):
        """It is refused instead of raising ``ValueError`` before the view.

        This is the arm that matters: the raise happened inside
        ``ctx.push()``, so it needed no session, no CSRF token and no account.
        ``app/error_handlers.py`` registers 400/403/404/413/429/500,
        ``BaselineMissingError`` and ``PayCalendarError`` -- no arm for a bare
        ``ValueError`` (both of those subclass it, and Flask looks a handler
        up along the RAISED class's MRO, so each arm catches only its own) --
        so it surfaced as an unhandled 500 to an anonymous caller.

        **The refusal is the login redirect, not a 404, since plan step
        ``bank_import:X-gi-4``** (ruling **R-BI4**, developer 2026-09-11): an
        anonymous request that matches no route is bounced by the login gate,
        which runs after routing and so still sits downstream of the
        ``ValueError`` this case exists to keep out.  It asserted 404 until
        then; the arm graded is unchanged.

        **It grades the APPLICATION's answer -- no 500 -- and not whose
        refusal produced it.**  On Werkzeug 3.1.9 the stock converter refuses
        this run too (the premise test below), so this case would pass with
        :class:`RowIdConverter` handing the run to ``super()``.  The
        converter's own refusal of it is
        :func:`~app.utils.digit_strings.parse_row_id`'s, graded by
        ``tests/test_utils/test_digit_strings.py``'s
        ``test_a_digit_run_past_the_conversion_limit_returns_none``.
        """
        client = app.test_client()
        response = client.get(f"/accounts/{_oversized_digits()}/details")
        assert response.status_code == 302
        assert response.headers["Location"].startswith("/login")

    def test_the_public_and_submodule_converter_are_one_class(self):
        """The import path this module depends on is the documented one.

        `RowIdConverter` subclasses a third-party class, so the narrower the
        surface the better: ``pyproject.toml`` declares Werkzeug and ``uv.lock``
        pins it (finding N-143), and every bump of that pin still reaches this
        subclass.
        Importing from the public ``werkzeug.routing`` rather than the
        ``werkzeug.routing.converters`` submodule costs nothing -- this
        asserts they really are the same object, so the choice is a free
        reduction in exposure rather than a guess.
        """
        from werkzeug.routing.converters import (  # pylint: disable=import-outside-toplevel
            IntegerConverter as SubmoduleConverter,
        )

        assert IntegerConverter is SubmoduleConverter
        assert issubclass(RowIdConverter, IntegerConverter)

    def test_the_stock_converter_refuses_an_oversized_run(self):
        """The 500's premise, on Werkzeug's own class rather than on our word.

        It asserted the opposite until plan step ``balance:X-dk``: through
        Werkzeug 3.1.8 the stock converter's bare ``int()`` raised
        ``ValueError("Exceeds the limit ...")`` out of ``match()``, the
        unauthenticated 500 plan step X-ae closed with this module's
        converter.  Werkzeug 3.1.9 closed it upstream (pallets/werkzeug#3237)
        and the stock converter now refuses the run itself, so ``match()``
        raises ``NotFound``.  **It pins that answer** so a Werkzeug that
        brought the raise back is seen here: :class:`RowIdConverter` would
        still refuse the run, but the docstrings in ``app/url_converters.py``
        that call the raise closed upstream would be false.
        """
        stock = Map([
            Rule("/x/<int:row_id>", endpoint="x"),
        ]).bind("localhost")
        assert isinstance(
            stock.map._rules[0]._converters["row_id"],  # pylint: disable=protected-access
            IntegerConverter,
        )
        with pytest.raises(NotFound):
            stock.match(f"/x/{_oversized_digits()}")
        # The control: the same stock rule matches an ordinary id, so the
        # refusal above is about the oversized value, not about the rule.
        assert stock.match("/x/7") == ("x", {"row_id": 7})

    def test_a_long_but_convertible_run_is_refused_by_the_converter(
        self, app, auth_client,
    ):
        """A 40-digit id is canonical ASCII and still names no row: no column holds it.

        ``'9' * 40`` is above :data:`MIN_ROW_ID` and round-trips through
        ``str``, and until plan step balance:X-dj it routed, reached the view
        and met the ordinary ownership lookup's 404 -- psycopg2 inlined the
        number and the comparison simply matched nothing.  psycopg 3 binds it
        with a server-side ``::INTEGER`` cast that REFUSES it (SQLSTATE
        22003), so it would have been a 500; the converter now refuses any id
        above :data:`~app.utils.digit_strings.MAX_INTEGER_COLUMN`, and the
        answer is the same 404 before any query runs (ruling R-BAL211).  The
        largest id a column holds still routes and finds nothing:
        ``tests/test_routes/test_an_integer_no_column_holds.py``.

        **The MAP is asserted before the response**, because a 404 alone
        cannot tell "routed and found nothing" from "refused by the
        converter" -- and those are opposite claims about this input.
        """
        huge = "9" * 40
        with pytest.raises(NotFound):
            app.url_map.bind("localhost").match(f"/accounts/{huge}/details")

        response = auth_client.get(f"/accounts/{huge}/details")
        assert response.status_code == 404


class TestTheSpellingOfAPathId:
    """One row id, one path spelling."""

    def test_a_non_ascii_digit_path_does_not_reach_the_route(
        self, app, auth_client, seed_user,
    ):
        """``/accounts/١/details`` no longer answers as ``/accounts/1/details``.

        Measured before the fix: byte-identical responses, because
        ``int('١')`` is ``1``.  The authenticated client is the point --
        this is not a login failure, it is a routing refusal, so the owner
        of the account gets the 404 too.
        """
        with app.app_context():
            account_id = seed_user["account"].id

        ascii_response = auth_client.get(f"/accounts/{account_id}/details")
        assert ascii_response.status_code == 200

        respelled = str(account_id).translate(
            str.maketrans("0123456789", "٠١٢٣٤"
                                        "٥٦٧٨٩"),
        )
        # The premise: same id, and the stock converter's regex matched it.
        assert int(respelled) == account_id
        assert respelled.isdigit()

        assert auth_client.get(f"/accounts/{respelled}/details").status_code == 404

    def test_the_stock_converter_still_admits_every_digit_script(self):
        """The spelling arm's premise, on Werkzeug's own class.

        The case above says the stock converter's regex matched a non-ASCII
        spelling, and ``isdigit()`` alone cannot show that: this asks the
        stock class.  It is the half of plan step X-ae that Werkzeug 3.1.9
        left open (measured at plan step ``balance:X-dk``), so
        ``app/url_converters.py`` says it is unchanged upstream; the day
        Werkzeug compiles the regex ASCII-only, this fails and that sentence
        is re-read.
        """
        stock = Map([
            Rule("/x/<int:row_id>", endpoint="x"),
        ]).bind("localhost")

        assert stock.match("/x/١") == ("x", {"row_id": 1})

    def test_a_zero_padded_path_id_is_refused(
        self, app, auth_client, seed_user,
    ):
        """``/accounts/007/details`` is a second spelling of row 7.

        ``url_for`` emits ``str(int)`` and never pads, so nothing the
        application generates is affected; a padded id can only be
        hand-made, and it names a row that already has a spelling.
        """
        with app.app_context():
            account_id = seed_user["account"].id

        padded = f"00{account_id}"
        assert int(padded) == account_id
        assert auth_client.get(f"/accounts/{padded}/details").status_code == 404

    def test_a_zero_id_is_refused_by_ROUTING_not_by_the_lookup(self, app):
        """No table in either database holds a row with ``id < 1``.

        Asserted at the MAP rather than through a request, because through a
        request this test is vacuous -- an adversarial review measured it
        passing against the stock lax converter, since ``/accounts/0/details``
        routes to the view and ``get_or_404`` answers 404 anyway.  A 404 from
        two different causes is not evidence about the converter.  Matching
        the map directly distinguishes them: the rule must not match at all.
        """
        adapter = app.url_map.bind("localhost")
        with pytest.raises(NotFound):
            adapter.match("/accounts/0/details")
        # The control: the same rule DOES match a canonical id, so the
        # refusal above is about the value and not about the path.
        endpoint, args = adapter.match("/accounts/7/details")
        assert args == {"account_id": 7}
        assert endpoint == "accounts.cash_detail"

    def test_url_for_still_builds_every_id_url(self, app, seed_user):
        """The override must not break URL GENERATION, only matching.

        ``to_url`` is inherited unchanged, but a converter override is
        exactly the kind of change that can pass every match test and break
        every rendered link, so the build direction is asserted explicitly.
        """
        from flask import url_for  # pylint: disable=import-outside-toplevel

        with app.test_request_context():
            account_id = seed_user["account"].id
            assert url_for(
                "accounts.cash_detail", account_id=account_id,
            ).endswith(f"/accounts/{account_id}/details")


class TestARefusalEndsMatching:
    """What :class:`RowIdConverter`'s two layers each do to ROUTING.

    Both claims are the converter docstring's, and until plan step
    ``balance:X-dk`` neither was graded: an adversarial review set the
    converter's ``regex`` back to the stock ``r"\\d+"`` and 312 tests across
    this module and its neighbours stayed green.  A map with a second rule
    that WOULD match each refused segment is what tells the two layers apart.
    """

    @staticmethod
    def _id_rule_beside_a_name_rule():
        """Bind ``/z/<int:a>`` and ``/z/<b>`` under the application's converter.

        Werkzeug tries the ``int`` rule first (its converter weighs less), so
        every segment below reaches it before the name rule.
        """
        return Map(
            [Rule("/z/<int:a>", endpoint="by_id"), Rule("/z/<b>", endpoint="by_name")],
            converters={"int": RowIdConverter},
        ).bind("localhost")

    def test_a_non_ascii_segment_never_selects_the_id_rule(self):
        """The REGEX layer: ``'١'`` is not a candidate, so the name rule takes it.

        With the stock ``\\d+`` the id rule would be selected and its refusal
        would end matching in a 404 -- the case below -- so falling through to
        the name rule is what only the ASCII regex produces.
        """
        adapter = self._id_rule_beside_a_name_rule()

        assert adapter.match("/z/١") == ("by_name", {"b": "١"})
        # The control: a canonical id still selects the id rule.
        assert adapter.match("/z/7") == ("by_id", {"a": 7})

    @pytest.mark.parametrize("segment", ["0", "007", "9" * 40])
    def test_a_refused_id_ends_matching_before_the_name_rule(self, segment):
        """The PARSE layer: a ``ValidationError`` is ``NoMatch``, not "try the next rule".

        The name rule would match every one of these segments; it is never
        tried.
        """
        adapter = self._id_rule_beside_a_name_rule()

        with pytest.raises(NotFound):
            adapter.match(f"/z/{segment}")

    def test_a_refusal_on_a_path_with_another_methods_rule_is_a_405(self, app):
        """``DELETE /transactions/0`` is a 405, not the 404 a missing row gets.

        The ``PATCH`` rule on the same path is visited first and records its
        method, so the refusal surfaces as ``MethodNotAllowed`` -- which is why
        the converter's docstring says 404 OR 405.  The control: a canonical
        id routes to the ``DELETE`` view.
        """
        adapter = app.url_map.bind("localhost")

        with pytest.raises(MethodNotAllowed):
            adapter.match("/transactions/0", method="DELETE")
        assert adapter.match("/transactions/7", method="DELETE") == (
            "transactions.delete_transaction", {"txn_id": 7},
        )


class TestARealIdStillWorks:
    """The regression that would matter most: ordinary routing is unchanged."""

    def test_the_owner_still_reaches_their_own_account(
        self, app, auth_client, seed_user,
    ):
        """A canonical id routes exactly as before, content included."""
        with app.app_context():
            account_name = seed_user["account"].name
            account_id = seed_user["account"].id

        response = auth_client.get(f"/accounts/{account_id}/details")
        assert response.status_code == 200
        assert account_name.encode() in response.data
