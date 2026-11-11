"""The LIABILITY balance doors ask for the amount owed (plan step credit_card:CC-5-5b).

Rulings, all the developer's:

* **R-CC52** -- every door that takes a balance for a LIABILITY account asks
  for the amount OWED (positive = you owe; a card's credit entered as a
  negative amount owed) and stores the held sign through the one ``owed()``
  flip, so R-CC47's premise ("every balance is what the account holds,
  negative when owed") is true by construction.
* **R-CC57** -- the anchor editor speaks owed on EVERY surface it opens from,
  and each surface keeps its own sign: a card's grid shows the held -$1,000
  its rows are summed in, and the editor it opens pre-fills 1,000.00.
* **R-CC58** -- the create form's box is labelled by the type picked (a
  script), and without the script it names both cases.
* **R-CC53** -- a loan's read-only anchor cell shows no figure and points to
  the loan's page.

Every figure is hand-computed from one rule: a liability holds MINUS what it
owes, and an asset's figure crosses unchanged.  A card owing ``$1,000.00``
holds ``-1,000.00``; one holding a ``$50.00`` credit holds ``+50.00``.  The
money is graded here at the route, where the crossing lives; the crossing's
own contract (both directions, every category) is
``tests/test_services/test_liability_sign.py``.

**Each stored-sign case reads the ROW back**, not the response, because a door
that stored the typed figure unchanged renders a plausible page too.
"""

import re
from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import AcctCategoryEnum
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.account_opening import AccountOpening
from app.models.ref import AccountType
from app.routes.accounts._door_meaning import LOAN_ANCHOR_REFUSAL
from app.services import cash_ledger
from app.utils.dates import display_today
from tests._test_helpers import create_account_of_type


#: What a liability's balance form submits beside its figure (ruling R-CC61):
#: the meaning its box was rendered in.  Every card request below sends it,
#: because the rendered card forms do -- asserted in
#: ``TestTheAnchorEditor.test_a_card_owing_1000_opens_on_1000`` and
#: ``TestTheBooksOpeningCard.test_a_card_states_and_prefills_what_its_books_opened_owing``
#: -- and a request without it is the stale form the doors refuse.
_OWED_FORM = {"asks_owed": "true"}


def _created(name):
    """Return the account the create door just made, by its unique name."""
    return db.session.query(Account).filter_by(name=name).one()


def _create(client, type_name, name, typed):
    """POST the create form the way the rendered page does."""
    acct_type = db.session.query(AccountType).filter_by(name=type_name).one()
    return client.post(
        "/accounts",
        data={
            "name": name,
            "account_type_id": str(acct_type.id),
            "anchor_balance": typed,
            "observed_on": display_today().isoformat(),
        },
        follow_redirects=True,
    )


def _stored(account):
    """Return ``(opening equity, governing assertion)`` as the door stored them."""
    return (
        cash_ledger.account_opening_fact(account.id).opening_equity,
        cash_ledger.resolve_anchor(account).balance,
    )


