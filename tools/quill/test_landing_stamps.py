"""CI's backstop to the commit hook (ruling plan#1872; plan#1861's spec, item 5,
``tools/quill/_stamps.py``): a card's pull request is refused while a commit of its claim names
no task under the card.

Each history starts from a base whose tree holds the hook's code (:func:`_hooked`), as ``dev``
does once the hook lands, unless the test is of a branch claimed before that.  Every history
is built and judged, and every tracker answered, by the builders the landing tests share
(:mod:`tools.quill._histories`): card 7, a step with task 12 under it; card 9, a step with task
21 under it; and card 30, a step.  Each refusal's sentence is held word for word once.
"""
from __future__ import annotations

import pytest
import yaml

from tools.ci.arcs import REPO
from tools.ci.scratch import commit_files, run
from tools.quill import _stamps, commit_hook
from tools.quill._git import marker, task_file
from tools.quill._histories import MON, TUE, Lookup, begun, card_node, judging, merge, never
from tools.quill._histories import reader, start, tracker, verdict


@pytest.fixture(name="code")
def _code(tmp_path):
    """A repository whose ``origin/dev`` and ``origin/main`` hold one base commit; the
    repository and the base (:func:`_histories.start`)."""
    return start(tmp_path)


@pytest.fixture(name="check")
def _check(capsys, code):
    """Judge pull request #600 of branch ``head`` at ``head_sha`` into ``base``, whose tip is
    ``onto`` (:func:`_histories.judging`), reading the tracker through ``github`` (no read
    allowed when None): its exit status, output and error."""
    judged = judging(capsys, code[0])

    def _run(onto, head_sha, head, *, base="dev", github=None):
        """Judge pull request #600 of ``head`` into ``base`` (the fixture)."""
        return judged(onto, head_sha, head, base, reader(github) if github else never)

    return _run


def _cards():
    """The tracker the module's docstring names, as GitHub answers it."""
    return Lookup(tracker(card_node(7), card_node(12, "task", parent=7), card_node(9),
                          card_node(21, "task", parent=9), card_node(30)))


def _hooked(root, on):
    """``on`` with the commit hook's code landed (:data:`_stamps.HOOK`); its sha."""
    return commit_files(root, on, "the commit hook lands", {_stamps.HOOK: "the hook\n"})


def _claimed(root, on):
    """Card 7's claim on ``on``, as ``quill claim`` makes it: a commit adding its marker and
    nothing else; its sha."""
    return commit_files(root, on, "claim plan#7", {marker(7): f"claimed {MON}\n"})


def _begun(root, on):
    """Task 12 begun on ``on``, as ``quill start`` begins it on ``card/7``
    (:func:`_histories.begun`); its sha."""
    return begun(root, on, 7, 12)


def _ended(root, on):
    """The commit ending task 12's piece on ``on``: its last work and its done file, named
    by the hook's stamp; its sha."""
    return commit_files(root, on, "the piece's end\n\nTask: plan#12",
                        {"end.py": "end\n", task_file(12): f"done {MON}\n"})


def _unnamed(sha, subject):
    """The refusal of card/7's commit ``sha``, whose subject is ``subject``, naming no task."""
    return (f"REFUSED -- commit {sha[:12]} ({subject}): card/7 brings it, and it names no task, "
            "though its tree holds the commit hook, which stamps each commit of card/7 with the "
            "task under plan#7 in progress (`Task: plan#T`): reword it to name the task it "
            "builds, then push card/7 again")


def test_a_claim_whose_every_commit_names_a_task_under_its_card_lands(check, code):
    """card/7's claim commit, then task 12's piece in three commits -- its start, its work,
    and its end with its done file in the last -- each naming task 12: it lands, and the
    tracker is read once, for the card and the task whose done file it writes, as before."""
    root, base = code
    onto = _hooked(root, base)
    started = _begun(root, _claimed(root, onto))
    work = commit_files(root, started, "half the piece\n\nTask: plan#12", {"half.py": "half\n"})
    github = _cards()
    status, out, _ = check(onto, _ended(root, work), "card/7", github=github)
    assert (status, out) == (0, verdict("card/7", 1, 0, tasks=1) + "\n")
    assert github.asked == [[7, 12]]


@pytest.mark.parametrize("base", ["dev", "main"])
def test_a_commit_of_the_claim_naming_no_task_is_refused(check, code, base):
    """A commit of card/7 made with the hook skipped names no task: the pull request is
    refused, into dev or main, naming that commit alone, its claim commit and the commits
    naming task 12 passed."""
    root, start_ = code
    onto = _hooked(root, start_)
    loose = commit_files(root, _begun(root, _claimed(root, onto)), "made with the hook skipped",
                         {"loose.py": "loose\n"})
    status, out, _ = check(onto, _ended(root, loose), "card/7", base=base, github=_cards())
    assert status == 1
    assert out.splitlines() == [
        f"REFUSED -- commit {loose[:12]} (made with the hook skipped): card/7 brings it, and it "
        "names no task, though its tree holds the commit hook, which stamps each commit of "
        "card/7 with the task under plan#7 in progress (`Task: plan#T`): reword it to name the "
        "task it builds, then push card/7 again",
        verdict("card/7", 1, 1, base=base, tasks=1)]


