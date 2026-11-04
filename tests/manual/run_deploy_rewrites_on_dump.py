"""What the leaf's deploy rewrites on a production dump, and the read against base's store.

Plan task plan#2618 (plan step plan#2513; review plan#2521, finding C-H1).  The run of plan
task plan#2528 (``run_worked_out_ledger_on_dump.py``) ran THIS tree's deploy step on its copy
and then compared the worked-out read with the store that step had just rebuilt.  The writer
and the read share the target rules, the records walk, the loan walk and ``filing_period``
under one pinned clock, so every line that step rebuilds agrees by construction, and what the
step REWROTE went unmeasured.  This run measures it, on the release kit's bench
(``tools/rehearsal``): one dump restored once and cloned per copy, each copy deployed by its own
commit's step (``rows.deploy``) under ONE instant, noon of the dump's civil day
(``rows.pinned_instant``).  The copies are the dump as it was (never deployed), base's (the
commit the card was claimed from), base's again, and head's.

1. **What the leaf's deploy rewrites**: base's store and head's, their journals diffed line by
   line by (owner, scenario, stable chart identity, paycheck, day, source kind, source)
   (:func:`read_journal`, :func:`diff`).  Every differing key is named with its lines on each
   side, then the differences are summed by (source kind, chart kind), each with its count and
   dollars.  Zero is the spec's claim ("this leaf moves no figure anyone sees").  Beside it,
   what each deploy appended to the dump as it was, and the kit's every-table comparison of the
   two deployed copies (``rows.compare``, its noise gate and its planted controls run first,
   ``rows.controls``), so a difference outside the journal is seen and graded too.
2. **The read against the store base's writer leaves**: head's harness
   (``verify_worked_out_ledger.py``) run on BASE's deployed copy, where the anchor corrections
   were written by the postings walk, not the read's; and on head's, re-measuring plan#2528's
   run with the excuse rules plan#2617 narrowed.
3. and 4. **The read's statements per owner, and where each store files its corrections**:
   head's ``measure_worked_out_read.py`` on the dump as it was and on each deployed copy.

**And every figure the balance seam answers**, since this leaf changed code that seam runs (the
cash walk, the loan walk and its loaders, the seam's assertion replay) as well as the writers:
each side's own ``verify_balance_baseline.py`` (every figure the seam answers, for every account
of every kind), run from that side's worktree on that side's deployed copy with the clock
pinned at the deploys' instant (``run_pinned.py``), the pair compared byte for byte
(:data:`_BASELINE`).  It is the project's exhaustive "did anything move" check over the seam.
The two statements are not run: at the leaf this run was built for they read the stored ledger
alone, through code that leaf changed in comments only, so with every table row-identical
(graded above) they could not part.  Since plan task plan#2771 they read the worked-out ledger,
and ``run_statements_on_dump.py`` compares them (plan task plan#2773).
Not compared: a route's render, the figures outside the seam and the statements (a loan's
payment history and escrow breakdown among them), and every go-forward writer door this leaf
changed (the removal act, the held reconcile, the moved-off re-check), which only the suite
grades; this run exercises the deploy step's three bulk hooks alone.

**The journal is read in SQL by this process** (:data:`_JOURNAL`), never by either side's code,
so neither writer's version decides what the diff sees.  A line's **stable chart identity** is
what its chart row stands for, read off the row's columns: its owner, kind, class and the one
record it links (account, category or loan), or its owner-bucket flag; a row that links
nothing and is no bucket (an orphan) is named by its id, which every copy shares since no
deploy creates one.  Never the row's id otherwise: a deploy creates chart rows as it goes, and
two deploys may number them apart.  A paycheck is its id and the day it starts, a source its
movement's id or none.

**A gate and two controls before any difference is believed**, the standard's rule 4:

* **the noise gate**: base's step run on two clones must leave copies neither the diff nor the
  kit's comparison can tell apart.  A GATE, not a control: on a dump no deploy writes to it holds
  by construction, and it guards only against a step that writes differently each time;
* **a planted cent and a planted pair**: one cent added to one posting, and a reversal pair (a
  posting's amount and its negative) added beside another, on a clone of head's copy (triggers
  off, so nothing else moves), must be found as exactly two keys: the one moved by $0.01, the
  other moved by nothing but holding the two lines more, which the diff's multiset rule sees;
* **a deploy that rewrites**: on a clone of the dump for each side, one stored entry of each
  deploy hook's (:data:`_REMOVED_KINDS`) is removed, which the diff against the dump must see
  as exactly those entries' keys; that side's step must then write them back, the clone netting
  key for key what the same step left on the whole dump.  Graded against the side's own
  deployed copy, never the dump, so a side whose step rewrites the dump still fires it and its
  rewrite is reported (exit 1), not read as a run that measured nothing.  Without it, "neither
  deploy appended anything" would read the same from a step whose hooks never ran.

Usage, with production's locale pinned as ``scripts/test.sh`` pins it::

    LC_ALL=C.UTF-8 .venv/bin/python tests/manual/run_deploy_rewrites_on_dump.py \\
        --dump <pg_dump -Fc file> --out <a directory that does not exist yet> \\
        --base <the commit the card was claimed from> [--head <commit, default HEAD>]

It writes into ``--out``: ``run.txt`` (each deploy's verdict, the controls, the summaries and
each measurement's exit status), ``verify_balance_baseline_<side>.json`` with its ``_log.txt``
and, where the pair parts, ``verify_balance_baseline.diff``, ``rewrites.txt`` (every key whose
lines part between base's store and head's), ``appended_base.txt`` and ``appended_head.txt``
(what each deploy appended to the dump as it was), ``tables.txt`` (every row the kit's
comparison finds parting), ``harness_on_<copy>.txt`` with its ``_log.txt``, and
``read_<copy>.txt`` with its ``_log.txt``.  Keep ``--out`` in the handoff folder: production
figures never enter the repository (ruling R-BAL132), and a commit or pull request cites the
path.

Exit status: 0 the gate held and the controls fired, base's and head's copies agree line for
line and row for row, both harness runs exited 0, the probes found the read's count steady, and
the baseline pair is byte-identical; 1 the journals, a table or the baseline pair part (each
difference a figure this leaf moves, a question for the developer), a harness run found
something UNEXPLAINED, or a probe found the read's count moved between two reads; 2 nothing was
measured: the bench, a deploy, the gate, a control, a harness run, a probe or a baseline failed,
or the run itself could not write or read a file, each named, or it crashed (``exit_status``).
"""