class TestTheCreateDoor:
    """R-CC52 at the create form: a liability's figure is typed owed, stored held."""

    def test_a_card_typed_as_owing_1000_is_stored_minus_1000(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """1,000.00 typed on a Credit Card: opening and assertion both -1,000.00."""
        with app.app_context():
            resp = _create(auth_client, "Credit Card", "Visa", "1000.00")
            assert resp.status_code == 200
            assert _stored(_created("Visa")) == (
                Decimal("-1000.00"), Decimal("-1000.00"),
            )

    def test_a_card_credit_typed_negative_is_stored_as_a_positive_hold(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """-50.00 typed (the issuer owes the owner $50.00) stores +50.00 held."""
        with app.app_context():
            _create(auth_client, "Credit Card", "Amex", "-50.00")
            assert _stored(_created("Amex")) == (
                Decimal("50.00"), Decimal("50.00"),
            )

    def test_a_loan_without_terms_typed_5000_is_stored_minus_5000(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """R-CC52's own worked case: a car loan typed 5,000.00 holds -5,000.00.

        The create door redirects an amortizing account to its setup page, and
        that page's "Balance today" -- a field of the loan's OWED figure, with
        ``min="0"`` -- opens on the 5,000.00 the owner typed, crossed back.
        """
        with app.app_context():
            resp = _create(auth_client, "Auto Loan", "Car loan", "5000.00")
            assert _stored(_created("Car loan")) == (
                Decimal("-5000.00"), Decimal("-5000.00"),
            )
            assert b'name="anchor_balance"' in resp.data
            assert b'value="5000.00"' in resp.data

    def test_an_asset_is_stored_exactly_as_typed(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """The asset arm crosses nothing: 1,000.00 and an overdrawn -25.00 stand."""
        with app.app_context():
            _create(auth_client, "Checking", "Joint checking", "1000.00")
            _create(auth_client, "Savings", "Overdrawn savings", "-25.00")
            assert _stored(_created("Joint checking")) == (
                Decimal("1000.00"), Decimal("1000.00"),
            )
            assert _stored(_created("Overdrawn savings")) == (
                Decimal("-25.00"), Decimal("-25.00"),
            )


    def test_a_custom_liability_type_asks_owed_and_stores_held(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """An owner's own Liability-category type with no schedule is a liability.

        The review's L6: the seeded types alone cannot tell "every liability
        type" from "every seeded liability type".  2,000.00 typed on a custom
        "Family loan" type stores -2,000.00, and its option asks owed.
        """
        with app.app_context():
            family = AccountType(
                name="Family loan",
                category_id=ref_cache.acct_category_id(
                    AcctCategoryEnum.LIABILITY,
                ),
                user_id=seed_user["user"].id,
            )
            db.session.add(family)
            db.session.commit()
            assert family.has_amortization is False
            html = auth_client.get("/accounts/new").data.decode()
            option = re.search(rf'<option value="{family.id}"[^>]*>', html)
            assert option is not None
            assert "data-asks-owed" in option.group(0)
            _create(auth_client, "Family loan", "Loan from Mom", "2000.00")
            assert _stored(_created("Loan from Mom")) == (
                Decimal("-2000.00"), Decimal("-2000.00"),
            )


class TestTheCreateFormAsksInTheTypesWords:
    """R-CC58: the box is labelled by the type; without a script it names both."""

    def test_exactly_the_liability_types_are_marked_to_ask_owed(
        self, app, auth_client, seed_user,
    ):  # pylint: disable=unused-argument
        """Every visible type's option carries ``data-asks-owed`` iff it is a liability.

        Classified here by the cached category id, never by a type name, so a
        liability type added later is covered without editing this test.
        """
        with app.app_context():
            html = auth_client.get("/accounts/new").data.decode()
            liability_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
            types = db.session.query(AccountType).filter(
                AccountType.user_id.is_(None),
            ).all()
            assert any(t.category_id == liability_id for t in types)
            assert any(t.category_id != liability_id for t in types)
            for acct_type in types:
                option = re.search(
                    rf'<option value="{acct_type.id}"[^>]*>', html,
                )
                assert option is not None, acct_type.id
                assert ("data-asks-owed" in option.group(0)) is (
                    acct_type.category_id == liability_id
                ), acct_type.id

    def test_without_the_script_the_box_names_both_cases(
        self, app, auth_client, seed_user,
    ):  # pylint: disable=unused-argument
        """The server-rendered label and help: R-CC58's no-script fallback.

        Plus the two wordings the script chooses between and the script
        itself, loaded as a file (no inline handler, the app's CSP shape).
        """
        with app.app_context():
            html = auth_client.get("/accounts/new").data.decode()
            assert (
                '<label for="anchor_balance" id="anchor-label" '
                'class="form-label">Opening Balance</label>'
            ) in html
            assert (
                "What the account holds today. For a loan or credit card, "
                "enter the amount you OWE; a card holding a credit is a "
                "negative number."
            ) in html
            assert 'data-owed-label="Amount owed"' in html
            assert (
                'data-owed-help="What you owe today. A card holding a '
                'credit: a negative number."'
            ) in html
            assert 'data-held-label="Opening balance"' in html
            assert 'data-held-help="What the account holds today."' in html
            assert "js/account_form.js" in html

    def test_the_edit_form_marks_nothing_and_loads_no_script(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The edit form asks no balance, so it carries neither mark nor script."""
        with app.app_context():
            html = auth_client.get(
                f"/accounts/{seed_user['account'].id}/edit",
            ).data.decode()
            assert "data-asks-owed" not in html
            assert "account_form.js" not in html


class TestTheAnchorEditor:
    """R-CC57: the editor asks a liability for the amount owed, on any surface."""

    def _card(self, seed_user, held):
        """A committed Credit Card whose governing assertion holds *held*."""
        card = create_account_of_type(
            seed_user, db.session, "Credit Card", "Visa", anchor_balance=held,
        )
        db.session.commit()
        return card

    def test_a_card_owing_1000_opens_on_1000(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """Held -1,000.00 pre-fills 1,000.00 under a visible "Amount owed"."""
        with app.app_context():
            card = self._card(seed_user, Decimal("-1000.00"))
            html = auth_client.get(
                f"/accounts/{card.id}/anchor-form",
            ).data.decode()
            assert 'value="1000.00"' in html
            assert (
                f'<label for="anchor-balance-{card.id}" '
                'class="form-label form-label-sm text-muted mb-0">'
                "Amount owed"
            ) in html
            assert f'id="anchor-balance-{card.id}"' in html
            # ONE accessible name: the visible label, never a competing
            # aria-label beside it.
            assert "Visa account balance" not in html
            # ...and the label still names the account to a screen reader.
            assert '<span class="visually-hidden"> on Visa</span>' in html
            assert "A credit is a negative amount." in html
            # What the box asked, submitted with the figure (ruling R-CC61).
            assert '<input type="hidden" name="asks_owed" value="true">' in html

    def test_a_card_in_credit_opens_on_a_negative_owed(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """Held +50.00 (the issuer owes the owner) pre-fills -50.00 owed."""
        with app.app_context():
            card = self._card(seed_user, Decimal("50.00"))
            html = auth_client.get(
                f"/accounts/{card.id}/anchor-form",
            ).data.decode()
            assert 'value="-50.00"' in html

    def test_an_asset_opens_exactly_as_it_did(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """Checking pre-fills its held balance, aria-labelled, no owed wording."""
        with app.app_context():
            checking = seed_user["account"]
            held = cash_ledger.resolve_anchor(checking).balance
            html = auth_client.get(
                f"/accounts/{checking.id}/anchor-form",
            ).data.decode()
            assert f'value="{held}"' in html
            assert f'aria-label="{checking.name} account balance"' in html
            assert "Amount owed" not in html
            assert "A credit is a negative amount." not in html
            assert 'name="asks_owed"' not in html

    def test_typing_1200_on_a_card_stores_minus_1200(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """The save crosses once: 1,200.00 owed is a -1,200.00 assertion."""
        with app.app_context():
            card = self._card(seed_user, Decimal("-1000.00"))
            resp = auth_client.patch(
                f"/accounts/{card.id}/true-up",
                data={"anchor_balance": "1200.00", **_OWED_FORM},
            )
            assert resp.status_code == 200
            assert cash_ledger.resolve_anchor(
                db.session.get(Account, card.id),
            ).balance == Decimal("-1200.00")

    def test_a_credit_typed_negative_stores_a_positive_hold(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """-50.00 typed on a card owing 1,000.00 records a +50.00 credit."""
        with app.app_context():
            card = self._card(seed_user, Decimal("-1000.00"))
            auth_client.patch(
                f"/accounts/{card.id}/true-up",
                data={"anchor_balance": "-50.00", **_OWED_FORM},
            )
            assert cash_ledger.resolve_anchor(
                db.session.get(Account, card.id),
            ).balance == Decimal("50.00")

    def test_a_refused_card_submission_echoes_what_was_typed(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """A future day is refused; the box re-shows 1200.00 owed, not -1200.00.

        The echo is the owner's own text, which on a liability is already the
        amount owed -- so it is shown back uncrossed, still labelled owed, and
        nothing is written.
        """
        with app.app_context():
            card = self._card(seed_user, Decimal("-1000.00"))
            future = (display_today() + timedelta(days=3)).isoformat()
            resp = auth_client.patch(
                f"/accounts/{card.id}/true-up",
                data={
                    "anchor_balance": "1200.00", "observed_on": future,
                    **_OWED_FORM,
                },
            )
            assert resp.status_code == 400
            html = resp.data.decode()
            assert 'value="1200.00"' in html
            assert "Amount owed" in html
            assert cash_ledger.resolve_anchor(
                db.session.get(Account, card.id),
            ).balance == Decimal("-1000.00")

    def test_the_acknowledgement_names_the_figure_owed(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """Re-recording 1,000.00 owed for today reads "$1,000.00 owed".

        The fixture asserts -1,000.00 for YESTERDAY, so the same figure for
        today appends a row (the day moved) while the GOVERNING balance does
        not move -- the state the acknowledgement exists for (finding N-204).
        """
        with app.app_context():
            card = self._card(seed_user, Decimal("-1000.00"))
            html = auth_client.patch(
                f"/accounts/{card.id}/true-up",
                data={
                    "anchor_balance": "1000.00",
                    "observed_on": display_today().isoformat(),
                    **_OWED_FORM,
                },
            ).data.decode()
            assert "Balance recorded" in html
            assert (
                '<span class="font-mono">$1,000.00</span> owed'
            ) in html

    def test_the_display_cell_keeps_the_held_sign(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """The cell the editor opens FROM is not crossed (R-CC57): -$1,000."""
        with app.app_context():
            card = self._card(seed_user, Decimal("-1000.00"))
            html = auth_client.get(
                f"/accounts/{card.id}/anchor-display",
            ).data.decode()
            assert "-$1,000" in html
            assert "Amount owed" not in html


class TestTheDifferencePreview:
    """R-CC57's worked preview: held arithmetic, owed figures on screen."""

    def _preview(self, client, account_id, typed, form=None):
        """GET the preview for *typed* on today, as a CARD editor sends it."""
        return client.get(
            f"/accounts/{account_id}/anchor-difference",
            query_string={
                "anchor_balance": typed,
                "observed_on": display_today().isoformat(),
                **(_OWED_FORM if form is None else form),
            },
        ).data.decode()

    def test_a_card_owing_1000_typed_as_1200_reads_200_unrecorded_spend(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """Records -1,000.00; typed 1,200.00 owed = -1,200.00 held.

        Held difference: -1,200.00 - (-1,000.00) = -200.00, which is spend
        Shekel has not recorded.  On screen, in the words typed: $1,000.00 /
        $1,200.00 / $200.00.
        """
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Visa",
                anchor_balance=Decimal("-1000.00"),
            )
            db.session.commit()
            html = self._preview(auth_client, card.id, "1200.00")
            figures = re.findall(r'<dd class="font-mono mb-0">([^<]+)</dd>', html)
            assert figures == ["$1,000.00", "$1,200.00", "$200.00"]
            assert "spend Shekel has not recorded" in html

    def test_a_card_in_credit_typed_as_its_own_credit_agrees(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """Records +50.00 held; typed -50.00 owed = +50.00 held: no difference."""
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Amex",
                anchor_balance=Decimal("50.00"),
            )
            db.session.commit()
            html = self._preview(auth_client, card.id, "-50.00")
            figures = re.findall(r'<dd class="font-mono mb-0">([^<]+)</dd>', html)
            assert figures == ["-$50.00", "-$50.00", "$0.00"]
            assert "your records agree with this balance" in html


    def test_a_card_owing_1000_typed_as_800_reads_money_not_accounted_for(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """Records -1,000.00; typed 800.00 owed = -800.00 held.

        Held difference: -800.00 - (-1,000.00) = +200.00, money Shekel has not
        accounted for (a payment it never recorded).  On screen: $1,000.00 /
        $800.00 / -$200.00 -- the owner owes $200.00 LESS than the records say.
        """
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Visa",
                anchor_balance=Decimal("-1000.00"),
            )
            db.session.commit()
            html = self._preview(auth_client, card.id, "800.00")
            figures = re.findall(r'<dd class="font-mono mb-0">([^<]+)</dd>', html)
            assert figures == ["$1,000.00", "$800.00", "-$200.00"]
            assert "money Shekel has not accounted for" in html


class TestTheBooksOpeningCard:
    """R-CC52 at the books-opening door: stated, pre-filled and stored."""

    def _card(self, seed_user, held):
        """A committed Credit Card whose books opened holding *held*."""
        card = create_account_of_type(
            seed_user, db.session, "Credit Card", "Visa", anchor_balance=held,
        )
        db.session.commit()
        return card

    def test_a_card_states_and_prefills_what_its_books_opened_owing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """Opened holding -1,000.00: "owing $1,000.00", pre-filled 1000.00."""
        with app.app_context():
            card = self._card(seed_user, Decimal("-1000.00"))
            assert cash_ledger.account_opening_fact(
                card.id,
            ).opening_equity == Decimal("-1000.00")
            html = auth_client.get(f"/accounts/{card.id}/edit").data.decode()
            assert "owing <strong>$1,000.00</strong>" in html
            assert '<label for="opening_equity" class="form-label">Owed at opening</label>' in html
            assert re.search(
                r'name="opening_equity"[^>]*value="1000\.00"', html,
            )
            assert "What you really owed at the end of that day." in html
            assert '<input type="hidden" name="asks_owed" value="true">' in html

    def test_restating_a_card_stores_the_held_sign(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """1,500.00 owed restated one day earlier stores -1,500.00."""
        with app.app_context():
            card = self._card(seed_user, Decimal("-1000.00"))
            standing = cash_ledger.account_opening_fact(card.id)
            new_day = standing.opened_on - timedelta(days=1)
            resp = auth_client.post(
                f"/accounts/{card.id}/opening",
                data={
                    "opened_on": new_day.isoformat(),
                    "opening_equity": "1500.00",
                    **_OWED_FORM,
                },
                follow_redirects=True,
            )
            governing = cash_ledger.account_opening_fact(card.id)
            assert governing.opened_on == new_day
            assert governing.opening_equity == Decimal("-1500.00")
            assert "owing $1500.00" in resp.data.decode()

    def test_an_asset_card_reads_as_it_did(
        self, app, auth_client, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Checking keeps "holding", "Opening equity" and its own help text."""
        with app.app_context():
            html = auth_client.get(
                f"/accounts/{seed_user['account'].id}/edit",
            ).data.decode()
            assert "holding <strong>" in html
            assert '<label for="opening_equity" class="form-label">Opening equity</label>' in html
            assert "What the account really held at the end of that day." in html
            assert "Owed at opening" not in html
            assert 'name="asks_owed"' not in html


class TestTheLoanCellShowsNoFigure:
    """R-CC53: a loan's read-only anchor cell points to its page instead."""

    def _mortgage(self, seed_user):
        """A committed Mortgage whose cash assertion was typed +178,103.41."""
        loan = create_account_of_type(
            seed_user, db.session, "Mortgage", "Mortgage",
            anchor_balance=Decimal("178103.41"),
        )
        db.session.commit()
        return loan

    def test_the_display_cell_points_to_the_loan_page(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """No figure from the typed row, in either sign; a link to the loan."""
        with app.app_context():
            loan = self._mortgage(seed_user)
            resp = auth_client.get(f"/accounts/{loan.id}/anchor-display")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "178,103" not in html
            assert "$" not in html
            assert f'href="/accounts/{loan.id}/loan"' in html
            assert "Balance on the loan's page" in html

    def test_a_raced_loan_submission_refuses_beside_the_pointer(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """The N-199 race's 422 names where the balance lives, and no figure."""
        with app.app_context():
            loan = self._mortgage(seed_user)
            resp = auth_client.patch(
                f"/accounts/{loan.id}/true-up",
                data={"anchor_balance": "1.00"},
            )
            assert resp.status_code == 422
            html = resp.data.decode()
            assert LOAN_ANCHOR_REFUSAL.replace("'", "&#39;") in html
            assert "178,103" not in html
            assert f'href="/accounts/{loan.id}/loan"' in html


class TestTheLoanSetupPrefill:
    """The setup page's "Balance today" is an OWED field (``min="0"``)."""

    def test_an_unconfigured_loan_prefills_what_it_owes(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """A loan with no terms holding -5,000.00 opens its setup on 5000.00.

        Read raw, the field would open on -5000.00, which its own ``min="0"``
        refuses.
        """
        with app.app_context():
            loan = create_account_of_type(
                seed_user, db.session, "Auto Loan", "Car loan",
                anchor_balance=Decimal("-5000.00"),
            )
            db.session.commit()
            html = auth_client.get(f"/accounts/{loan.id}/loan").data.decode()
            assert re.search(
                r'id="anchor_balance"\s+name="anchor_balance"[^>]*'
                r'value="5000\.00"',
                html,
            )


class TestTheStaleFormIsRefused:
    """Ruling R-CC61: a save under the meaning the box no longer has stores nothing.

    Each case builds the race the review measured: the form is rendered under
    one meaning, the account is re-typed across asset and liability (as a
    second tab would), and the form is submitted.  "Nothing stored" is read
    back from the TABLE after every loaded object is expired, never from
    ``db.session.dirty`` -- which cannot tell a rollback from a flush.
    """

    @staticmethod
    def _retype(account, type_name):
        """Re-type *account* and commit, as the other tab's edit would."""
        account.account_type_id = db.session.query(AccountType).filter_by(
            name=type_name,
        ).one().id
        db.session.commit()
        db.session.expire_all()

    @staticmethod
    def _rows(model, account_id):
        """Count *account_id*'s rows in *model*'s table, read fresh."""
        db.session.expire_all()
        return db.session.query(model).filter_by(account_id=account_id).count()

    def test_an_asset_editor_saved_after_the_account_became_a_card(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """The review's measured race: $0 Checking, re-typed, 2,500.00 saved.

        Without the guard it stored -2,500.00 (a card owing $2,500.00) from a
        box that asked for an asset's balance.  With it: 400, not one
        assertion row written, and the editor re-opened AS A FRESH CLICK OPENS
        IT (ruling R-CC62): the standing 0.00 in the new words, the typed
        2500.00 gone.  Then the keystroke the re-review measured: the
        re-opened form submitted EXACTLY as emitted -- one Enter -- stores the
        standing 0.00, never the refused -2,500.00.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Checking", "Joint",
                anchor_balance=Decimal("0.00"),
            )
            db.session.commit()
            before = self._rows(AccountAnchorHistory, account.id)
            self._retype(account, "Credit Card")
            resp = auth_client.patch(
                f"/accounts/{account.id}/true-up",
                data={"anchor_balance": "2500.00"},
            )
            assert resp.status_code == 400
            html = resp.data.decode()
            assert (
                "This account&#39;s type changed while you were editing; the "
                "box now asks for the amount owed. Check the figure and save "
                "again."
            ) in html
            assert "Amount owed" in html
            assert 'value="0.00"' in html
            assert "2500" not in html
            assert self._rows(AccountAnchorHistory, account.id) == before
            assert cash_ledger.resolve_anchor(
                db.session.get(Account, account.id),
            ).balance == Decimal("0.00")

            # One Enter on the re-opened editor: every named input it emits,
            # posted back unchanged.
            emitted = dict(re.findall(
                r'<input[^>]*name="([a-z_]+)"[^>]*value="([^"]*)"', html,
            ))
            assert emitted["asks_owed"] == "true"
            assert emitted["anchor_balance"] == "0.00"
            again = auth_client.patch(
                f"/accounts/{account.id}/true-up", data=emitted,
            )
            assert again.status_code == 200
            assert cash_ledger.resolve_anchor(
                db.session.get(Account, account.id),
            ).balance == Decimal("0.00")

    def test_a_card_editor_saved_after_the_account_became_an_asset(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """The reverse: 1,200.00 typed as owed, the account now Checking."""
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Visa",
                anchor_balance=Decimal("-1000.00"),
            )
            db.session.commit()
            before = self._rows(AccountAnchorHistory, card.id)
            self._retype(card, "Checking")
            resp = auth_client.patch(
                f"/accounts/{card.id}/true-up",
                data={"anchor_balance": "1200.00", **_OWED_FORM},
            )
            assert resp.status_code == 400
            html = resp.data.decode()
            assert "the box now asks for the account&#39;s balance." in html
            assert "Amount owed" not in html
            # Re-opened fresh: the standing held -1,000.00, now an asset's
            # balance, and not the 1,200.00 typed as owed.  Asked of the
            # amount's spellings, never of the bare digits, which the
            # account's own id can hold (its sequence starts at a million per
            # table in name order, so a new table moves it; plan#2538).
            assert 'value="-1000.00"' in html
            assert "1200.00" not in html
            assert "1,200.00" not in html
            assert self._rows(AccountAnchorHistory, card.id) == before

    def test_the_preview_of_a_stale_form_says_why_instead_of_pricing_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """An asset editor's preview on an account that is now a card."""
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Checking", "Joint",
                anchor_balance=Decimal("0.00"),
            )
            db.session.commit()
            self._retype(account, "Credit Card")
            html = auth_client.get(
                f"/accounts/{account.id}/anchor-difference",
                query_string={
                    "anchor_balance": "2500.00",
                    "observed_on": display_today().isoformat(),
                },
            ).data.decode()
            assert "so saving this box would be refused." in html
            assert "font-mono" not in html

    def test_a_forged_mark_on_the_preview_is_refused_not_a_500(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """A mark that is not a boolean reads as "balance": a card refuses it."""
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Visa",
                anchor_balance=Decimal("-1000.00"),
            )
            db.session.commit()
            resp = auth_client.get(
                f"/accounts/{card.id}/anchor-difference",
                query_string={
                    "anchor_balance": "1200.00",
                    "observed_on": display_today().isoformat(),
                    "asks_owed": "not-a-boolean",
                },
            )
            assert resp.status_code == 200
            assert "so saving this box would be refused." in resp.data.decode()

    def test_a_books_card_posted_after_the_account_became_an_asset(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """The restatement door refuses too, and no opening row is appended."""
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Visa",
                anchor_balance=Decimal("-1000.00"),
            )
            db.session.commit()
            standing = cash_ledger.account_opening_fact(card.id)
            before = self._rows(AccountOpening, card.id)
            self._retype(card, "Checking")
            resp = auth_client.post(
                f"/accounts/{card.id}/opening",
                data={
                    "opened_on": (
                        standing.opened_on - timedelta(days=1)
                    ).isoformat(),
                    "opening_equity": "1500.00",
                    **_OWED_FORM,
                },
                follow_redirects=True,
            )
            assert (
                "the box now asks for the account&#39;s balance."
            ) in resp.data.decode()
            assert self._rows(AccountOpening, card.id) == before
            assert cash_ledger.account_opening_fact(
                card.id,
            ).opening_equity == Decimal("-1000.00")
