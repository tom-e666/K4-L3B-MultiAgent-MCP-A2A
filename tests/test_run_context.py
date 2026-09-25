import json
from datetime import UTC, datetime, timedelta

import pytest

from student_agent.config import Settings
from student_agent.run_context import _fingerprint, verify_run_context


def test_package_rejects_evidence_from_previous_credential(tmp_path):
    (tmp_path / "traces").mkdir()
    now = datetime.now(UTC)
    context = {
        "key_fingerprint": _fingerprint("sk-team-current_key_12345678"),
        "credential_created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
    }
    (tmp_path / "traces" / ".run-context.json").write_text(json.dumps(context))
    (tmp_path / "traces" / "trace.jsonl").write_text(
        json.dumps({"occurred_at": (now - timedelta(minutes=1)).isoformat()}) + "\n"
    )
    settings = Settings("https://example.test", "sk-team-current_key_12345678",
                        "https://example.test/mcp", tmp_path)
    with pytest.raises(RuntimeError, match="Trace predates this Team API Key"):
        verify_run_context(settings, check_trace=True)


def test_resume_rejects_changed_key(tmp_path):
    (tmp_path / "traces").mkdir()
    context = {
        "key_fingerprint": _fingerprint("sk-team-previous_key_1234567"),
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    (tmp_path / "traces" / ".run-context.json").write_text(json.dumps(context))
    settings = Settings("https://example.test", "sk-team-current_key_12345678",
                        "https://example.test/mcp", tmp_path)
    with pytest.raises(RuntimeError, match="Team API Key changed"):
        verify_run_context(settings)
