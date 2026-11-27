"""CI publishing, smoke-test, and dependency invariants."""
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
PINNED_ACTION = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
PUSH_EVENT = "github.event_name == 'push'"

# Read from .python-version so a version bump has one place to change.
PRIMARY_PYTHON = (REPO / ".python-version").read_text(encoding="utf-8").strip()


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
        push = step.get("with", {}).get("push")
        if push in (None, "false", False):
            continue
        gated = PUSH_EVENT in str(push) or PUSH_EVENT in str(step.get("if", ""))
        assert gated, f"a publish is not gated on a push event: {step.get('name')}"


def test_a_publish_is_reached_only_after_the_image_has_been_run():
    steps = workflow()["jobs"]["build"]["steps"]

    smoke = [i for i, step in enumerate(steps)
             if "docker run" in str(step.get("run", ""))]
    assert smoke, "the image is never run, so nothing is tested"
    first_smoke = min(smoke)

    for i, step in enumerate(steps):
        publishes = (step.get("with") or {}).get("push") in (True, "true") or \
            str((step.get("with") or {}).get("push", "")).startswith("${{") and \
            "== 'push'" in str((step.get("with") or {}).get("push"))
        if publishes:
            assert i > first_smoke, (
                f"step {i} ({step.get('name')}) publishes before the smoke test at "
                f"{first_smoke}, so a broken image can reach latest"
            )


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
    assert PRIMARY_PYTHON in [str(value) for value in matrix["python-version"]]


def test_the_tested_python_is_the_one_the_project_ships():
    matrix = workflow()["jobs"]["test"]["strategy"]["matrix"]
    tested = {str(value) for value in matrix["python-version"]}
    tested |= {str(entry["python-version"]) for entry in matrix.get("include", [])}

    stated = (REPO / ".python-version").read_text(encoding="utf-8").strip()

    assert stated in tested, \
        f".python-version says {stated!r} but CI tests {sorted(tested)}"

    # Parse FROM so directives/comments cannot satisfy the version check.
    base_images = re.findall(r"^FROM\s+(\S+)", (REPO / "Dockerfile").read_text(encoding="utf-8"),
                             flags=re.MULTILINE | re.IGNORECASE)
    assert base_images, "the Dockerfile has no FROM line"
    minor = ".".join(stated.split(".")[:2])
    assert any(re.fullmatch(rf"python:{re.escape(minor)}(\.\d+)?-\w+", image)
               for image in base_images), \
        f"the Dockerfile builds from {base_images}, not {minor}"


def test_a_weekly_run_guards_the_floating_dependencies():
    schedule = triggers(workflow()).get("schedule")
    assert schedule and schedule[0].get("cron"), (
        "requirements.txt floats, so a weekly run on fresh dependencies is the "
        "only guard against a bad release"
    )


def test_duplicate_pull_request_runs_are_cancelled():
    concurrency = workflow()["concurrency"]
    assert "pull_request" in str(concurrency["cancel-in-progress"]), (
        "cancelling a run that is mid-publish is worse than waiting for it"
    )


def test_attestations_are_built_with_a_driver_that_supports_them():
    """provenance: mode=max requires the containerd driver."""
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
    steps = workflow()["jobs"]["build"]["steps"]
    smoke = [step for step in steps if "docker run" in str(step.get("run", ""))]
    assert smoke, "the image is built and pushed without ever being started"

    build = [step for step in steps
             if str(step.get("uses", "")).startswith("docker/build-push-action")]
    assert build, "the smoke test needs an image to run"
    with_ = build[0].get("with", {})

    # The image has no config.env; supply required placeholders.
    smoke_run = " ".join(str(step.get("run", "")) for step in smoke)
    smoke_env = {}
    for step in smoke:
        smoke_env.update(step.get("env", {}) or {})
    supplied = " ".join(smoke_env.values()) + smoke_run
    for required in ("API_ID", "API_HASH", "BOT_TOKEN", "BIN_CHANNEL", "DATABASE_URL"):
        assert required in supplied, (
            f"smoke test never sets {required}; Thunder.vars raises without it, "
            "failing a good image"
        )

    # Test the locally loaded image, not a registry pull.
    assert with_.get("load") in (True, "true"), (
        "the image under test must be loaded into the daemon, or `docker run` "
        "finds nothing before any pull"
    )
    assert with_.get("push") in (None, "false", False), \
        "the build that gets tested must not also be the one that publishes"

    # Interpolated step outputs must declare matching IDs.
    id_values = {step.get("id") for step in steps}
    referenced = set()
    for step in smoke:
        for value in list(step.get("env", {}).values()) + [str(step.get("run", ""))]:
            referenced |= set(re.findall(r"steps\.([A-Za-z0-9_-]+)\.", str(value)))
    assert referenced <= id_values, (
        f"interpolates steps.{sorted(referenced - id_values)} but no such id exists"
    )


