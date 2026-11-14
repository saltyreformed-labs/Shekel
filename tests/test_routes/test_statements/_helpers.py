"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_statements.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
import re
from io import BytesIO

from app.models.account import Account
from app.models.ref import AccountType
from app.services.statement_import import bank_levels
from app.services.statement_match import NEW_ENVELOPE, Verb
from tests.test_services.test_statement_import import _csv_builder as build


_ENTRIES = [
    (date(2026, 3, 2), "-25.00", "POINT OF SALE DEBIT L340 COFFEE"),
    (date(2026, 3, 3), "1500.00", "ACH DEPOSIT TOWN OF CLAYTON  PAYROLL"),
]


def _placed_on(account_id, import_id):
    """Return the day *import_id*'s figure STANDS placed on, or ``None``.

    The level relation's answer (plan step ``balance:X-bj-1``): a standing
    bank level naming the import gives its day; a released one, or an import
    that never placed a figure, gives ``None`` -- exactly what the placement
    column on the import row used to say before the placement became a row.
    """
    for level, release in bank_levels(account_id):
        if level.statement_import_id == import_id and release is None:
            return level.observed_on
    return None


def _payload(entries=None, start="100.00", **kwargs):
    """Return a well-formed SECU CSV payload."""
    return build.build(build.chained(start, entries or _ENTRIES, **kwargs))


def _flash_toasts(body):
    """Return ``(category, message)`` for every flash toast the page rendered.

    **An assertion about the RECEIPT has to read the receipt**, which
    :func:`_delete_form` next door already says about forms and which this
    package's receipt assertions then went on to get wrong anyway: the
    imports table renders each evidence sentence in a ``title`` attribute, so
    ``sentence in response.data`` passed against a receipt that said nothing
    at all.  Proven by isolation --
    dropping the receipt sentence alone left 3 of 4 balance tests green, and
    dropping the table attribute alone left all 4 green.  Found by adversarial
    review of this step's own tests, 2026-08-23.

    **It returns the CATEGORY as well, because the colour is half the
    message.**  ``_import_flash`` exists to say that a green tick over an
    unconfirmed balance tells the owner the opposite of what happened, and the
    same review measured that flipping every category to ``success`` -- or to
    ``warning`` -- left all 32 tests of the one file these parts were split
    from passing.

    Args:
        body: The rendered page.

    Returns:
        One ``(category, message)`` pair per toast, in render order.
    """
    return re.findall(
        r'class="toast text-bg-(\w+)".*?<div class="toast-body">(.*?)</div>',
        body, re.S,
    )


def _delete_form(body):
    """Return just the delete form's markup from a rendered statements page.

    An assertion about a state-changing form has to read THAT form: this page
    renders two, and "the token is somewhere in the body" is satisfied by the
    other one.

    Args:
        body: The rendered page.

    Returns:
        The delete form's markup, from its ``<form`` to its ``</form>``.
    """
    marker = body.index("/statements/delete")
    start = body.rindex("<form", 0, marker)
    return body[start:body.index("</form>", start)]


def _delete_form_for(body, import_id):
    """Return the delete form for ONE import, on a page rendering one per row.

    :func:`_delete_form` returns the first delete form on the page, which on
    a newest-first table is the LATEST import's.  A confirmation assertion
    about a particular import has to read that import's own form, or it
    grades the sentence the page prints for a different delete.

    Args:
        body: The rendered page.
        import_id: The import whose delete form to return.

    Returns:
        That form's markup, from its ``<form`` to its ``</form>``.
    """
    marker = body.index(f'name="import_id" value="{import_id}"')
    start = body.rindex("<form", 0, marker)
    return body[start:body.index("</form>", start)]


def _a_loan_account(db, seed_user):
    """Return a loan account, which the statement pages do not serve.

    Args:
        db: The session fixture.
        seed_user: The seeded user bundle.

    Returns:
        The staged :class:`~app.models.account.Account`.
    """
    loan_type = (
        db.session.query(AccountType).filter_by(name="Auto Loan").one()
    )
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=loan_type.id,
        name="Van Loan",
    )
    db.session.add(account)
    db.session.flush()
    return account


def _upload(client, account_id, payload, source="secu_checking_csv",
            filename="statement.csv", **extra):
    """POST a statement file the way the form does."""
    data = {"source": source, **extra}
    if payload is not None:
        data["statement_file"] = (BytesIO(payload), filename)
    return client.post(
        f"/accounts/{account_id}/statements",
        data=data,
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def _record_in_a_new_envelope(client, account_id, line, category_id,
                              name="Coffee"):
    """Record one bank line as a purchase in a new envelope, as a browser does.

    **Through the RECONCILE door**, which since plan step
    ``bank_import:X-gi-2`` is the only one there is: the review queue this
    posted to until then was deleted with the register and the workbench.  The
    body is the one that page's cards emit -- the OK checkbox consents to a
    line, the verb radio names the act, and the three ADD controls carry the
    destination -- rather than the queue's, which keyed everything off the
    destination select alone.

    **It is a SETUP step and it asserts its own success**, because both cases
    using it are about what the import DELETE then destroys: a recording that
    silently did not happen would leave them asserting a receipt over nothing.
    The 200 is the whole check, the door answering a refusal with its own
    designed 400.

    Args:
        client: The logged-in client.
        account_id: The account being reconciled.
        line: The bank line row to record.
        category_id: The category the new envelope is filed under.
        name: What to call the new envelope.

    Returns:
        The response.
    """
    response = client.post(
        f"/accounts/{account_id}/statements/reconcile",
        data={
            "ok": str(line.id),
            f"verb-{line.id}": Verb.ADD.value,
            f"destination-{line.id}": NEW_ENVELOPE,
            f"envelope_name-{line.id}": name,
            f"category_id-{line.id}": str(category_id),
        },
    )
    assert response.status_code == 200, (
        f"the Reconcile door refused this recording ({response.status_code}), "
        f"so nothing was created and the delete under test would have "
        f"nothing to destroy"
    )
    return response
