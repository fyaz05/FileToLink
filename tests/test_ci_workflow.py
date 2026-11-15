"""The CI workflow must keep the properties its gate depends on.

Each check below stands for a failure this project has had or could have had:

* a build job without ``needs: [test]`` publishes an image from a red main --
  which is exactly what dockerize.yml did before the two files were merged;
* a login step reachable from a pull request asks for ``secrets.DOCKER_TOKEN``
  on fork PRs, which never receive secrets, so every fork PR fails;
* an action referenced by a tag can be repointed at new code by whoever owns it;
* with a floating requirements.txt and no weekly run, a bad upstream release is
  found by a user rather than by CI.

None of this is visible to a code test, so it is pinned here instead.
"""
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
PINNED_ACTION = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
PUSH_EVENT = "github.event_name == 'push'"


def load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def workflow():
    return load(WORKFLOWS / "tests.yml")


def triggers(doc):
    # PyYAML implements YAML 1.1, where the bare key `on` parses as boolean True.
    return doc.get("on", doc.get(True, {})) or {}


def uses_of(doc, job, prefix):
    return [step for step in doc["jobs"][job]["steps"]
            if str(step.get("uses", "")).startswith(prefix)]


def test_no_other_workflow_can_publish_an_image():
    """A second publisher is how main went red and shipped anyway.

    Any workflow other than the gated one that builds or pushes an image is a
    regression, whether it is dockerize.yml returning or a new one appearing.
    """
    others = [entry for entry in WORKFLOWS.iterdir() if entry.name != "tests.yml"]
    publishers = [
        entry.name for entry in others
        if any(marker in entry.read_text(encoding="utf-8")
               for marker in ("docker/build-push-action", "docker push"))
    ]
    assert publishers == [], f"{publishers} can publish an image outside the gated workflow"


def test_nothing_publishes_before_the_tests_pass():
    doc = workflow()
    needs = doc["jobs"]["build"].get("needs") or []
    needs = [needs] if isinstance(needs, str) else needs
    assert "test" in needs, "the image must not build unless the tests passed"

    logins = uses_of(doc, "build", "docker/login-action")
    assert logins, "pushing needs a login step"
    for step in logins:
        assert PUSH_EVENT in step.get("if", ""), "no secret may be requested on a pull request"

    builds = uses_of(doc, "build", "docker/build-push-action")
    assert builds, "the build job must build something"
    for step in builds:
        assert PUSH_EVENT in str(step["with"]["push"]), "only a push event may publish"


def test_actions_are_pinned_to_commits():
    text = (WORKFLOWS / "tests.yml").read_text(encoding="utf-8")
    for reference in re.findall(r"uses:\s*(\S+)", text):
        assert PINNED_ACTION.match(reference), f"{reference} can be repointed; pin a commit SHA"


def test_jobs_are_bounded_and_read_only():
    doc = workflow()
    assert doc["permissions"] == {"contents": "read"}
    for name, job in doc["jobs"].items():
        assert "github.repository ==" in job.get("if", ""), f"{name} would also run in forks"
        assert job.get("timeout-minutes"), f"{name} can hang until the 6-hour limit"


def test_the_suite_runs_on_both_operating_systems():
    matrix = workflow()["jobs"]["test"]["strategy"]["matrix"]
    assert any(os_name.startswith("ubuntu") for os_name in matrix["os"]), matrix["os"]
    assert any(os_name.startswith("windows") for os_name in matrix["os"]), (
        "the uvloop marker and update.py's chmod fallback only run on Windows"
    )
    assert "3.13" in [str(value) for value in matrix["python-version"]]


def test_the_documented_python_range_is_actually_tested():
    """README says 3.13+; a floating dependency can quietly drop the newer one."""
    matrix = workflow()["jobs"]["test"]["strategy"]["matrix"]
    tested = {str(value) for value in matrix["python-version"]}
    tested |= {str(entry["python-version"]) for entry in matrix.get("include", [])}
    assert "3.14" in tested, (
        "3.14 was deferred only until uvloop shipped cp314 wheels, which it now "
        "does; the range is documented, so it is tested"
    )


def test_a_weekly_run_guards_the_floating_dependencies():
    schedule = triggers(workflow()).get("schedule")
    assert schedule and schedule[0].get("cron"), (
        "requirements.txt floats on purpose, so a weekly run against fresh "
        "dependencies is the only thing that catches a bad release"
    )


def test_duplicate_pull_request_runs_are_cancelled():
    concurrency = workflow()["concurrency"]
    assert "pull_request" in str(concurrency["cancel-in-progress"]), (
        "cancelling a run that is mid-publish is worse than waiting for it"
    )