def test_every_advertised_command_has_a_handler():
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


def test_the_archived_library_stays_out_of_the_requirements():
    def package_name(entry: str) -> str:
        # PEP 503 normalizes case and -/_/. separators.
        raw = re.split(r"[<>=!~;\[ (]", entry.strip(), maxsplit=1)[0]
        return re.sub(r"[-_.]+", "-", raw).lower()

    entries = [
        line.strip() for line in
        (REPO / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    names = {package_name(entry) for entry in entries}

    for retired in ("pyrofork", "tgcrypto-pyrofork"):
        assert retired not in names, f"{retired} is retired; wzgram replaces it"

    assert "wzgram" in names, "wzgram is what the `pyrogram` imports resolve through"


def test_the_crypto_backend_is_checked_in_the_image():
    steps = workflow()["jobs"]["build"]["steps"]
    runs = [step for step in steps if "docker run" in str(step.get("run", ""))]
    assert runs, "the image is never run, so nothing proves it starts"

    # Inspect executed run values, not commented-out text.
    commands = " ".join(str(step["run"]) for step in runs)
    imports = [line for line in commands.splitlines() if "import Thunder" in line]
    assert imports, "the smoke test no longer imports the app"

    assert any("warpcrypto" in line for line in imports), \
        "the smoke test does not import warpcrypto, so nothing proves the backend shipped"
    assert not any("tgcrypto" in line for line in imports), \
        "tgcrypto is no longer a dependency; importing it would fail the gate"


def test_the_dev_install_is_documented_the_way_ci_does_it():
    """PyYAML has no cp315 wheel; dev installs need PYYAML_FORCE_LIBYAML=0."""
    sets_the_var = re.compile(r"PYYAML_FORCE_LIBYAML\s*=\s*[\"']?0")

    for relative in ("requirements-dev.txt", "AGENTS.md", "README.md"):
        text = (REPO / relative).read_text(encoding="utf-8")
        assert sets_the_var.search(text), (
            f"{relative} no longer documents PYYAML_FORCE_LIBYAML=0, so a 3.15 "
            "dev install fails where CI succeeds"
        )
        commands = [line.strip() for line in text.splitlines()
                    if "install" in line and "requirements-dev" in line
                    and not line.lstrip().startswith("#")]
        for command in commands:
            assert sets_the_var.search(command), (
                f"{relative} documents `{command}` without the env var, which "
                "fails on 3.15; pip and uv forms both need it"
            )

    install = next(step for step in workflow()["jobs"]["test"]["steps"]
                   if step.get("name") == "Install")
    assert (install.get("env") or {}).get("PYYAML_FORCE_LIBYAML") == "0", (
        "the CI install step dropped the env var, so the job fails on the "
        "sdist build it avoids"
    )


def test_the_image_can_be_told_to_check_without_starting():
    """Only ENTRYPOINT lets `docker run image --check` reach thunder.sh."""
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^ENTRYPOINT\s+\[\s*\"bash\"\s*,\s*\"thunder\.sh\"\s*\]",
                     dockerfile, re.MULTILINE), (
        "no ENTRYPOINT, so `docker run image --check` cannot reach thunder.sh"
    )
    assert "CMD [\"bash\", \"thunder.sh\"]" not in dockerfile, (
        "a CMD-only start discards command-line arguments; use ENTRYPOINT"
    )


def test_smoke_step_overrides_the_entrypoint_for_the_import_probe():
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    if not re.search(r"^ENTRYPOINT\s+", dockerfile, re.MULTILINE | re.IGNORECASE):
        return  # no entrypoint: trailing args already reach the probe

    steps = workflow()["jobs"]["build"]["steps"]
    runs = [str(step["run"]) for step in steps if "docker run" in str(step.get("run", ""))]
    assert runs, "the image is never run, so there is no smoke test to check"
    command = " ".join(" ".join(run.split()) for run in runs)

    assert re.search(r"--entrypoint(?:=|\s+\S)", command), (
        "the smoke step must override the ENTRYPOINT (e.g. --entrypoint python); "
        "otherwise the import probe is never executed"
    )

    build = [step for step in steps
             if str(step.get("uses", "")).startswith("docker/build-push-action")]
    tags = str(build[0].get("with", {}).get("tags", "")).split()
    assert tags, "the build step declares no tag; cannot locate the trailing args"
    trailing = re.split(re.escape(tags[0]), command, maxsplit=1)[-1].strip()
    assert not re.match(r"python[\d.]*\s+-c\b", trailing), (
        "`python -c ...` after the image is appended to thunder.sh, not run by Python"
    )


def test_thunder_sh_forwards_its_arguments():
    script = (REPO / "thunder.sh").read_text(encoding="utf-8")
    assert '-m Thunder "$@"' in script, (
        "thunder.sh must forward args, or --check is dropped and the bot starts "
        "instead of validating"
    )