import pathlib
import secrets
import sys
from collections import Counter, defaultdict, namedtuple
from decimal import Decimal

# The repository root, so ``tools`` is importable when this runs by path.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these must follow the path bootstrap
# above; there is no import order that resolves ``tools`` before it runs.
# pylint: disable=wrong-import-position
from tests.manual import exit_status

exit_status.arm(__name__)

import psycopg

from tests.manual import _dump_runs
from tools.rehearsal import bench, rows

#: The harness and the probe, as paths inside head's worktree.
_HARNESS = pathlib.Path("tests") / "manual" / "verify_worked_out_ledger.py"
_PROBE = pathlib.Path("tests") / "manual" / "measure_worked_out_read.py"
#: Each side's balance baseline, as a path inside its worktree.
_BASELINE = pathlib.Path("tests") / "manual" / "verify_balance_baseline.py"
#: The copies the run reads, beside the bench's two states: base's step run
#: again (the noise control), the dump as it was, and head's copy with a cent
#: planted (the planted control).
_AGAIN = "rehearsal_base_again"
_UNDEPLOYED = "rehearsal_undeployed"
_PLANTED = "rehearsal_planted"
#: The rewrite control's copy of each side, by the side's name.
_REWRITTEN = "rehearsal_rewrite_{}"
#: The source kinds the rewrite control removes one stored entry of, one per
#: hook of the deploy step (``scripts/init_database.py``): the cash resync's,
#: the every-loan backfill's and the every-account anchor backfill's.
_REMOVED_KINDS = ("purchase", "loan_trueup", "account_trueup")
#: Of those, the kind a movement's sync writes: an entry of it that links no
#: movement is hard-delete residue, which no hook can write back.
_MOVEMENT_KIND = "purchase"
#: What the planted control adds to one posting.
_CENT = Decimal("0.01")
_ZERO = Decimal("0.00")

