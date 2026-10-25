"""The ``migrate`` command's ``check``: its report and exit status, on the small corpus.

``main`` takes the registries' reader and the repository root, so the command runs on
:mod:`_migrate_fake`'s plan and the ``code`` fixture's throwaway repository; nothing reads
the live registries, and nothing writes anywhere.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace

import pytest

from tools.ci.arcs import ARC_DOCS, REPO
from tools.ci.gitcmd import GitError
from tools.ci.scratch import run
from tools.plan_gate.registries import (
    LEDGER,
    RULINGS,
    STEPS,
    ledger_rows,
    outcome_scopes,
    ruling_rows,
    step_rows,
)
from tools.quill import _migrate_source, migrate, quill
from tools.quill.setup_tracker import DEPLOY_TOGETHER_LABEL, FILING, MOVES_MONEY_LABEL
from tools.quill._fake import FakeTracker
from tools.quill._migrate_bodies import masked
from tools.quill._migrate_fake import BALANCE, complete_input
from tools.quill._migrate_plan_fake import corpus_plan, corpus_texts, filed_tracker, with_change
from tools.quill._migrate_source import read_registries
from tools.quill._tracker import TrackerError
from tools.quill.test__migrate_numbers import recording


def _run(corpus, code, path, capsys, *extra, connect=None):
    """``migrate check --input path`` (or another command, by ``extra``: ``("verify",
    "--target", ...)``) on the corpus, reading the tracker ``connect`` reaches; its exit
    status and output."""
    command = list(extra) if extra else ["check"]
    status = migrate.main([command[0], "--input", str(path), *command[1:]],
                          read=lambda _root, _commit: corpus, root=code,
                          connect=connect or _unreachable)
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def _unreachable(place):
    """A connect no test without ``--target`` may reach."""
    raise AssertionError(f"connected to {place}")


def test_a_clean_check_prints_what_moves_and_exits_0(corpus, code, tmp_path, capsys):
    """Every count by arc and kind, the census, the bare #N count, and the write estimate:
    the two rulings' 4, then the steps' 18, the findings' 9, the questions' 12 and the one
    milestone's create, as the plan counts them on a tracker holding nothing."""
    path = tmp_path / "input.json"
    path.write_text(json.dumps(complete_input(corpus)))
    status, out, _ = _run(corpus, code, path, capsys)
    lines = out.splitlines()
    assert status == 0
    assert lines[0] == f"check at {corpus.commit}: 13 cards, 13 to file"
    assert lines[1] == "  balance: steps 4, findings 2, questions 2, rulings 1"
    assert "  salary: steps 1, findings 1, questions 1, rulings 1" in lines
    assert ("census balance: 16 lines -- on a card 11; a heading 2; a note, moving nowhere 2; "
            "a shipped step's pointer, moving nowhere 1") in lines
    assert "bare #N, which GitHub links to the tracker's card N: none" in lines
    assert ("bare #N: not graded by GitHub's renderer (`check --target` grades them once "
            "nothing above is refused)") in lines
    assert lines[-2:] == [
        "writes if every card is new, not counting the links of a card filed already "
        "(`check --target` counts them): 4 filing the rulings, then 40 filing the rest and the "
        "1 milestones, before the board's moves",
        "0 refusal(s)"]


def test_the_write_estimate_counts_each_write_of_a_new_card(corpus):
    """A ruling: create, then its close with its mark (one PATCH).  A-2b: create, parent
    link, one blocker link, board place, unmark.  A question: create, comment, board
    place, unmark.  Counted by the plan on a tracker holding nothing, the one producer of
    what is left to write (``migrate.writes``, a second, is gone)."""
    found = corpus_plan(corpus, FakeTracker())
    made = Counter(write.key for write in (*found.history, *found.freeze))
    assert [made[key] for key in (
        "balance:R-A", "balance:A-2b", "balance:Q-2", "balance:A-2", "balance:F-1")] == [
        2, 5, 4, 2, 3]


def test_a_refusal_is_printed_and_exits_1(corpus, code, tmp_path, capsys):
    """One missing name: one REFUSED line, exit 1."""
    data = complete_input(corpus)
    del data["names"]["balance:A-3"]
    path = tmp_path / "input.json"
    path.write_text(json.dumps(data))
    status, out, _ = _run(corpus, code, path, capsys)
    assert status == 1
    assert [line for line in out.splitlines() if line.startswith("REFUSED")] == [
        "REFUSED: names: balance:A-3: no name"]
    assert out.splitlines()[-1] == "1 refusal(s)"


def test_an_unreadable_input_or_git_failure_exits_2_before_any_check(corpus, code, tmp_path,
                                                                     capsys):
    """No file, a file that is not a JSON object, a root that is no repository: each fails
    with exit 2 and checks nothing."""
    listed = tmp_path / "list.json"
    listed.write_text("[]")
    assert _run(corpus, code, tmp_path / "missing.json", capsys)[0] == 2
    status, out, err = _run(corpus, code, listed, capsys)
    assert (status, out) == (2, "") and "holds list, not an object" in err
    path = tmp_path / "input.json"
    path.write_text(json.dumps(complete_input(corpus)))
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    status, out, err = _run(corpus, nowhere, path, capsys)
    assert (status, out) == (2, "") and err.startswith("failed: git rev-parse HEAD")


def test_planning_files_that_differ_from_head_are_checked_not_refused(
        corpus, code, tmp_path, capsys):
    """The registries are read as HEAD holds them (R-BAL257), so an uncommitted planning
    file no longer stops the check, as it did before that ruling (B1's review, M1)."""
    path = tmp_path / "input.json"
    path.write_text(json.dumps(complete_input(corpus)))
    (code / "docs" / "plans").mkdir(parents=True)
    (code / "docs" / "plans" / "steps.md").write_text("| an uncommitted edit |\n")
    assert _run(corpus, code, path, capsys)[0] == 0


