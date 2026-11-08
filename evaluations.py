import argparse
import json
import threading
import time
from pathlib import Path
from collections.abc import Callable
from typing import TypeVar

import pandas as pd

from build_base_db import DB_PATH, build_base_db
from injector import OUTPUT_DIR
from src.llm_client import load_agent_limits
from src.system_multi import run_multi_agent_team
from src.system_oneshot import run_oneshot_sql
from src.system_react import run_single_react_agent


PROJECT_ROOT = Path(__file__).resolve().parent
LABELS_PATH = OUTPUT_DIR / "labels.json"
RESULTS_PATH = PROJECT_ROOT / "evaluations_results.csv"
NUM_PARTS = 3
ERROR_COLUMNS = ("oneshot_error", "react_error", "multi_agent_error")
REACT_COLUMNS = (
    "react_answer",
    "react_steps",
    "react_query_count",
    "react_max_steps",
    "react_error",
)
MULTI_AGENT_COLUMNS = (
    "multi_agent_answer",
    "multi_agent_audit_rounds",
    "multi_agent_query_count",
    "multi_agent_analyst_steps",
    "multi_agent_error",
)
# Errors from a provider outage or quota rather than from the model's answer.
# rerun_agents retries these; other errors are kept as the system's result.
TRANSIENT_ERRORS = (
    "RateLimitError",
    "APITimeoutError",
    "APIConnectionError",
    "InternalServerError",
)
T = TypeVar("T")

# Ten unique cases per benchmark group: wrong discounts, shipping delays,
# and clean controls.
BENCHMARK_SCENARIO_IDS = (
    tuple(range(1, 11))
    + tuple(range(11, 21))
    + tuple(range(71, 81))
)


def _run_with_progress(label: str, operation: Callable[[], T]) -> T:
    started = time.monotonic()
    stop_heartbeat = threading.Event()

    def heartbeat() -> None:
        while not stop_heartbeat.wait(5):
            elapsed = int(time.monotonic() - started)
            print(
                f"    {label}: waiting for API response "
                f"({elapsed}s elapsed)...",
                flush=True,
            )

    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()
    try:
        return operation()
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join()


def ensure_scenarios() -> None:
    if not DB_PATH.exists():
        print("Base database not found. Building it from archive CSV files...")
        build_base_db()
    scenario_databases = list(OUTPUT_DIR.glob("scenario_*.db"))
    if not LABELS_PATH.exists() or not scenario_databases:
        print("Scenario databases not found. Generating scenarios...")
        from injector import generate_scenarios

        generate_scenarios()
        return

    with LABELS_PATH.open(encoding="utf-8") as labels_file:
        scenario_ids = {int(item["scenario_id"]) for item in json.load(labels_file)}
    expected_ids = set(range(1, 81))
    missing = [
        scenario_id
        for scenario_id in expected_ids
        if not (OUTPUT_DIR / f"scenario_{scenario_id:02d}.db").exists()
    ]
    if scenario_ids != expected_ids or missing:
        print("Scenario set is not the required 80-scenario dataset. Regenerating...")
        from injector import generate_scenarios

        generate_scenarios()


def _load_cases() -> list[dict]:
    with LABELS_PATH.open(encoding="utf-8") as labels_file:
        scenarios = {
            int(item["scenario_id"]): item
            for item in json.load(labels_file)
        }

    missing = sorted(set(BENCHMARK_SCENARIO_IDS) - scenarios.keys())
    if missing:
        raise RuntimeError(f"Missing benchmark scenario labels: {missing}")

    return [scenarios[scenario_id] for scenario_id in BENCHMARK_SCENARIO_IDS]


def results_path_for_part(part: int | None) -> Path:
    if part is None:
        return RESULTS_PATH
    return PROJECT_ROOT / f"evaluations_results_part{part}.csv"


def _load_completed_results(results_path: Path) -> list[dict]:
    """Return saved rows that finished without errors, so they are not rerun."""
    if not results_path.exists():
        return []
    saved = pd.read_csv(results_path)
    for column in ERROR_COLUMNS:
        if column in saved.columns:
            saved = saved[saved[column].isna()]
    return [
        {key: value for key, value in row.items() if not pd.isna(value)}
        for row in saved.to_dict("records")
    ]


