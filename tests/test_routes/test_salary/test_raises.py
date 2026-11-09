"""Salary route tests: raises, a raise's end year, and the raise label filter.

Split from ``tests/test_routes/test_salary.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.salary_raise import SalaryRaise
from app.models.ref import RaiseType

from ._helpers import _create_profile, _create_other_user_profile


# ── Raises ─────────────────────────────────────────────────────────


class TestRaises:
    """Tests for raise add/delete endpoints."""

    def test_add_raise(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/<id>/raises adds a raise and redirects."""
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            response = auth_client.post(
                f"/salary/{profile.id}/raises",
                data={
                    "raise_type_id": raise_type.id,
                    "effective_date": "2026-07-01",
                    "percentage": "3",
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Raise added." in response.data

    def test_add_raise_htmx_returns_partial(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/<id>/raises with HX-Request returns a partial."""
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="cola").one()

            response = auth_client.post(
                f"/salary/{profile.id}/raises",
                data={
                    "raise_type_id": raise_type.id,
                    "effective_date": "2027-01-01",
                    "flat_amount": "2000.00",
                },
                headers={"HX-Request": "true"},
            )

            assert response.status_code == 200
            assert b"Cola" in response.data
            assert b"2000.00" in response.data

    def test_add_raise_htmx_response_contains_raises_section_id(
        self, app, auth_client, seed_user, seed_periods
    ):
        """HTMX partial must include id='raises-section' so subsequent swaps work.

        Without this id on the partial's root element, the first HTMX swap
        replaces the target and subsequent operations cannot find #raises-section.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            response = auth_client.post(
                f"/salary/{profile.id}/raises",
                data={
                    "raise_type_id": raise_type.id,
                    "effective_date": "2027-06-01",
                    "percentage": "3",
                },
                headers={"HX-Request": "true"},
            )

            assert response.status_code == 200
            assert b'id="raises-section"' in response.data

    def test_delete_raise_htmx_response_contains_raises_section_id(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Delete-raise HTMX partial must also preserve the raises-section id."""
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            salary_raise = SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=raise_type.id,
                effective_date=date(2026, 3, 1),
                percentage=Decimal("0.0200"),
            )
            db.session.add(salary_raise)
            db.session.commit()

            response = auth_client.post(
                f"/salary/raises/{salary_raise.id}/delete",
                headers={"HX-Request": "true"},
            )

            assert response.status_code == 200
            assert b'id="raises-section"' in response.data

    def test_update_raise(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/raises/<id>/edit updates an existing raise."""
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()
            cola_type = db.session.query(RaiseType).filter_by(name="cola").one()

            salary_raise = SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=raise_type.id,
                effective_date=date(2026, 3, 1),
                percentage=Decimal("0.0300"),
            )
            db.session.add(salary_raise)
            db.session.commit()

            response = auth_client.post(
                f"/salary/raises/{salary_raise.id}/edit",
                data={
                    "raise_type_id": cola_type.id,
                    "effective_date": "2027-06-01",
                    "percentage": "4.50",
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Raise updated." in response.data

            db.session.refresh(salary_raise)
            assert salary_raise.raise_type_id == cola_type.id
            assert salary_raise.effective_date == date(2027, 6, 1)
            assert salary_raise.percentage == Decimal("0.0450")

    def test_update_raise_htmx_returns_partial(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /salary/raises/<id>/edit with HX-Request returns the raises partial."""
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            salary_raise = SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=raise_type.id,
                effective_date=date(2026, 1, 1),
                flat_amount=Decimal("5000.00"),
            )
            db.session.add(salary_raise)
            db.session.commit()

            response = auth_client.post(
                f"/salary/raises/{salary_raise.id}/edit",
                data={
                    "raise_type_id": raise_type.id,
                    "effective_date": "2026-01-01",
                    "flat_amount": "6000.00",
                },
                headers={"HX-Request": "true"},
            )

            assert response.status_code == 200
            assert b'id="raises-section"' in response.data
            assert b"6000.00" in response.data

    def test_update_raise_other_user_blocked(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /salary/raises/<id>/edit on another user's raise is rejected."""
        with app.app_context():
            # Create a raise owned by a different user.
            other = _create_other_user_profile()
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            other_raise = SalaryRaise(
                salary_profile_id=other["profile"].id,
                raise_type_id=raise_type.id,
                effective_date=date(2026, 6, 1),
                percentage=Decimal("0.0300"),
            )
            db.session.add(other_raise)
            db.session.commit()
            orig_pct = other_raise.percentage

            response = auth_client.post(
                f"/salary/raises/{other_raise.id}/edit",
                data={
                    "raise_type_id": raise_type.id,
                    "effective_date": "2026-06-01",
                    "percentage": "10.00",
                },
            )

            # Security: 404 for both not-found and not-yours.
            assert response.status_code == 404

            # Raise should be unchanged.
            db.session.expire_all()
            after = db.session.get(SalaryRaise, other_raise.id)
            assert after.percentage == orig_pct, (
                "IDOR attack modified another user's raise!"
            )

    def test_delete_raise(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/raises/<id>/delete removes a raise."""
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            salary_raise = SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=raise_type.id,
                effective_date=date(2026, 7, 1),
                percentage=Decimal("0.0300"),
            )
            db.session.add(salary_raise)
            db.session.commit()

            response = auth_client.post(
                f"/salary/raises/{salary_raise.id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Raise removed." in response.data

    def test_add_raise_validation_error(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/<id>/raises with missing fields shows a validation error."""
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            # Missing both percentage and flat_amount.
            response = auth_client.post(
                f"/salary/{profile.id}/raises",
                data={
                    "raise_type_id": raise_type.id,
                    "effective_date": "2026-07-01",
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Please correct the highlighted errors" in response.data

    def test_add_raise_without_start_date_rejected(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """POST /salary/<id>/raises without a start date is rejected (plan#3090)."""
        with app.app_context():
            profile = _create_profile(seed_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            response = auth_client.post(
                f"/salary/{profile.id}/raises",
                data={
                    "raise_type_id": raise_type.id,
                    "percentage": "3",
                    "is_recurring": "on",
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Please correct the highlighted errors" in response.data

    def test_add_raise_profile_not_found(self, app, auth_client, seed_user):
        """POST /salary/<id>/raises for non-existent profile returns 404 (security)."""
        with app.app_context():
            response = auth_client.post(
                "/salary/999999/raises",
                data={"raise_type_id": "1", "effective_date": "2026-01-01"},
                follow_redirects=True,
            )

            assert response.status_code == 404

    def test_delete_other_users_raise_redirects(
        self, app, auth_client, seed_user
    ):
        """POST /salary/raises/<id>/delete for another user's raise returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_profile()
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            salary_raise = SalaryRaise(
                salary_profile_id=other["profile"].id,
                raise_type_id=raise_type.id,
                effective_date=date(2026, 7, 1),
                percentage=Decimal("0.0300"),
            )
            db.session.add(salary_raise)
            db.session.commit()

            response = auth_client.post(
                f"/salary/raises/{salary_raise.id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 404


class TestARaisesEndYearIsWrittenHere:
    """The write door plan step **salary:S3-c** opens (ruling **R-SAL11**).

    ``salary.salary_raises.terminal_year`` existed and was UNREACHABLE from
    the moment plan step salary:S3-b added it: no schema declared it and
    ``BaseSchema.Meta.unknown = EXCLUDE`` dropped it, so a POST naming it did
    nothing at all.  This is the only construction site in ``app/`` -- both
    routes here -- so these cases are the whole of how a value ever gets into
    that column.

    **The end-year question is ASKED, never defaulted** (developer ruling,
    2026-09-05).  The form posts a ``raise_end_mode`` beside the year, and a
    recurring raise that says nothing is REFUSED rather than quietly believed
    forever, because ``NULL`` is a real answer ("no end year") and a blank
    control cannot be told apart from an unanswered one.  A default would be
    one global belief in per-raise clothing, which is the shape of the
    ``merit_raise_horizon_years`` setting this step deletes.

    Each refusal is graded on what was STORED, not only on the flash: a
    schema that accepted the payload and a route that dropped it look
    identical from the response.
    """

    @staticmethod
    def _post_add(auth_client, profile_id, raise_type_id, **fields):
        """POST the add-raise form with *fields* over the required minimum."""
        data = {
            "raise_type_id": raise_type_id,
            "effective_date": "2027-01-01",
            "percentage": "2.5",
        }
        data.update(fields)
        return auth_client.post(
            f"/salary/{profile_id}/raises", data=data, follow_redirects=True,
        )

    @staticmethod
    def _only_raise(profile_id):
        """The single raise on *profile_id*, or ``None`` if none was stored."""
        return (
            db.session.query(SalaryRaise)
            .filter_by(salary_profile_id=profile_id)
            .one_or_none()
        )

    def test_a_recurring_raise_can_state_the_year_it_stops(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """mode=year plus a year stores that year."""
        with app.app_context():
            profile = _create_profile(seed_user)
            merit = db.session.query(RaiseType).filter_by(name="merit").one()

            response = self._post_add(
                auth_client, profile.id, merit.id,
                is_recurring="on", raise_end_mode="year",
                terminal_year="2031",
            )

            assert response.status_code == 200
            assert self._only_raise(profile.id).terminal_year == 2031

    def test_a_recurring_raise_can_state_that_it_never_stops(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """mode=none stores ``NULL``, which is the answer "indefinitely".

        The control the ruling turns on: this and an unanswered form must NOT
        reach the same row, which is why the next case exists.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            merit = db.session.query(RaiseType).filter_by(name="merit").one()

            # ``terminal_year=""`` is posted because the FORM posts it: the
            # number input is rendered whenever the group is, so the browser
            # sends an empty value beside the mode rather than omitting the
            # key.  A payload that omits it would grade a request no browser
            # makes.
            response = self._post_add(
                auth_client, profile.id, merit.id,
                is_recurring="on", raise_end_mode="none", terminal_year="",
            )

            assert response.status_code == 200
            stored = self._only_raise(profile.id)
            assert stored is not None
            assert stored.terminal_year is None

    def test_a_recurring_raise_that_says_nothing_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """No mode, no raise.  The developer's 2026-09-05 ruling, enforced.

        **The assertion that matters is that NOTHING was stored.**  Accepting
        the post would write ``NULL`` -- believed forever -- which is a real
        answer the owner never gave, and it is the money-increasing one: it
        compounds the raise over every projected year rather than stopping it.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            merit = db.session.query(RaiseType).filter_by(name="merit").one()

            response = self._post_add(
                auth_client, profile.id, merit.id, is_recurring="on",
            )

            assert response.status_code == 200
            assert self._only_raise(profile.id) is None, (
                "a recurring raise was stored without answering how long it "
                "is believed; the unanswered form silently means 'forever', "
                "which is the default ruling R-SAL11 refuses"
            )

    def test_a_one_time_raise_may_not_carry_an_end_year(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The schema refuses it, so the CHECK never has to.

        ``ck_salary_raises_terminal_year_only_on_a_recurring_raise`` would
        reject the INSERT, and ``add_raise`` handles only unique violations --
        so without this rule the owner gets a 500 rather than a message on the
        control.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            merit = db.session.query(RaiseType).filter_by(name="merit").one()

            response = self._post_add(
                auth_client, profile.id, merit.id,
                raise_end_mode="year", terminal_year="2031",
            )

            assert response.status_code == 200
            assert self._only_raise(profile.id) is None

    def test_an_end_year_before_the_effective_year_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A raise cannot end before it starts -- refused at the form.

        Mirrors ``ck_salary_raises_terminal_year_not_before_effective``.  The
        stored form of this state is what plan step salary:S3-b made
        unrepresentable; this is the door that stops the owner reaching it and
        getting an IntegrityError instead of a sentence.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            merit = db.session.query(RaiseType).filter_by(name="merit").one()

            response = self._post_add(
                auth_client, profile.id, merit.id,
                is_recurring="on", raise_end_mode="year",
                effective_date="2030-01-01", terminal_year="2029",
            )

            assert response.status_code == 200
            assert self._only_raise(profile.id) is None

    def test_ends_after_with_no_year_is_an_unfinished_answer(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """"Ends after" and an empty box is refused -- there is no year to store."""
        with app.app_context():
            profile = _create_profile(seed_user)
            merit = db.session.query(RaiseType).filter_by(name="merit").one()

            self._post_add(
                auth_client, profile.id, merit.id,
                is_recurring="on", raise_end_mode="year", terminal_year="",
            )

            assert self._only_raise(profile.id) is None

    def test_no_end_year_WINS_over_a_year_the_box_still_holds(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The mode is authoritative, so this stores ``NULL`` -- it is not refused.

        **This case inverted on review.**  It asserted a REFUSAL, because the
        schema policed the two controls against each other; that refusal is
        what made "this raise no longer has an end year" impossible in a
        browser, since the edit form leaves the stored year in the box when
        you pick "no end year".  Deleting a home rather than keeping two in
        step (CLAUDE.md rule 14) is the remedy, so the disagreement is no
        longer representable -- and this is the case that would fail if the
        policing came back.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            merit = db.session.query(RaiseType).filter_by(name="merit").one()

            self._post_add(
                auth_client, profile.id, merit.id,
                is_recurring="on", raise_end_mode="none",
                terminal_year="2031",
            )

            stored = self._only_raise(profile.id)
            assert stored is not None, (
                "'no end year' with a year still in the box was refused; the "
                "mode is the answer, and refusing this is what made clearing "
                "an end year impossible in a browser"
            )
            assert stored.terminal_year is None

    def test_an_edit_can_clear_a_stored_end_year(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """ends-2031 -> no end year actually writes ``NULL``.

        **It posts the year the FORM still holds, not an empty box**, and
        an adversarial review of this step is why.  ``_populateRaiseForm``
        fills the year input from the stored value when the edit form opens,
        and choosing "no end year" does not empty it -- so a browser sends
        ``raise_end_mode=none`` WITH ``terminal_year=2031``.  This test
        posted ``terminal_year=""``, a state no rendered form reaches, and
        passed while the operation it names was impossible in a browser: the
        schema refused the disagreement.  The mode is authoritative now
        (``drop_end_year_mode``), so the disagreement cannot exist.

        **The case a partial-update door silently loses.**  ``update_raise``
        applies only the keys present in the loaded payload -- so without
        ``drop_end_year_mode`` forcing the key present, this edit would
        submit nothing for the column and the raise would keep compounding to
        2031 while the form showed "no end year".
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            merit = db.session.query(RaiseType).filter_by(name="merit").one()
            row = SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=merit.id,
                effective_date=date(2027, 1, 1),
                percentage=Decimal("0.0250"),
                is_recurring=True,
                terminal_year=2031,
            )
            db.session.add(row)
            db.session.commit()

            response = auth_client.post(
                f"/salary/raises/{row.id}/edit",
                data={
                    "raise_type_id": merit.id,
                    "effective_date": "2027-01-01",
                    "percentage": "2.5",
                    "is_recurring": "on",
                    "raise_end_mode": "none",
                    # What the browser sends: the box still holds the stored
                    # year, because picking "no end year" does not clear it.
                    "terminal_year": "2031",
                    "version_id": row.version_id,
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            db.session.refresh(row)
            assert row.terminal_year is None, (
                "clearing the end year left 2031 stored; the update path "
                "writes only the keys it is handed, so an absent "
                "terminal_year is a silent no-op"
            )

    def test_an_edit_can_set_an_end_year_on_a_raise_that_had_none(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The other direction, which the allowlist has to permit.

        ``_RAISE_UPDATE_FIELDS`` gates which loaded keys reach the model, so a
        schema field without an allowlist entry validates cleanly and then
        does nothing at all -- green everywhere, and the owner's edit lost.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            merit = db.session.query(RaiseType).filter_by(name="merit").one()
            row = SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=merit.id,
                effective_date=date(2027, 1, 1),
                percentage=Decimal("0.0250"),
                is_recurring=True,
            )
            db.session.add(row)
            db.session.commit()

            response = auth_client.post(
                f"/salary/raises/{row.id}/edit",
                data={
                    "raise_type_id": merit.id,
                    "effective_date": "2027-01-01",
                    "percentage": "2.5",
                    "is_recurring": "on",
                    "raise_end_mode": "year",
                    "terminal_year": "2033",
                    "version_id": row.version_id,
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            db.session.refresh(row)
            assert row.terminal_year == 2033

    def test_the_form_renders_the_end_year_choice_unanswered(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Neither radio arrives ``checked``, and the list states each answer.

        A form test rather than a route one, because the ruling lives in the
        MARKUP: a ``checked`` attribute on either radio would make the answer
        a default again, and ``form.reset()`` restores exactly those
        attributes when the edit form returns to add mode.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            merit = db.session.query(RaiseType).filter_by(name="merit").one()
            db.session.add(SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=merit.id,
                effective_date=date(2027, 1, 1),
                percentage=Decimal("0.0250"),
                is_recurring=True,
                terminal_year=2031,
            ))
            db.session.commit()

            html = auth_client.get(
                f"/salary/{profile.id}/edit",
            ).data.decode()

            assert 'name="raise_end_mode"' in html
            assert 'value="none"' in html
            assert 'name="terminal_year"' in html
            # The stored answer reaches the row and the edit button.
            assert "through 2031" in html
            assert 'data-raise-terminal-year="2031"' in html
            # And nothing pre-answers the question for the next raise.
            mode_block = html[html.index('name="raise_end_mode"'):]
            mode_block = mode_block[:mode_block.index('name="terminal_year"')]
            assert "checked" not in mode_block, (
                "a raise_end_mode radio renders checked, so the form answers "
                "the end-year question for the owner -- the default ruling "
                "R-SAL11's write door refuses"
            )


class TestRaiseLabelFilter:
    """The app-wide ``raise_label`` Jinja filter (salary raise displays)."""

    def test_filter_registered_and_cleans(self, app):
        """raise_label is registered and wraps clean_raise_label.

        "MERIT +2.5000%" -> title-case + trailing-zero trim -> "Merit
        +2.5%"; "COLA +3.0000%" -> whole number drops the dot -> "Cola
        +3%"; None renders as "" so templates can pipe an absent event
        through without guarding.
        """
        with app.app_context():
            filter_fn = app.jinja_env.filters["raise_label"]
            assert filter_fn("MERIT +2.5000%") == "Merit +2.5%"
            assert filter_fn("COLA +3.0000%") == "Cola +3%"
            assert filter_fn("MERIT +$2,000.00") == "Merit +$2,000.00"
            assert filter_fn(None) == ""