def test_an_input_file_inside_the_repository_is_refused(corpus, code, capsys):
    """Its text may quote a real figure, which the public repository may not hold
    (R-BAL132; M6 of B1's review): exit 2, before it is even read."""
    path = code / "input.json"
    path.write_text(json.dumps(complete_input(corpus)))
    status, out, err = _run(corpus, code, path, capsys)
    assert (status, out) == (2, "") and "is inside the repository" in err


def test_a_card_filed_already_is_counted_apart_and_takes_only_its_links(
        corpus, code, tmp_path, capsys, monkeypatch):
    """S-1 mapped (as X-cx is card #1): 13 cards, 12 to file, and its writes are its links
    alone (none here), so the rest's estimate drops by its 3."""
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 1)
    path = tmp_path / "input.json"
    path.write_text(json.dumps(complete_input(corpus)))
    status, out, _ = _run(corpus, code, path, capsys)
    lines = out.splitlines()
    assert status == 0
    assert lines[0] == (f"check at {corpus.commit}: 13 cards, 12 to file (salary:S-1 is "
                        "plan#1 already)")
    assert lines[-2].startswith("writes if every card is new, not counting the links of a card "
                                "filed already (`check --target` counts them): 4 filing the "
                                "rulings, then 37 ")


def _without_line(text, opening):
    """``text`` without its first line opening ``opening``."""
    lines = text.splitlines(keepends=True)
    index = next(index for index, line in enumerate(lines) if line.startswith(opening))
    return "".join(lines[:index] + lines[index + 1:])


def _commit_planning_files(code):
    """Commit the live registries and arc documents into ``code`` at their paths, each
    registry without its first row (``steps.md`` without its first outcome too) and each arc
    document with a line of its own added, so the commit's rows and text are no checkout's:
    the texts by path, and the commit."""
    texts = {path: f"{path.read_text()}\nCommitted in {path.name}.\n"
             for path in ARC_DOCS.values()}
    texts.update({path: _without_line(path.read_text(), f"| {rows[0].arc} | {rows[0].ident} |")
                  for path, rows in ((STEPS, step_rows()), (LEDGER, ledger_rows()),
                                     (RULINGS, ruling_rows()))})
    texts[STEPS] = _without_line(texts[STEPS], f"| {outcome_scopes()[0][0]} |")
    for path, text in texts.items():
        target = code / path.relative_to(REPO)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    run(code, "add", "-A")
    run(code, "commit", "-q", "-m", "planning files")
    return texts, run(code, "rev-parse", "HEAD")


@pytest.mark.parametrize("change", ["modified", "staged", "readme", "untracked"])
def test_the_registries_are_read_as_the_commit_holds_them_whatever_the_checkout_holds(
        code, change):
    """R-BAL257 and R-BAL259: every registry and arc document is read from git at the
    commit the cards' As filed blocks name, so a planning file changed since -- unstaged,
    staged, the one arc document outside docs/plans, or a new file beside the registries
    -- changes nothing read (the four cases B1 refused, review of B1 M1 and MEDIUM-2)."""
    texts, commit = _commit_planning_files(code)
    edited = ARC_DOCS["balance"] if change == "readme" else STEPS
    (code / edited.relative_to(REPO)).write_text("edited\n")
    if change == "staged":
        run(code, "add", str(code / edited.relative_to(REPO)))
    if change == "untracked":
        (code / STEPS.relative_to(REPO)).write_text(texts[STEPS])
        (code / "docs" / "plans" / "archive").mkdir()
        (code / "docs" / "plans" / "archive" / "new.md").write_text("HISTORICAL\n")
    registries = read_registries(code, commit)
    assert registries.commit == commit
    assert all(texts[path] != path.read_text() for path in texts)
    assert registries.steps == tuple(step_rows()[1:]) and registries.steps
    assert registries.findings == tuple(ledger_rows()[1:]) and registries.findings
    assert registries.rulings == tuple(ruling_rows()[1:]) and registries.rulings
    assert registries.outcomes == tuple((name, tuple(scope)) for name, scope in
                                        outcome_scopes()[1:]) and registries.outcomes
    assert {arc: registries.documents[arc] for arc in ARC_DOCS} == {
        arc: texts[path] for arc, path in ARC_DOCS.items()}


def test_the_registries_are_read_at_the_commit_named_not_at_head(code):
    """Read at an earlier commit, the registries are that commit's, though HEAD has moved on
    (review of B2a, L4: a read of ``HEAD:`` passed every other test)."""
    texts, commit = _commit_planning_files(code)
    (code / STEPS.relative_to(REPO)).write_text(STEPS.read_text())
    run(code, "commit", "-q", "-am", "the next commit")
    assert run(code, "rev-parse", "HEAD") != commit
    assert read_registries(code, commit).steps == tuple(step_rows(text=texts[STEPS]))


def test_a_planning_file_the_commit_lacks_fails_with_exit_2(code, tmp_path, capsys):
    """A commit that holds no ``steps.md`` (here, ``code``'s empty base): git cannot read
    it, so nothing is checked."""
    path = tmp_path / "input.json"
    path.write_text("{}")
    status = migrate.main(["check", "--input", str(path)], root=code)
    captured = capsys.readouterr()
    assert (status, captured.out) == (2, "") and "docs/plans/steps.md" in captured.err


def test_planning_files_that_are_not_utf8_exit_2(corpus, code, tmp_path, capsys):
    """A planning file that is not UTF-8 fails like any unreadable one (review of B1, L3)."""
    path = tmp_path / "input.json"
    path.write_text(json.dumps(complete_input(corpus)))

    def unreadable(_root, _commit):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    status = migrate.main(["check", "--input", str(path)], read=unreadable, root=code)
    assert (status, capsys.readouterr().out) == (2, "")


def test_quill_file_step_takes_both_release_labels():
    """``--label`` offers the two labels a release reads, from their one home."""
    args = quill.parser().parse_args(["file", "step", "--arc", "balance", "--title", "t",
                                      "--body-file", "b", "--label", "moves-money",
                                      "--label", "deploy-together"])
    assert args.label == [MOVES_MONEY_LABEL, DEPLOY_TOGETHER_LABEL]


# -- check --target, and verify --------------------------------------------------

