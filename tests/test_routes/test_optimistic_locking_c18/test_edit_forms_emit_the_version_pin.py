"""C-18 optimistic-lock tests: every edit form renders a hidden version_id input holding the row's
current counter, so the round trip closes.
"""

from ._helpers import (
    _make_template,
    _make_transaction,
    _make_envelope_template_and_txn,
    _make_entry,
    _make_savings_account,
    _make_savings_goal,
    _make_salary_profile,
    _make_salary_raise,
    _make_paycheck_line,
    _make_transfer,
    _make_transfer_template,
)


# ═════════════════════════════════════════════════════════════════════
# Templates ship the version_id pin -- regression guards
# ═════════════════════════════════════════════════════════════════════


class TestEditTemplatesEmitVersionPin:
    """Edit forms must include ``<input type="hidden" name="version_id">``."""

    def test_transaction_quick_edit_includes_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The grid quick-edit template ships the txn's current version."""
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods[0])
            txn_id = txn.id
            v = txn.version_id

            response = auth_client.get(f"/transactions/{txn_id}/quick-edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_transaction_full_edit_includes_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The grid full-edit popover ships the txn's current version."""
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods[0])
            txn_id = txn.id
            v = txn.version_id

            response = auth_client.get(f"/transactions/{txn_id}/full-edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_transfer_quick_edit_includes_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The grid transfer quick-edit ships the xfer's current version."""
        with app.app_context():
            xfer = _make_transfer(seed_user, seed_periods[0])
            xfer_id = xfer.id
            v = xfer.version_id

            response = auth_client.get(
                f"/transfers/quick-edit/{xfer_id}",
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_transfer_full_edit_includes_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The grid transfer full-edit ships the xfer's current version."""
        with app.app_context():
            xfer = _make_transfer(seed_user, seed_periods[0])
            xfer_id = xfer.id
            v = xfer.version_id

            response = auth_client.get(
                f"/transfers/{xfer_id}/full-edit",
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_transaction_template_edit_form_includes_version(
        self, app, auth_client, seed_user,
    ):
        """The recurring-transaction edit form ships the row's version."""
        with app.app_context():
            cat = seed_user["categories"]["Groceries"]
            tmpl = _make_template(
                seed_user["user"].id,
                seed_user["account"].id,
                cat.id,
            )
            tmpl_id = tmpl.id
            v = tmpl.version_id

            response = auth_client.get(f"/templates/{tmpl_id}/edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_transaction_template_create_form_omits_version(
        self, app, auth_client,
    ):
        """The create form has no version pin (no row to lock yet).

        Mirrors test_account_create_form_omits_version_pin in
        test_accounts/test_stale_data.py: a copy-paste regression that put a
        ``template.version_id`` reference into the create path
        would Jinja-error against ``template = None``.
        """
        with app.app_context():
            response = auth_client.get("/templates/new")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' not in body

    def test_transfer_template_edit_form_includes_version(
        self, app, auth_client, seed_user,
    ):
        """The recurring-transfer edit form ships the row's version."""
        with app.app_context():
            savings = _make_savings_account(seed_user["user"].id)
            cat = seed_user["categories"]["Groceries"]
            tmpl = _make_transfer_template(
                seed_user["user"].id,
                seed_user["account"].id,
                savings.id,
                cat.id,
            )
            tmpl_id = tmpl.id
            v = tmpl.version_id

            response = auth_client.get(f"/transfers/{tmpl_id}/edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_savings_goal_edit_form_includes_version(
        self, app, auth_client, seed_user,
    ):
        """The savings-goal edit form ships the row's version."""
        with app.app_context():
            savings = _make_savings_account(seed_user["user"].id)
            goal = _make_savings_goal(seed_user["user"].id, savings.id)
            goal_id = goal.id
            v = goal.version_id

            response = auth_client.get(
                f"/savings/goals/{goal_id}/edit",
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_salary_profile_edit_form_includes_version(
        self, app, auth_client, seed_user,
    ):
        """The salary-profile edit form ships the row's version."""
        with app.app_context():
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            profile_id = profile.id
            v = profile.version_id

            response = auth_client.get(f"/salary/{profile_id}/edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body

    def test_salary_raise_edit_button_carries_version(
        self, app, auth_client, seed_user,
    ):
        """The raise edit button carries ``data-raise-version-id``.

        Salary raises share a single form for create and edit; the
        version pin is plumbed into the form by app.js when the user
        clicks the edit button, which carries the row's version_id
        as a data attribute.  This test asserts the data attribute
        is rendered server-side so the JS hand-off cannot regress
        silently.
        """
        with app.app_context():
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            sraise = _make_salary_raise(profile.id)
            v = sraise.version_id

            response = auth_client.get(f"/salary/{profile.id}/edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert (
                f'data-raise-version-id="{v}"' in body
            ), (
                "Raise edit button must include data-raise-version-id "
                "so app.js can populate the form's hidden version "
                "input on edit."
            )

    def test_paycheck_line_edit_button_carries_version(
        self, app, auth_client, seed_user,
    ):
        """The deduction edit button carries ``data-line-version-id``.

        Same shape as the raise test: the edit button surfaces the
        row's current version as a data attribute that app.js wires
        into the shared add/edit form.
        """
        with app.app_context():
            profile = _make_salary_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            ded = _make_paycheck_line(profile.id)
            v = ded.version_id

            response = auth_client.get(f"/salary/{profile.id}/edit")
            assert response.status_code == 200
            body = response.data.decode()
            assert (
                f'data-line-version-id="{v}"' in body
            ), (
                "Deduction edit button must include "
                "data-line-version-id so app.js can populate the "
                "form's hidden version input on edit."
            )

    def test_entry_inline_edit_form_includes_version(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The entry inline-edit form ships the row's version_id."""
        with app.app_context():
            _tmpl, txn = _make_envelope_template_and_txn(
                seed_user, seed_periods[0],
            )
            entry = _make_entry(txn.id, seed_user["user"].id)
            entry_id = entry.id
            v = entry.version_id

            response = auth_client.get(
                f"/transactions/{txn.id}/entries?editing={entry_id}",
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert 'name="version_id"' in body
            assert f'value="{v}"' in body
