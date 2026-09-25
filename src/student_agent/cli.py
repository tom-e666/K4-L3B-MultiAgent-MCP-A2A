from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .cases import load_case_set
from .config import Settings
from .contracts import Contracts
from .mcp_gateway import connect_gateway
from .run_context import start_run, verify_run_context
from .submission import package_submission, validate_artifacts
from .trace import TraceWriter
from .workflow import solve_case


def _root(value: str) -> Path:
    return Path(value).resolve()


def _error_summary(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(_error_summary(child) for child in exc.exceptions)
    return f"{type(exc).__name__}: {exc}"


async def _show_tools(root: Path) -> None:
    settings = Settings.load(root)
    contracts = Contracts(root / "contracts" / "schemas")
    async with connect_gateway(settings.mcp_endpoint, settings.team_api_key, contracts) as gateway:
        for tool in await gateway.list_tools():
            print(tool)


async def _run(root: Path, *, resume: bool = False, case_id: str | None = None) -> None:
    settings = Settings.load(root)
    case_set = load_case_set(root)
    if case_id is not None and (not resume or case_id not in case_set.case_ids):
        raise ValueError("--case-id requires --resume and an ID from case-set.json")
    if resume:
        verify_run_context(settings)
    else:
        start_run(settings)
    contracts = Contracts(root / "contracts" / "schemas")
    output_root = root / "outputs"
    trace_path = root / "traces" / "trace.jsonl"
    output_root.mkdir(parents=True, exist_ok=True)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    events_by_case: dict[str, list[str]] = {}
    if resume and trace_path.exists():
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                event_case_id = json.loads(line)["case_id"]
                events_by_case.setdefault(event_case_id, []).append(line + "\n")
    if not resume:
        for stale in output_root.glob("*.json"):
            stale.unlink()
        trace_path.unlink(missing_ok=True)
    pending: list[str] = []
    selected_case_id = case_id
    for case_id in case_set.case_ids:
        if selected_case_id is not None and case_id != selected_case_id:
            continue
        if selected_case_id == case_id:
            pending.append(case_id)
            continue
        target = output_root / f"{case_id}.json"
        if not resume or not target.exists():
            pending.append(case_id)
            continue
        try:
            output = json.loads(target.read_text(encoding="utf-8"))
            checks = [json.loads(line) for line in events_by_case.get(case_id, [])
                      if json.loads(line)["event_type"] == "verification_completed"]
            if (output["entity_resolution"]["status"] != "resolved"
                    or output["assessment"]["primary_issue"] == "insufficient_evidence"
                    or not checks or checks[-1].get("decision_code") != "CHECKED"):
                pending.append(case_id)
        except (ValueError, KeyError, TypeError):
            pending.append(case_id)
    index = 0
    reconnects = 0
    while index < len(pending):
        try:
            async with connect_gateway(
                settings.mcp_endpoint, settings.team_api_key, contracts
            ) as gateway:
                if not await gateway.list_tools():
                    raise RuntimeError("MCP Gateway returned no tools")
                while index < len(pending):
                    case_id = pending[index]
                    case = case_set.cases[case_id]
                    staged = trace_path.with_name(f".{case_id}.partial.jsonl")
                    staged.unlink(missing_ok=True)
                    trace = TraceWriter(staged, contracts)
                    trace.emit(case_id=case_id, event_type="case_received", actor="coordinator")
                    output = await solve_case(case, gateway, trace)
                    contracts.validate_output(output, f"outputs/{case_id}.json")
                    if output.get("case_id") != case_id:
                        raise ValueError(f"solver returned a mismatched case_id for {case_id}")
                    trace.emit(case_id=case_id, event_type="case_finalized",
                               actor="coordinator")
                    target = output_root / f"{case_id}.json"
                    temporary = target.with_suffix(".json.tmp")
                    temporary.write_text(
                        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    temporary.replace(target)
                    events_by_case[case_id] = staged.read_text(
                        encoding="utf-8"
                    ).splitlines(keepends=True)
                    trace_temporary = trace_path.with_suffix(".jsonl.tmp")
                    trace_temporary.write_text("".join(
                        line for item in case_set.case_ids
                        for line in events_by_case.get(item, [])
                    ), encoding="utf-8")
                    trace_temporary.replace(trace_path)
                    staged.unlink()
                    index += 1
                    reconnects = 0
                    await asyncio.sleep(1)
        except Exception as exc:
            if index >= len(pending):
                break
            reconnects += 1
            print(
                f"Retry {reconnects}/6 for {pending[index]} after {_error_summary(exc)}",
                file=sys.stderr,
                flush=True,
            )
            if reconnects > 6:
                raise RuntimeError(
                    f"case failed repeatedly near {pending[index]}"
                ) from exc
            await asyncio.sleep(min(5 * 2 ** (reconnects - 1), 60))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Day09 L3B student workflow")
    result.add_argument("--root", default=".", help="repository root (default: current directory)")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-inputs", help="validate case-set.json and all 100 inputs")
    commands.add_parser("mcp-tools", help="authenticate and list discovered MCP tools")
    run = commands.add_parser("run", help="run the implemented workflow for all cases")
    run.add_argument("--resume", action="store_true", help="retry incomplete cases")
    run.add_argument("--case-id", help="rerun one case with --resume")
    commands.add_parser("validate", help="validate outputs and observable trace")
    package = commands.add_parser("package", help="validate and build the submission ZIP")
    package.add_argument("--output", default="dist/submission.zip")
    return result


def main() -> None:
    args = parser().parse_args()
    root = _root(args.root)
    try:
        if args.command == "validate-inputs":
            case_set = load_case_set(root)
            print(
                f"OK: {case_set.variant_id} / {case_set.version} / "
                f"{len(case_set.case_ids)} cases"
            )
        elif args.command == "mcp-tools":
            asyncio.run(_show_tools(root))
        elif args.command == "run":
            asyncio.run(_run(root, resume=args.resume, case_id=args.case_id))
        elif args.command == "validate":
            case_set = load_case_set(root)
            contracts = Contracts(root / "contracts" / "schemas")
            _, trace = validate_artifacts(root, case_set, contracts)
            print(f"OK: {len(case_set.case_ids)} outputs / {len(trace)} trace events")
        elif args.command == "package":
            verify_run_context(Settings.load(root), check_trace=True)
            destination = package_submission(root, root / args.output)
            print(f"OK: {destination}")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