def _input(corpus, tmp_path):
    """The corpus's input file, written outside the repository."""
    path = tmp_path / "input.json"
    path.write_text(json.dumps(complete_input(corpus)))
    return path


def _a2b_filed_already(corpus, monkeypatch):
    """The corpus wholly filed, #1 a deleted number, and A-2b mapped onto its card, which
    holds neither its parent link nor its blocker link and is off the board, as a container
    is."""
    tracker, numbers = filed_tracker(corpus)
    tracker.gone.add(1)
    monkeypatch.setitem(_migrate_source.MAPPED, "balance:A-2b", numbers["balance:A-2b"])
    with_change(tracker, numbers["balance:A-2b"], parent=None, blocked_by=())
    tracker.board.items.remove(numbers["balance:A-2b"])
    return tracker


def test_a_card_filed_already_s_links_are_counted_by_target_alone(corpus, code, tmp_path,
                                                                  capsys, monkeypatch):
    """A-2b filed already, without its two links: without ``--target`` the count leaves
    them out, as it says (40 less A-2b's five writes as a new card); with it, the plan
    on the tracker as read counts them, and nothing else is left."""
    tracker = _a2b_filed_already(corpus, monkeypatch)
    path = _input(corpus, tmp_path)
    status, out, _ = _run(corpus, code, path, capsys)
    assert status == 0 and out.splitlines()[-2] == (
        "writes if every card is new, not counting the links of a card filed already (`check "
        "--target` counts them): 4 filing the rulings, then 35 filing the rest and the 1 "
        "milestones, before the board's moves")
    status, out, _ = _run(corpus, code, path, capsys, "check", "--target", "rehearsal",
                          connect=lambda place: tracker)
    assert status == 0 and out.splitlines()[-2] == (
        "writes left on the tracker: 0 filing the rulings, then 2 filing the rest and the 0 "
        "milestones, before the board's moves")


def test_check_target_counts_what_is_left_and_refuses_what_the_plan_refuses(
        corpus, code, tmp_path, capsys):
    """On a tracker part filed, A-3 still marked: the one write left.  On a tracker holding
    a card no item claims: the plan's refusal, the writes not counted, exit 1."""
    tracker, _ = filed_tracker(corpus, marked=["balance:A-3"])
    tracker.gone.add(1)
    path = _input(corpus, tmp_path)
    status, out, _ = _run(corpus, code, path, capsys, "check", "--target", "plan",
                          connect=lambda place: tracker)
    assert status == 0 and out.splitlines()[-2] == (
        "writes left on the tracker: 0 filing the rulings, then 1 filing the rest and the 0 "
        "milestones, before the board's moves")
    tracker.add(max(tracker.cards_by_number) + 1, title="A stray card", on_board=False)
    status, out, _ = _run(corpus, code, path, capsys, "check", "--target", "plan",
                          connect=lambda place: tracker)
    lines = out.splitlines()
    assert status == 1 and "writes: not counted while anything is refused" in lines
    refused = [line for line in lines if line.startswith("REFUSED")]
    assert len(refused) == 1 and refused[0].startswith(
        f"REFUSED: plan#{max(tracker.cards_by_number)} ('A stray card') is no item's card")


def test_check_without_target_refuses_what_the_plan_refuses_on_any_tracker(
        corpus, code, tmp_path, capsys):
    """A-2a waiting on A-2b, which waits on A-2a: no source refusal, but the plan refuses
    the cycle whatever the tracker holds, so ``check`` refuses it before any run, naming
    the cycle and A-3, which waits on it, but not their container; the writes not
    counted."""
    cycle = replace(corpus, steps=tuple(
        replace(row, blocked="after #2 / balance:A-2b") if row.ident == "A-2a" else row
        for row in corpus.steps))
    status, out, _ = _run(cycle, code, _input(cycle, tmp_path), capsys)
    lines = out.splitlines()
    assert status == 1 and "writes: not counted while anything is refused" in lines
    assert [line for line in lines if line.startswith("REFUSED")] == [
        "REFUSED: steps wait in a cycle, so these can be filed in no order (the cycle and "
        "every step waiting on it): ['balance:A-2a', 'balance:A-2b', 'balance:A-3']"]


@pytest.mark.parametrize("command", ["check", "verify"])
def test_a_tracker_that_cannot_be_read_exits_2(corpus, code, tmp_path, capsys, command):
    """The read refused (here the connect; the numbering probe's refusal is the same
    error): nothing checked, exit 2, the cause on stderr."""
    def refused(place):
        raise TrackerError(f"no board for {place.name}")
    status, out, err = _run(corpus, code, _input(corpus, tmp_path), capsys, command,
                            "--target", "rehearsal", connect=refused)
    assert (status, out) == (2, "") and err.startswith("failed: no board for ")


def _at_head(corpus, code):
    """The corpus read at ``code``'s HEAD, which every As filed block then names."""
    return replace(corpus, commit=run(code, "rev-parse", "HEAD"))


def test_verify_a_finished_migration_exits_0(corpus, code, tmp_path, capsys):
    """Every card as a finished filing leaves it, each block naming HEAD: the counts, the
    census, no card of the tracker's own, no refusal."""
    at_head = _at_head(corpus, code)
    tracker, _ = filed_tracker(at_head)
    tracker.gone.add(1)
    status, out, _ = _run(at_head, code, _input(at_head, tmp_path), capsys, "verify",
                          "--target", "plan", connect=lambda place: tracker)
    lines = out.splitlines()
    assert status == 0
    assert lines[0] == f"verify at {at_head.commit}: 13 cards, 13 the migration files"
    assert lines[-2:] == ["the tracker's own cards, no item's: none", "0 refusal(s)"]