#: Every posting of the copy with its entry, its chart row's identity and its
#: paycheck's first day, by entry and posting id.  A chart row that links no
#: record and is no owner bucket is named by its id (the module docstring).
_JOURNAL = """
SELECT entry.user_id, entry.scenario_id,
       chart.user_id, kind.name, klass.name, chart.account_id,
       chart.category_id, chart.loan_account_id, chart.is_owner_bucket,
       CASE WHEN chart.account_id IS NULL AND chart.category_id IS NULL
                 AND chart.loan_account_id IS NULL
                 AND NOT chart.is_owner_bucket
            THEN chart.id END,
       entry.pay_period_id, period.start_date, entry.entry_date, source.name,
       entry.transaction_entry_id, entry.id, posting.amount
  FROM budget.account_postings AS posting
  JOIN budget.journal_entries AS entry ON entry.id = posting.journal_entry_id
  JOIN budget.ledger_accounts AS chart ON chart.id = posting.ledger_account_id
  JOIN ref.ledger_account_kinds AS kind ON kind.id = chart.kind_id
  JOIN ref.ledger_account_classes AS klass ON klass.id = chart.class_id
  JOIN budget.pay_periods AS period ON period.id = entry.pay_period_id
  JOIN ref.posting_sources AS source ON source.id = entry.source_kind_id
 ORDER BY entry.id, posting.id
"""

# A chart row's stable identity (the module docstring), and where a line sits:
# its owner and scenario, chart identity, paycheck (id and first day), day,
# source kind and source movement (``None`` for none).
Chart = namedtuple(
    "Chart", "owner kind ledger_class account category loan bucket row",
)
Where = namedtuple(
    "Where",
    "owner scenario chart paycheck starts day source_kind movement",
)
# One posting as a key holds it: its amount and its journal entry.
Line = namedtuple("Line", "amount entry_id")
# A key whose lines part between two journals, with each side's lines.
Parted = namedtuple("Parted", "where before after")
# What a measurement on a copy runs with: head's state (its worktree), the
# run's secret key, the deploys' pinned instant, the run's directory, and the
# line the kit's comparison reads a deploy's stamps by (``rows.read_line``).
Measuring = namedtuple("Measuring", "head secret moment out line")


def read_journal(url):
    """Read every posting of the copy at *url*, by where it sits.

    Returns:
        ``{Where: [Line, ...]}``, each key's lines by entry and posting id.
    """
    journal = defaultdict(list)
    with psycopg.connect(url) as connection:
        for row in connection.execute(_JOURNAL).fetchall():
            (owner, scenario, *chart, paycheck, starts, day, kind, movement,
             entry_id, amount) = row
            where = Where(owner, scenario, Chart(*chart), paycheck, starts, day,
                          kind, movement)
            journal[where].append(Line(amount, entry_id))
    return journal


def diff(before, after):
    """Return every key whose lines part between two journals, in key order.

    Lines part when their amounts do, as multisets: two lines that net equal
    but are not the same lines (a reversal pair one side appended) part too,
    so a rewrite that moves no net is still seen.
    """
    parted = []
    for where in sorted(before.keys() | after.keys(), key=_order):
        was, now = before.get(where, []), after.get(where, [])
        if Counter(line.amount for line in was) != Counter(
                line.amount for line in now):
            parted.append(Parted(where, was, now))
    return parted


