import json
import time
from pathlib import Path

import pandas as pd

from injector import OUTPUT_DIR
from evaluations import LABELS_PATH, _run_with_progress, ensure_scenarios
from src.system_multi import run_multi_agent_team
from src.system_oneshot import run_oneshot_sql
from src.system_react import run_single_react_agent


PROJECT_ROOT = Path(__file__).resolve().parent
RESULTS_PATH = PROJECT_ROOT / "trial_results_1_3.csv"
RECOVERY_RESULTS_PATH = PROJECT_ROOT / "trial_results_1_3_recovery.csv"
TRIAL_SCENARIO_IDS = {1, 2, 3}


def run_trial() -> None:
    ensure_scenarios()
    with LABELS_PATH.open(encoding="utf-8") as labels_file:
        scenarios = json.load(labels_file)

    scenarios = [item for item in scenarios if item["scenario_id"] in TRIAL_SCENARIO_IDS]
    scenarios.sort(key=lambda item: item["scenario_id"])
    results = []

    for index, test in enumerate(scenarios, start=1):
        scenario_id = test["scenario_id"]
        db_path = OUTPUT_DIR / f"scenario_{scenario_id:02d}.db"
        question = test["question"]
        started = time.monotonic()
        print(f"\nTrial scenario {index}/{len(scenarios)}: {test['planted_cause']}", flush=True)
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
            f"  Completed scenario {scenario_id} "
            f"({int(time.monotonic() - started)}s) | trial progress: "
            f"{index}/{len(scenarios)}",
            flush=True,
        )

    print(f"Saved trial results to {saved_path}")


def _save_results(results: list[dict]) -> Path:
    output = pd.DataFrame(results)
    try:
        output.to_csv(RESULTS_PATH, index=False)
        return RESULTS_PATH
    except PermissionError:
        output.to_csv(RECOVERY_RESULTS_PATH, index=False)
        print(
            f"Could not write {RESULTS_PATH.name}; it is locked by another program. "
            f"Saved results to {RECOVERY_RESULTS_PATH.name} instead.",
            flush=True,
        )
        return RECOVERY_RESULTS_PATH


if __name__ == "__main__":
    run_trial()
