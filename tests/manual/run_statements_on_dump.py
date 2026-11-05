"""The two statements before X-ew's second leaf and after, on a production dump.

Plan task plan#2773 (plan step plan#2515, "The proof"): the leaf moves the income statement
and the balance sheet off the stored journal onto the worked-out ledger (plan task plan#2771),
and its proof is ``verify_statement_baseline.py`` byte-identical on a same-day production
dump, before the leaf against after, with ``verify_render_surfaces.py`` for the pages that
render a statement or a lock.  On the release kit's bench (``tools/rehearsal``): one dump
restored once and cloned per copy, base's copy (the commit the card was claimed from) and
head's each deployed by its own commit's step (``rows.deploy``) under ONE instant, noon of the
dump's civil day (``rows.pinned_instant``).  Then each INSTRUMENT runs once per side, from the
side's worktree on the side's copy at that instant (``_dump_runs.run_pinned_on``), and each
pair is compared byte for byte:

* ``verify_statement_baseline.py``: every figure both statements give, for every paycheck,
  month and year the ledger attributes into and every as-of date it names;
* ``verify_render_surfaces.py``: every page's status and size, the statements' partials and
  the pay-periods section's lock chips among them;
* ``verify_balance_baseline.py``: every figure the balance seam answers, since plan task
  plan#2770 changed the loan loader that seam runs.

**One instrument, two apps.**  Each side runs HEAD's copy of each instrument, written over
its own in its bench worktree (a detached throwaway) before it runs, so a pair that parts is
the apps' difference and never the instruments': an instrument the leaf grew (the render
harness's statement routes) runs whole on base too.  Each run must import its own side's
``app`` (``_dump_runs.read_its_own``), so a pair cannot be one tree measured twice.

**Base's statements read the store and head's the records, and the controls show each reads
its own** (the verification standard's rule 4, before any agreement is believed).  The
statement baseline runs again on four clones, each side's deployed copy with one of two
plants, triggers off for the session so nothing else moves:

* **a cent on the store**, on its largest posting (``plant_a_cent_and_a_pair``'s, whose
  reversal pair nets $0.00 on its key): base's statements must move and head's must hold;
* **a cent on the records**, on every account's opening equity (:func:`plant_opening_cents`):
  head's statements must move and base's must hold.

A control graded the other way is a run that measured nothing.  So a pair that reads alike is
two sources agreeing, not one source read twice; and a head whose statements still read the
store, or read nothing, fails a control rather than passing the comparison.

**What a parting pair is.**  Ruling plan#2518 labels a line from its key's LIVE record, where
base labelled a counter or loan line from the chart row's snapshot name, and the statement
baseline keys lines by label: a renamed account's counter or loan line parts as one line gone
and one added, and a deleted category's lines likewise.  The run names every parting line
(``<instrument>.diff``); explaining each is its report's job, never this file's.

Usage, with production's locale pinned as ``scripts/test.sh`` pins it::

    LC_ALL=C.UTF-8 .venv/bin/python tests/manual/run_statements_on_dump.py \\
        --dump <pg_dump -Fc file> --out <a directory that does not exist yet> \\
        --base <the commit the card was claimed from> [--head <commit, default HEAD>]

It writes into ``--out``: ``run.txt`` (each deploy's verdict, each pair's and each control's),
and per instrument and side ``<instrument>_<side>.json`` with its ``_log.txt``, a pair that
parts as ``<instrument>.diff``, and per control ``control_<side>_<plant>.json`` with its
``_log.txt`` and, where it moved, ``.diff``.  Keep ``--out`` in the handoff folder: production
figures never enter the repository (ruling R-BAL132), and a commit or pull request cites the
path.

Exit status: 0 every pair byte-identical and every control as it must be; 1 a pair parts (each
difference one the report must name and explain); 2 nothing was measured: the bench, a deploy,
an instrument run or a control failed, or the run could not write or read a file, each named, or
it crashed (``exit_status``).
"""