def _order(where):
    """Return a sort key for a :data:`Where` (``None`` sorts first)."""
    return tuple((value is not None, value) for value in (
        where.owner, where.scenario, where.starts, where.day,
        where.source_kind, where.chart.kind, where.chart.ledger_class,
        where.chart.account, where.chart.category, where.chart.loan,
        where.chart.row, where.movement,
    ))


def _net(lines):
    """Return the net of *lines*."""
    return sum((line.amount for line in lines), _ZERO)


def nets_part(before, after):
    """Return every key whose NET parts between two journals, a missing key netting $0.00.

    The rewrite control's grain (:func:`_rewrite_fired`): a step that brings
    a source to target writes whatever lines get it there, so two copies a
    step left at one target net equal key for key, though their lines may
    not be the same lines.
    """
    return sorted(
        (where for where in before.keys() | after.keys()
         if _net(before.get(where, [])) != _net(after.get(where, []))),
        key=_order,
    )


def chart_text(chart):
    """Return a chart identity as ``kind/class/record``, its owner when it is another's."""
    if chart.account is not None:
        record = f"account {chart.account}"
    elif chart.category is not None:
        record = f"category {chart.category}"
    elif chart.loan is not None:
        record = f"loan {chart.loan}"
    elif chart.bucket:
        record = "owner bucket"
    else:
        record = f"row #{chart.row}"
    return f"{chart.kind}/{chart.ledger_class}/{record}"


def parted_text(parted, before, after):
    """Return one parted key and its lines on each side as a report line."""
    where = parted.where
    other = "" if where.chart.owner == where.owner else (
        f" (owner {where.chart.owner}'s row)")
    lines = {
        label: ", ".join(
            f"{line.amount} (entry {line.entry_id})" for line in side) or "none"
        for label, side in ((before, parted.before), (after, parted.after))
    }
    moved = _net(parted.after) - _net(parted.before)
    return (
        f"owner {where.owner} scenario {where.scenario} "
        f"{chart_text(where.chart)}{other} paycheck {where.paycheck} (from "
        f"{where.starts}) day {where.day} {where.source_kind} movement "
        f"{where.movement}: net {_net(parted.before)} -> {_net(parted.after)} "
        f"(moved {moved:+}); {before}: {lines[before]}; {after}: {lines[after]}"
    )


def summary(parted):
    """Return the parted keys summed by (source kind, chart kind), as report lines.

    Each kind's count of keys, how many moved their net and by how much
    (signed, and the sum of each move's size), and the lines either side
    holds alone with their dollars.
    """
    kinds = defaultdict(list)
    for each in parted:
        kinds[(each.where.source_kind, each.where.chart.kind)].append(each)
    lines = [f"{len(parted)} key(s) part; by (source kind, chart kind):"]
    for (source_kind, chart_kind), group in sorted(kinds.items()):
        moves = [_net(each.after) - _net(each.before) for each in group]
        moving = [move for move in moves if move != _ZERO]
        alone = Counter(), Counter()
        for each in group:
            was = Counter(line.amount for line in each.before)
            now = Counter(line.amount for line in each.after)
            alone[0].update(was - now)
            alone[1].update(now - was)
        lines.append(
            f"   {source_kind} on {chart_kind}: {len(group)} key(s), "
            f"{len(moving)} moved their net by {sum(moving, _ZERO):+} "
            f"({sum((abs(move) for move in moving), _ZERO)} in all); lines only "
            f"before: {sum(alone[0].values())} "
            f"({_dollars(alone[0])}), only after: {sum(alone[1].values())} "
            f"({_dollars(alone[1])})"
        )
    return lines


def _dollars(amounts):
    """Return a multiset of amounts as its debits and credits."""
    debited = sum((amount * count for amount, count in amounts.items()
                   if amount > 0), _ZERO)
    credited = sum((amount * count for amount, count in amounts.items()
                    if amount < 0), _ZERO)
    return f"{debited} debited, {credited} credited"