def _save_results(results: list[dict], results_path: Path) -> Path:
    output = pd.DataFrame(results).sort_values("evaluation_case")
    try:
        output.to_csv(results_path, index=False)
        return results_path
    except PermissionError:
        recovery_path = results_path.with_name(f"{results_path.stem}_recovery.csv")
        output.to_csv(recovery_path, index=False)
        print(
            f"Could not write {results_path.name}; it is locked by another "
            f"program. Saved results to {recovery_path.name} instead.",
            flush=True,
        )
        return recovery_path


def _run_react(db_path: Path, question: str) -> dict:
    """Run the ReAct system and return its result columns for one case."""
    fields = {"react_max_steps": int(load_agent_limits()["max_react_steps"])}
    try:
        result = _run_with_progress(
            "ReAct",
            lambda: run_single_react_agent(str(db_path), question),
        )
        fields.update(
            react_answer=result["final_answer"],
            react_steps=result["steps_taken"],
            react_query_count=len(result["queries_executed"]),
        )
    except Exception as exc:
        fields["react_error"] = f"{type(exc).__name__}: {exc}"
    return fields


def _run_multi_agent(db_path: Path, question: str) -> dict:
    """Run the multi-agent system and return its result columns for one case."""
    fields = {"multi_agent_analyst_steps": int(load_agent_limits()["max_react_steps"])}
    try:
        result = _run_with_progress(
            "Multi-agent",
            lambda: run_multi_agent_team(str(db_path), question),
        )
        fields.update(
            multi_agent_answer=result["final_answer"],
            multi_agent_audit_rounds=result["audit_rounds"],
            multi_agent_query_count=len(result["queries_executed"]),
        )
    except Exception as exc:
        fields["multi_agent_error"] = f"{type(exc).__name__}: {exc}"
    return fields


STEP_LIMIT_TEXT = "step limit without a verified final answer"


def _needs_rerun(
    row: dict,
    steps_column: str,
    error_column: str,
    max_steps: int,
    retry_failed: bool = False,
) -> bool:
    if row.get(steps_column) != max_steps:
        return True
    error = str(row.get(error_column, ""))
    if retry_failed:
        # Every run without an answer: any error, or the step limit reached.
        answer_column = error_column.replace("_error", "_answer")
        return bool(error) or STEP_LIMIT_TEXT in str(row.get(answer_column, ""))
    return error.startswith(TRANSIENT_ERRORS)


def rerun_agents(
    part: int | None = None,
    retry_failed: bool = False,
    only: str | None = None,
) -> None:
    """Redo the ReAct and multi-agent columns of saved results that need it.

    A system is rerun for a case when its saved result used a different
    max_react_steps (multi-agent's analyst is the ReAct loop) or failed with a
    transient provider error. With retry_failed, every run that ended without
    an answer (any error or the step limit) is rerun instead. One-shot answers
    are kept. Rerunning the command resumes where it stopped.
    """
    ensure_scenarios()
    results_path = results_path_for_part(part)
    if not results_path.exists():
        raise SystemExit(f"{results_path.name} not found. Run the full evaluation first.")
    max_steps = int(load_agent_limits()["max_react_steps"])
    questions = {int(item["scenario_id"]): item["question"] for item in _load_cases()}
    results = [
        {key: value for key, value in row.items() if not pd.isna(value)}
        for row in pd.read_csv(results_path).to_dict("records")
    ]
    systems = (
        ("Multi-agent", MULTI_AGENT_COLUMNS, "multi_agent_analyst_steps", _run_multi_agent),
        ("ReAct", REACT_COLUMNS, "react_max_steps", _run_react),
    )
    if only:
        systems = tuple(system for system in systems if system[0].lower().startswith(only))
    saved_path = results_path

    for position, row in enumerate(results, start=1):
        pending = [
            system
            for system in systems
            if _needs_rerun(row, system[2], system[1][-1], max_steps, retry_failed)
        ]
        if not pending:
            continue
        scenario_id = int(row["scenario_id"])
        db_path = OUTPUT_DIR / f"scenario_{scenario_id:02d}.db"
        started = time.monotonic()
        print(
            f"\nRerun case {position}/{len(results)} (scenario {scenario_id}, "
            f"max {max_steps} steps): {row['ground_truth_cause']}",
            flush=True,
        )
        for label, columns, _, run_system in pending:
            print(f"  {label}: running...", flush=True)
            for column in columns:
                row.pop(column, None)
            row.update(run_system(db_path, questions[scenario_id]))
            saved_path = _save_results(results, results_path)
            error = row.get(columns[-1])
            if error:
                print(f"  {label} error: {error[:200]}", flush=True)
        print(
            f"  Completed rerun case {position}/{len(results)} "
            f"({int(time.monotonic() - started)}s)",
            flush=True,
        )

    print(f"Saved evaluation results to {saved_path}")


