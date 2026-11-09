"""Tests for the loan data-loader leaf (anchor-fact synthesis).

:mod:`app.services.loan_loaders` is the leaf layer every loan consumer loads
through.  These tests pin its C11 centerpiece --
:func:`~app.services.loan_loaders.load_loan_anchor_facts` -- the ONE anchor
loader the genesis posting walk and every resolver-input builder share: the
origination anchor is SYNTHESIZED from the immutable :class:`LoanParams` and is
ALWAYS the opening (step C1; the origination :class:`LoanAnchorEvent` write is
retired), while stored ``tracking_start`` and ``user_trueup`` events load as
non-opening balance ASSERTIONS, and legacy stored origination rows are ignored
(they were always verbatim copies of the params, verified on production
data).

All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from itertools import permutations

from app import ref_cache
from app.enums import LoanAnchorSourceEnum
from app.extensions import db as _db
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_params import LoanParams
from app.services import loan_ledger, loan_loaders, loan_resolver
from app.utils.dates import anchor_chronology_key
from tests._ledger_reads import posted_loan_balance_at
from tests._test_helpers import (
    create_loan_account,
    insert_tracking_start_event,
    insert_trueup_event,
)

_PRINCIPAL = Decimal("250000.00")
_ORIGINATION = date(2025, 1, 1)


def _params(loan):
    """Return the loan account's :class:`LoanParams` row."""
    return _db.session.query(LoanParams).filter_by(account_id=loan.id).one()