@pytest.mark.parametrize(("named", "why"), [
    (21, "task plan#21 is under plan#9, not plan#7"), (30, "plan#30 is a step, no task"),
    (99, "plan#99 does not exist")], ids=["another card's task", "a step", "no card"])
def test_a_commit_naming_a_card_that_is_no_task_under_the_claims_card_is_refused(
        check, code, named, why):
    """A commit of card/7 naming, twice over, a task under card 9, a step, or a card the
    tracker lacks: refused once, saying what the tracker says that card is, which is read
    then, in one read of its own; the cards it named before (7 and 12) are not read again."""
    root, base = code
    onto = _hooked(root, base)
    wrong = commit_files(root, _begun(root, _claimed(root, onto)),
                         f"another card's piece\n\nTask: plan#{named}\nTask: plan#{named}",
                         {"wrong.py": "wrong\n"})
    github = _cards()
    status, out, _ = check(onto, _ended(root, wrong), "card/7", github=github)
    assert status == 1
    assert out.splitlines() == [
        f"REFUSED -- commit {wrong[:12]} (another card's piece): card/7 brings it, naming "
        f"plan#{named}, but {why}, so it builds no piece of plan#7: reword it to name the task "
        "under plan#7 it builds, then push card/7 again",
        verdict("card/7", 1, 1, tasks=1)]
    assert github.asked == [[7, 12], [named]]


def test_the_claim_commit_and_a_merge_of_dev_are_never_work(check, code):
    """card/7 merges dev in, which moved on with a commit of its own; neither the merge nor
    the claim commit names a task, and neither is work: it lands."""
    root, base = code
    onto = _hooked(root, base)
    moved = commit_files(root, onto, "dev's own work", {"dev.py": "dev\n"})
    merged = merge(root, _begun(root, _claimed(root, onto)), moved, "Merge origin/dev into card/7")
    status, out, _ = check(moved, _ended(root, merged), "card/7", github=_cards())
    assert (status, out) == (0, verdict("card/7", 1, 0, tasks=1) + "\n")


def test_a_claim_made_before_the_hook_is_held_from_the_commits_made_once_it_takes_it_in(
        check, code):
    """card/7, claimed before dev held the hook, has a commit naming no task; it then merges
    dev, which took the hook in, and makes another: only the one whose tree holds the hook is
    refused (the spec's item 12)."""
    root, base = code
    before = commit_files(root, _claimed(root, base), "work before the hook", {"a.py": "a\n"})
    onto = _hooked(root, base)
    merged = merge(root, before, onto, "Merge origin/dev into card/7")
    after = commit_files(root, _begun(root, merged), "work after the hook", {"b.py": "b\n"})
    status, out, _ = check(onto, _ended(root, after), "card/7", github=_cards())
    assert (status, out.splitlines()) == (1, [_unnamed(after, "work after the hook"),
                                              verdict("card/7", 1, 1, tasks=1)])


def test_work_a_claim_was_made_on_is_not_held_and_work_after_it_is(check, code):
    """``quill claim --here``: card/7 claimed on work already started on another branch, which
    no hook judged; that work is not held, and a commit after the claim naming no task is."""
    root, base = code
    onto = _hooked(root, base)
    started = commit_files(root, onto, "work already started", {"a.py": "a\n"})
    after = commit_files(root, _claimed(root, started), "work after the claim", {"b.py": "b\n"})
    status, out, _ = check(onto, _ended(root, _begun(root, after)), "card/7", github=_cards())
    assert (status, out.splitlines()) == (1, [_unnamed(after, "work after the claim"),
                                              verdict("card/7", 1, 1, tasks=1)])


def test_a_commit_rewriting_the_marker_alone_frees_nothing_made_before_it(check, code):
    """A commit of card/7 naming no task, then one rewriting its marker alone, as a claim's
    commit writes it: the claim the rewrite grew from bounds the claim, so the commit before
    the rewrite is still refused, and the rewrite, which builds nothing, is not."""
    root, base = code
    onto = _hooked(root, base)
    loose = commit_files(root, _claimed(root, onto), "work naming no task", {"a.py": "a\n"})
    again = commit_files(root, loose, "the marker rewritten", {marker(7): f"claimed {TUE}\n"})
    status, out, _ = check(onto, _ended(root, _begun(root, again)), "card/7", github=_cards())
    assert (status, out.splitlines()) == (1, [_unnamed(loose, "work naming no task"),
                                              verdict("card/7", 1, 1, tasks=1)])


