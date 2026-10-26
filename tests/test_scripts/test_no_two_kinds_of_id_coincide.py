"""In a fresh clone, no two non-ref tables hand out the same id (ruling R-BAL356).

``scripts/build_test_template.py`` starts every non-ref sequence in a range of
its own (``_start_each_table_in_an_id_range_of_its_own``), so a code path that
reads one kind of id where another is meant, an account id where a user id
belongs, fails a case instead of passing by coincidence.  **Almost nothing else
would notice the ranges going**: on correct code nearly every case passes
either way, and the catchers the ranges arm stop catching in silence.  This
file is what fails if they go, by name.  One other case fails too, and not by
name: ``test_the_door_settles_the_payments_row_and_not_the_row_sharing_its_id``
(``tests/test_services/test_cc5_4a1_settlement_subject/``) restarts the rows'
sequence at a movement's id, which without the ranges a row already holds in
that case, so its setup raises a unique violation on ``transactions_pkey``
(measured by the review of ruling R-BAL356).

It reads the clone's catalog rather than importing the builder, because that
module rewrites the process environment (``TEST_DATABASE_URL``) when imported.
"""
from __future__ import annotations

from sqlalchemy import text

# More ids than any one case draws from one table, so two sequences this far
# apart never meet inside a case.  It is this file's own bound on what
# "disjoint" means, not a copy of the builder's width.
_A_MILLION_IDS = 1_000_000

# The kinds of id the ruling names as coinciding before the ranges (user and
# account, transfer and row, period and scenario).  The scan must find each,
# so it cannot pass by reading nothing.
_THE_RULINGS_KINDS = frozenset({
    ("auth", "users_id_seq"),
    ("budget", "accounts_id_seq"),
    ("budget", "transfers_id_seq"),
    ("budget", "transactions_id_seq"),
    ("budget", "pay_periods_id_seq"),
    ("budget", "scenarios_id_seq"),
})


class TestEveryTableStartsInARangeOfItsOwn:
    """Each non-ref sequence's next id lies a million ids from every other."""

    def test_no_two_non_ref_sequences_hand_out_ids_within_a_million_of_each_other(
        self, app, db,
    ):
        """The next ids, sorted, are all above a million and a million apart.

        ``ref`` is left out because its rows carry literal ids that production
        shares (the builder's docstring says why).  Read in a clone no case
        has written to, so each next id is the template's own.  A sequence
        never drawn from shows ``last_value`` NULL, and its next id is its
        ``start_value``: that is the state an unranged sequence would be in,
        and it is the one this case must refuse.
        """
        with app.app_context():
            rows = db.session.execute(text(
                "SELECT schemaname, sequencename, "
                "COALESCE(last_value + increment_by, start_value) AS next_id "
                "FROM pg_sequences WHERE schemaname <> 'ref' "
                "ORDER BY next_id"
            )).all()

        scanned = {(row.schemaname, row.sequencename) for row in rows}
        assert _THE_RULINGS_KINDS <= scanned, (
            f"the scan missed {sorted(_THE_RULINGS_KINDS - scanned)}"
        )
        next_ids = [row.next_id for row in rows]
        below = [
            (row.schemaname, row.sequencename, row.next_id)
            for row in rows if row.next_id <= _A_MILLION_IDS
        ]
        assert not below, (
            f"these sequences hand out an id at or below {_A_MILLION_IDS}, "
            f"where a case's 'no such row' literal lives: {below}"
        )
        too_close = [
            (rows[i].sequencename, rows[i + 1].sequencename)
            for i in range(len(next_ids) - 1)
            if next_ids[i + 1] - next_ids[i] < _A_MILLION_IDS
        ]
        assert not too_close, (
            f"these sequences' next ids are under {_A_MILLION_IDS} apart, so "
            f"their tables' ids can coincide: {too_close}"
        )
