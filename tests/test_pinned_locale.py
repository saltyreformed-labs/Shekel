"""
Shekel Budget App -- The Pinned Process Locale

Tests that the application factory refuses to start under any ``LC_ALL`` but
the pinned one, and that the production image sets that one (plan step
``recurrence:R12``, ruling ``R-R92``).  A module of its own because the refusal
is the factory's, not a configuration class's (whose tests are the package
``tests/test_config/``), and that package's one module sat at pylint's
module-length ceiling when this one was written.
"""

from pathlib import Path

import pytest

import app as app_package
from app import PINNED_LOCALE, create_app
from app.config import DevConfig, ProdConfig

_DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


class TestTheFactoryRequiresThePinnedLocale:
    """``create_app`` refuses to start unless ``LC_ALL`` is ``C.UTF-8``.

    Plan step ``recurrence:R12``, ruling ``R-R92`` (extending ``R-R54``,
    closing finding F-15).  Month and weekday names from ``strftime`` and
    :mod:`calendar` follow the process locale; the image pins ``LC_ALL`` and
    the factory asserts it, so no process starts under a locale that could
    rename them.  ``./scripts/test.sh`` exports the pin, so the whole suite
    builds its app under it; these tests set the environment themselves so
    the refusal and its acceptance are graded apart from the wrapper.
    """

    @pytest.mark.parametrize(
        "config_name", ["development", "testing", "production"],
    )
    def test_an_unset_lc_all_is_refused_with_the_remedy(
        self, app, monkeypatch, config_name,
    ):
        """No ``LC_ALL`` at all -- a host shell's default -- is refused.

        Under EVERY configuration, production's included: the check precedes
        the configuration, so it answers before ``ProdConfig`` could refuse
        for a reason of its own.  The message is the operator's only
        instruction, so it is pinned: it must say what it saw, what it
        requires, and where the value is set -- including that a container
        built before the pin needs its image rebuilt.

        A regression in the check would BUILD the app, and the development
        and production classes resolve their database from the host's
        ``.env`` at import -- the developer's dev database, whose schemas and
        reference rows the development arm writes.  Measured: a mutation that
        disabled the check reached ``localhost:5432/shekel``.  So every class
        is pointed at this run's own test database first, and a failure here
        cannot touch anything outside the run's cluster.
        """
        for config_class in (DevConfig, ProdConfig):
            monkeypatch.setattr(
                config_class, "SQLALCHEMY_DATABASE_URI",
                app.config["SQLALCHEMY_DATABASE_URI"],
            )
        monkeypatch.delenv("LC_ALL", raising=False)
        with pytest.raises(ValueError) as exc:
            create_app(config_name)
        message = str(exc.value)
        assert message.startswith("LC_ALL is unset; ")
        assert f"requires LC_ALL={PINNED_LOCALE}" in message
        assert "R-R92" in message
        assert "The Dockerfile sets it" in message
        assert "must be rebuilt" in message
        assert "./scripts/test.sh" in message
        assert f"add LC_ALL={PINNED_LOCALE} to .env" in message
        assert ".env.example" in message

    @pytest.mark.parametrize(
        "wrong",
        [
            # A developer host's LANG value, set as LC_ALL.
            "en_US.UTF-8",
            # A near-miss spelling of the pin: the comparison is exact.
            "C.utf8",
            # Set but empty, which the C library reads as unset.
            "",
        ],
    )
    def test_any_other_value_is_refused_and_named(self, monkeypatch, wrong):
        """A value that is not the pin is refused, and quoted back."""
        monkeypatch.setenv("LC_ALL", wrong)
        with pytest.raises(ValueError) as exc:
            create_app("testing")
        assert str(exc.value).startswith(f"LC_ALL is {wrong!r}; ")

    def test_the_refusal_comes_before_anything_is_built(self, monkeypatch):
        """Nothing is constructed before the locale is checked.

        The factory's dev/test arm creates schemas and seeds reference rows,
        so a check placed later would refuse after side effects.  The Flask
        object is the factory's first construction; it must never be made.
        """
        constructed = []
        monkeypatch.setattr(
            app_package, "Flask",
            lambda *args, **kwargs: constructed.append(args),
        )
        monkeypatch.delenv("LC_ALL", raising=False)
        with pytest.raises(ValueError):
            create_app("testing")
        assert not constructed

    def test_the_pinned_locale_builds_the_app(self, monkeypatch):
        """Under the pin the factory builds, so the refusals above are the
        locale's and not a factory that refuses everything."""
        monkeypatch.setenv("LC_ALL", PINNED_LOCALE)
        built = create_app("testing")
        assert built.config["TESTING"] is True


class TestTheImageSetsThePin:
    """The production image's runtime stage sets ``LC_ALL`` to the pin.

    The factory's refusal makes a wrong or missing Dockerfile line fail every
    start -- but nothing in CI builds the image and starts the app, so without
    this test the first start to fail would be a production deploy (the
    entrypoint's first script calls ``create_app``, the container crash-loops,
    and the deploy rolls back).  It compares the Dockerfile's value with
    :data:`app.PINNED_LOCALE`.  Ruling ``recurrence:R-R92`` names ``create_app``,
    ``scripts/test.sh`` and a host ``.env``; the Dockerfile's ``ENV`` and
    ``.env.example`` are as built at plan step ``recurrence:R12``.
    """

    def test_the_runtime_stage_sets_lc_all_to_the_pin(self):
        """``ENV LC_ALL=<pin>`` sits after the LAST ``FROM``, the runtime stage.

        A line in the builder stage would not reach the image that runs.
        """
        text = _DOCKERFILE.read_text(encoding="utf-8")
        runtime_stage = text.rsplit("\nFROM ", 1)[1]
        assert f"\nENV LC_ALL={PINNED_LOCALE}\n" in runtime_stage