def test_a_commit_deleting_the_marker_or_writing_it_beside_work_is_work(check, code):
    """Of card/7's commits naming no task, oldest first: one deleting its marker alone, and
    one rewriting it beside work, are each work, so each is refused; the one adding the
    marker back alone is no work, and frees nothing made before it."""
    root, base = code
    onto = _hooked(root, base)
    deleted = commit_files(root, _claimed(root, onto), "the marker deleted", {marker(7): None})
    added = commit_files(root, deleted, "the marker again", {marker(7): f"claimed {TUE}\n"})
    beside = commit_files(root, added, "work and the marker",
                          {"a.py": "a\n", marker(7): f"claimed {MON}\n"})
    status, out, _ = check(onto, _ended(root, _begun(root, beside)), "card/7", github=_cards())
    assert (status, out.splitlines()) == (1, [_unnamed(deleted, "the marker deleted"),
                                              _unnamed(beside, "work and the marker"),
                                              verdict("card/7", 1, 2, tasks=1)])


def test_work_made_on_another_branch_and_merged_into_the_claim_is_held(check, code):
    """A commit made on a side branch cut from card/7, where the hook judges nothing, then
    merged into card/7: it names no task, so it is refused, though the merge is not."""
    root, base = code
    onto = _hooked(root, base)
    claimed = _claimed(root, onto)
    side = commit_files(root, claimed, "made on a side branch", {"side.py": "side\n"})
    merged = merge(root, _begun(root, claimed), side, "Merge side into card/7")
    status, out, _ = check(onto, _ended(root, merged), "card/7", github=_cards())
    assert (status, out.splitlines()) == (1, [_unnamed(side, "made on a side branch"),
                                              verdict("card/7", 1, 1, tasks=1)])


def test_a_commit_writing_the_marker_alone_and_naming_a_task_is_judged_by_its_name(check, code):
    """A claim's commit names no task (``quill claim`` writes it from objects alone): one that
    rewrites card/7's marker alone and names task 21, under card 9, is judged by the name it
    gives, and refused."""
    root, base = code
    onto = _hooked(root, base)
    named = commit_files(root, _claimed(root, onto), "the marker rewritten\n\nTask: plan#21",
                         {marker(7): f"claimed {TUE}\n"})
    status, out, _ = check(onto, _ended(root, _begun(root, named)), "card/7", github=_cards())
    assert (status, out.splitlines()[1:]) == (1, [verdict("card/7", 1, 1, tasks=1)])
    assert out.startswith(f"REFUSED -- commit {named[:12]} (the marker rewritten): card/7 brings "
                          "it, naming plan#21, but task plan#21 is under plan#9, not plan#7")


def test_a_root_commit_merged_in_from_an_unrelated_history_is_held(check, code):
    """A commit of no parent whose tree holds the hook, merged into card/7 from a history of
    its own: it names no task, so it is refused, read as no claim of card 7's."""
    root, base = code
    onto = _hooked(root, base)
    built = commit_files(root, onto, "a tree", {"lone.py": "lone\n"})
    lone = run(root, "commit-tree", f"{built}^{{tree}}", "-m", "an unrelated root")
    claimed = _begun(root, _claimed(root, onto))
    tree = run(root, "merge-tree", "--write-tree", "--allow-unrelated-histories", claimed, lone)
    merged = run(root, "commit-tree", tree, "-p", claimed, "-p", lone, "-m", "Merge unrelated")
    status, out, _ = check(onto, _ended(root, merged), "card/7", github=_cards())
    assert (status, out.splitlines()) == (1, [_unnamed(lone, "an unrelated root"),
                                              verdict("card/7", 1, 1, tasks=1)])


def test_a_branch_that_is_no_claim_is_never_held(check, code):
    """A feature branch's commit naming no task, its tree holding the hook: no claim's pull
    request, so it lands, and the tracker is never read."""
    root, base = code
    onto = _hooked(root, base)
    feature = commit_files(root, onto, "a feature", {"x.py": "x\n"})
    assert check(onto, feature, "feat/x")[:2] == (0, verdict("feat/x", 0, 0) + "\n")


def test_the_hooks_code_is_the_module_pre_commits_entry_runs():
    """:data:`_stamps.HOOK` is the file this repository holds for the module
    ``.pre-commit-config.yaml``'s hook ``quill-task`` runs: renaming either parts them here."""
    config = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hook = next(hook for repo in config["repos"] for hook in repo["hooks"]
                if hook["id"] == "quill-task")
    assert hook["entry"] == f".venv/bin/python -m {commit_hook.__name__}"
    assert _stamps.HOOK == "tools/quill/commit_hook.py"
    assert (REPO / _stamps.HOOK).is_file()
