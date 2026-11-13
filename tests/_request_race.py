"""
Shekel Budget App -- two real requests raced on the owner's write lock

The harness every test that races two of one owner's REQUESTS stages them on:
:func:`race_two_requests`, and the clients it races, :func:`signed_in_clients`.
Since plan step ``balance:X-bn`` (rulings
**R-CC106**, **R-CC114**) the serialisation a door relies on is the
REQUEST's: every command transaction a signed-in request opens takes its
owner's write lock before it reads any of the owner's data
(:mod:`app.db_transaction`), so the second of two clicks waits there, before
its door has read anything, and then reads the first click's committed work.
Two threads calling a service directly race with no lock at all, which
production never does, so a race is two requests.

**Every race asserts the second request waited on the owner's key**
(:attr:`RaceAnswer.waited`), and that is each race's control: with the
owner's lock removed the second request does not wait there, and the race
fails whatever its outcome assertions would have read.

Built at plan step ``credit_card:CC-5-4a-4`` inside
``tests/test_services/test_cc5_4a4_row_lock_races/test_door_pairs.py``, and
moved here unchanged (the clients from that package's ``_helpers``) when
plan#2331 raced a second pair of doors (two removes of a card's terms
versions), so both race on one harness.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.extensions import db as _db
from tests._test_helpers import owner_lock_key
from tests._seed_owner_fixtures import SEED_USER_EMAIL, SEED_USER_PASSWORD


#: How long a race waits for a request to reach its commit, or the second
#: request to block, before calling the harness broken.  The cluster's own
#: ``lock_timeout`` is 10 s.  The raw-statement races of
#: ``tests/test_services/test_cc5_4a4_row_lock_races`` wait by it too.
RACE_WAIT_SECONDS = 8.0


def signed_in_clients(app, count=2):
    """*count* clients, each signed in as the seed user.

    Each sign-in's redirect is checked not to be the sign-in page, so a race
    cannot quietly run one client signed out.

    Returns:
        The signed-in clients, in order.
    """
    clients = []
    for _ in range(count):
        client = app.test_client()
        response = client.post("/login", data={
            "email": SEED_USER_EMAIL, "password": SEED_USER_PASSWORD,
        })
        assert response.status_code == 302, response.status_code
        assert urlsplit(response.headers["Location"]).path != "/login"
        clients.append(client)
    return clients


#: Whether a backend in THIS database waits, ungranted, for one owner's write
#: lock.  Scoped to the owner's key and to ``current_database()``: a poll of
#: every ungranted lock on the cluster reads a wait on any other lock -- a row
#: lock, another test's key -- as this one, which is how a broken build once
#: passed a race (plan step credit_card:CC-5-4a-4's third checkpoint).
_WAITING_ON_THE_OWNER = text(
    "SELECT EXISTS (SELECT 1 FROM pg_locks "
    "WHERE locktype = 'advisory' AND NOT granted "
    "AND database = (SELECT oid FROM pg_database "
    "WHERE datname = current_database()) "
    "AND classid = CAST(:namespace AS oid) AND objid = CAST(:owner AS oid) "
    "AND objsubid = 2)"
)


@dataclass
class RaceAnswer:
    """What one request came to: its response, and whether it waited on the owner's key."""

    response: object = None
    waited: bool = False


def race_two_requests(app, owner_id, first, second):
    """Race two REQUESTS: *first* held at its commit, *second* started, then both released.

    *first* and *second* are no-argument callables that each issue one
    request and return its response.  Each runs on a thread of its own with
    no app context pushed, so the request pushes its own, with its own
    session, as production gives it.  The first is held at its FIRST commit
    by a ``before_commit`` hook on its thread alone; the second starts once
    it is held, and the test polls for the second waiting on *owner_id*'s
    write lock (:data:`_WAITING_ON_THE_OWNER`).  Then the first commits and
    the second runs to its answer.

    Args:
        app: The application.
        owner_id: The owner whose write lock both requests take.
        first: The request that commits first.
        second: The request that arrives while the first is held.

    Returns:
        ``(first_answer, second_answer)``; ``second_answer.waited`` is whether
        the second was seen waiting on the owner's key.

    Raises:
        AssertionError: When the first request never reached a commit, or a
            thread did not finish -- the harness measured nothing.
    """
    namespace, owner_key = owner_lock_key(owner_id)
    answers = (RaceAnswer(), RaceAnswer())
    errors = []
    held, release = threading.Event(), threading.Event()
    box = {"first": None, "held": False}

    def hold_the_first_commit(_session):
        if threading.get_ident() == box["first"] and not box["held"]:
            box["held"] = True
            held.set()
            release.wait(RACE_WAIT_SECONDS)

    def run(click, answer, is_first):
        if is_first:
            box["first"] = threading.get_ident()
        try:
            answer.response = click()
        except BaseException as exc:  # pylint: disable=broad-exception-caught
            # Re-raised on the test's thread below: an exception a request
            # raised is the failure the test must report, with its traceback,
            # rather than a thread dying unseen.
            errors.append(exc)
        finally:
            if is_first:
                held.set()

    event.listen(Session, "before_commit", hold_the_first_commit)
    try:
        one = threading.Thread(target=run, args=(first, answers[0], True))
        one.start()
        assert held.wait(RACE_WAIT_SECONDS) and box["held"], (
            "the first request never reached its commit -- nothing was held, "
            f"so nothing raced ({errors or answers[0].response})"
        )
        two = threading.Thread(target=run, args=(second, answers[1], False))
        two.start()
        deadline = time.monotonic() + RACE_WAIT_SECONDS
        while time.monotonic() < deadline and two.is_alive():
            if _db.session.execute(
                _WAITING_ON_THE_OWNER,
                {"namespace": namespace, "owner": owner_key},
            ).scalar_one():
                answers[1].waited = True
                break
            time.sleep(0.02)
        _db.session.rollback()
        release.set()
        one.join(RACE_WAIT_SECONDS * 3)
        two.join(RACE_WAIT_SECONDS * 3)
        assert not one.is_alive() and not two.is_alive(), (
            "a request never finished"
        )
    finally:
        release.set()
        event.remove(Session, "before_commit", hold_the_first_commit)
    if errors:
        raise errors[0]
    _db.session.expire_all()
    return answers