def test_verify_refuses_a_block_naming_a_short_id(corpus, code, tmp_path, capsys):
    """One card's block names HEAD by a short id, which the plan does not compare: refused
    by git's answer through :func:`migrate.resolver`, exit 1."""
    at_head = _at_head(corpus, code)
    tracker, numbers = filed_tracker(at_head)
    tracker.gone.add(1)
    number = numbers["balance:A-3"]
    tracker.bodies[number] = tracker.bodies[number].replace(at_head.commit,
                                                            at_head.commit[:12])
    status, out, _ = _run(at_head, code, _input(at_head, tmp_path), capsys, "verify",
                          "--target", "plan", connect=lambda place: tracker)
    assert status == 1 and [line for line in out.splitlines() if line.startswith("REFUSED")] == [
        f"REFUSED: plan#{number}: the As filed block names {at_head.commit[:12]}, not the "
        "full id of a commit this checkout's HEAD descends from, so a reader cannot find the "
        "text it quotes"]


def test_verify_reads_no_tracker_while_anything_above_refuses(corpus, code, tmp_path, capsys):
    """A name missing from the input file: refused, the tracker never connected to."""
    data = complete_input(corpus)
    del data["names"]["balance:A-3"]
    path = tmp_path / "input.json"
    path.write_text(json.dumps(data))
    status, out, _ = _run(corpus, code, path, capsys, "verify", "--target", "plan")
    assert status == 1 and out.splitlines()[-2:] == [
        "REFUSED: names: balance:A-3: no name", "1 refusal(s); the tracker not read"]


def test_a_commit_resolves_only_as_the_full_id_of_one_head_descends_from(code):
    """HEAD and its parent resolve; a short id, a ref name (one as long as an id among
    them), an id of HEAD's length naming no object, a tree's id, and a commit off HEAD's
    history do not."""
    parent = run(code, "rev-parse", "HEAD")
    tree = run(code, "hash-object", "-t", "tree", "/dev/null")
    run(code, "commit", "-q", "--allow-empty", "-m", "the next")
    head = run(code, "rev-parse", "HEAD")
    aside = run(code, "commit-tree", tree, "-m", "aside")
    branch = "x" * len(head)
    run(code, "branch", branch, head)
    run(code, "tag", "-a", "-m", "a tag", "a-tag", parent)
    tag = run(code, "rev-parse", "a-tag")
    resolves = migrate.resolver(code, head)
    assert len(tag) == len(head) and tag != parent
    assert [resolves(commit) for commit in (head, parent, head[:12], "HEAD", branch,
                                            "0" * len(head), tree, aside, tag)] == [
        True, True, False, False, False, False, False, False, False]


def _x_then_y(corpus, code, change=lambda corpus: corpus):
    """``code`` at two commits, X then HEAD Y; the registries at Y, ``change`` made to X's;
    X; every card filed at X; their numbers; and the reader giving each commit's
    registries."""
    x = run(code, "rev-parse", "HEAD")
    run(code, "commit", "-q", "--allow-empty", "-m", "Y")
    y = run(code, "rev-parse", "HEAD")
    at_x, at_y = replace(corpus, commit=x), replace(change(corpus), commit=y)
    tracker, numbers = filed_tracker(at_x)
    tracker.gone.add(1)
    by_commit = {x: at_x, y: at_y}
    return at_y, x, tracker, numbers, lambda _root, commit: by_commit[commit]


def _changed(rows, ident, **cells):
    """A change giving the row ``ident`` of the registry ``rows`` (``"steps"``,
    ``"findings"`` or ``"rulings"``) the ``cells`` (figures made up)."""
    return lambda corpus: replace(corpus, **{rows: tuple(
        replace(row, **cells) if row.ident.split()[0] == ident else row
        for row in getattr(corpus, rows))})


def _verify_read(read, code, path, capsys, tracker):
    """``migrate verify --input path --target plan`` on the registries ``read`` gives each
    commit, the tracker ``tracker``; its exit status and its REFUSED lines and last line."""
    status = migrate.main(["verify", "--input", str(path), "--target", "plan"], read=read,
                          root=code, connect=lambda place: tracker)
    out = capsys.readouterr().out.splitlines()
    return status, [line for line in out if line.startswith("REFUSED")], out[-1]


def _holding(key, built):
    """The text of ``key``'s card ``built`` holding its As filed block: a question's As
    filed comment, any other card's body (as :func:`_quote` sets it)."""
    return built.comment if key.startswith("balance:Q-") else built.body


def _quote(tracker, number, key, text):
    """Card ``number``'s text holding its As filed block set to ``text``: a question's As
    filed comment, any other card's body."""
    if key.startswith("balance:Q-"):
        (comment,) = tracker.notes[number]
        tracker.notes[number] = [replace(comment, body=text)]
    else:
        tracker.bodies[number] = text


@pytest.mark.parametrize("edit", [
    ("balance:F-1", _changed("findings", "F-1", worst="$7.77"), "$7.77"),
    ("balance:Q-2", _changed("findings", "Q-2", worst="$7.77"), "$7.77"),
    ("balance:F-1", _changed("findings", "F-1", finding="The first half reads a stale row twice."),
     "The first half reads a stale row twice."),
    ("balance:R-A", _changed("rulings", "R-A", rule="Question: where? Answer: there."),
     "Question: where? Answer: there."),
    ("balance:A-3", _changed("steps", "A-3", title="Do the last part, again."),
     "Do the last part, again."),
    ("balance:A-3", _changed(
        "steps", "A-3",
        blocked="after #2 / balance:A-2a (its rows, read again) / balance:A-1 (shipped)"),
     "balance:A-2a (its rows, read again)"),
    ("balance:A-2b", _changed("steps", "A-2b", blocked="after #1 / balance:A-2a / balance:A-1"),
     "blocked by: balance:A-2a / balance:A-1"),
], ids=["review7-a-findings-cell", "review8-a-questions-cell", "review9-a-findings-lead",
        "review9-a-rulings-rule", "review9-a-steps-sentence", "review10-a-blockers-prose",
        "review10-a-blocker-list"])