class TestLoadLoanAnchorFacts:
    """The anchor facts: synthesized origination + stored true-ups, one loader."""

    def test_origination_fact_is_synthesized_from_params(
        self, app, db, seed_user,
    ):
        """The opening fact mirrors the immutable params, never a stored row.

        A fresh loan's fact list is exactly one OPENING fact carrying the
        params' origination date and original principal, dated with the
        earliest possible UTC instant so any same-day true-up outranks it
        in the ``(anchor_date, created_at, event_id)`` chronology
        (:func:`app.utils.dates.anchor_chronology_key`).
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_PRINCIPAL,
                rate=Decimal("0.06000"), origination_date=_ORIGINATION,
            )
            facts = loan_loaders.load_loan_anchor_facts(_params(loan))

            (opening,) = [fact for fact in facts if fact.is_opening]
            assert opening.account_id == loan.id
            assert opening.anchor_date == _ORIGINATION
            assert opening.anchor_balance == _PRINCIPAL
            assert opening.created_at.tzinfo is timezone.utc

    def test_legacy_origination_rows_are_ignored(self, app, db, seed_user):
        """A stored origination event never becomes a second opening fact.

        Real production data still carries the pre-retirement origination
        :class:`LoanAnchorEvent` rows (the Mortgage's 2018-12-01, the Van
        Loan's 2023-02-14): the write is retired but the rows were never
        migrated.  ``load_loan_anchor_facts`` loads only the USER_TRUEUP and
        TRACKING_START sources, so an ORIGINATION-source row never enters --
        the opening is synthesized from the params and IGNORES that row,
        exactly one opening fact, value-identical to the synthesis, so
        pre-retirement and post-retirement loans walk the same facts.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_PRINCIPAL,
                rate=Decimal("0.06000"), origination_date=_ORIGINATION,
            )
            # Seed a legacy ORIGINATION-source row (the retired write), as the
            # real loans still carry; the loader must not read it.
            db.session.add(LoanAnchorEvent(
                account_id=loan.id,
                anchor_date=_ORIGINATION,
                anchor_balance=_PRINCIPAL,
                source_id=ref_cache.loan_anchor_source_id(
                    LoanAnchorSourceEnum.ORIGINATION,
                ),
            ))
            db.session.commit()
            assert (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=loan.id)
                .count()
            ) == 1

            facts = loan_loaders.load_loan_anchor_facts(_params(loan))
            assert len(facts) == 1
            assert facts[0].is_opening is True
            assert facts[0].is_tracking_start is False

    def test_trueup_events_become_non_opening_facts(self, app, db, seed_user):
        """Each stored user true-up loads as a non-opening fact with its values.

        A $200,000 true-up on 2026-02-15 loads beside the synthesized
        opening: two facts, the true-up carrying the stored date / balance
        and ``is_opening=False`` (it books the TRUEUP kinds, never a second
        opening).
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_PRINCIPAL,
                rate=Decimal("0.06000"), origination_date=_ORIGINATION,
            )
            insert_trueup_event(
                _params(loan), Decimal("200000.00"), date(2026, 2, 15),
            )
            db.session.commit()

            facts = loan_loaders.load_loan_anchor_facts(_params(loan))
            assert len(facts) == 2
            (trueup,) = [fact for fact in facts if not fact.is_opening]
            assert trueup.anchor_date == date(2026, 2, 15)
            assert trueup.anchor_balance == Decimal("200000.00")

    def test_tracking_start_is_a_non_opening_assertion(self, app, db, seed_user):
        """A ``tracking_start`` loads as an assertion, NOT the opening (step C1).

        For a mid-life-imported loan the opening stays the synthesized
        ORIGINATION; the ``tracking_start`` is an ``is_opening=False`` balance
        assertion carrying ``is_tracking_start=True`` for the display label.  A
        $180,000 tracking-start as of 2025-06-01 on a loan that originated at
        $250,000 on 2025-01-01 yields TWO facts: the origination opening
        (250000 / 2025-01-01, ``is_tracking_start=False``) and the tracking-start
        assertion (180000 / 2025-06-01, ``is_opening=False``,
        ``is_tracking_start=True``).
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_PRINCIPAL,
                rate=Decimal("0.06000"), origination_date=_ORIGINATION,
            )
            insert_tracking_start_event(
                _params(loan), Decimal("180000.00"), date(2025, 6, 1),
            )
            db.session.commit()

            facts = loan_loaders.load_loan_anchor_facts(_params(loan))
            assert len(facts) == 2
            (opening,) = [fact for fact in facts if fact.is_opening]
            assert opening.anchor_date == _ORIGINATION
            assert opening.anchor_balance == _PRINCIPAL
            assert opening.is_tracking_start is False
            (tracking,) = [fact for fact in facts if not fact.is_opening]
            assert tracking.anchor_date == date(2025, 6, 1)
            assert tracking.anchor_balance == Decimal("180000.00")
            assert tracking.is_opening is False
            assert tracking.is_tracking_start is True

    def test_same_day_tracking_start_correction_latest_created_governs(
        self, app, db, seed_user,
    ):
        """Two same-day tracking-starts (a correction): latest created governs.

        The table is append-only, so a correction is another ``tracking_start``
        row.  Both load as non-opening assertions (``is_tracking_start=True``);
        on a shared date the walk applies them in ``created_at`` order, so the
        LATEST-created is the last reset and governs.  A later-committed
        $180,000 correction on 2025-05-01 supersedes the earlier
        $185,000/2025-05-01 assertion, and
        :func:`~app.services.loan_resolver.select_latest_anchor` (keyed on
        :func:`app.utils.dates.anchor_chronology_key`) picks it.

        *This docstring claimed the shape was "the exact model the real Van
        Loan carries (two 2026-04-11 tracking-starts)" until plan step
        X-an-b.  It is not: production account 8 carries NO ``tracking_start``
        row, and the only one on the database is account 3's, dated
        2026-03-31.  The table is append-only, so that state was never
        production's -- the claim was invented.*
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_PRINCIPAL,
                rate=Decimal("0.06000"), origination_date=_ORIGINATION,
            )
            insert_tracking_start_event(
                _params(loan), Decimal("185000.00"), date(2025, 5, 1),
            )
            db.session.commit()
            # A later-committed correction on the SAME date (distinct balance, so
            # the same-day unique index permits it); its created_at is strictly
            # later, so it is the authoritative assertion.
            insert_tracking_start_event(
                _params(loan), Decimal("180000.00"), date(2025, 5, 1),
            )
            db.session.commit()

            facts = loan_loaders.load_loan_anchor_facts(_params(loan))
            assertions = [fact for fact in facts if not fact.is_opening]
            assert len(assertions) == 2
            assert all(fact.is_tracking_start for fact in assertions)
            latest = loan_resolver.select_latest_anchor(facts)
            assert latest.is_tracking_start is True
            assert latest.anchor_balance == Decimal("180000.00")
            assert latest.anchor_date == date(2025, 5, 1)

    def test_same_day_trueup_outranks_the_synthesized_opening(
        self, app, db, seed_user,
    ):
        """A true-up dated ON the origination date is the latest anchor.

        The resolver picks its replay anchor by
        :func:`app.utils.dates.anchor_chronology_key` DESC.  The synthesized
        opening carries the earliest possible instant,
        so a true-up asserted on the very origination date still wins the
        tie -- exactly as the stored origination row (created at setup,
        before any true-up) behaved.  A $240,000 same-day true-up must be
        the selected anchor, not the $250,000 opening.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_PRINCIPAL,
                rate=Decimal("0.06000"), origination_date=_ORIGINATION,
            )
            insert_trueup_event(
                _params(loan), Decimal("240000.00"), _ORIGINATION,
            )
            db.session.commit()

            facts = loan_loaders.load_loan_anchor_facts(_params(loan))
            latest = loan_resolver.select_latest_anchor(facts)
            assert latest.is_opening is False
            assert latest.anchor_balance == Decimal("240000.00")


