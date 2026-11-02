from pathlib import Path

import pandas as pd
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parent
RESULTS_PATH = PROJECT_ROOT / "evaluations_results.csv"


def _value(row: pd.Series, column: str, default: object = "") -> object:
    value = row.get(column, default)
    return default if pd.isna(value) else value


st.set_page_config(page_title="TeleTask Results", layout="wide")
st.title("TeleTask Agent Comparison")
st.write("Compare the one-shot, Single ReAct, and Multi-Agent results for each scenario.")

if not RESULTS_PATH.exists():
    st.info(
        "No evaluation results found. Run `python evaluations.py` first to create "
        "evaluations_results.csv."
    )
    st.stop()

results = pd.read_csv(RESULTS_PATH)
if results.empty or "scenario_id" not in results.columns:
    st.error(f"{RESULTS_PATH.name} is empty or missing the scenario_id column.")
    st.stop()

scenario_ids = results["scenario_id"].dropna().astype(int).tolist()
selected_id = st.sidebar.selectbox("Scenario", scenario_ids)
scenario = results[results["scenario_id"] == selected_id].iloc[0]

st.subheader(f"Scenario {selected_id}")
ground_truth_columns = [
    "ground_truth_cause",
    "ground_truth_entity",
    "ground_truth_dollar_impact",
]
ground_truth = {
    column.replace("ground_truth_", "").replace("_", " ").title(): _value(
        scenario, column, "Unavailable"
    )
    for column in ground_truth_columns
    if column in scenario.index
}
st.dataframe(pd.DataFrame([ground_truth]), use_container_width=True, hide_index=True)

comparison = pd.DataFrame(
    [
        {
            "System": "One-shot",
            "Answer": _value(scenario, "oneshot_answer", "No result"),
            "SQL": _value(scenario, "oneshot_sql"),
            "Steps": "",
            "Queries": "",
            "Audit rounds": "",
        },
        {
            "System": "Single ReAct",
            "Answer": _value(scenario, "react_answer", "No result"),
            "SQL": "",
            "Steps": _value(scenario, "react_steps"),
            "Queries": _value(scenario, "react_query_count"),
            "Audit rounds": "",
        },
        {
            "System": "Multi-Agent",
            "Answer": _value(scenario, "multi_agent_answer", "No result"),
            "SQL": "",
            "Steps": "",
            "Queries": _value(scenario, "multi_agent_query_count"),
            "Audit rounds": _value(scenario, "multi_agent_audit_rounds"),
        },
    ]
)

st.subheader("System comparison")
st.dataframe(comparison, use_container_width=True, hide_index=True)

errors = {
    system: scenario[column]
    for system, column in (
        ("One-shot", "oneshot_error"),
        ("Single ReAct", "react_error"),
        ("Multi-Agent", "multi_agent_error"),
    )
    if column in scenario.index and pd.notna(scenario[column])
}
if errors:
    st.subheader("Errors")
    for system, error in errors.items():
        st.error(f"{system}: {error}")