def test_verify_refuses_a_card_holding_head_s_text_under_an_older_commit(
        corpus, code, tmp_path, capsys, edit):
    """Every card filed at X, HEAD Y changes one row's text, and the natural hand edit gives
    its card Y's text but keeps X's commit line, which the plan, comparing with the commit
    left out, does not see: review 7's MEDIUM-1, a cell F-1's block quotes; review 8's
    LOW-3, one in Q-2's As filed comment, its question unchanged; review 9's MEDIUM-1, the
    lead no block quotes -- F-1's finding, R-A's rule (a ruling's whole record), A-3's
    sentence; and the stored part of a step's ``starts`` cell, which its block quotes as
    ``blocked by`` (the coordinator's ruling on review 10, LOW-1) -- A-3's blockers' prose,
    A-2b's list.  verify refuses each: X's row writes otherwise; following the refusal,
    naming Y, passes.  ``shown``: the text Y's card holds and X's lacks."""
    key, change, shown = edit
    at_y, x, tracker, numbers, read = _x_then_y(corpus, code, change)
    number, quoted = numbers[key], _holding(key, corpus_texts(at_y)[key])
    assert shown in quoted
    assert shown not in _holding(key, corpus_texts(replace(corpus, commit=x))[key])
    assert key != "balance:Q-2" or tracker.bodies[number] == corpus_texts(at_y)[key].body
    _quote(tracker, number, key, quoted.replace(at_y.commit, x))
    assert _verify_read(read, code, _input(at_y, tmp_path), capsys, tracker)[:2] == (1, [
        f"REFUSED: {key} (plan#{number}): its As filed block names {x}, at which its row "
        "reads otherwise; the card holds this checkout's text, so set the commit its As "
        f"filed block names to this checkout's ({at_y.commit})"])
    _quote(tracker, number, key, quoted)
    assert _verify_read(read, code, _input(at_y, tmp_path), capsys, tracker) == (
        0, [], "0 refusal(s)")


@pytest.mark.parametrize("cells", [
    ("after #2 / balance:A-2a (its rows) / balance:A-1 (shipped) / not before the freeze",
     "after #2 / balance:A-2a (its rows) / balance:A-1 (shipped) / not before the thaw"),
    ("after #2 / balance:A-2a (its rows / read twice) / balance:A-1 (shipped)",
     "after #2 / balance:A-2a (its rows / read thrice) / balance:A-1 (shipped)"),
    ("after #2 / not before the freeze / balance:A-2a (its rows) / balance:A-1 (shipped)",
     "after #2 / not before the thaw / balance:A-2a (its rows) / balance:A-1 (shipped)"),
], ids=["review11-an-aside", "review11-after-an-inner-slash", "review12-an-aside-first"])
def test_verify_refuses_a_card_whose_blockers_changed_where_no_key_is(
        corpus, code, tmp_path, capsys, cells):
    """Review 11's MEDIUM-1: A-3's ``starts`` cell holds, at X and at HEAD Y, a colon-less
    aside (last, or first after the head: review 12's LOW-1), or an annotation holding
    `` / ``, and Y changes only that text.  Its block
    quotes the cell after its head whole (``StepRow.starts_stored``), so the text shows on
    the card, and the natural hand edit (Y's text under X's commit) is refused: X's row
    writes otherwise.  A reading by keys dropped the one and tore the other, and passed."""
    at_x_cell, at_y_cell = cells
    at_y, x, tracker, numbers, read = _x_then_y(
        _changed("steps", "A-3", blocked=at_x_cell)(corpus), code,
        _changed("steps", "A-3", blocked=at_y_cell))
    number, quoted = numbers["balance:A-3"], corpus_texts(at_y)["balance:A-3"].body
    assert f"blocked by: {at_y_cell.partition(' / ')[2]}" in quoted
    _quote(tracker, number, "balance:A-3", quoted.replace(at_y.commit, x))
    assert _verify_read(read, code, _input(at_y, tmp_path), capsys, tracker)[:2] == (1, [
        f"REFUSED: balance:A-3 (plan#{number}): its As filed block names {x}, at which its "
        "row reads otherwise; the card holds this checkout's text, so set the commit its As "
        f"filed block names to this checkout's ({at_y.commit})"])


def test_verify_refuses_a_card_whose_item_is_no_item_at_the_commit_it_names(
        corpus, code, tmp_path, capsys):
    """Review 9's LOW-2: every card filed at X, A-3's text alike at X and HEAD Y, but at X
    A-3 is SHIPPED, so no item there (the source reads open steps alone).  Its card names
    X, and is refused as no item there, naming X -- never a traceback, never a pass; naming
    Y, as the refusal says, passes."""
    at_y, x, tracker, numbers, read = _x_then_y(corpus, code)
    shipped = _changed("steps", "A-3", state="SHIPPED", commit="`abc1234`")

    def shipped_at_x(root, commit):
        """The registries at ``commit``, A-3 shipped at X."""
        return shipped(read(root, commit)) if commit == x else read(root, commit)
    number = numbers["balance:A-3"]
    path = _input(at_y, tmp_path)
    assert _verify_read(shipped_at_x, code, path, capsys, tracker)[:2] == (1, [
        f"REFUSED: balance:A-3 (plan#{number}): its As filed block names {x}, at which it is "
        "no item; the card holds this checkout's text, so set the commit its As filed block "
        f"names to this checkout's ({at_y.commit})"])
    tracker.bodies[number] = corpus_texts(at_y)["balance:A-3"].body
    assert _verify_read(shipped_at_x, code, path, capsys, tracker) == (0, [], "0 refusal(s)")


def _tick(corpus):
    """The corpus after a tick that only renumbers ranks, as one filing a step at #1 does
    (review 10's probe; the step it files is none of this): every rank one down, the
    board's order kept, and every ``after #N`` head, which the plan gate derives from the
    ranks, moved one with them (the fake corpus's heads are not the gate's derivation, and
    need not be: what moves is the point); every blocker and its prose unchanged."""
    corpus = _changed("steps", "A-2a", state="#2")(corpus)
    corpus = _changed("steps", "A-2b", state="#3", blocked="after #2 / balance:A-2a")(corpus)
    corpus = _changed("steps", "A-3", state="#4",
                      blocked="after #3 / balance:A-2a (its rows) / balance:A-1 (shipped)")(corpus)
    return _changed("steps", "S-1", state="#5")(corpus)