class TestTheOneAnchorChronology:
    """Plan step X-an-b / finding N-196: ONE anchor order, read and write.

    A loan's anchors had no defined order and its two consumers each broke a
    tie their own way -- the fold's walk reset on the LAST of a tie, the
    resolver's ``max()`` seeded from the FIRST -- so on a full tie carrying two
    balances they were guaranteed to name opposite rows, and which row each
    named was whatever PostgreSQL returned.  The order is now stated ONCE, by
    :func:`~app.services.loan_loaders.load_loan_anchor_facts`, on the TOTAL key
    ``(anchor_date, created_at, event_id)``.

    These pin the contract and both consumers against it.
    """

    def test_facts_come_back_in_the_canonical_chronology(
        self, app, db, seed_user,
    ):
        """The loader returns ascending ``(anchor_date, created_at, event_id)``.

        Two assertions are written in an order that is NOT their chronology (a
        later-dated true-up first, an earlier-dated one second), so a loader
        that simply concatenated the query's rows would return them the wrong
        way round.  The list must come back in business-date order with the
        synthesized origination first.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_PRINCIPAL,
                rate=Decimal("0.06000"), origination_date=_ORIGINATION,
            )
            insert_trueup_event(
                _params(loan), Decimal("180000.00"), date(2025, 9, 1),
            )
            db.session.commit()
            insert_trueup_event(
                _params(loan), Decimal("200000.00"), date(2025, 3, 1),
            )
            db.session.commit()

            facts = loan_loaders.load_loan_anchor_facts(_params(loan))
            assert [fact.anchor_date for fact in facts] == [
                _ORIGINATION, date(2025, 3, 1), date(2025, 9, 1),
            ]
            assert facts[0].is_opening is True
            assert facts == sorted(
                facts,
                key=lambda fact: (
                    fact.anchor_date, fact.created_at, fact.event_id,
                ),
            )

    def test_the_resolver_takes_the_last_fact_the_loader_returns(
        self, app, db, seed_user,
    ):
        """The loader's LAST fact and the resolver's pick are the same row.

        Both read :func:`app.utils.dates.anchor_chronology_key`, so this is one
        rule seen from two ends: the loader sorts ascending by it, the resolver
        takes its ``max()``.  Pinned because the walk depends on the identity --
        it resets the running balance at each anchor in turn, so the one it
        reaches LAST must be the one the resolver seeds from.

        The two assertions are written in REVERSE chronological order (the later
        business date committed first), so the query's own row order disagrees
        with the chronology and a loader that returned rows unsorted fails here
        rather than passing by luck.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_PRINCIPAL,
                rate=Decimal("0.06000"), origination_date=_ORIGINATION,
            )
            insert_trueup_event(
                _params(loan), Decimal("175000.00"), date(2025, 8, 1),
            )
            db.session.commit()
            insert_tracking_start_event(
                _params(loan), Decimal("190000.00"), date(2025, 4, 1),
            )
            db.session.commit()

            facts = loan_loaders.load_loan_anchor_facts(_params(loan))
            assert len(facts) == 3
            assert loan_resolver.select_latest_anchor(facts) is facts[-1]
            # ...and it is the 2025-08-01 row, not the later-INSERTED 04-01 one.
            assert facts[-1].anchor_date == date(2025, 8, 1)
            assert facts[-1].anchor_balance == Decimal("175000.00")

    def test_a_full_tie_is_named_the_same_row_by_the_walk_and_the_resolver(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL: a real tie, and both sides name ONE row.

        Two ``user_trueup`` events for ONE date carrying DIFFERENT balances and
        sharing a ``created_at``.  **Half of that state is production's today
        and the other half is one backfill away**: ``CreatedAtMixin.created_at``
        is ``server_default=func.now()``, which PostgreSQL evaluates at
        TRANSACTION START, so every row written in one transaction shares an
        instant -- ``shekel-prod-db`` carries four such rows (ids 1-4, all
        ``2026-05-22 02:41:22.187019+00``), and they escape the tie only because
        no two of them share an ``anchor_date``.  The HTTP door cannot produce
        the full tie (it writes at most one row per transaction), so reaching it
        takes a backfill, a migration or hand-SQL.

        The instant is set EXPLICITLY because the suite's frozen-clock rewriter
        issues each ``now()`` one microsecond past the last, deliberately, so
        that fixtures cannot tie (see ``_test_helpers._FrozenDbClock``).  Left
        to the helper, this test would build a state it claims to test and
        silently not have it -- so it ASSERTS the tie before asserting anything
        about it, and asserts that the pre-X-an-b key cannot break it.

        Before X-an-b the resolver's ``max()`` returned the FIRST maximal row
        while the walk reset on the LAST, over a list loaded with no
        ``ORDER BY`` -- so the loan's rendered balance and its posted ledger
        could land ``$10,000.00`` apart, non-deterministically, from unchanged
        data.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_PRINCIPAL,
                rate=Decimal("0.06000"), origination_date=_ORIGINATION,
            )
            tie_day = date(2025, 6, 1)
            shared_instant = datetime(
                2025, 6, 2, 15, 30, tzinfo=timezone.utc,
            )
            trueup_source_id = ref_cache.loan_anchor_source_id(
                LoanAnchorSourceEnum.USER_TRUEUP,
            )
            for balance in (Decimal("150000.00"), Decimal("140000.00")):
                _db.session.add(LoanAnchorEvent(
                    account_id=loan.id,
                    anchor_date=tie_day,
                    anchor_balance=balance,
                    source_id=trueup_source_id,
                    created_at=shared_instant,
                ))
            db.session.commit()

            facts = loan_loaders.load_loan_anchor_facts(_params(loan))
            tied = [fact for fact in facts if fact.anchor_date == tie_day]
            assert len(tied) == 2
            # The tie is REAL: both terms above ``event_id`` are equal, so
            # ``event_id`` is the only thing that can decide it.
            assert tied[0].created_at == tied[1].created_at
            assert tied[0].event_id < tied[1].event_id
            assert [fact.anchor_balance for fact in tied] == [
                Decimal("150000.00"), Decimal("140000.00"),
            ]

            def superseded_key(fact):
                """The pre-X-an-b key: ``(anchor_date, created_at)``, no id."""
                return (fact.anchor_date, fact.created_at)

            # The defect, stated as an assertion: under the old key ``max()``
            # answers whichever row it happened to see first, so the SAME two
            # facts in two orders name two different rows.
            assert (
                max(tied, key=superseded_key)
                is not max(list(reversed(tied)), key=superseded_key)
            )

            # Under the total key the later INSERT governs -- the same "last
            # one recorded is that day's closing balance" rule this table's
            # write door (``loan_anchor_service._governing_loan_anchor``) applies.
            governing = loan_resolver.select_latest_anchor(facts)
            assert governing is tied[1]
            assert governing.anchor_balance == Decimal("140000.00")
            # ...and order no longer decides it.
            assert loan_resolver.select_latest_anchor(
                list(reversed(facts)),
            ) is governing

            # The walk resets on the anchors in stream order, so its LAST
            # correction is the anchor its running balance ends on.  It is the
            # same row the resolver seeds from -- which is the whole invariant.
            # Compared by VALUE, not identity: the walk loads its own facts, and
            # ``event_id`` is exactly what makes value-equality name one row.
            walked = loan_ledger.walk_loan_ledger(
                loan.id, seed_user["scenario"].id,
            ).anchor_corrections
            assert walked[-1].anchor == governing
            assert walked[-1].anchor.event_id == tied[1].event_id
            # And the walk ended on it: the earlier row is what it reset FROM.
            assert walked[-1].owed_before == Decimal("150000.00")

            # THE MONEY.  The two same-day corrections merge into one posted
            # target whose legs SUM, so the posted balance telescopes to the
            # LAST anchor's asserted value.  This is the figure that would have
            # disagreed with the resolver by $10,000.00 when the two picked
            # opposite rows -- assert it, not just the in-memory walk.
            assert posted_loan_balance_at(
                loan.id, seed_user["scenario"].id, tie_day,
            ) == Decimal("140000.00")
            assert loan_resolver.select_latest_anchor(
                facts,
            ).anchor_balance == posted_loan_balance_at(
                loan.id, seed_user["scenario"].id, tie_day,
            )

    def test_the_merge_defers_to_the_loaders_order_and_does_not_re_sort(
        self, app, db, seed_user,
    ):
        """The walk's replay adds NO ordering rule of its own within a kind.

        Handed two same-date anchors in an order the loader would never
        produce, the replay must apply them in exactly that order: its stable
        sort keys only on ``(on_date, kind)``, so the anchors' relative order is
        whatever the caller supplied.  That is the contract -- the loader is the
        one home of the anchor key, so a re-sort here would be a second
        statement of it, and it was an INCOMPLETE second statement
        (``(anchor_date, created_at)``, no ``event_id``) until X-an-b.

        The stream builder and the replay were ONE function
        (``merge_anchor_and_payment_events``) until plan step X-au-g-2c-3b-2
        split the mapping from the ordering; the contract this pins is
        unchanged, and it is now the replay's.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_PRINCIPAL,
                rate=Decimal("0.06000"), origination_date=_ORIGINATION,
            )
            tie_day = date(2025, 7, 1)
            insert_trueup_event(_params(loan), Decimal("160000.00"), tie_day)
            insert_trueup_event(_params(loan), Decimal("155000.00"), tie_day)
            db.session.commit()

            params = _params(loan)
            facts = loan_loaders.load_loan_anchor_facts(params)
            reversed_facts = list(reversed(facts))
            replay = loan_ledger.replay_loan_events(
                Decimal("0.00"),
                loan_ledger.loan_event_stream(
                    reversed_facts, [],
                    loan_ledger.LoanCalendar(
                        origination_date=params.origination_date,
                        payment_day=params.payment_day,
                        periods=loan_resolver.resolve_periods(
                            params, loan_loaders.load_rate_changes(loan.id),
                        ),
                        escrow_lines=[],
                    ),
                ),
            )
            # Same date, so only the caller's order can decide: the replay
            # preserved it rather than imposing one.
            assert [
                outcome.event.source.event_id for outcome in replay.resets[-2:]
            ] == [reversed_facts[0].event_id, reversed_facts[1].event_id]


class TestTheChronologyKeyItself:
    """The key as a pure function, over every permutation -- no database.

    :func:`app.utils.dates.anchor_chronology_key` is the ONE definition both
    the loader and the resolver read, so the property that matters is a property
    of the KEY: it totally orders a loan's assertions, and every one of its three
    terms is load-bearing.  Testing that through the database can only reach the
    orderings PostgreSQL happens to return; testing it here reaches all of them.
    """

    @staticmethod
    def _fact(anchor_date, instant, event_id, balance="1000.00"):
        """Return a :class:`LoanAnchorFact` with the given ordering terms."""
        return loan_loaders.LoanAnchorFact(
            account_id=1,
            anchor_date=anchor_date,
            anchor_balance=Decimal(balance),
            is_opening=False,
            created_at=instant,
            event_id=event_id,
            is_tracking_start=False,
        )

    def test_every_permutation_sorts_to_the_same_chronology(self):
        """Sorting by the key is order-independent -- for all 24 input orders.

        Four facts that differ in each of the three terms in turn: two share a
        date, two of those also share an instant.  Under the pre-X-an-b
        two-term key the last pair is indistinguishable, so their relative order
        would be whatever the input happened to carry; under the total key every
        permutation lands on ONE sequence.
        """
        early = datetime(2026, 1, 1, tzinfo=timezone.utc)
        late = datetime(2026, 6, 1, tzinfo=timezone.utc)
        facts = [
            self._fact(date(2025, 1, 1), early, 11),
            self._fact(date(2025, 5, 1), early, 12),
            self._fact(date(2025, 5, 1), late, 13),
            self._fact(date(2025, 5, 1), late, 14),
        ]
        expected = [11, 12, 13, 14]
        for order in permutations(facts):
            ordered = sorted(order, key=anchor_chronology_key)
            assert [fact.event_id for fact in ordered] == expected
            # The resolver's end of the same rule: its max IS that last row,
            # from any input order.  This is the identity the walk relies on.
            assert loan_resolver.select_latest_anchor(
                list(order),
            ).event_id == expected[-1]

    def test_each_term_decides_when_the_ones_before_it_are_equal(self):
        """All three terms are load-bearing, one assertion each."""
        early = datetime(2026, 1, 1, tzinfo=timezone.utc)
        late = datetime(2026, 6, 1, tzinfo=timezone.utc)
        # anchor_date leads: a LATER business day wins despite an earlier
        # recording instant and a lower id.
        assert anchor_chronology_key(
            self._fact(date(2025, 9, 1), early, 1),
        ) > anchor_chronology_key(self._fact(date(2025, 3, 1), late, 99))
        # created_at breaks an equal date.
        assert anchor_chronology_key(
            self._fact(date(2025, 5, 1), late, 1),
        ) > anchor_chronology_key(self._fact(date(2025, 5, 1), early, 99))
        # event_id breaks an equal date AND instant -- the term X-an-b added,
        # and the only thing separating two rows written in one transaction.
        assert anchor_chronology_key(
            self._fact(date(2025, 5, 1), late, 99),
        ) > anchor_chronology_key(self._fact(date(2025, 5, 1), late, 98))

    def test_the_synthesized_origination_sorts_below_a_same_day_assertion(
        self, app, db, seed_user,
    ):
        """The sentinels do their job: origination first, on its own date.

        The synthesized origination carries the earliest possible instant and an
        id below the sequence start, so a true-up asserted ON the origination
        date outranks it -- the property both sentinels exist for, asserted
        against the real loader rather than a hand-built pair.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, principal=_PRINCIPAL,
                rate=Decimal("0.06000"), origination_date=_ORIGINATION,
            )
            insert_trueup_event(
                _params(loan), Decimal("240000.00"), _ORIGINATION,
            )
            db.session.commit()

            facts = loan_loaders.load_loan_anchor_facts(_params(loan))
            assert [fact.is_opening for fact in facts] == [True, False]
            assert anchor_chronology_key(facts[0]) < anchor_chronology_key(
                facts[1],
            )
            assert loan_resolver.select_latest_anchor(facts) is facts[1]


class TestLoadLoanAccountIdsForUser:
    """``load_loan_account_ids_for_user``: ONE owner's loans, ascending.

    It moved here from the stored ledger's writer's tests, whose per-user
    resync it served, when plan step plan#2914 deleted the writer; its reader
    since is the loan-destination route (``routes/_loan_destination.py``).
    """

    def test_enumerator_returns_only_the_users_loans(
        self, app, db, seed_user, seed_second_user,
    ):
        """It filters LoanParams by owner, ascending.

        Two loans on the first user and one on the second: each owner's scoped
        enumeration returns exactly its own loan ids -- proving the join is
        owner-scoped, not global.
        """
        with app.app_context():
            loan_a = create_loan_account(seed_user, _db.session, name="Loan A")
            loan_b = create_loan_account(seed_user, _db.session, name="Loan B")
            loan_c = create_loan_account(
                seed_second_user, _db.session, name="Loan C",
            )
            _db.session.commit()

            assert loan_loaders.load_loan_account_ids_for_user(
                seed_user["user"].id,
            ) == sorted([loan_a.id, loan_b.id])
            assert loan_loaders.load_loan_account_ids_for_user(
                seed_second_user["user"].id,
            ) == [loan_c.id]
