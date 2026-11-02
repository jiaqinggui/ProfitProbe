from collections.abc import Callable
import json
import threading
import time
from pathlib import Path
from typing import TypeVar

import pandas as pd

from build_base_db import DB_PATH, build_base_db
from injector import OUTPUT_DIR, generate_scenarios
from src.system_multi import run_multi_agent_team
from src.system_oneshot import run_oneshot_sql
from src.system_react import run_single_react_agent


PROJECT_ROOT = Path(__file__).resolve().parent
LABELS_PATH = OUTPUT_DIR / "labels.json"
RESULTS_PATH = PROJECT_ROOT / "evaluation_results.csv"
T = TypeVar("T")


def _run_with_progress(label: str, operation: Callable[[], T]) -> T:
    """Run one system while periodically showing that the API call is alive."""
    started = time.monotonic()
    stop_heartbeat = threading.Event()

    def heartbeat() -> None:
        while not stop_heartbeat.wait(5):
            elapsed = int(time.monotonic() - started)
            print(f"    {label}: waiting for API response ({elapsed}s elapsed)...", flush=True)

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
        generate_scenarios()
        return

    with LABELS_PATH.open(encoding="utf-8") as labels_file:
        scenario_ids = {int(item["scenario_id"]) for item in json.load(labels_file)}
    expected_ids = set(range(1, 41))
    missing = [
        scenario_id
        for scenario_id in expected_ids
        if not (OUTPUT_DIR / f"scenario_{scenario_id:02d}.db").exists()
    ]
    if scenario_ids != expected_ids or missing:
        print("Scenario set is not the required 40-scenario dataset. Regenerating...")
        generate_scenarios()


def run_evaluation() -> None:
    ensure_scenarios()
    with LABELS_PATH.open(encoding="utf-8") as labels_file:
        test_scenarios = json.load(labels_file)

    results = []
    total_scenarios = len(test_scenarios)
    for scenario_index, test in enumerate(test_scenarios, start=1):
        scenario_id = test["scenario_id"]
        db_path = OUTPUT_DIR / f"scenario_{scenario_id:02d}.db"
        question = test["question"]
        scenario_started = time.monotonic()
        print(
            f"\nScenario {scenario_index}/{total_scenarios} "
            f"[{scenario_index / total_scenarios:.0%}]: "
            f"{test['planted_cause']}",
            flush=True,
        )

        row = {
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
                {
                    "oneshot_answer": result["final_answer"],
                    "oneshot_sql": result["generated_sql"],
                }
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
                {
                    "react_answer": result["final_answer"],
                    "react_steps": result["steps_taken"],
                    "react_query_count": len(result["queries_executed"]),
                }
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
                {
                    "multi_agent_answer": result["final_answer"],
                    "multi_agent_audit_rounds": result["audit_rounds"],
                    "multi_agent_query_count": len(result["queries_executed"]),
                }
            )
        except Exception as exc:
            row["multi_agent_error"] = f"{type(exc).__name__}: {exc}"

        results.append(row)
        pd.DataFrame(results).to_csv(RESULTS_PATH, index=False)
        elapsed = int(time.monotonic() - scenario_started)
        print(
            f"  Completed scenario {scenario_id} "
            f"({elapsed}s) | progress: {'#' * scenario_index}{'.' * (total_scenarios - scenario_index)} "
            f"{scenario_index}/{total_scenarios}",
            flush=True,
        )

    print(f"Saved results to {RESULTS_PATH}")


if __name__ == "__main__":
    run_evaluation()
