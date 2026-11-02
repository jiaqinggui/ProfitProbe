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
RECOVERY_RESULTS_PATH = PROJECT_ROOT / "evaluations_results_recovery.csv"
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


def _save_results(results: list[dict]) -> Path:
    output = pd.DataFrame(results)
    try:
        output.to_csv(RESULTS_PATH, index=False)
        return RESULTS_PATH
    except PermissionError:
        output.to_csv(RECOVERY_RESULTS_PATH, index=False)
        print(
            f"Could not write {RESULTS_PATH.name}; it is locked by another "
            f"program. Saved results to {RECOVERY_RESULTS_PATH.name} instead.",
            flush=True,
        )
        return RECOVERY_RESULTS_PATH


def run_evaluations() -> None:
    ensure_scenarios()
    cases = _load_cases()
    results = []

    for index, test in enumerate(cases, start=1):
        scenario_id = test["scenario_id"]
        db_path = OUTPUT_DIR / f"scenario_{scenario_id:02d}.db"
        question = test["question"]
        started = time.monotonic()
        print(
            f"\nEvaluation case {index}/{len(cases)} "
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
        saved_path = _save_results(results)
        print(
            f"  Completed case {index}/{len(cases)} "
            f"({int(time.monotonic() - started)}s)",
            flush=True,
        )

    print(f"Saved evaluation results to {saved_path}")


if __name__ == "__main__":
    run_evaluations()
