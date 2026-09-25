"""Keep locally generated evidence tied to one team credential and active run."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import Settings


def _request(settings: Settings, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(
        settings.competition_api_url + path,
        data=body,
        headers={
            "Authorization": f"Bearer {settings.team_api_key}",
            "User-Agent": "Mozilla/5.0 day09-student-agent",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
        method="POST" if body is not None else "GET",
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Competition API request failed at {path}: {exc}") from exc


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _context_path(root: Path) -> Path:
    return root / "traces" / ".run-context.json"


def _verify_active_run(settings: Settings) -> None:
    request = Request(
        settings.competition_api_url + "/api/v2/runs/active/l3b/inputs",
        headers={
            "Authorization": f"Bearer {settings.team_api_key}",
            "User-Agent": "Mozilla/5.0 day09-student-agent",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise RuntimeError("Competition run is no longer active")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(
            "Competition run is no longer active; regenerate all evidence before packaging"
        ) from exc


def start_run(settings: Settings) -> None:
    """Start the run before any MCP call so all refs share its audit scope."""
    identity = _request(settings, "/api/v2/me")
    run = _request(settings, "/api/v2/runs", {"variant_id": "l3b"})
    if run.get("case_set_version") != "l3b-competition-v1":
        raise RuntimeError("Competition run uses an unexpected case-set version")
    context = {
        "team_code": identity["team_code"],
        "credential_id": identity["credential_id"],
        "credential_created_at": identity["created_at"],
        "key_fingerprint": _fingerprint(settings.team_api_key),
        "run_started_at": datetime.now(UTC).isoformat(),
        "expires_at": run["expires_at"],
        "case_set_version": run["case_set_version"],
    }
    target = _context_path(settings.root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(context, indent=2) + "\n", encoding="utf-8")


def verify_run_context(settings: Settings, *, check_trace: bool = False) -> None:
    target = _context_path(settings.root)
    if not target.exists():
        raise RuntimeError("Missing run context; rerun all cases with the current Team API Key")
    context = json.loads(target.read_text(encoding="utf-8"))
    if context["key_fingerprint"] != _fingerprint(settings.team_api_key):
        raise RuntimeError("Team API Key changed since evidence generation; rerun all cases")
    if _parse_time(context["expires_at"]) <= datetime.now(UTC):
        raise RuntimeError("Competition run expired; start a fresh run and regenerate evidence")
    if check_trace:
        trace_path = settings.root / "traces" / "trace.jsonl"
        with trace_path.open(encoding="utf-8") as stream:
            first = next((json.loads(line) for line in stream if line.strip()), None)
        if first is None or _parse_time(first["occurred_at"]) < _parse_time(
            context["credential_created_at"]
        ):
            raise RuntimeError("Trace predates this Team API Key; regenerate all evidence")
    _verify_active_run(settings)