def test_verify_passes_cards_filed_at_an_older_commit_after_a_rank_only_tick(
        corpus, code, tmp_path, capsys):
    """Every card filed at X, and before HEAD Y a tick renumbered every rank and so the
    ``starts`` heads derived from them (:func:`_tick`; review 10's LOW-1, which refused it):
    no card's text moves, as its block quotes the stored part of ``starts`` alone, and no
    part of what a row writes is a rank or a value derived from one, so verify passes (the
    coordinator's rulings on reviews 9 and 10: never whole-row equality, and no derived
    value quoted)."""
    at_y, x, tracker, _, read = _x_then_y(corpus, code, _tick)
    at_x = read(code, x)
    assert [(row.state, row.blocked) for row in at_x.steps if row.ident == "A-3"] == [
        ("#3", "after #2 / balance:A-2a (its rows) / balance:A-1 (shipped)")]
    assert [(row.state, row.blocked) for row in at_y.steps if row.ident == "A-3"] == [
        ("#4", "after #3 / balance:A-2a (its rows) / balance:A-1 (shipped)")]
    assert {key: replace(built, body=built.body.replace(at_y.commit, x),
                         comment=built.comment and built.comment.replace(at_y.commit, x))
            for key, built in corpus_texts(at_y).items()} == corpus_texts(at_x)
    assert _verify_read(read, code, _input(at_y, tmp_path), capsys, tracker) == (
        0, [], "0 refusal(s)")


def _note_edited(corpus):
    """The corpus with A-2a's flush-left note edited: a paragraph the input file calls a
    note, on no card."""
    note = "*A flush-left note about archived steps"
    assert note in BALANCE
    return replace(corpus, documents={**corpus.documents, "balance": BALANCE.replace(
        note, "*A flush-left note (edited) about archived steps")})


def test_verify_passes_cards_truly_quoting_an_older_commit_after_a_note_edit(
        corpus, code, tmp_path, capsys):
    """Review 8's MEDIUM-1 (its P1), measured: every card filed at X, and before HEAD Y
    A-2a's NOTE edited -- a paragraph no card carries, but the hash of A-2a's name covers
    it, so the input file is re-hashed for Y.  No card's text moves, every card still
    says what X's row writes, word for word, and verify passes: a card claims what its
    row writes, not the arc document's text, which the plan grades against Y (the
    coordinator's rulings on reviews 8 and 9)."""
    at_y, x, tracker, numbers, read = _x_then_y(corpus, code, _note_edited)
    at_x = replace(corpus, commit=x)
    assert complete_input(at_y) != complete_input(at_x)
    assert all((built.title, masked(built.body)) == (corpus_texts(at_y)[key].title,
                                                      masked(corpus_texts(at_y)[key].body))
               for key, built in corpus_texts(at_x).items())
    assert tracker.bodies[numbers["balance:A-2a"]] == corpus_texts(at_x)["balance:A-2a"].body
    assert _verify_read(read, code, _input(at_y, tmp_path), capsys, tracker) == (
        0, [], "0 refusal(s)")


def test_verify_passes_a_card_truly_quoting_an_older_commit_after_a_rename(
        corpus, code, tmp_path, capsys):
    """Every card filed at X, and before HEAD Y F-1's NAME changed in the input file: the
    plan refuses its title, graded against Y; re-titled by hand, it still says what X's
    row writes, word for word, and verify passes: a card claims what its row writes, not
    its name (review 8, LOW-3; the coordinator's rulings on reviews 8 and 9)."""
    at_y, _, tracker, numbers, read = _x_then_y(corpus, code)
    data = complete_input(at_y)
    data["names"]["balance:F-1"]["name"] = "Stale row, read again"
    path = tmp_path / "input.json"
    path.write_text(json.dumps(data))
    number = numbers["balance:F-1"]
    status, refused, _ = _verify_read(read, code, path, capsys, tracker)
    assert status == 1 and len(refused) == 1 and refused[0].startswith(
        f"REFUSED: balance:F-1 (plan#{number}): it differs in its title")
    with_change(tracker, number,
                title=migrate.prepare(at_y, data).built["balance:F-1"].title)
    assert tracker.cards_by_number[number].title == "[F-1] Stale row, read again"
    assert _verify_read(read, code, path, capsys, tracker) == (0, [], "0 refusal(s)")


def test_following_the_stale_refusal_passes_verify(corpus, code, tmp_path, capsys):
    """F-1 filed at X and left as filed, HEAD Y changing its figure: the plan refuses it as
    stale, naming Y as the commit its block is to name -- and that refusal ALONE, though
    the card names X, whose row writes otherwise than Y's: verify grades no commit while
    the plan refuses anything, so the remedy "set the commit ... to this checkout's",
    true only of a card holding this checkout's text, is never printed for a stale card
    (the coordinator's condition on review 9's refusal text).  Giving it the text the
    registries make at Y, as the refusal says, passes verify; every other card still names
    X, whose rows write what Y's do."""
    at_y, _, tracker, numbers, read = _x_then_y(corpus, code,
                                                _changed("findings", "F-1", worst="$7.77"))
    number = numbers["balance:F-1"]
    path = _input(at_y, tmp_path)
    assert _verify_read(read, code, path, capsys, tracker)[:2] == (1, [
        f"REFUSED: balance:F-1 (plan#{number}): it differs in its body from what the "
        "registries make it at this commit: a stale card is never finished; settle it by hand, "
        f"giving it that text, its As filed block naming this commit ({at_y.commit})"])
    tracker.bodies[number] = corpus_texts(at_y)["balance:F-1"].body
    assert _verify_read(read, code, path, capsys, tracker) == (0, [], "0 refusal(s)")


