"""How a read of the board waits out its lag: the one wait, :func:`read_until`.

The board's order LAGS a write: L1 measured 7 of 9 just-added cards listed, all 9 about 20
seconds later (``_tracker``'s module docstring), so a write that places a card reads the board
back until it shows the card where it was put (``_tracker.Board.shows``), and a stuck read is
reported rather than waited on forever.

Split out of :mod:`_tracker`, which the board's back-to-back move (``_tracker.Board.move``,
plan#4021) would take past the 1,000 lines ``too-many-lines`` allows.  A leaf: it imports no
module of quill's.
"""
from __future__ import annotations

from collections.abc import Callable

#: How long a write waits for the board to show what it placed: three times
#: the ~20 seconds L1 measured, so a slow read is waited out and a stuck one
#: is reported rather than waited on forever.
BOARD_WAIT_SECONDS = 60
#: How often a board that lags a write is read again.
POLL_SECONDS = 5


def read_until[T](read: Callable[[], list[T]], holds: Callable[[list[T]], bool],
                  sleep: Callable[[float], None]) -> tuple[list[T], bool]:
    """Read the board (``read``, ``_tracker.Board.held``) every :data:`POLL_SECONDS` until a
    read of it ``holds``, for at most :data:`BOARD_WAIT_SECONDS`: the board's lag waited out in
    one place.  The last read, and whether it holds."""
    waited = 0
    while True:
        order = read()
        if holds(order):
            return order, True
        if waited >= BOARD_WAIT_SECONDS:
            return order, False
        sleep(POLL_SECONDS)
        waited += POLL_SECONDS
