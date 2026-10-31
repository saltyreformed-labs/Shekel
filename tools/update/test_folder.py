"""The folder the Monday refresh writes and the publish reads (:mod:`tools.update.folder`): read
back as written, and refused whole where it holds anything outside its layout, since it comes
from a job that ran package code into one holding a key."""
from __future__ import annotations

import pytest

from tools.quill.door import LOCK
from tools.update import folder
from tools.update.folder import ALONE, BODY, SUMMARY, TITLE, WEEK, Change, FolderError
from tools.update.pyproject import PYPROJECT

#: The week's lock, and pyee prepared alone, as the refresh writes them.
_WEEK = Change(WEEK, {LOCK: "the week's lock\n"}, "chore(deps): the lock moves 6 packages",
               "| pillow | 12.3.0 | 12.4.1 |\n")
_PYEE = Change(folder.alone("pyee"), {LOCK: "pyee's lock\n", PYPROJECT: "[project]\n"},
               "chore(deps): pyee from 13.0.1 to 14.0.0, waiting for a card", "It needs one.\n")


@pytest.fixture(name="out")
def _out(tmp_path):
    """A folder as the refresh's command leaves it: the week's lock, pyee alone, and the
    report."""
    out = tmp_path / "out"
    folder.write(out, _WEEK)
    folder.write(out, _PYEE)
    folder.write_summary(out, "The Monday lock refresh moved 6 packages.\n")
    return out


def test_a_folder_is_read_back_as_written_the_week_first(out):
    """Each change read as written, the week's lock first, each on its branch."""
    assert folder.read(out) == (_WEEK, _PYEE)
    assert [change.branch for change in folder.read(out)] == ["update/lock", "update/major/pyee"]


def test_a_week_with_nothing_to_publish_is_its_report_alone(tmp_path):
    """The report alone is a refresh that finished with no change to publish."""
    folder.write_summary(tmp_path / "out", "Nothing moved.\n")
    assert not folder.read(tmp_path / "out")


@pytest.mark.parametrize(("branch", "ours"), [
    ("update/lock", True), ("update/major/pyee", True), ("update/major/typing-extensions", True),
    ("update/major/PyEE", False), ("update/major/", False), ("update/major/a/b", False),
    ("update/lock/x", False), ("update/other", False), ("feature/lock", False),
    ("update/majorx/pyee", False),
])
def test_a_branch_is_ours_only_where_the_layout_could_name_it(branch, ours):
    """Only a branch a change of the layout is published on is the publish's to delete."""
    assert folder.ours(branch) is ours


def _symlink(out):
    """The week's lock replaced by a link to a file outside the folder."""
    outside = out.parent / "secret"
    outside.write_text("not the lock\n", encoding="utf-8")
    (out / WEEK / LOCK).unlink()
    (out / WEEK / LOCK).symlink_to(outside)


@pytest.mark.parametrize(("spoil", "said"), [
    (lambda out: (out / SUMMARY).unlink(), "holds no summary.md, so the refresh never finished"),
    (lambda out: (out / "extra.txt").write_text("x\n", encoding="utf-8"),
     "holds extra.txt, outside the layout"),
    (lambda out: (out / WEEK / TITLE).unlink(), "lock holds body.md, uv.lock: a change holds"),
    (lambda out: (out / WEEK / PYPROJECT).write_text("x\n", encoding="utf-8"),
     "lock holds body.md, pyproject.toml, title.txt, uv.lock"),
    (lambda out: (out / WEEK / LOCK).unlink(), "lock holds body.md, title.txt: a change holds"),
    (lambda out: (out / ALONE / "pyee").rename(out / ALONE / "PyEE"),
     "major/PyEE names no project as PEP 503 normalizes one"),
    (lambda out: (out / ALONE / "pyee" / "setup.py").write_text("x\n", encoding="utf-8"),
     "major/pyee holds body.md, pyproject.toml, setup.py, title.txt, uv.lock"),
    (lambda out: (out / WEEK / TITLE).write_text("two\nlines\n", encoding="utf-8"),
     "lock/title.txt is not one line"),
    (lambda out: (out / WEEK / TITLE).write_text("\n", encoding="utf-8"),
     "lock/title.txt is not one line"),
    (lambda out: (out / WEEK / TITLE).write_text("no newline", encoding="utf-8"),
     "lock/title.txt is not one line"),
    (_symlink, "holds a link: uv.lock"),
    (lambda out: (out / WEEK / BODY).write_bytes(b"\xff\n"), "body.md holds no UTF-8 text"),
    (lambda out: (out / WEEK / LOCK).unlink() or (out / WEEK / LOCK).mkdir(),
     "uv.lock is no file"),
], ids=["no report", "an unknown entry", "no title", "a file the week may not carry",
        "no file", "a name PEP 503 does not normalize to", "a file a package may not carry",
        "a title of two lines", "an empty title", "a title with no line end", "a link",
        "no UTF-8", "a folder for a file"])
def test_a_folder_holding_anything_outside_its_layout_is_refused_whole(out, spoil, said):
    """Each spoiling refuses the folder, saying what it holds, before any change is read."""
    spoil(out)
    with pytest.raises(FolderError) as raised:
        folder.read(out)
    assert said in str(raised.value)


def test_a_missing_folder_is_refused(tmp_path):
    """A folder the artifact never held is no folder, never a week with nothing in it."""
    with pytest.raises(FolderError, match="is no folder"):
        folder.read(tmp_path / "out")
