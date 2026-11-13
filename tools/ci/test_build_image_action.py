"""Controls for ``.github/actions/build-image``: the one recipe for building the app image.

The release (``docker-publish.yml``'s ``build-and-push``) builds the image it pushes and signs
by this action, ci.yml's ``image`` job builds by it the image it loads and checks
(:mod:`tools.ci.test_ci_image`), and updates.yml's ``scan`` job the image of dev it loads and
scans (:mod:`tools.ci.test_updates_workflow`), so the settings the three share have one home
(ruling plan#2752), the image job checks the build the release makes and the scan scans it.
The action's one step runs the pinned
``docker/build-push-action``, which runs only on a runner: why each setting is there, and what
of each was measured or read, is the action's header.
"""
from __future__ import annotations

import re

import yaml

from tools.ci import arcs
from tools.ci.test_ci_workflow import _every_step, _workflows

#: The action's directory, and the ``$/`` form GitHub loads it by from the running commit
#: (ruling plan#2375).
DIRECTORY = ".github/actions/build-image"
USES = f"$/{DIRECTORY}"
#: The file that defines the action.
ACTION_FILE = arcs.REPO / DIRECTORY / "action.yml"
#: The third-party build action it runs, by name.
BUILD_PUSH = "docker/build-push-action"


def _action() -> dict:
    """The action, read."""
    return yaml.safe_load(ACTION_FILE.read_text(encoding="utf-8"))


class TestTheAppImageHasOneRecipe:
    """The action's inputs, output and one step, read, and every caller of it and of the
    build action it runs."""

    def test_its_one_step_holds_every_setting_the_two_builds_share(self):
        """Four inputs, ``push``, ``load`` and ``tags`` required and ``labels`` empty unless
        given; one output, the digest the release signs, from its one step.  That step runs the
        build action pinned by its commit on the checkout's Dockerfile, with no layer cache, no
        builder of its own and the provenance record at ``mode=max`` asked for by name (rulings
        plan#2471, plan#2742, plan#2746): its ``with`` is read exactly, so a cache or another
        record added here fails it, and no input can add one."""
        action = _action()
        assert set(action) == {"name", "description", "inputs", "outputs", "runs"}
        assert {name: (spec["required"], spec.get("default"))
                for name, spec in action["inputs"].items()} == {
            "push": (True, None), "load": (True, None), "tags": (True, None),
            "labels": (False, "")}
        assert action["outputs"] == {"digest": {
            "description": "The digest of the image built, the one the release signs.",
            "value": "${{ steps.build.outputs.digest }}"}}
        assert action["runs"]["using"] == "composite"
        step, = action["runs"]["steps"]
        assert set(step) == {"name", "id", "uses", "with"}
        assert step["id"] == "build"
        assert re.fullmatch(rf"{BUILD_PUSH}@[0-9a-f]{{40}}", step["uses"])
        assert step["with"] == {
            "context": ".", "push": "${{ inputs.push }}", "load": "${{ inputs.load }}",
            "tags": "${{ inputs.tags }}", "labels": "${{ inputs.labels }}",
            "provenance": "mode=max"}
        assert not re.search(r"\bsecrets\b|\btoken\b", yaml.safe_dump(step))

    def test_its_three_callers_alone_call_it_and_no_step_else_names_its_action(self):
        """Its callers, in every workflow and every action of this repository's: the release's
        build step, ``id: build``, pushing with the metadata's tags and labels, the digest it
        hands back the one the signing step signs and the summary prints for pinning;
        ci.yml's ``image`` job (its call is :mod:`tools.ci.test_ci_image`'s to read); and
        updates.yml's ``scan`` job (its call is :mod:`tools.ci.test_updates_workflow`'s).  Each
        calls it by ``$/``, which GitHub loads from the running commit.  No other step of a
        workflow or an action names the build action, in any case, as GitHub reads an
        action's owner and name.  What each caller's job holds around the call (its runner
        label, its environment, a build its own ``run`` makes) is not read here: the action's
        header says what review holds."""
        callers, builds = {}, []
        for where, step in _every_step():
            uses = step.get("uses", "")
            if "build-image" in uses.lower():
                assert uses == USES, where
                callers[where] = step
            if uses.split("@")[0].lower() == BUILD_PUSH:
                builds.append(where)
        assert sorted(callers) == [("ci.yml", "image"), ("docker-publish.yml", "build-and-push"),
                                   ("updates.yml", "scan")]
        assert builds == [("build-image", 0)]
        assert callers[("docker-publish.yml", "build-and-push")] == {
            "name": "Build and push image", "id": "build", "uses": USES, "with": {
                "push": True, "load": False, "tags": "${{ steps.meta.outputs.tags }}",
                "labels": "${{ steps.meta.outputs.labels }}"}}
        publish = _workflows()["docker-publish.yml"]["jobs"]["build-and-push"]["steps"]
        readers = {step["name"]: step["env"]["DIGEST"] for step in publish
                   if "DIGEST" in step.get("env", {})}
        assert readers == dict.fromkeys(
            ("Sign image with Cosign (keyless OIDC)", "Emit image digest for downstream pinning"),
            "${{ steps.build.outputs.digest }}")