def plant_a_cent_and_a_pair(url):
    """Add :data:`_CENT` to the copy's largest posting and a reversal pair beside its first.

    The pair is the first posting of another entry, copied twice into its
    own entry, once at its amount and once at the negative: two lines more on
    that posting's key, netting $0.00.  Triggers are off for the session, as
    the kit's own plants have them (``rows._plant``), so the posting guard
    lets the rows move and nothing else does.  The largest takes the cent, so
    the amount stays a positive one.

    Returns:
        ``(the cent's entry id, the pair's entry id, its amount)``.

    Raises:
        bench.BenchError: The copy holds no two postings of two entries.
    """
    with psycopg.connect(url) as connection:
        connection.execute("SET session_replication_role = replica")
        cent = connection.execute(
            "SELECT id, journal_entry_id FROM budget.account_postings "
            "ORDER BY amount DESC, id LIMIT 1").fetchone()
        pair = None if cent is None else connection.execute(
            "SELECT id, journal_entry_id, amount FROM budget.account_postings "
            "WHERE journal_entry_id <> %s ORDER BY id LIMIT 1",
            [cent[1]]).fetchone()
        if pair is None:
            raise bench.BenchError(
                "the copy holds no two postings of two entries to plant on")
        connection.execute(
            "UPDATE budget.account_postings SET amount = amount + %s "
            "WHERE id = %s", [_CENT, cent[0]])
        connection.execute(
            "INSERT INTO budget.account_postings (journal_entry_id, "
            "ledger_account_id, amount, posting_kind_id) "
            "SELECT journal_entry_id, ledger_account_id, sign * amount, "
            "posting_kind_id FROM budget.account_postings, "
            "(VALUES (1), (-1)) AS signs (sign) WHERE id = %s", [pair[0]])
    return cent[1], pair[1], pair[2]


def planted_fired(parted, planted):
    """Whether the planted control fired: exactly the cent's key and the pair's.

    The cent's key moved by :data:`_CENT` with its entry among its lines; the
    pair's moved by nothing, holding its amount and the negative more.

    Args:
        parted: :func:`diff` of head's journal against the planted copy's.
        planted: :func:`plant_a_cent_and_a_pair`'s answer.
    """
    cent_entry, pair_entry, amount = planted
    moves = {
        _net(each.after) - _net(each.before): each for each in parted
    }
    if len(parted) != 2 or set(moves) != {_CENT, _ZERO}:
        return False
    more = (Counter(line.amount for line in moves[_ZERO].after)
            - Counter(line.amount for line in moves[_ZERO].before))
    return (cent_entry in {line.entry_id for line in moves[_CENT].after}
            and pair_entry in {line.entry_id for line in moves[_ZERO].after}
            and more == Counter((amount, -amount)))


def removable_entries(journal):
    """Return, per kind of :data:`_REMOVED_KINDS`, an entry the rewrite control removes.

    The latest of its kind whose every key holds that entry's lines alone,
    so its removal parts exactly its own keys and its rewrite restores them
    whatever entry id the step gives it; a reversal pair, whose two entries
    share their keys, is never one.  A movement kind's entry must link its
    movement: one that links nothing is residue, which no hook writes back.

    Raises:
        bench.BenchError: A kind holds no such entry, so its hook cannot be
            shown to rewrite.
    """
    keys_of, where_of = defaultdict(set), {}
    for where, held in journal.items():
        for line in held:
            keys_of[line.entry_id].add(where)
            where_of[line.entry_id] = where
    chosen = {}
    for entry_id in sorted(keys_of):
        kind = where_of[entry_id].source_kind
        alone = all(line.entry_id == entry_id for where in keys_of[entry_id]
                    for line in journal[where])
        linked = (kind != _MOVEMENT_KIND
                  or where_of[entry_id].movement is not None)
        if alone and linked and kind in _REMOVED_KINDS:
            chosen[kind] = entry_id
    missing = [kind for kind in _REMOVED_KINDS if kind not in chosen]
    if missing:
        raise bench.BenchError(
            f"the dump holds no {', '.join(missing)} entry standing alone in "
            "its keys, so the rewrite control cannot run")
    return chosen


