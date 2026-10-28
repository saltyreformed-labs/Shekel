"""``quill pr``: a card's pull request into ``dev`` from ``card/N``, its text ending with the line
naming the card (plan#1861's item 10), opened by a stand-in ``gh`` first on ``PATH`` that keeps
what it was run with and answers as the test sets it, in a throwaway git repository (the
``code`` fixture, ``conftest.py``).  Nothing here calls GitHub, and nothing connects to the
tracker.
"""
from __future__ import annotations

import os

import pytest

from tools.quill import quill

#: The stand-in ``gh``: it keeps its arguments, one a line, what it reads, and where it ran,
#: then prints ``$GH_OUT`` and ``$GH_ERR`` and exits ``$GH_EXIT``.
FAKE_GH = """#!/bin/sh
printf '%s\\n' "$@" > "$GH_KEPT/argv"
cat > "$GH_KEPT/stdin"
pwd > "$GH_KEPT/cwd"
printf '%s' "$GH_OUT"
printf '%s' "$GH_ERR" >&2
exit "$GH_EXIT"
"""
#: The address the stand-in answers a pull request opened with.
URL = "https://github.com/saltyreformed-labs/Shekel/pull/700"


@pytest.fixture(name="gh")
def _gh(tmp_path, monkeypatch):
    """The stand-in ``gh`` first on ``PATH``, opening a pull request at :data:`URL`; the
    directory it keeps what it was run with in."""
    bin_dir, kept = tmp_path / "bin", tmp_path / "kept"
    bin_dir.mkdir()
    kept.mkdir()
    script = bin_dir / "gh"
    script.write_text(FAKE_GH)
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GH_KEPT", str(kept))
    monkeypatch.setenv("GH_OUT", URL + "\n")
    monkeypatch.setenv("GH_ERR", "")
    monkeypatch.setenv("GH_EXIT", "0")
    return kept


def _unreached():
    """A tracker connection ``quill pr`` must never make: it reads nothing of the tracker."""
    raise AssertionError("quill pr connected to the tracker")


def _pr(code, tmp_path, body="Ships the piece.\n"):
    """Run ``quill pr plan#7`` in ``code``, its text ``body``; its exit status."""
    text = tmp_path / "body.md"
    text.write_text(body)
    return quill.main(["pr", "plan#7", "--title", "feat(quill): the piece", "--body-file",
                       str(text)], connect=_unreached, root=code)


def test_pr_opens_the_cards_pull_request_into_dev_naming_the_card(code, tmp_path, gh, capsys):
    """Spec item 10: from ``card/N`` into ``dev``, the text the file holds and then the line
    ``Card: saltyreformed-labs/shekel-plan#N``, plain text that GitHub links; the address
    printed, with how to queue it."""
    assert _pr(code, tmp_path) == 0
    assert (gh / "argv").read_text().splitlines() == [
        "pr", "create", "--base", "dev", "--head", "card/7", "--title", "feat(quill): the piece",
        "--body-file", "-"]
    assert (gh / "stdin").read_text() == (
        "Ships the piece.\n\nCard: saltyreformed-labs/shekel-plan#7\n")
    assert (gh / "cwd").read_text().strip() == str(code)
    out = capsys.readouterr().out
    assert out.startswith(f"opened {URL}: plan#7's pull request from card/7 into dev")
    assert f"queue it with `gh pr merge {URL} --merge`" in out


def test_pr_refuses_a_text_that_is_only_space_and_runs_nothing(code, tmp_path, gh, capsys):
    """A pull request says what it changes: an empty text is refused before ``gh`` runs."""
    assert _pr(code, tmp_path, body="  \n") == 1
    assert "holds no text: a pull request says what it changes" in capsys.readouterr().err
    assert not (gh / "argv").exists()


@pytest.mark.usefixtures("gh")
def test_a_failed_gh_is_a_failure_that_says_to_read_it_back(code, tmp_path, monkeypatch, capsys):
    """Exit status 2, with what ``gh`` said and how to read the pull request back: GitHub may
    have opened it before its answer was lost, so it is never opened again unread."""
    monkeypatch.setenv("GH_OUT", "")
    monkeypatch.setenv("GH_ERR", "a pull request for branch \"card/7\" into branch \"dev\" "
                                 "already exists\n")
    monkeypatch.setenv("GH_EXIT", "1")
    assert _pr(code, tmp_path) == 2
    err = capsys.readouterr().err
    assert err.startswith("failed: gh pr create --head card/7: a pull request for branch "
                          "\"card/7\" into branch \"dev\" already exists; it is never run again "
                          "unread, as GitHub may have opened it: `gh pr list --head card/7` "
                          "reads it back")


@pytest.mark.usefixtures("gh")
def test_what_gh_warns_of_is_printed_beside_the_opened_pull_request(code, tmp_path, monkeypatch,
                                                                    capsys):
    """A warning ``gh`` prints as it opens one (uncommitted changes in this checkout) is the
    session's to read."""
    monkeypatch.setenv("GH_ERR", "Warning: 2 uncommitted changes\n")
    assert _pr(code, tmp_path) == 0
    captured = capsys.readouterr()
    assert captured.err == "Warning: 2 uncommitted changes\n"
    assert captured.out.startswith(f"opened {URL}")
