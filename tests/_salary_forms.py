"""The salary pages' forms, as a test posts them: what the page renders.

Shared by the pay list's route suites (plan step salary:X-av-3b): the Fix
suite, ``tests/test_routes/test_salary_pay_fix.py``, held these until the
Record suite, ``test_salary_pay_record.py``, needed a job made the way the
owner makes one -- through the create form, which is also what gives the job
its paycheck template and its projected paychecks.
"""

from app.extensions import db
from app.models.salary_profile import SalaryProfile
from tests._form_controls import rendered_form_controls
from tests._stub_period import stub_period_on


def rendered_page_form(client, page, action):
    """Return the controls the form posting to *action* on *page* submits.

    Args:
        client: The signed-in test client.
        page: The page's URL.
        action: The form's ``action``, as the page renders it.

    Returns:
        ``{name: [value, ...]}``, as :func:`rendered_form_controls` reads it.
    """
    response = client.get(page)
    assert response.status_code == 200
    return rendered_form_controls(response.get_data(as_text=True), action)


def create_through_the_form(client, seed_user, pay, payday):
    """POST the create form as ``salary/form.html`` renders it, typing *pay* and *payday*.

    Args:
        client: The signed-in test client.
        seed_user: The ``seed_user`` fixture dict.
        pay: What one paycheck pays, as typed (a string).
        payday: The payday it pays from, as typed (an ISO string).

    Returns:
        The new :class:`~app.models.salary_profile.SalaryProfile`, named
        ``Day Job``.
    """
    controls = rendered_page_form(client, "/salary/new", "/salary")
    # The owner types the name and pay and PICKS the state: the form opens on
    # "Choose your state" and chooses none (plan step salary:X-at-3).
    controls.update({
        "name": "Day Job", "pay_amount": pay, "pay_payday": payday,
        "state_code": "NC", **stub_period_on(payday),
    })
    response = client.post("/salary", data=controls)
    assert response.status_code == 302, response.get_data(as_text=True)
    return db.session.query(SalaryProfile).filter_by(
        user_id=seed_user["user"].id, name="Day Job",
    ).one()


__all__ = ["create_through_the_form", "rendered_page_form"]
