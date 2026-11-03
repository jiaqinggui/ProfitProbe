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
from src.system_multi import run_multi_agent_team
from src.system_oneshot import run_oneshot_sql
from src.system_react import run_single_react_agent


PROJECT_ROOT = Path(__file__).resolve().parent
LABELS_PATH = OUTPUT_DIR / "labels.json"
RESULTS_PATH = PROJECT_ROOT / "evaluations_results.csv"
NUM_PARTS = 3
ERROR_COLUMNS = ("oneshot_error", "react_error", "multi_agent_error")
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

        try:
            print("  [2/3] ReAct: running...", flush=True)
            result = _run_with_progress(
                "ReAct",
                lambda: run_single_react_agent(str(db_path), question),
            )
            row.update(
                react_answer=result["final_answer"],
                react_steps=result["steps_taken"],
                react_query_count=len(result["queries_executed"]),
            )
        except Exception as exc:
            row["react_error"] = f"{type(exc).__name__}: {exc}"

        try:
            print("  [3/3] Multi-agent: running...", flush=True)
            result = _run_with_progress(
                "Multi-agent",
                lambda: run_multi_agent_team(str(db_path), question),
            )
            row.update(
                multi_agent_answer=result["final_answer"],
                multi_agent_audit_rounds=result["audit_rounds"],
                multi_agent_query_count=len(result["queries_executed"]),
            )
        except Exception as exc:
            row["multi_agent_error"] = f"{type(exc).__name__}: {exc}"

        results.append(row)
        saved_path = _save_results(results, results_path)
        print(
            f"  Completed case {position}/{len(numbered_cases)} "
            f"({int(time.monotonic() - started)}s)",
            flush=True,
        )

    print(f"Saved evaluation results to {saved_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the TeleTask benchmark.")
    parser.add_argument(
        "--part",
        type=int,
        choices=range(1, NUM_PARTS + 1),
        help=f"Run only one of {NUM_PARTS} interleaved shares of the 30 cases.",
    )
    run_evaluations(parser.parse_args().part)
