"""An integer no PostgreSQL ``integer`` column holds never reaches a query.

Plan step balance:X-dj.  psycopg 3's SQLAlchemy dialect binds an ``Integer``
with a server-side cast (``%(id_1)s::INTEGER``), so an integer outside the
column's range is refused by the CAST -- SQLSTATE 22003, a ``DataError`` and
an unhandled 500 -- where psycopg2 inlined the literal and matched no row.

**What this module grades: the query-string integers that REACH A QUERY.**
:func:`~app.utils.digit_strings.integer_arg` holds every one of them to the
column's range and reads an out-of-range value exactly as it reads an
unparseable one -- as the site's default.  :data:`QUERY_SITES` holds one
request per code site whose integer reaches a query, from a census MEASURED
on 2026-10-05 rather than read off the code: the ``integer_arg`` reads in
``app/routes`` were driven with a value one past the ceiling and
``integer_arg`` replaced by a bare ``int()``, and eight code sites raised
``DataError`` (the quick-create form's owner lookup reads two of its
arguments, so the eight take nine requests here; a site several endpoints
reach, such as the grid partials' shared resolver, is requested once).  A
ninth site is MASKED: ``analytics.py``'s ``_resolve_window_params`` reads the
income statement's ``period_id`` into a query too, but only after the same
request's ownership check has read it, so the census saw the first site
raise and the one income-statement request grades both.  Every other site
the census drove reads its integer in Python alone -- a window offset or
length, a year or month, a cadence count, an id looked up in a calendar
already loaded or compared in a template -- so it could not 500 this way and
grades nothing here.  One read was not driven: ``investment.py``'s growth
chart ``horizon_years``, which the service clamps to 1-40 in Python.  That
every such read goes through ``integer_arg`` at all is the architecture
guard's (``tests/test_arch/test_a_query_string_integer_is_bounded.py``).

One site is not ``request.args``: the cell fragment reads the PAGE's
``account_id`` off htmx's ``HX-Current-URL`` header, through the same
``type=integer_arg`` coercion.  And the other doors a request integer
arrives through are graded where their rule lives: a PATH id by the
``<int:>`` converter (``tests/test_routes/test_url_id_converter.py``), a form
or schema id by :func:`~app.utils.digit_strings.parse_row_id` and
:class:`~app.schemas.validation._helpers.RowId`
(``tests/test_utils/test_digit_strings.py``,
``tests/test_schemas/test_row_id_field.py``).

**Every case asserts the URL still ROUTES before it reads the response**,
because a 404 from the URL map and a 404 from the code look identical and are
opposite claims about the input.
"""

import pytest

from app.enums import RecurrenceUnitEnum
from app.extensions import db
from app.utils.digit_strings import MAX_INTEGER_COLUMN, MIN_INTEGER_COLUMN
from tests._csrf_tokens import without_csrf_tokens
from tests._test_helpers import add_txn, cadence_payload, make_salary_profile

#: The header htmx sends.  An analytics tab answers a request without it with
#: the analytics shell rather than its fragment (D13), and the grid partial is
#: only ever requested by htmx, so both are requested the way htmx does.
_HTMX = {"HX-Request": "true"}

#: The query sites whose integer reaches a query (see the module docstring
#: for how the census was taken).  Each is ``(url, headers)``, formatted with
#: ``v`` (the value under test) and the ids :func:`world` seeds.
QUERY_SITES = (
    pytest.param("/grid?account_id={v}", {}, id="grid-page"),
    pytest.param("/grid/balance-row?account_id={v}", _HTMX, id="grid-partials"),
    pytest.param(
        "/analytics/calendar?account_id={v}", _HTMX, id="analytics-calendar",
    ),
    pytest.param(
        "/analytics/income-statement?period_id={v}", _HTMX,
        id="analytics-income-statement",
    ),
    pytest.param(
        "/transactions/new/quick?category_id={v}&period_id={period}"
        "&account_id={account}",
        {}, id="quick-create-category",
    ),
    pytest.param(
        "/transactions/new/quick?category_id={category}&period_id={period}"
        "&account_id={v}",
        {}, id="quick-create-account",
    ),
    pytest.param("/salary?profile={v}", {}, id="salary-cockpit-profile"),
    pytest.param(
        "/templates/preview-recurrence?{cadence}&account_id={v}", {},
        id="recurrence-preview-account",
    ),
    pytest.param(
        "/transactions/{txn}/cell",
        {"HX-Current-URL": "http://localhost/grid?account_id={v}"},
        id="cell-fragment-current-url",
    ),
)