def test_a_commit_whose_registries_cannot_be_read_is_refused_naming_it(
        corpus, code, tmp_path, capsys):
    """Every card filed at X, whose registries git cannot read now, but F-1, settled at Y:
    one refusal naming X, its cards (F-1's not among them) and why, exit 1 -- never a
    traceback, never a pass."""
    at_y, x, tracker, numbers, read = _x_then_y(corpus, code,
                                                _changed("findings", "F-1", worst="$7.77"))
    number = numbers["balance:F-1"]
    tracker.bodies[number] = corpus_texts(at_y)["balance:F-1"].body

    def unreadable_x(root, commit):
        """The registries at ``commit``, X's unreadable."""
        if commit == x:
            raise GitError(f"git cat-file blob {commit}:docs/plans/steps.md: no such path")
        return read(root, commit)
    status, refused, _ = _verify_read(unreadable_x, code, _input(at_y, tmp_path), capsys,
                                      tracker)
    assert status == 1 and len(refused) == 1
    assert refused[0].endswith(
        f": the As filed block names {x}, whose registries cannot be read (git cat-file blob "
        f"{x}:docs/plans/steps.md: no such path), so whether it quotes them is unknown")
    assert refused[0].startswith("REFUSED: plan#2, plan#3, ")
    assert f"plan#{number}," not in refused[0] and f"plan#{number}:" not in refused[0]


def test_a_commit_older_than_the_registries_tables_is_refused_naming_it(
        corpus, code, tmp_path, capsys):
    """Review 8's LOW-1: every card filed at X, an ancestor of HEAD whose steps.md holds no
    steps table yet, as the registries' first commits did not.  X resolves, the plan
    gate's own reader refuses it (its AssertionError), and verify refuses the cards naming
    X, naming it and why, exit 1 -- never a traceback, never a pass."""
    target = code / STEPS.relative_to(REPO)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Steps\n\nNo table yet.\n")
    run(code, "add", "-A")
    run(code, "commit", "-q", "-m", "steps.md before its tables")
    with pytest.raises(AssertionError):
        read_registries(code, run(code, "rev-parse", "HEAD"))
    at_y, x, tracker, _, read = _x_then_y(corpus, code)

    def at_head_or_git(root, commit):
        """Y's registries as the corpus holds them; any other commit's from git."""
        return read(root, commit) if commit == at_y.commit else read_registries(root, commit)
    status, refused, _ = _verify_read(at_head_or_git, code, _input(at_y, tmp_path), capsys,
                                      tracker)
    assert status == 1 and len(refused) == 1
    assert refused[0].startswith("REFUSED: plan#2, plan#3, ")
    assert f": the As filed block names {x}, whose registries cannot be read (no table with " \
        "the header " in refused[0]
    assert refused[0].endswith("), so whether it quotes them is unknown")


def test_verify_has_no_default_target(corpus, code, tmp_path):
    """Without ``--target`` the command line is refused (exit 2), and nothing connects."""
    connected = []
    with pytest.raises(SystemExit) as stop:
        migrate.main(["verify", "--input", str(_input(corpus, tmp_path))],
                     read=lambda _root, _commit: corpus, root=code, connect=connected.append)
    assert (stop.value.code, connected) == (2, [])


def test_nothing_is_planned_or_read_while_anything_above_refuses(corpus, code, tmp_path,
                                                                 capsys):
    """The input file's milestone missing: refused, and ``check`` counts no writes, with or
    without ``--target``, and reads no tracker (the plan, which reads the input file's
    milestones, runs only once nothing above refuses)."""
    data = complete_input(corpus)
    del data["milestones"]
    path = tmp_path / "input.json"
    path.write_text(json.dumps(data))
    for target in ([], ["--target", "plan"]):
        status, out, _ = _run(corpus, code, path, capsys, "check", *target)
        assert status == 1 and "writes: not counted while anything is refused" in out.splitlines()


def test_verify_lists_the_trackers_own_cards_and_a_link_still_to_make(
        corpus, code, tmp_path, capsys, monkeypatch):
    """S-1 mapped onto its card with a step of the tracker's own, #1, under it: listed.  Then
    A-2b still marked and unlinked from its parent: its two writes still to make, each
    named as a run prints it, and the tracker's own not listed."""
    at_head = _at_head(corpus, code)
    tracker, numbers = filed_tracker(at_head)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    tracker.board.items.remove(numbers["salary:S-1"])
    tracker.add(1, "step", "Its own.", on_board=False, title="Its own leaf", labels=("salary",),
                parent=numbers["salary:S-1"])
    path = _input(at_head, tmp_path)
    status, out, _ = _run(at_head, code, path, capsys, "verify", "--target", "plan",
                          connect=lambda place: tracker)
    assert (status, out.splitlines()[-2:]) == (0, ["the tracker's own cards, no item's: plan#1",
                                                   "0 refusal(s)"])
    number = numbers["balance:A-2b"]
    with_change(tracker, number, parent=None,
                labels=(*tracker.cards_by_number[number].labels, FILING))
    status, out, _ = _run(at_head, code, path, capsys, "verify", "--target", "plan",
                          connect=lambda place: tracker)
    assert (status, out.splitlines()[-3:]) == (1, [
        "REFUSED: link balance:A-2b -> balance:A-2: still to make (`migrate open` makes it), or "
        "this read lags the run's writes",
        "REFUSED: unmark balance:A-2b: still to make (`migrate open` makes it), or this read "
        "lags the run's writes",
        "2 refusal(s)"])


def _numbered(corpus):
    """The corpus with R-A's rule citing a tracker card, an app pull request and a rank."""
    return _changed("rulings", "R-A",
                    rule="Question: may card #8 cite PR #47 at rank #3? Answer: yes.")(corpus)


def test_check_counts_each_number_by_its_treatment_and_refuses_an_undecided_card(
        corpus, code, tmp_path, capsys):
    """R-BAL277: R-A's three numbers decided card, pr and plain are counted so and check
    passes; with R-A's entry gone, its three are undecided, refused naming each, and nothing
    is counted as written."""
    numbered = _numbered(corpus)
    data = complete_input(numbered)
    data["numbers"]["balance:R-A"]["each"] = [["#8", "card"], ["#47", "pr"], ["#3", "plain"]]
    path = tmp_path / "input.json"
    path.write_text(json.dumps(data))
    status, out, _ = _run(numbered, code, path, capsys)
    assert status == 0
    assert "bare #N, which GitHub links to the tracker's card N: card 1, pr 1, plain 1" in (
        out.splitlines())
    del data["numbers"]["balance:R-A"]
    path.write_text(json.dumps(data))
    status, out, _ = _run(numbered, code, path, capsys)
    lines = out.splitlines()
    assert status == 1
    assert "bare #N, which GitHub links to the tracker's card N: undecided 3" in lines
    (refused,) = [line for line in lines if line.startswith("REFUSED")]
    assert refused.startswith("REFUSED: numbers: balance:R-A: its card holds 3 bare #N, none "
                              "decided: #8 at 'Question: may card #8 cite PR #47 at rank'; "
                              "#47 at ")


