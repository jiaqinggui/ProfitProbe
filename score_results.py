"""Score each system's answers in evaluations_results.csv against ground truth.

Scoring is keyword based, so it is a first pass for the group to review, not a
final grade. Each answer gets one of these statuses:

- correct: names the planted cause and the affected entity (for controls:
  reports no specific cause and does not invent one)
- cause_only: names the planted cause but not the affected entity
- false_cause: a control answer that blames a planted-style cause
- wrong: an answer that does not name the planted cause
- no_answer: the system ran out of steps without a final answer
- error: the system raised an error

An answer that lists several entities still counts as naming the entity.

Labels the group corrected by hand are kept in manual_review.csv and replace
the keyword label, as long as the answer still gets the keyword label that was
reviewed (a rerun answer is scored afresh and flagged for review).
"""

import re

import pandas as pd

from evaluations import PROJECT_ROOT, RESULTS_PATH


SCORES_PATH = PROJECT_ROOT / "evaluation_scores.csv"
MANUAL_REVIEW_PATH = PROJECT_ROOT / "manual_review.csv"
SYSTEMS = {"oneshot": "One-shot", "react": "ReAct", "multi_agent": "Multi-agent"}
SCENARIO_TYPES = {
    "Wrong discount code": "discount",
    "Shipping delay": "shipping",
    "No single cause (normal week-to-week noise)": "control",
}
SELLER_ID = re.compile(r"\b[0-9a-f]{32}\b")
DELAY = re.compile(r"\b(delay|delays|delayed|late|slow|slower)\b")
# Phrases an answer uses when it reports no specific cause.
NO_CAUSE_PHRASES = (
    "no single cause",
    "noise",
    "normal variation",
    "normal fluctuation",
    "normal week",
    "no significant",
    "no root cause",
    "no verified root cause",
    "not identified",
    "no anomal",
    "not supported",
    "contradicted",
    "unverified",
    "impossible to",
    "cannot be established",
)


def _mentions(text: str, entity: str) -> bool:
    entity = entity.lower()
    return entity in text or entity.replace("_", " ") in text


def score_answer(scenario_type: str, entity: str, answer: str) -> str:
    text = answer.lower()
    reports_no_cause = any(phrase in text for phrase in NO_CAUSE_PHRASES)
    if scenario_type == "control":
        if "promo_bug" in text or SELLER_ID.search(text):
            return "false_cause"
        return "correct" if reports_no_cause else "wrong"

    if scenario_type == "discount":
        names_cause = "promo_bug" in text or ("discount" in text and not reports_no_cause)
    else:
        names_cause = _mentions(text, entity) or (
            DELAY.search(text) is not None and not reports_no_cause
        )
    if not names_cause:
        return "wrong"
    return "correct" if _mentions(text, entity) else "cause_only"


def _status(row: pd.Series, system: str, scenario_type: str) -> str:
    error = row.get(f"{system}_error")
    if isinstance(error, str) and error:
        return "error"
    answer = row.get(f"{system}_answer")
    if not isinstance(answer, str) or not answer:
        return "error"
    if "step limit without a verified final answer" in answer:
        return "no_answer"
    return score_answer(scenario_type, str(row["ground_truth_entity"]), answer)


def load_manual_review() -> dict[tuple[int, str], tuple[str, str]]:
    """Map (scenario_id, system) to (reviewed keyword label, corrected label)."""
    if not MANUAL_REVIEW_PATH.exists():
        return {}
    review = pd.read_csv(MANUAL_REVIEW_PATH)
    return {
        (int(item.scenario_id), item.system): (item.keyword_label, item.final_label)
        for item in review.itertuples()
    }


def score_results() -> pd.DataFrame:
    if not RESULTS_PATH.exists():
        raise SystemExit(f"{RESULTS_PATH.name} not found. Run merge_results.py first.")
    results = pd.read_csv(RESULTS_PATH)
    manual = load_manual_review()
    rows = []
    applied = 0
    for _, row in results.iterrows():
        scenario_type = SCENARIO_TYPES[row["ground_truth_cause"]]
        scenario_id = int(row["scenario_id"])
        scored = {"scenario_id": scenario_id, "scenario_type": scenario_type}
        for system in SYSTEMS:
            label = _status(row, system, scenario_type)
            review = manual.get((scenario_id, system))
            if review and review[0] == label:
                label = review[1]
                applied += 1
            elif review:
                print(
                    f"Scenario {scenario_id} {system}: answer changed since manual "
                    f"review (now '{label}'); review it again."
                )
            scored[f"{system}_score"] = label
        rows.append(scored)
    if manual:
        print(f"Applied {applied} of {len(manual)} manual corrections from {MANUAL_REVIEW_PATH.name}.")
    scores = pd.DataFrame(rows).sort_values("scenario_id")
    scores.to_csv(SCORES_PATH, index=False)
    return scores


def print_summary(scores: pd.DataFrame) -> None:
    for system, label in SYSTEMS.items():
        column = f"{system}_score"
        print(f"\n{label}: {(scores[column] == 'correct').sum()}/{len(scores)} correct")
        table = pd.crosstab(scores["scenario_type"], scores[column])
        print(table.to_string())


if __name__ == "__main__":
    scores = score_results()
    print_summary(scores)
    print(f"\nSaved per-scenario scores to {SCORES_PATH.name}")