@pytest.fixture()
def world(app, seed_user, seed_periods_today):
    """Seed the rows each site needs before its integer is read.

    The salary cockpit reads ``?profile`` only once a profile exists, the
    cell fragment needs a transaction to draw, and the quick-create form
    reads ``account_id`` only after its category resolves.

    Returns:
        The ids :data:`QUERY_SITES` is formatted with, beside the
        ``cadence`` query a previewable recurrence carries.
    """
    with app.app_context():
        make_salary_profile(seed_user, db.session)
        txn = add_txn(
            db.session, seed_user, seed_periods_today[3], "Rent", "100.00",
        )
        db.session.commit()
        cadence = cadence_payload(
            unit=RecurrenceUnitEnum.PERIOD,
            starts_on=seed_periods_today[0].start_date,
        )
        return {
            "txn": txn.id,
            "period": seed_periods_today[3].id,
            "account": seed_user["account"].id,
            "category": next(iter(seed_user["categories"].values())).id,
            "cadence": "&".join(f"{k}={val}" for k, val in cadence.items()),
        }


def _get(client, url, headers, ids, value):
    """GET *url* with *headers*, each formatted with *ids* and ``v=value``."""
    return client.get(
        url.format(v=value, **ids),
        headers={name: text.format(v=value) for name, text in headers.items()},
    )


def _body(response):
    """Return *response*'s text with the CSRF token's value blanked."""
    return without_csrf_tokens(response.get_data(as_text=True))


class TestAQueryStringIntegerOutsideTheRange:
    """Out of range is read as the site's default -- never a 500."""

    @pytest.mark.parametrize(("url", "headers"), QUERY_SITES)
    @pytest.mark.parametrize(
        "beyond", [MAX_INTEGER_COLUMN + 1, MIN_INTEGER_COLUMN - 1],
    )
    def test_it_answers_as_an_unparseable_value_does(
        self, app, auth_client, world, url, headers, beyond,
    ):
        """The same response as ``abc`` at the same site, and that is not a 500.

        ``abc`` is the oracle because it is the input every site already
        answered with its default (Werkzeug catches the ``ValueError`` its
        ``type=`` raises); an out-of-range integer must now be
        indistinguishable from it -- status AND body.
        """
        path = url.format(v=beyond, **world).split("?", 1)[0]
        assert app.url_map.bind("localhost").match(path, method="GET")
        out_of_range = _get(auth_client, url, headers, world, beyond)
        unparseable = _get(auth_client, url, headers, world, "abc")
        assert out_of_range.status_code != 500
        assert out_of_range.status_code == unparseable.status_code
        assert _body(out_of_range) == _body(unparseable)


class TestThePathIdAtTheColumnCeiling:
    """The largest id an ``id`` column holds is a well-formed path id."""

    def test_the_ceiling_routes_and_finds_nothing(self, app, auth_client):
        """``2147483647`` routes, and no row has it.

        The far side -- one past the ceiling, refused by the converter -- is
        pinned at exactly ``MAX_INTEGER_COLUMN + 1`` by ``parse_row_id``'s own
        tests (``tests/test_utils/test_digit_strings.py``); that the converter
        delegates to ``parse_row_id`` is pinned by ``test_url_id_converter.py``.
        """
        url = f"/accounts/{MAX_INTEGER_COLUMN}/details"
        endpoint, args = app.url_map.bind("localhost").match(url)
        assert endpoint == "accounts.cash_detail"
        assert args == {"account_id": MAX_INTEGER_COLUMN}
        assert auth_client.get(url).status_code == 404
