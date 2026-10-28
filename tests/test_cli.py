import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from blastradius import __version__
from blastradius.cli import app

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
V1 = str(EXAMPLES / "orders-service" / "openapi.v1.yaml")
V2 = str(EXAMPLES / "orders-service" / "openapi.v2.yaml")
CONSUMERS = str(EXAMPLES / "consumers")

runner = CliRunner()


def run(*args):
    return runner.invoke(app, list(args))


# --- check -----------------------------------------------------------------


def test_check_fails_when_a_consumer_will_break():
    result = run("check", V1, V2, "-C", CONSUMERS)

    assert result.exit_code == 1
    assert "3 consumers will break" in result.output
    assert "billing/invoices.py:11" in result.output


def test_check_passes_when_nothing_changed():
    result = run("check", V1, V1, "-C", CONSUMERS)

    assert result.exit_code == 0
    assert "No consumer is affected" in result.output


def test_breaking_changes_nobody_uses_pass_by_default(tmp_path):
    # A consumer that calls the changed endpoint but only reads an unchanged field.
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "daily.py").write_text(
        'def ids():\n    r = requests.get(f"{API}/orders/{oid}")\n    return r.json()["id"]\n'
    )

    result = run("check", V1, V2, "-c", str(tmp_path / "reports"))
    assert result.exit_code == 0
    assert "No consumer is affected" in result.output

    # The stricter policy still fails on the spec's breaking changes.
    assert (
        run("check", V1, V2, "-c", str(tmp_path / "reports"), "--fail-on", "breaking").exit_code
        == 1
    )


def test_fail_on_never_always_passes():
    assert run("check", V1, V2, "-C", CONSUMERS, "--fail-on", "never").exit_code == 0


@pytest.mark.parametrize("fail_on, expected", [("breaking", 1), ("impacted", 0), ("never", 0)])
def test_fail_on_with_no_consumers(fail_on, expected):
    result = run("check", V1, V2, "--fail-on", fail_on)

    assert result.exit_code == expected


def test_check_markdown_to_file(tmp_path):
    out = tmp_path / "report.md"

    result = run("check", V1, V2, "-C", CONSUMERS, "-f", "markdown", "-o", str(out))

    text = out.read_text()
    assert result.exit_code == 1
    assert text.startswith("## 💥 BlastRadius report")
    assert "| `billing-service` | `billing/invoices.py:11` | 🔴 will break |" in text
    assert "<summary>8 more changes with no affected consumers" in text


def test_check_json():
    result = run("check", V1, V2, "-C", CONSUMERS, "-f", "json")

    data = json.loads(result.output)
    assert data["summary"]["will_break"] == ["billing-service", "shipping-service", "web-frontend"]
    assert data["summary"]["breaking_changes"] == 11
    first = data["changes"][0]
    assert first["worst_impact"] == "will_break"
    assert first["impacts"][0]["impact"] == "will_break"


# --- errors ----------------------------------------------------------------


def test_missing_spec_is_exit_code_2():
    result = run("check", "nope.yaml", V2)

    assert result.exit_code == 2
    assert "could not read" in result.output


def test_missing_consumer_folder_is_exit_code_2():
    result = run("check", V1, V2, "-c", "does/not/exist")

    assert result.exit_code == 2
    assert "not a folder" in result.output


# --- diff and scan ---------------------------------------------------------


def test_diff():
    result = run("diff", V1, V2)

    assert result.exit_code == 1
    assert "Endpoint was removed" in result.output
    assert "5 safe changes hidden" in result.output


def test_diff_all_and_json():
    assert "New endpoint" in run("diff", V1, V2, "--all").output

    changes = json.loads(run("diff", V1, V2, "-f", "json").output)
    assert len(changes) == 19


def test_scan():
    result = run("scan", "-C", CONSUMERS)

    assert result.exit_code == 0
    assert "7 call sites in 3 consumers" in result.output


def test_version():
    assert run("--version").output.strip() == f"blastradius {__version__}"
