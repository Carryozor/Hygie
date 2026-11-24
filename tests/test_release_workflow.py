"""Static guards on the tag release pipeline (ci.yml / test.yml / Dockerfile).

The workflows cannot run in the test suite, so these tests read the YAML and
pin the properties that protect production: a tag is gated by the *full* suite,
a pre-release tag never moves ``:latest`` (Arcane auto-updates it on prod), and
the Docker image builds the frontend with the same Node major as CI tests it.
"""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    # PyYAML (YAML 1.1) parses the bare key `on` as the boolean True.
    return workflow.get("on", workflow.get(True))


def _step_text(job: dict) -> str:
    return yaml.safe_dump(job.get("steps", []))


def _as_list(needs) -> list:
    return [needs] if isinstance(needs, str) else list(needs)


def test_test_workflow_is_callable():
    assert "workflow_call" in _triggers(_load("test.yml"))


def test_tag_gate_reuses_full_suite():
    ci = _load("ci.yml")
    assert ci["jobs"]["gate"]["uses"] == "./.github/workflows/test.yml"
    assert "test" not in ci["jobs"], "the thin pytest-only job must be gone"


def test_docker_job_needs_gate():
    assert "gate" in _as_list(_load("ci.yml")["jobs"]["docker"]["needs"])


def test_release_still_waits_for_docker():
    assert "docker" in _as_list(_load("ci.yml")["jobs"]["release"]["needs"])


def test_rc_tag_never_moves_latest():
    steps = _load("ci.yml")["jobs"]["docker"]["steps"]
    tags = [s["with"]["tags"] for s in steps if "with" in s and "tags" in s["with"]]
    assert len(tags) == 1
    latest_lines = [ln for ln in tags[0].splitlines() if ":latest" in ln]
    assert latest_lines, "the :latest tag must still be pushed for final tags"
    for ln in latest_lines:
        # :latest must sit inside an expression negating a '-' in the tag name.
        assert "!contains(github.ref_name, '-')" in ln


def test_prerelease_flag_for_rc_tags():
    text = _step_text(_load("ci.yml")["jobs"]["release"])
    assert "--prerelease" in text
    # The flag must be conditional on a '-' in the version, not unconditional.
    assert re.search(r"\*-\*\)", text), "expected a `*-*)` case guard"


def test_node_major_matches_between_ci_and_dockerfile():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    docker_major = re.search(r"^FROM node:(\d+)", dockerfile, re.M).group(1)
    steps = _load("test.yml")["jobs"]["frontend"]["steps"]
    node_versions = {
        str(s["with"]["node-version"])
        for s in steps
        if "setup-node" in s.get("uses", "")
    }
    assert node_versions == {docker_major}
    assert docker_major == "24"


def test_dockerfile_fails_build_without_mysqldump():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^RUN .*command -v mysqldump", dockerfile, re.M)