def remove_entries(url, entry_ids):
    """Remove *entry_ids* and their postings from the copy at *url*, triggers off."""
    with psycopg.connect(url) as connection:
        connection.execute("SET session_replication_role = replica")
        connection.execute(
            "DELETE FROM budget.account_postings "
            "WHERE journal_entry_id = ANY(%s)", [entry_ids])
        connection.execute(
            "DELETE FROM budget.journal_entries WHERE id = ANY(%s)",
            [entry_ids])


def _removed(undeployed, url):
    """Remove :func:`removable_entries` from the dump's clone at *url*.

    Args:
        undeployed: The dump's journal.
        url: A clone of the dump.

    Returns:
        ``(the entries removed by kind, whether the clone now parts from the
        dump in exactly the keys those entries held)``.
    """
    removed = removable_entries(undeployed)
    remove_entries(url, list(removed.values()))
    held = {where for where, lines in undeployed.items()
            if any(line.entry_id in removed.values() for line in lines)}
    parted = {each.where for each in diff(undeployed, read_journal(url))}
    return removed, parted == held


def _rewrite_fired(made, journals, state, measuring, say):
    """Run the rewrite control for one side; whether it fired.

    Before the step, the clone must part from the dump in exactly the removed
    entries' keys; after it, the clone must net key for key what the same
    step left on the whole dump (:func:`nets_part`), so a side whose step
    rewrites the dump fires it too.
    """
    database = _REWRITTEN.format(state.name)
    clone = bench.State(state.name, state.commit, state.worktree, database,
                        made.clone(bench.PRISTINE, database), state.python)
    removed, exact = _removed(journals["undeployed"], clone.url)
    refused = rows.deploy(clone, clone.url, measuring.secret, measuring.moment)
    rewritten = read_journal(clone.url)
    left = nets_part(journals[state.name], rewritten)
    fired = refused is None and exact and not left
    say(f"control 'a deploy that rewrites' ({state.name}'s step, entries "
        f"{removed} removed from a clone of the dump, "
        f"{'exactly their keys' if exact else 'NOT exactly their keys'} "
        "parting from the dump): "
        + ("exit 0" if refused is None else f"REFUSED, {refused}")
        + f", then {len(left)} key(s) net apart from {state.name}'s deployed "
        f"copy: {'FIRED' if fired else 'DID NOT FIRE'}")
    for where in left:
        say("   " + parted_text(
            Parted(where, journals[state.name].get(where, []),
                   rewritten.get(where, [])), state.name, "rewritten"))
    return fired


def _measured(script, url, name, measuring):
    """Run *script* from head's worktree on the copy at *url*; its exit status.

    In the environment the kit gives a side (``rows.side_environment``),
    under THIS interpreter, since the suite's helpers need ``pytest`` and a
    side's production pins do not hold it, with the clock pinned at the
    deploys' instant; its report goes to ``<out>/<name>.txt`` and what the
    app logged to ``<out>/<name>_log.txt``.

    Args:
        script: The script, as a path inside head's worktree.
        url: The copy it runs on.
        name: What its two files are named by.
        measuring: The run's :data:`Measuring`.
    """
    head, out = measuring.head, measuring.out
    environment = _dump_runs.side_environment(head, url, measuring.secret)
    with (out / f"{name}_log.txt").open("w", encoding="utf-8") as log:
        done = bench.run(
            [sys.executable, str(head.worktree / script),
             "--report", str(out / f"{name}.txt"),
             "--at", measuring.moment.isoformat()],
            environment, stdout=log, cwd=head.worktree,
        )
        log.write(done.stderr)
    return done.returncode


