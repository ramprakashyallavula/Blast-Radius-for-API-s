"""Run scripts/github-action.sh against a real git repo that simulates a pull request.

`gh` (the GitHub CLI the script uses to comment) is replaced by a stub that logs
what it was asked to do, so nothing talks to GitHub.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "github-action.sh"
EXAMPLES = ROOT / "examples"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash and git"
)

GH_STUB = """#!/usr/bin/env bash
# Log the call. For uploads (-F body=@file), log the file's contents too.
echo "gh $*" >> "$GH_LOG"
for arg in "$@"; do
  case "$arg" in body=@*) cat "${arg#body=@}" >> "$GH_LOG" ;; esac
done
# Listing comments: pretend one of ours exists if EXISTING_COMMENT_ID is set.
if [ "$2" != "-X" ] && [ -n "${EXISTING_COMMENT_ID:-}" ]; then echo "$EXISTING_COMMENT_ID"; fi
"""


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def pr(tmp_path):
    """A clone whose `origin/main` has the v1 spec, with v2 in the working tree (the PR)."""
    origin = tmp_path / "origin"
    (origin / "api").mkdir(parents=True)
    shutil.copy(EXAMPLES / "orders-service" / "openapi.v1.yaml", origin / "api" / "openapi.yaml")
    shutil.copytree(EXAMPLES / "consumers", origin / "consumers")
    git(origin, "init", "-q", "-b", "main")
    git(origin, "add", "-A")
    git(origin, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "v1")

    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(origin), str(work))
    shutil.copy(EXAMPLES / "orders-service" / "openapi.v2.yaml", work / "api" / "openapi.yaml")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(GH_STUB)
    (bin_dir / "gh").chmod(0o755)

    log = tmp_path / "gh.log"
    log.touch()
    env = {
        **os.environ,
        # The stub gh first, then the virtualenv that has `blastradius` installed.
        "PATH": os.pathsep.join(
            [str(bin_dir), str(Path(sys.executable).parent), os.environ["PATH"]]
        ),
        "GH_LOG": str(log),
        "SPEC": "api/openapi.yaml",
        "CONSUMERS_DIR": "consumers",
        "BASE_REF": "main",
        "PR_NUMBER": "7",
        "GITHUB_REPOSITORY": "me/orders",
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
    }
    return work, env, log, tmp_path


def run(work, env, **overrides):
    return subprocess.run(
        ["bash", str(SCRIPT)], cwd=work, env={**env, **overrides}, capture_output=True, text=True
    )


def test_comments_on_the_pr_and_fails_the_check(pr):
    work, env, log, tmp = pr

    result = run(work, env)

    assert result.returncode == 1, result.stderr
    calls = log.read_text()
    assert "gh api -X POST repos/me/orders/issues/7/comments" in calls
    assert "<!-- blastradius-report -->" in calls
    assert "3 consumers will break" in calls
    assert "exit-code=1" in (tmp / "output").read_text()
    assert "BlastRadius report" in (tmp / "summary.md").read_text()


def test_updates_its_previous_comment_instead_of_adding_another(pr):
    work, env, log, _ = pr

    run(work, env, EXISTING_COMMENT_ID="555")

    calls = log.read_text()
    assert "gh api -X PATCH repos/me/orders/issues/comments/555" in calls
    assert "-X POST" not in calls


def test_fail_on_never_still_comments_but_passes(pr):
    work, env, log, _ = pr

    result = run(work, env, FAIL_ON="never")

    assert result.returncode == 0
    assert "-X POST" in log.read_text()


def test_comment_false_does_not_call_github(pr):
    work, env, log, _ = pr

    run(work, env, COMMENT="false")

    assert log.read_text() == ""


def test_local_consumer_folders(pr):
    work, env, log, _ = pr
    env = {k: v for k, v in env.items() if k != "CONSUMERS_DIR"}

    result = run(work, env, CONSUMERS="consumers/billing-service\n  consumers/web-frontend  \n")

    assert result.returncode == 1
    calls = log.read_text()
    assert "billing-service" in calls and "web-frontend" in calls
    assert "shipping-service" not in calls


@pytest.mark.parametrize(
    "setup, message",
    [
        ("unchanged", "unchanged in this pull request"),
        ("not_a_pr", "Not a pull request"),
        ("new_file", "doesn't exist on main yet"),
    ],
)
def test_skips_when_there_is_nothing_to_compare(pr, setup, message):
    work, env, log, _ = pr
    overrides = {}
    if setup == "unchanged":
        git(work, "checkout", "--", "api/openapi.yaml")
    elif setup == "not_a_pr":
        overrides["BASE_REF"] = ""
    else:
        overrides["SPEC"] = "api/new-service.yaml"

    result = run(work, env, **overrides)

    assert result.returncode == 0
    assert message in result.stdout
    assert log.read_text() == ""


def test_deleted_spec_fails(pr):
    work, env, _, _ = pr
    (work / "api" / "openapi.yaml").unlink()

    result = run(work, env)

    assert result.returncode == 1
    assert "was deleted" in result.stdout