def test_attestations_are_built_with_a_driver_that_supports_them():
    """`provenance: mode=max` needs the containerd store on the docker driver.

    The action passes --attest whenever the input is set, and the default docker
    driver cannot write an image index to the classic image store, so the build
    fails outright on a stock ubuntu runner. A container-driver builder supports
    attestations regardless of the daemon's store.
    """
    doc = workflow()
    steps = doc["jobs"]["build"]["steps"]
    names = [str(step.get("uses", "")) for step in steps]

    asserted = [step for step in steps
                if step.get("with", {}).get("provenance") not in (None, "false", False)]
    if not asserted:
        return  # opted out of attestations; nothing to support

    assert any(name.startswith("docker/setup-buildx-action@") for name in names), (
        "an explicit provenance attestation requires a container-driver builder"
    )


def test_the_built_image_is_actually_run():
    """A build that succeeds is not proof the image starts.

    A missing dependency or a broken import still produces a valid image, so the
    published artifact is executed once before it is trusted.
    """
    steps = workflow()["jobs"]["build"]["steps"]
    smoke = [step for step in steps if "docker run" in str(step.get("run", ""))]
    assert smoke, "the image is built and pushed without ever being started"

    build = [step for step in steps
             if str(step.get("uses", "")).startswith("docker/build-push-action")]
    assert build, "the smoke test needs an image to run"
    with_ = build[0].get("with", {})

    # Thunder.vars validates its configuration at import and raises without it,
    # and the image ships no config.env (deliberately). The smoke test must
    # therefore supply placeholders, or it fails on a perfectly good image.
    smoke_run = " ".join(str(step.get("run", "")) for step in smoke)
    smoke_env = {}
    for step in smoke:
        smoke_env.update(step.get("env", {}) or {})
    supplied = " ".join(smoke_env.values()) + smoke_run
    for required in ("API_ID", "API_HASH", "BOT_TOKEN", "BIN_CHANNEL", "DATABASE_URL"):
        assert required in supplied, (
            f"the smoke test imports the entry point but never sets {required}; "
            "Thunder.vars raises without it and the step would fail on a good image"
        )

    # The smoke test can only run an image that is somewhere: loaded into the
    # local daemon, or pushed to the registry. Pushing covers only the events it
    # is true for, so a conditional push needs a load for the other events --
    # without it the container-driver build leaves nothing to run.
    push = str(with_.get("push", ""))
    load = str(with_.get("load", ""))
    pushing_is_conditional = "github.event_name" in push

    assert push in ("true", "false") or pushing_is_conditional, f"unexpected push value: {push!r}"
    if with_.get("push") in (None, "false", False) or pushing_is_conditional:
        assert "load" in with_, "no push and no load: the smoke test would find no image"
        assert load.strip() == "true" or "!= 'push'" in load, (
            f"load must cover the non-push events, got {load!r}"
        )

    # Any step output the smoke test interpolates must come from a step that
    # declares the matching id, or it silently expands to nothing.
    id_values = {step.get("id") for step in steps}
    referenced = set()
    for step in smoke:
        for value in list(step.get("env", {}).values()) + [str(step.get("run", ""))]:
            referenced |= set(re.findall(r"steps\.([A-Za-z0-9_-]+)\.", str(value)))
    assert referenced <= id_values, (
        f"interpolates steps.{sorted(referenced - id_values)} but no such id exists"
    )


def test_every_advertised_command_has_a_handler():
    """Guards the invariant test_plugin_registration.py checks end to end.

    Cheap and structural: it fails at review time rather than after a boot.
    """
    import ast

    commands_src = (REPO / "Thunder" / "utils" / "commands.py").read_text(encoding="utf-8")
    advertised = set()
    for node in ast.walk(ast.parse(commands_src)):
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "command_descriptions" for t in node.targets):
            advertised = {k.value for k in node.value.keys}
    assert advertised, "the command table could not be read"

    plugins = (REPO / "Thunder" / "bot" / "plugins").glob("*.py")
    registered = set()
    for plugin in plugins:
        for match in re.findall(r'command\(\s*"([^"]+)"', plugin.read_text(encoding="utf-8")):
            registered.add(match)

    assert advertised <= registered, \
        f"advertised to Telegram but never registered: {sorted(advertised - registered)}"


def test_dependabot_only_tracks_actions():
    doc = load(REPO / ".github" / "dependabot.yml")
    ecosystems = {update["package-ecosystem"] for update in doc["updates"]}
    assert ecosystems == {"github-actions"}, (
        "app dependencies float by policy, so only the pinned actions need updates"
    )