def _baseline(sides, measuring, say):
    """Run each side's own balance baseline on its copy and compare the pair; the status.

    Each runs from its side's worktree at the deploys' instant
    (:func:`tests.manual._dump_runs.run_pinned_on`), into
    ``<out>/verify_balance_baseline_<side>.json``; a pair that parts is
    written as a unified diff to ``<out>/verify_balance_baseline.diff``.

    Returns:
        2 when a side's run exited non-zero or read another tree's ``app``,
        1 when the pair parts, else 0.
    """
    out, stem = measuring.out, _BASELINE.stem
    written = {}
    for state in sides:
        written[state.name] = out / f"{stem}_{state.name}.json"
        wrong = _dump_runs.run_pinned_on(
            state.worktree / _BASELINE, state, state.url,
            _dump_runs.Pinned(measuring.moment, measuring.secret),
            written[state.name],
        )
        if wrong is not None:
            say(f"!  {state.name}'s {_BASELINE.name} {wrong}")
            return 2
    parted = _dump_runs.parted(*(written[state.name] for state in sides),
                               [state.name for state in sides])
    if parted:
        _dump_runs.write_lines(out / f"{stem}.diff", parted)
    say(f"{_BASELINE.name}, {sides[0].name}'s against {sides[1].name}'s: "
        + (f"{written[sides[0].name].stat().st_size} bytes each, "
           "byte-identical" if not parted
           else f"PARTS, {len(parted)} diff line(s) in {stem}.diff"))
    return 1 if parted else 0


def _measure_copies(urls, measuring, say):
    """Run head's harness on base's and head's copies, and its probe on every copy.

    Returns:
        The highest exit status among them (``exit_status.measurement_status``).
    """
    status = 0
    for label in ("base", "head"):
        measured = _measured(_HARNESS, urls[label], f"harness_on_{label}",
                             measuring)
        say(f"head's harness on {label}'s copy: exit {measured}")
        status = max(status, exit_status.measurement_status(measured))
    for label, url in urls.items():
        measured = _measured(_PROBE, url, f"read_{label}", measuring)
        say(f"head's probe on the {label} copy: exit {measured}")
        status = max(status, exit_status.measurement_status(measured))
    return status


def _deploy_all(copies, measuring, say):
    """Run each copy's own deploy step on it; whether every one exited 0."""
    deployed = True
    for label, state in copies:
        refused = rows.deploy(state, state.url, measuring.secret,
                              measuring.moment)
        say(f"deploy {label} (commit {state.commit}, onto {state.database}): "
            + ("exit 0" if refused is None else f"REFUSED, {refused}"))
        deployed = deployed and refused is None
    return deployed


def _controls(made, journals, copies, measuring, say):
    """Run the noise gate, the planted cent and pair, the rewrites and the kit's controls.

    Args:
        made: The bench.
        journals: Each copy's journal, by label.
        copies: ``(base, base again)``: the bench's base state and its twin.
        measuring: The run's :data:`Measuring`.
        say: Where the report's lines go.

    Returns:
        Whether the gate held and every control fired.
    """
    base = copies[0]
    held = _noise_held(journals, copies, measuring.line, say)
    fired = _planted_fired(made, journals, measuring.head, say)
    rewritten = [_rewrite_fired(made, journals, state, measuring, say)
                 for state in (base, measuring.head)]
    wrong = rows.controls(made, base, measuring.line)
    say("the kit's controls on its every-table comparison: "
        + ("each FIRED" if not wrong else "NOT ALL FIRED"),
        *(f"   {entry}" for entry in wrong))
    return held and fired and all(rewritten) and not wrong


def _noise_held(journals, copies, line, say):
    """Whether base's two copies read alike, by the diff and the kit's comparison."""
    base, again = copies
    noise = diff(journals["base"], journals["base, again"])
    rows_noise = rows.compare(base.url, again.url, line)
    held = not noise and not rows_noise
    say(f"gate 'noise' (base's step on two clones): {len(noise)} key(s) and "
        f"{len(rows_noise)} row(s) part: {'HELD' if held else 'BROKEN'}")
    for each in noise:
        say("   " + parted_text(each, "base", "base, again"))
    say(*(f"   {entry}" for entry in rows.summary(rows_noise, "base", "again")))
    return held