def run_evaluations(part: int | None = None) -> None:
    ensure_scenarios()
    # Interleave cases so each part gets a mix of discount, shipping, and
    # control scenarios.
    numbered_cases = list(enumerate(_load_cases(), start=1))
    if part is not None:
        numbered_cases = numbered_cases[part - 1::NUM_PARTS]

    results_path = results_path_for_part(part)
    results = _load_completed_results(results_path)
    completed_ids = {int(row["scenario_id"]) for row in results}
    if completed_ids:
        print(
            f"Resuming: {len(completed_ids)} completed case(s) found in "
            f"{results_path.name}.",
            flush=True,
        )
    saved_path = results_path

    for position, (index, test) in enumerate(numbered_cases, start=1):
        scenario_id = test["scenario_id"]
        if scenario_id in completed_ids:
            continue
        db_path = OUTPUT_DIR / f"scenario_{scenario_id:02d}.db"
        question = test["question"]
        started = time.monotonic()
        print(
            f"\nEvaluation case {position}/{len(numbered_cases)} "
            f"(scenario {scenario_id}): {test['planted_cause']}",
            flush=True,
        )
        row = {
            "evaluation_case": index,
            "scenario_id": scenario_id,
            "ground_truth_cause": test["planted_cause"],
            "ground_truth_entity": test["entity"],
            "ground_truth_dollar_impact": test["dollar_impact"],
        }

        try:
            print("  [1/3] One-shot: running...", flush=True)
            result = _run_with_progress(
                "One-shot",
                lambda: run_oneshot_sql(str(db_path), question),
            )
            row.update(
                oneshot_answer=result["final_answer"],
                oneshot_sql=result["generated_sql"],
            )
        except Exception as exc:
            row["oneshot_error"] = f"{type(exc).__name__}: {exc}"

        print("  [2/3] ReAct: running...", flush=True)
        row.update(_run_react(db_path, question))

        print("  [3/3] Multi-agent: running...", flush=True)
        row.update(_run_multi_agent(db_path, question))

        results.append(row)
        saved_path = _save_results(results, results_path)
        print(
            f"  Completed case {position}/{len(numbered_cases)} "
            f"({int(time.monotonic() - started)}s)",
            flush=True,
        )

    print(f"Saved evaluation results to {saved_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the ProfitProbe benchmark.")
    parser.add_argument(
        "--part",
        type=int,
        choices=range(1, NUM_PARTS + 1),
        help=f"Run only one of {NUM_PARTS} interleaved shares of the 30 cases.",
    )
    parser.add_argument(
        "--rerun-agents",
        action="store_true",
        help=(
            "Rerun ReAct and multi-agent on saved results where the step limit "
            "changed or a provider error occurred, keeping the other answers."
        ),
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help=(
            "With --rerun-agents, rerun every ReAct or multi-agent run that ended "
            "without an answer (any error or the step limit)."
        ),
    )
    parser.add_argument(
        "--only",
        choices=("react", "multi"),
        help="With --rerun-agents, rerun only one system.",
    )
    args = parser.parse_args()
    if args.rerun_agents:
        rerun_agents(args.part, args.retry_failed, args.only)
    else:
        run_evaluations(args.part)
