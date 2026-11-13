"""The boundary where a contribution is valued and screened, against real rows."""

from decimal import Decimal

from app import ref_cache
from app.enums import EmployerContributionTypeEnum
from app.services.investment_projection import (
    AccountPayrollFeed,
    build_contribution_timeline,
    calculate_investment_inputs,
)
from app.services.pay_calendar import calendar_for
from app.services.recorded_contributions import load_transfer_contributions_for_account
from tests._test_helpers import an_entered_day, basis_for

from ._helpers import _FakeInvestmentParams


class TestAContributionIsValuedAndScreenedAtTheBoundary:
    """The BOUNDARY where a contribution is valued and screened.

    ``load_transfer_contributions_for_account(s)`` became the one place a
    contribution's dollar is decided at plan step X-au-c2: it prices the whole
    row set through :func:`app.services.cash_ledger.contributions_by_key` and
    hands :mod:`app.services.investment_projection` frozen
    :class:`~app.services.investment_projection.PricedContribution` records.

    **These four cases are deep-quality-hunt #11, moved here whole with their
    hand-computed figures** from ``test_investment_projection`` .
    ``TestEstimatedVsEffectiveAlignment``.  A contribution is normally worth
    its transfer's plan, but a settle with a typed figure records that figure
    as what moved.  Once that happens the realized figure diverges
    from the estimate, and every feed the projection builds -- the averaged
    periodic contribution, the YTD display, the engine seed, and the per-period
    timeline -- has to read the realized one, or the cap/limit accounting
    charges a different dollar than the growth engine actually applies.  The
    realized figure is the TRANSFER's own since plan step
    ``balance:X-bi-6-4d-2``'s 4d-4 half (rulings R-BAL365, R-BAL366), where it
    was each side's payment RECORD, and a transfer is ONE row since that step
    (ruling R-BAL258): its two shadow rows carried the figure before it.
    :meth:`_contribution` settles through the transfer's door and writes no
    shadow (ruling R-BAL167 class 4).

    They live at this tier now because it is the only tier that can still fail:
    the projection module consumes a record carrying one ``amount`` field, so
    asserting the rule there would assert that $400 averages to $400.  Here the
    rule is exercised against real rows.
    """

    @staticmethod
    def _contribution(
        seed_user, db_session, period, estimated, actual=None, *,
        settled=False, cancelled=False, account=None,
    ):
        """Seed a contribution transfer into an investment account; return the account.

        The contribution is the transfer's TO-side leg, landing in the
        investment account, which is the leg the loader prices.  The helper
        was ``_contribution_shadow`` and returned ``(account,
        income_shadow)``, the shadow row the loader once selected, until plan
        step ``balance:X-bi-6-4d-2`` made a transfer one row (ruling
        R-BAL258).  No case read that row, so it returns the account alone
        (ruling R-BAL167 class 4, every assertion unchanged).

        Pass ``account`` to feed an EXISTING account a second contribution --
        the multi-period cases need one account across several periods, because
        the per-account arithmetic they grade (the average's distinct-period
        denominator, the two YTD windows) is defined over one account's rows.

        A *settled* contribution is settled through the transfer's own door
        since plan step ``balance:X-bi-6-4d-2`` (ruling R-BAL167 class 4): the
        TRANSFER's status is the one status and each side's payment record
        hangs off the transfer, where this helper wrote the status and day
        onto both twins and the parent and a covering movement under each
        twin until then.  Nothing reads a twin now, so those plants left the
        transfer settled with no side record -- a ``$0.00`` close -- and the
        loader rightly valued it at ``$0``.

        A *cancelled* contribution is cancelled through the same door, the
        transfer's status being the one status.  The helper wrote Cancelled
        onto both shadows and the transfer directly until they went; measured
        10-15, the door's cancel and a direct write of the transfer's status
        leave every ``budget.transfers`` column equal but the identity,
        figure, name and timestamps, and neither a side record, so the door
        changes no state a case reads and writes past no seam.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum
        from app.services import transfer_service
        from tests._test_helpers import (
            basis_for,
            create_transfer,
            make_investment_account,
            on_both_sides,
            typed,
        )

        if account is None:
            account = make_investment_account(
                seed_user, db_session, period, Decimal("1000.00"),
                name=f"401k-{period.id}-{estimated}",
            )
        transfer = create_transfer(
            seed_user, seed_user["account"], account, period,
            amount=Decimal(str(estimated)),
        )
        if settled:
            # The transfer and both sides' records, through the named verb:
            # the day entered on both sides, the record's figure the
            # transfer's own plan (``resolved``) or *actual*, a figure a HUMAN
            # typed (``typed``).  The verb settles a transfer at Paid (DONE);
            # this planted Received, a status no transfer door writes.
            transfer_service.settle_transfer(
                transfer.id, seed_user["user"].id,
                submitted=None if actual is None else typed(
                    Decimal(str(actual)),
                ),
                side_days=on_both_sides(
                    transfer.from_account_id, transfer.to_account_id,
                    an_entered_day(period.start_date),
                ),
            )
        elif actual is not None:
            raise AssertionError(
                "A figure RECORDS a settle (plan step X-au-c3), so an "
                "unsettled row cannot carry one -- the seam refuses it "
                "(reject_settlement_without_settled_status). Pass "
                "settled=True beside actual, or drop actual."
            )
        if cancelled:
            transfer_service.update_transfer(
                transfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.CANCELLED),
            )
        db_session.flush()
        return account

    @staticmethod
    def _params(limit=None):
        return _FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=limit,
            employer_contribution_type_id=ref_cache
            .employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
        )

    def test_settled_contribution_is_priced_at_its_realized_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """estimated $500 / actual $400 -> the record carries $400.

        The rule itself, at the tier that decides it: a figure a human read off
        a statement is a fact and the row's own amount is an inference, so the
        actual wins.  Summing ``estimated_amount`` (the pre-#11 bug) would
        carry $500.
        """
        with app.app_context():
            account = self._contribution(
                seed_user, db.session, seed_periods[0],
                estimated="500", actual="400", settled=True,
            )
            db.session.commit()

            records = load_transfer_contributions_for_account(
                basis_for(account, seed_user["scenario"]),
                account.id, [seed_periods[0].id],
            ).records

            assert len(records) == 1
            assert records[0].amount == Decimal("400")
            assert records[0].is_confirmed is True
            assert records[0].account_id == account.id
            assert records[0].payday == seed_periods[0].start_date

    def test_periodic_contribution_uses_the_realized_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """One settled $500-estimated / $400-actual transfer -> average $400.

        The averaged periodic contribution over one period is the realized
        $400, NOT the estimated $500.
        """
        with app.app_context():
            account = self._contribution(
                seed_user, db.session, seed_periods[0],
                estimated="500", actual="400", settled=True,
            )
            db.session.commit()
            # The pass's ``DerivedPeriod`` for that row (ruling R-SAL19).
            period = calendar_for(seed_user["user"].id).saved()[0]

            records = load_transfer_contributions_for_account(
                basis_for(account, seed_user["scenario"]),
                account.id, [period.period_id],
            ).records
            result = calculate_investment_inputs(
                investment_params=self._params(), feed=AccountPayrollFeed.absent(),
                all_contributions=records, current_period=period,
            )

            # effective $400 / 1 period = $400 (NOT estimated $500).
            assert result.periodic_contribution == Decimal("400")

    def test_over_contribution_actual_above_estimate_is_honored(
        self, app, db, seed_user, seed_periods,
    ):
        """estimated $400 / actual $500 -> $500, the direction the cap must catch.

        Symmetric to the under-contribution case: a settled transfer that came
        in HIGHER than planned contributes its realized $500.  Summing
        ``estimated_amount`` would under-count it at $400 and let the annual
        limit accounting miss an over-contribution.
        """
        with app.app_context():
            account = self._contribution(
                seed_user, db.session, seed_periods[0],
                estimated="400", actual="500", settled=True,
            )
            db.session.commit()
            # The pass's ``DerivedPeriod`` for that row (ruling R-SAL19).
            period = calendar_for(seed_user["user"].id).saved()[0]

            records = load_transfer_contributions_for_account(
                basis_for(account, seed_user["scenario"]),
                account.id, [period.period_id],
            ).records
            result = calculate_investment_inputs(
                investment_params=self._params(limit=Decimal("23500")),
                feed=AccountPayrollFeed.absent(), all_contributions=records,
                current_period=period,
            )

            assert result.periodic_contribution == Decimal("500")
            # The displayed YTD (<= current) also reflects the realized $500.
            assert result.ytd_contributions == Decimal("500")

    def test_ytd_and_seed_both_read_the_realized_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """Three settled $500-estimated / $400-actual transfers in 3 periods.

        ``ytd_contributions`` (<= current) = 3 x $400 = $1,200;
        ``ytd_contributions_seed`` (< current) = 2 x $400 = $800.  Summing
        ``estimated_amount`` would give $1,500 / $1,000.
        """
        with app.app_context():
            periods = seed_periods[:3]
            account = None
            for period in periods:
                account = self._contribution(
                    seed_user, db.session, period,
                    estimated="500", actual="400", settled=True,
                    account=account,
                )
            db.session.commit()

            records = load_transfer_contributions_for_account(
                basis_for(account, seed_user["scenario"]),
                account.id, [p.id for p in periods],
            ).records
            assert len(records) == 3

            result = calculate_investment_inputs(
                investment_params=self._params(limit=Decimal("23500")),
                feed=AccountPayrollFeed.absent(), all_contributions=records,
                # The pass's ``DerivedPeriod`` for the third row (R-SAL19).
                current_period=calendar_for(seed_user["user"].id).saved()[2],
            )

            assert result.ytd_contributions == Decimal("1200")
            assert result.ytd_contributions_seed == Decimal("800")

    def test_inputs_average_and_timeline_agree(
        self, app, db, seed_user, seed_periods,
    ):
        """Both feeds read the SAME record, so they cannot price the row twice.

        The whole point of #11: the timeline applies a figure per period and the
        periodic-average / YTD-seed feed must apply the same one, or the
        limit/cap accounting reads a different number than the engine does.
        Since X-au-c2 both read one ``PricedContribution.amount``, so the
        agreement is structural -- this pins it end to end anyway, because the
        two feeds are what a future edit could re-split.
        """
        with app.app_context():
            account = self._contribution(
                seed_user, db.session, seed_periods[0],
                estimated="500", actual="400", settled=True,
            )
            db.session.commit()
            # The pass's own period, not the ORM row: both readers take a
            # ``DerivedPeriod`` since plan step salary:S3-e-2 (ruling
            # **R-SAL19**), and this case handed them ``seed_periods[0]``
            # until then -- which passed only because an absent feed never
            # reads the period at all, so it was the standing counterexample
            # to the contract rather than a caller of it.
            period = calendar_for(seed_user["user"].id).saved()[0]
            assert period.period_id == seed_periods[0].id

            records = load_transfer_contributions_for_account(
                basis_for(account, seed_user["scenario"]),
                account.id, [period.period_id],
            ).records
            inputs = calculate_investment_inputs(
                investment_params=self._params(), feed=AccountPayrollFeed.absent(),
                all_contributions=records, current_period=period,
            )
            timeline = build_contribution_timeline(
                feed=AccountPayrollFeed.absent(), contribution_transactions=records,
                periods=[period], as_of=period.start_date,
            )

            assert inputs.periodic_contribution == Decimal("400")
            assert len(timeline) == 1
            assert timeline[0].amount == Decimal("400")
            assert inputs.periodic_contribution == timeline[0].amount

    def test_excluded_status_rows_are_dropped_not_zeroed(
        self, app, db, seed_user, seed_periods,
    ):
        """A Cancelled transfer is DROPPED from the records, not kept at $0.00.

        The ``status_contributes_to_balance`` screen the projection module used
        to apply four times now runs once, here.  Dropping and zeroing are NOT
        interchangeable and this is the case that proves it:
        ``_inputs._average_transfer_contribution`` divides by the number of
        DISTINCT
        pay periods it sees, so a Cancelled contribution carried through at
        ``$0.00`` in its own period would make the denominator 2 and halve the
        average -- $400 / 2 periods = $200 against the correct $400.
        """
        with app.app_context():
            account = self._contribution(
                seed_user, db.session, seed_periods[0],
                estimated="400", settled=True,
            )
            # The SAME account, a later period: the Cancelled row has to be
            # inside the queried set for its absence to mean anything.
            self._contribution(
                seed_user, db.session, seed_periods[1],
                estimated="999", cancelled=True, account=account,
            )
            db.session.commit()

            records = load_transfer_contributions_for_account(
                basis_for(account, seed_user["scenario"]),
                account.id, [seed_periods[0].id, seed_periods[1].id],
            ).records

            # The Cancelled row is gone entirely -- not present as a zero.
            assert len(records) == 1
            assert records[0].amount == Decimal("400")
            assert records[0].payday == seed_periods[0].start_date

            result = calculate_investment_inputs(
                investment_params=self._params(), feed=AccountPayrollFeed.absent(),
                all_contributions=records, current_period=seed_periods[1],
            )
            # ONE period contributed, so the average is the full $400.  A
            # zero-carrying record would have made this $200.
            assert result.periodic_contribution == Decimal("400")

    def test_projected_contribution_is_not_confirmed(
        self, app, db, seed_user, seed_periods,
    ):
        """A still-Projected contribution is priced but NOT confirmed.

        The ``status.is_settled -> ContributionRecord.is_confirmed`` mapping
        moved to this boundary at plan step X-au-c2, and an adversarial review
        found the False half had lost its only grader: converting the old
        module-level test left it asserting a field the test itself supplied.
        A loader that wrote ``is_confirmed=True`` unconditionally would pass
        every other case here, and the growth engine would treat every future
        contribution as money already in the account.
        """
        with app.app_context():
            account = self._contribution(
                seed_user, db.session, seed_periods[0], estimated="250",
            )
            db.session.commit()

            records = load_transfer_contributions_for_account(
                basis_for(account, seed_user["scenario"]),
                account.id, [seed_periods[0].id],
            ).records

            assert len(records) == 1
            assert records[0].amount == Decimal("250")
            assert records[0].is_confirmed is False

    def test_cancelled_contribution_still_counts_as_LINKED(
        self, app, db, seed_user, seed_periods,
    ):
        """A screened-out row still reports its account as linked.

        The companion to
        :meth:`test_excluded_status_rows_are_dropped_not_zeroed`, and the reason
        the loader returns two facts rather than one.  ``retirement_projection``
        asks whether ANYTHING funds an account to decide between rendering
        ``$0.00`` and a "link a contribution" call-to-action; a Cancelled
        contribution counts nothing but is still a link, so screening it out of
        the records must not take the account out of that set.  An adversarial
        review caught this flipping when the screen moved here.
        """
        with app.app_context():
            account = self._contribution(
                seed_user, db.session, seed_periods[0],
                estimated="600", cancelled=True,
            )
            db.session.commit()

            loaded = load_transfer_contributions_for_account(
                basis_for(account, seed_user["scenario"]),
                account.id, [seed_periods[0].id],
            )

            # Nothing COUNTS ...
            assert loaded.records == []
            # ... but something is LINKED.
            assert account.id in loaded.linked_account_ids