#: The grade's control (:func:`_migrate_numbers.grade_rendered`), and GitHub's answer to it on
#: the plan tracker, as captured for the grade's own tests.
CONTROL = "#1 #01 #001 #0001"
#: A paragraph linking the plan tracker's card 1, as GitHub links a number no decision names.
LINKS_CARD_1 = ('<p dir="auto">x <a class="issue-link js-issue-link" '
                'href="https://github.com/saltyreformed-labs/shekel-plan/issues/1">#1</a></p>')


def _renderer(asked, other):
    """GitHub's renderer for a flow: the control as GitHub answered it, and every other text
    ``other``; each text asked is kept in ``asked``."""
    control = recording()["renders"][CONTROL]
    return lambda text: asked.append(text) or (control if text == CONTROL else other)


def test_check_target_grades_the_numbers_by_githubs_renderer_before_it_plans(
        corpus, code, tmp_path, capsys):
    """R-BAL355: ``check --target`` renders R-A's body, its three numbers decided plain,
    through the tracker (the control, then as built, as filed and every number naming card
    1), and counts the writes left on a tracker holding it filed so;
    GitHub linking a card from it refuses, and the tracker is neither planned on nor
    read."""
    numbered = _numbered(corpus)
    path = _input(numbered, tmp_path)
    tracker, _ = filed_tracker(numbered)
    tracker.gone.add(1)
    asked = []
    tracker.renderer = _renderer(asked, '<p dir="auto">x</p>')
    status, out, _ = _run(numbered, code, path, capsys, "check", "--target", "plan",
                          connect=lambda place: tracker)
    lines = out.splitlines()
    assert status == 0 and lines[-2] == (
        "writes left on the tracker: 0 filing the rulings, then 0 filing the rest and the 0 "
        "milestones, before the board's moves")
    assert not [line for line in lines if "not graded" in line]
    assert asked[0] == CONTROL and len(asked) == 4
    assert all("may card #" in text for text in asked[1:])
    tracker.renderer = _renderer([], LINKS_CARD_1)
    tracker.all_cards = lambda: pytest.fail("the tracker was read after the grade refused")
    status, out, _ = _run(numbered, code, path, capsys, "check", "--target", "plan",
                          connect=lambda place: tracker)
    lines = out.splitlines()
    assert status == 1 and "writes: not counted while anything is refused" in lines
    assert [line for line in lines if line.startswith("REFUSED")] == [
        "REFUSED: numbers: balance:R-A: GitHub links a card of the tracker from its body that "
        "no decision names card: '#1' after 'x' (every number named card 1 here, so each link "
        "stands for one GitHub makes once its card is filed): reword its source, or decide it "
        "(a number decided plain still links in a table cell, an alert or a paragraph holding "
        "math)"]


def test_a_writing_phase_writes_nothing_once_githubs_renderer_refuses(
        corpus, code, tmp_path, capsys):
    """``history`` grades the numbers on the tracker it connects to before its first
    write: refused, it writes nothing (exit 1)."""
    numbered = _numbered(corpus)
    tracker = FakeTracker()
    tracker.renderer = _renderer([], LINKS_CARD_1)
    status, out, _ = _run(numbered, code, _input(numbered, tmp_path), capsys,
                          "history", "--target", "plan", connect=lambda place: tracker)
    assert (status, out.splitlines()[-1], tracker.writes) == (
        1, "1 refusal(s); nothing written", [])


def test_verify_holds_a_cards_stored_text_to_its_numbers_as_decided(
        corpus, code, tmp_path, capsys):
    """Every phase files and grades the text with its numbers written: a finished filing
    holds R-A's markers and passes; the same card holding its numbers bare, as a run that
    skipped the transform would leave it, is refused as differing in its body."""
    at_head = _at_head(_numbered(corpus), code)
    tracker, numbers = filed_tracker(at_head)
    tracker.gone.add(1)
    number = numbers["balance:R-A"]
    assert "card #<!-- -->8 cite PR #<!-- -->47 at rank #<!-- -->3" in tracker.bodies[number]
    path = _input(at_head, tmp_path)
    status, out, _ = _run(at_head, code, path, capsys, "verify", "--target", "plan",
                          connect=lambda place: tracker)
    assert (status, out.splitlines()[-1]) == (0, "0 refusal(s)")
    tracker.bodies[number] = tracker.bodies[number].replace("#<!-- -->", "#")
    status, out, _ = _run(at_head, code, path, capsys, "verify", "--target", "plan",
                          connect=lambda place: tracker)
    refused = [line for line in out.splitlines() if line.startswith("REFUSED")]
    assert status == 1 and len(refused) == 1
    assert "balance:R-A" in refused[0] and "it differs in its body" in refused[0]


def test_the_census_reads_a_step_cards_text_before_its_numbers_are_written(corpus):
    """A step's spec line citing a rank is on its card as the census reads it, and is filed
    with its marker: the census reads the text as built and the numbers are written after
    it (review of S2, L1).  The other way round, the census would find the line on no card."""
    text = BALANCE.replace("A-3's own flush-left spec paragraph.\n",
                           "A-3's own flush-left spec paragraph, rank #3.\n")
    numbered = replace(corpus, documents={**corpus.documents, "balance": text})
    prepared = migrate.prepare(numbered, complete_input(numbered))
    assert not prepared.refusals
    assert prepared.treated == Counter({"plain": 1})
    assert [key for key, card in prepared.built.items()
            if "spec paragraph, rank #<!-- -->3." in card.body] == ["balance:A-3"]
