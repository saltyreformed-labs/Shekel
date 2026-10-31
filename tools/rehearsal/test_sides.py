"""How a side runs (step X-cx's K1b): its environment built whole from its own tree, its
clock pinned, and its deploy step and boot each run by a side probe from its worktree.  Every
case plants a side's tree and interpreter; none starts a cluster.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from tools.rehearsal import bench, sides


def _side(root: Path, dockerfile: str, compose: str) -> Path:
    """A planted side's tree: its Dockerfile and production compose file."""
    (root / "deploy").mkdir(parents=True)
    (root / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    (root / "deploy" / "docker-compose.prod.yml").write_text(compose, encoding="utf-8")
    return root


_DOCKERFILE = ('FROM python\nENV PATH="/opt/venv/bin:$PATH"\nRUN true\nENV PSYCOPG_IMPL=c\n'
               "ENV LC_ALL=C.UTF-8\n")
_PROD_COMPOSE = "services:\n  app:\n    environment:\n      TZ: America/New_York\n"


def _state(worktree: Path, python: Path) -> bench.State:
    """A state over a planted tree and interpreter."""
    return bench.State("cut", "0" * 40, worktree, "rehearsal_cut", "postgresql:///x", python)


#: The instant a planted deploy is pinned at: noon in New York, the planted compose file's zone.
_MOMENT = datetime(2026, 10, 20, 12, tzinfo=ZoneInfo("America/New_York"))
#: The side probe a deploy runs, by path.
_PROBE = Path(sides.__file__).resolve().parent / "side" / "deploy.py"


def test_the_side_environment_is_built_whole_from_the_side_s_tree(tmp_path, monkeypatch):
    """Production's variables from the side's own Dockerfile (not PATH) and compose file, the
    configuration, URL, key, store and seed, and the side's venv first: nothing else, so a
    variable of the shell that runs the kit never reaches the side."""
    monkeypatch.setenv("DATABASE_URL_APP", "postgresql:///operator")
    monkeypatch.setenv("SHEKEL_FAKE_TODAY", "2026-01-01")
    tree = _side(tmp_path / "tree", _DOCKERFILE, _PROD_COMPOSE)
    built = sides.side_environment(_state(tree, tmp_path / "venv" / "bin" / "python"),
                                    "postgresql:///copy", "k" * 64)
    assert built == {
        "PSYCOPG_IMPL": "c", "LC_ALL": "C.UTF-8", "TZ": "America/New_York",
        "FLASK_ENV": "production", "SECRET_KEY": "k" * 64,
        "DATABASE_URL": "postgresql:///copy",
        "RATELIMIT_STORAGE_URI": "redis://rehearsal.invalid:6379/0", "PYTHONHASHSEED": "0",
        "PYTHON_DOTENV_DISABLED": "1",
        "PATH": f"{tmp_path / 'venv' / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin"}


@pytest.mark.parametrize("compose", ["services: {}\n", _PROD_COMPOSE + "      TZ: UTC\n"])
def test_a_side_whose_compose_file_pins_no_single_timezone_is_refused(tmp_path, compose):
    """No ``TZ``, or two, is refused by name rather than guessed."""
    tree = _side(tmp_path, _DOCKERFILE, compose)
    with pytest.raises(bench.BenchError, match=r"docker-compose\.prod\.yml pins [02] TZ lines"):
        sides.image_environment(tree)


_RECORDER = """#!{python}
import json, os, sys
with open("seen.json", "w", encoding="utf-8") as seen:
    json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd(), "env": dict(os.environ)}}, seen)
print("Database initialised.")
print("the planted refusal", file=sys.stderr)
sys.exit({status})
"""


@pytest.mark.parametrize("status", [0, 3])
def test_a_deploy_runs_the_side_s_step_from_its_tree_in_that_environment_alone(tmp_path,
                                                                            status):
    """The side's interpreter runs the side probe ``side/deploy.py`` with the pinned instant,
    from the side's worktree in exactly :func:`sides.side_environment`; exit 0 is None, else
    its status and stderr."""
    tree = _side(tmp_path / "tree", _DOCKERFILE, _PROD_COMPOSE)
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(_RECORDER.format(python=sys.executable, status=status), encoding="utf-8")
    python.chmod(0o755)
    state = _state(tree, python)
    said = sides.deploy(state, "postgresql:///copy", "k" * 64, _MOMENT)
    assert said == (None if status == 0 else "exit 3:\nthe planted refusal")
    seen = json.loads((tree / "seen.json").read_text(encoding="utf-8"))
    assert seen["argv"] == [str(_PROBE), "2026-10-20T12:00:00-04:00"]
    assert seen["cwd"] == str(tree)
    assert seen["env"] == sides.side_environment(state, "postgresql:///copy", "k" * 64)


_PLANTED_STEP = """import datetime, json, sys, time
with open("seen.json", "w", encoding="utf-8") as seen:
    json.dump({"now": datetime.datetime.now().isoformat(),
               "today": datetime.date.today().isoformat(), "time": time.time(),
               "argv": sys.argv, "path": sys.path[0], "name": __name__, "file": __file__}, seen)
"""


def test_the_side_s_step_runs_as_the_image_runs_it_with_its_clock_pinned(tmp_path):
    """``side/deploy.py`` runs the tree's ``scripts/init_database.py`` as ``__main__``, its
    ``__file__`` and only argument its absolute path, ``sys.path[0]`` the tree's ``scripts/``
    (never the kit's ``side/``), and every wall clock frozen at the instant: local time noon
    in the zone the side's compose file pins."""
    tree = _side(tmp_path / "tree", _DOCKERFILE, _PROD_COMPOSE)
    (tree / "scripts").mkdir()
    step = tree / "scripts" / "init_database.py"
    step.write_text(_PLANTED_STEP, encoding="utf-8")
    assert sides.deploy(_state(tree, Path(sys.executable)), "postgresql:///copy", "k" * 64,
                       _MOMENT) is None
    assert json.loads((tree / "seen.json").read_text(encoding="utf-8")) == {
        "now": "2026-10-20T12:00:00", "today": "2026-10-20", "time": _MOMENT.timestamp(),
        "argv": [str(step)], "path": str(tree / "scripts"), "name": "__main__",
        "file": str(step)}


_DOTENV_STEP = """import json, os
from dotenv import load_dotenv
loaded = load_dotenv()
with open("seen.json", "w", encoding="utf-8") as seen:
    json.dump({"loaded": loaded, "url": os.environ.get("DATABASE_URL_APP")}, seen)
"""


def test_no_dotenv_file_reaches_a_side(tmp_path):
    """A ``.env`` in the side's tree, where ``load_dotenv()`` finds it from the step's own
    directory, sets nothing: the side reads only the environment built whole, as production,
    whose image holds no ``.env``."""
    tree = _side(tmp_path / "tree", _DOCKERFILE, _PROD_COMPOSE)
    (tree / ".env").write_text("DATABASE_URL_APP=postgresql:///elsewhere\n", encoding="utf-8")
    (tree / "scripts").mkdir()
    (tree / "scripts" / "init_database.py").write_text(_DOTENV_STEP, encoding="utf-8")
    assert sides.deploy(_state(tree, Path(sys.executable)), "postgresql:///copy", "k" * 64,
                       _MOMENT) is None
    assert json.loads((tree / "seen.json").read_text(encoding="utf-8")) == {
        "loaded": False, "url": None}


def test_the_pinned_instant_is_noon_of_the_dump_s_day_in_base_s_zone(tmp_path):
    """A dump made at 03:30 UTC on the 20th was made on the 19th in New York, the zone base's
    compose file pins: every side's clock is pinned at noon there on the 19th."""
    tree = _side(tmp_path / "tree", _DOCKERFILE, _PROD_COMPOSE)
    made = SimpleNamespace(
        made={"base": _state(tree, tmp_path / "python")},
        listing=bench.Listing(18, datetime(2026, 10, 20, 3, 30, tzinfo=ZoneInfo("UTC"))))
    assert sides.pinned_instant(made).isoformat() == "2026-10-19T12:00:00-04:00"


#: A planted ``app`` package: what ``side/boot.py`` imports, recording what it was given and
#: read, and when, into ``seen.json`` once the query is answered.
_PLANTED_APP = {
    "__init__.py": """import contextlib, datetime, sys
seen = {"imported": datetime.datetime.now().isoformat()}


class _App:
    @contextlib.contextmanager
    def app_context(self):
        seen["context"] = True
        yield
        seen["context"] = False


def create_app(**given):
    seen.update(given=given, now=datetime.datetime.now().isoformat(), path=sys.path[0])
    return _App()
""",
    "extensions.py": """import json
from app import seen


class _Answer:
    def scalar_one(self):
        with open("seen.json", "w", encoding="utf-8") as out:
            json.dump(seen, out)
        return 0


class _Session:
    def execute(self, statement):
        seen.update(query=statement, in_context=seen.get("context", False))
        return _Answer()


class _Db:
    session = _Session()

    def text(self, query):
        return "text: " + query


db = _Db()
""",
}


def test_the_boot_imports_the_side_s_app_and_boots_it_as_gunicorn_does_under_the_pin(tmp_path):
    """``side/boot.py`` imports the tree's own ``app`` (``sys.path[0]`` the tree, never the
    kit's ``side/``) with the clock already pinned, calls ``create_app()`` with nothing (the
    reference cache loaded eagerly, as ``run.py`` has it), and answers its one query inside
    the app's context."""
    tree = _side(tmp_path / "tree", _DOCKERFILE, _PROD_COMPOSE)
    (tree / "app").mkdir()
    for name, source in _PLANTED_APP.items():
        (tree / "app" / name).write_text(source, encoding="utf-8")
    assert sides.boot(_state(tree, Path(sys.executable)), "postgresql:///copy", "k" * 64,
                     _MOMENT) is None
    assert json.loads((tree / "seen.json").read_text(encoding="utf-8")) == {
        "imported": "2026-10-20T12:00:00", "given": {}, "now": "2026-10-20T12:00:00",
        "path": str(tree), "context": True,
        "query": "text: SELECT count(*) FROM salary.pay_stubs", "in_context": True}