def _planted_fired(made, journals, head, say):
    """Plant the cent and the pair on a clone of head's copy; whether the diff found them."""
    url = made.clone(head.database, _PLANTED)
    planted = plant_a_cent_and_a_pair(url)
    parted = diff(journals["head"], read_journal(url))
    fired = planted_fired(parted, planted)
    say(f"control 'a planted cent and pair' (entries {planted[:2]}, on a "
        f"clone of head's copy): {len(parted)} key(s) part: "
        f"{'FIRED' if fired else 'DID NOT FIRE'}")
    for each in parted:
        say("   " + parted_text(each, "head", "planted"))
    return fired


def _run(dump, out, revisions, say):
    """Bring the bench up on *dump*, deploy, measure into *out*; the exit status.

    Its report lines go to *say* (:func:`tests.manual._dump_runs.main`).

    Raises:
        bench.BenchError: The bench could not be made, its teardown left
            something, or a side probe could not be run.
        psycopg.Error: A copy could not be read or cloned.
    """
    with bench.Bench(dump, dict(zip(("base", "head"), revisions))) as made:
        base, head = made.made["base"], made.made["head"]
        again = bench.State(base.name, base.commit, base.worktree, _AGAIN,
                            made.clone(bench.PRISTINE, _AGAIN), base.python)
        urls = {"undeployed": made.clone(bench.PRISTINE, _UNDEPLOYED)}
        measuring = Measuring(head, secrets.token_hex(32),
                              rows.pinned_instant(made), out,
                              rows.read_line(base.url))
        say(f"dump {dump}; clock pinned at {measuring.moment.isoformat()}")
        if not _deploy_all((("base", base), ("base, again", again),
                            ("head", head)), measuring, say):
            return 2
        urls.update(base=base.url, head=head.url)
        journals = {label: read_journal(url) for label, url in (
            *urls.items(), ("base, again", again.url))}
        if not _controls(made, journals, (base, again), measuring, say):
            say("!  the gate broke or a control did not fire: this run "
                "measured nothing")
            return 2
        status = _report_journals(out, journals, say)
        moved = rows.compare(base.url, head.url, measuring.line)
        _dump_runs.write_lines(out / "tables.txt", rows.report(moved, "base", "head"))
        say(f"every table, base's copy against head's ({len(moved)} row(s) "
            "part, each in tables.txt):", *rows.summary(moved, "base", "head"))
        status = max(status, 1 if moved else 0,
                     _measure_copies(urls, measuring, say))
        return max(status, _baseline((base, head), measuring, say))


def _report_journals(out, journals, say):
    """Diff base's journal against head's, and each deploy's appends; the status.

    Returns:
        1 when base's and head's journals part, else 0.
    """
    rewrites = diff(journals["base"], journals["head"])
    _dump_runs.write_lines(out / "rewrites.txt",
           [parted_text(each, "base", "head") for each in rewrites])
    say("== what head's deploy rewrites, against base's (each key in "
        "rewrites.txt):", *summary(rewrites))
    for label in ("base", "head"):
        appended = diff(journals["undeployed"], journals[label])
        _dump_runs.write_lines(out / f"appended_{label}.txt",
               [parted_text(each, "undeployed", label) for each in appended])
        say(f"-- what {label}'s deploy appended to the dump as it was (each "
            f"key in appended_{label}.txt):", *summary(appended))
    return 1 if rewrites else 0


def main(argv=None):
    """Parse the arguments and run; the exit status."""
    return _dump_runs.main(_run, "deploy rewrites", __doc__.splitlines()[0],
                           argv)


if __name__ == "__main__":
    sys.exit(main())