import pathlib
import secrets
import shutil
import sys
from decimal import Decimal

# The repository root, so ``tools`` and ``tests`` are importable when this runs by path.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these must follow the path bootstrap
# above; there is no import order that resolves ``tools`` before it runs.
# pylint: disable=wrong-import-position
from tests.manual import exit_status

exit_status.arm(__name__)

import psycopg

from tests.manual import _dump_runs
from tests.manual.run_deploy_rewrites_on_dump import plant_a_cent_and_a_pair
from tools.rehearsal import bench, rows

#: The instruments, as paths inside a worktree, the statement baseline first:
#: its pair is the spec's proof and its output the controls' reference.
_MANUAL = pathlib.Path("tests") / "manual"
_STATEMENTS = _MANUAL / "verify_statement_baseline.py"
_INSTRUMENTS = (
    _STATEMENTS,
    _MANUAL / "verify_render_surfaces.py",
    _MANUAL / "verify_balance_baseline.py",
)
#: The bench's two states, the side whose instruments both run last.
_SIDES = ("base", "head")
#: What the records plant adds to each opening.
_CENT = Decimal("0.01")
#: The line an instrument prints on success opens with this (the statement
#: baseline's ``wrote <path>: <n> users, <n> statements``).
_WROTE = "wrote "


def plant_opening_cents(url):
    """Add a cent to every account's opening equity on the copy at *url*; how many.

    Every opening row, not only the one governing each account: the plant
    must reach a record the worked-out read books, and an account's governing
    row is among its rows.  Triggers are off for the session, as the kit's
    own plants have them (``rows._plant``), so the append-only guard lets the
    rows move and nothing else does.

    Raises:
        bench.BenchError: The copy holds no account opening.
    """
    with psycopg.connect(url) as connection:
        connection.execute("SET session_replication_role = replica")
        planted = connection.execute(
            "UPDATE budget.account_openings "
            "SET opening_equity = opening_equity + %s", [_CENT]).rowcount
    if not planted:
        raise bench.BenchError("the copy holds no account opening to plant on")
    return planted


#: Each control's plant, and of each side whether its statements must move.
_PLANTS = {
    "store": (plant_a_cent_and_a_pair, {"base": True, "head": False}),
    "records": (plant_opening_cents, {"base": False, "head": True}),
}


def _wrote(written):
    """The instrument's own summary line from *written*'s log, or a sentence saying it has none."""
    log = written.with_name(f"{written.stem}_log.txt")
    lines = [line for line in log.read_text(encoding="utf-8").splitlines()
             if line.startswith(_WROTE)]
    return lines[-1] if lines else "(no summary line)"


def _give_head_instruments(made, say):
    """Write head's copy of each instrument over base's, in base's bench worktree."""
    base, head = (made.made[side] for side in _SIDES)
    for instrument in _INSTRUMENTS:
        same = ((base.worktree / instrument).read_bytes()
                == (head.worktree / instrument).read_bytes())
        shutil.copyfile(head.worktree / instrument, base.worktree / instrument)
        say(f"base runs head's {instrument.name} "
            + ("(the two are byte-identical)" if same
               else "(the two differ: head's replaces base's)"))


def _instruments(made, pinned, out, say):
    """Run each instrument on each side's copy and compare each pair; the status.

    Returns:
        2 when a run failed or read another tree's ``app``, 1 when a pair
        parts, else 0.
    """
    status = 0
    for instrument in _INSTRUMENTS:
        written = {}
        for side in _SIDES:
            state = made.made[side]
            written[side] = out / f"{instrument.stem}_{side}.json"
            wrong = _dump_runs.run_pinned_on(
                state.worktree / instrument, state, state.url, pinned,
                written[side])
            if wrong is not None:
                say(f"!  {side}'s {instrument.name} {wrong}")
                return 2
            say(f"{side}'s {instrument.name}: {_wrote(written[side])}")
        parted = _dump_runs.parted(written["base"], written["head"], _SIDES)
        if parted:
            _dump_runs.write_lines(out / f"{instrument.stem}.diff", parted)
        say(f"== {instrument.name}, base's against head's: "
            + (f"{written['base'].stat().st_size} bytes each, byte-identical"
               if not parted else
               f"PARTS, {len(parted)} diff line(s) in {instrument.stem}.diff"))
        status = max(status, 1 if parted else 0)
    return status


def _control(made, side, plant_name, pinned, out):
    """Run *side*'s statement baseline on a clone of its copy planted by *plant_name*.

    Returns:
        ``(what the plant planted, the diff lines from the side's own
        unplanted output, or None when the run failed, and a sentence saying
        how)``.

    Raises:
        bench.BenchError: The plant found nothing to plant on.
        psycopg.Error: The clone could not be made or planted.
    """
    state = made.made[side]
    url = made.clone(state.database, f"rehearsal_{side}_{plant_name}")
    planted = _PLANTS[plant_name][0](url)
    written = out / f"control_{side}_{plant_name}.json"
    wrong = _dump_runs.run_pinned_on(
        state.worktree / _STATEMENTS, state, url, pinned, written)
    if wrong is not None:
        return planted, None, wrong
    parted = _dump_runs.parted(
        out / f"{_STATEMENTS.stem}_{side}.json", written,
        (side, f"{side}, planted"))
    if parted:
        _dump_runs.write_lines(written.with_suffix(".diff"), parted)
    return planted, parted, None


def _controls(made, pinned, out, say):
    """Run every control (:func:`_control`); whether each was as it must be.

    Raises:
        bench.BenchError: A plant found nothing to plant on.
        psycopg.Error: A clone could not be made or planted.
    """
    graded = True
    for plant_name, (_plant, moves) in _PLANTS.items():
        for side in _SIDES:
            planted, parted, wrong = _control(made, side, plant_name, pinned,
                                              out)
            if wrong is not None:
                say(f"!  {side}'s {_STATEMENTS.name} on its {plant_name} "
                    f"clone {wrong}")
                return False
            right = bool(parted) == moves[side]
            say(f"control 'a cent on the {plant_name}' ({planted}), on a clone "
                f"of {side}'s copy: its statements "
                + (f"MOVED, {len(parted)} diff line(s) in "
                   f"control_{side}_{plant_name}.diff" if parted else "HELD")
                + f", where they must {'move' if moves[side] else 'hold'}: "
                + ("as they must" if right else "NOT AS THEY MUST"))
            graded = graded and right
    return graded


def _run(dump, out, revisions, say):
    """Bring the bench up on *dump*, deploy, measure into *out*; the exit status.

    Its report lines go to *say* (:func:`tests.manual._dump_runs.main`).

    Raises:
        bench.BenchError: The bench could not be made, its teardown left
            something, an instrument could not be run, or a plant found
            nothing to plant on.
        psycopg.Error: A copy could not be cloned or planted.
    """
    with bench.Bench(dump, dict(zip(_SIDES, revisions))) as made:
        pinned = _dump_runs.Pinned(rows.pinned_instant(made),
                                   secrets.token_hex(32))
        say(f"dump {dump}; clock pinned at {pinned.moment.isoformat()}")
        for side in _SIDES:
            state = made.made[side]
            refused = rows.deploy(state, state.url, pinned.secret,
                                  pinned.moment)
            say(f"deploy {side} (commit {state.commit}, onto {state.database}): "
                + ("exit 0" if refused is None else f"REFUSED, {refused}"))
            if refused is not None:
                return 2
        _give_head_instruments(made, say)
        status = _instruments(made, pinned, out, say)
        if status == 2:
            return 2
        if not _controls(made, pinned, out, say):
            say("!  a control was not as it must be: this run measured "
                "nothing")
            return 2
        return status


def main(argv=None):
    """Parse the arguments and run; the exit status."""
    return _dump_runs.main(_run, "statements", __doc__.splitlines()[0], argv)


if __name__ == "__main__":
    sys.exit(main())
