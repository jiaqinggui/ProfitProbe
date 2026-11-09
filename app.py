import json
from pathlib import Path

import pandas as pd
import streamlit as st
import altair as alt
from score_results import SCENARIO_TYPES, _status as _keyword_status


PROJECT_ROOT = Path(__file__).resolve().parent
RESULTS_PATH = PROJECT_ROOT / "evaluations_results.csv"
LABELS_PATH = PROJECT_ROOT / "scenarios" / "labels.json"
MANUAL_REVIEW_PATH = PROJECT_ROOT / "manual_review.csv"


def _load_manual_review() -> dict[tuple[int, str], str]:
    """Hand-corrected labels that override the keyword scores."""
    if not MANUAL_REVIEW_PATH.exists():
        return {}
    review = pd.read_csv(MANUAL_REVIEW_PATH)
    return {
        (int(row.scenario_id), row.system): row.final_label
        for row in review.itertuples()
    }


MANUAL_LABELS = _load_manual_review()


def _status(row: pd.Series, system_key: str, scenario_type: str) -> str:
    override = MANUAL_LABELS.get((int(row["scenario_id"]), system_key))
    return override or _keyword_status(row, system_key, scenario_type)

SYSTEMS = (
    ("oneshot", "One-shot"),
    ("react", "Single ReAct"),
    ("multi_agent", "Multi-Agent"),
)
SCENARIO_TYPES_DISPLAY = (
    ("discount", "Discount"),
    ("shipping", "Shipping"),
    ("control", "Clean controls"),
)
STATUS_LABELS = {
    "correct": "Roughly correct",
    "cause_only": "Cause found; entity missing",
    "false_cause": "False cause on a control",
    "wrong": "Likely incorrect",
    "no_answer": "No final answer",
    "error": "Run error",
}
OUTCOME_ORDER = (
    "Roughly correct",
    "Cause found; entity missing",
    "Wrong / false cause",
    "No final answer",
    "Run error",
)
OUTCOME_COLORS_LIGHT = {
    "Roughly correct": "#4B9B73",
    "Cause found; entity missing": "#D7B84B",
    "Wrong / false cause": "#D66A6A",
    "No final answer": "#9A86B8",
    "Run error": "#8793A0",
}
OUTCOME_COLORS_DARK = {
    "Roughly correct": "#347B55",
    "Cause found; entity missing": "#95700B",
    "Wrong / false cause": "#A94343",
    "No final answer": "#71529A",
    "Run error": "#56616C",
}


def _value(row: pd.Series, column: str, default: object = "") -> object:
    value = row.get(column, default)
    return default if pd.isna(value) else value


def _scenario_type(row: pd.Series) -> str:
    return SCENARIO_TYPES.get(row.get("ground_truth_cause"), "other")


def _scenario_label(scenario_id: int, results: pd.DataFrame) -> str:
    row = results[results["scenario_id"] == scenario_id].iloc[0]
    scenario_type = _scenario_type(row)
    type_label = dict(SCENARIO_TYPES_DISPLAY).get(scenario_type, "Other")
    entity = _value(row, "ground_truth_entity", "")
    if scenario_type == "control" or not entity:
        detail = "clean control" if scenario_type == "control" else "entity unavailable"
    elif scenario_type == "shipping":
        detail = f"seller {str(entity)[:8]}…"
    else:
        detail = str(entity).replace("_", " ")
    return f"Scenario {scenario_id:02d} · {type_label} · {detail}"


def _format_ground_truth(column: str, value: object) -> str:
    text = str(value)
    if column == "ground_truth_dollar_impact":
        try:
            return f"${float(value):,.2f}"
        except (TypeError, ValueError):
            return text
    if column == "ground_truth_entity" and len(text) == 32:
        return " · ".join(text[index : index + 8] for index in range(0, 32, 8))
    return text


def _move_scenario(key: str, scenario_ids: list[int], direction: int) -> None:
    current_id = st.session_state.get(key, scenario_ids[0])
    current_index = scenario_ids.index(current_id)
    next_index = max(0, min(len(scenario_ids) - 1, current_index + direction))
    st.session_state[key] = scenario_ids[next_index]


def _is_dark_theme() -> bool:
    try:
        return st.context.theme.type == "dark"
    except (AttributeError, RuntimeError):
        return st.get_option("theme.base") == "dark"


st.set_page_config(page_title="ProfitProbe Results", layout="wide")
st.title("ProfitProbe Workflow Comparison")
st.write("Compare three analysis approaches across the benchmark scenarios.")

if not RESULTS_PATH.exists():
    st.info(
        "No evaluation results found. Run `python merge_results.py` first to create "
        "evaluations_results.csv."
    )
    st.stop()

results = pd.read_csv(RESULTS_PATH)
if results.empty or "scenario_id" not in results.columns:
    st.error(f"{RESULTS_PATH.name} is empty or missing the scenario_id column.")
    st.stop()

scenario_questions = {}
if LABELS_PATH.exists():
    try:
        with LABELS_PATH.open(encoding="utf-8") as labels_file:
            scenario_questions = {
                int(item["scenario_id"]): item["question"]
                for item in json.load(labels_file)
                if item.get("question")
            }
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        scenario_questions = {}

overview_tab, review_tab = st.tabs(["Overview", "Scenario review"])
with overview_tab:
    dark_theme = _is_dark_theme()
    outcome_colors = OUTCOME_COLORS_DARK if dark_theme else OUTCOME_COLORS_LIGHT
    st.info(
        "This dashboard shows the 30-case benchmark: scenarios 1–10 are discount cases, "
        "11–20 are shipping cases, and 71–80 are clean controls. Scenarios 21–70 are "
        "generated for other anomaly types and are not part of this benchmark."
    )
    completion_rows = []
    answer_quality_rows = []
    statuses_by_system = {}
    for system_key, system_name in SYSTEMS:
        statuses = [
            _status(row, system_key, _scenario_type(row))
            for _, row in results.iterrows()
        ]
        statuses_by_system[system_key] = statuses
        completed_count = sum(status not in {"error", "no_answer"} for status in statuses)
        completion_rows.append(
            {
                "Approach": system_name,
                "Completed": f"{completed_count} / {len(results)}",
                "No final answer": f"{statuses.count('no_answer')} / {len(results)}",
                "Run error": f"{statuses.count('error')} / {len(results)}",
            }
        )
        answer_quality_rows.append(
            {
                "Approach": system_name,
                "Roughly correct": f"{statuses.count('correct')} / {completed_count}",
                "Cause found; entity missing": (
                    f"{statuses.count('cause_only')} / {completed_count}"
                ),
                "Wrong / false cause": (
                    f"{statuses.count('wrong') + statuses.count('false_cause')} / "
                    f"{completed_count}"
                ),
            }
        )

    st.subheader("Overall results")
    st.caption(
        "Run completion counts use all loaded cases. Answer quality is calculated only "
        "among completed runs. 'Cause found; entity missing' means the answer named the "
        "cause but not the expected affected entity. Outcome labels use the project's "
        "rough keyword-based scorer."
    )
    completion_column, quality_column = st.columns(2)
    with completion_column:
        st.markdown("**Run completion**")
        st.dataframe(
            pd.DataFrame(completion_rows),
            use_container_width=True,
            hide_index=True,
        )
    with quality_column:
        st.markdown("**Answer quality · completed runs**")
        st.dataframe(
            pd.DataFrame(answer_quality_rows),
            use_container_width=True,
            hide_index=True,
        )

    def _outcome_group(status: str) -> str:
        if status == "correct":
            return "Roughly correct"
        if status == "cause_only":
            return "Cause found; entity missing"
        if status in {"wrong", "false_cause"}:
            return "Wrong / false cause"
        if status == "no_answer":
            return "No final answer"
        return "Run error"

    outcome_rows = []
    for system_key, system_name in SYSTEMS:
        grouped = [_outcome_group(status) for status in statuses_by_system[system_key]]
        for outcome in OUTCOME_ORDER:
            outcome_rows.append(
                {
                    "Approach": system_name,
                    "Outcome": outcome,
                    "Count": grouped.count(outcome),
                }
            )

    outcome_data = pd.DataFrame(outcome_rows)
    bars = alt.Chart(outcome_data).mark_bar().encode(
        x=alt.X(
            "Count:Q",
            stack="zero",
            title=f"Scenario outcomes (out of {len(results)})",
            scale=alt.Scale(domain=[0, len(results)]),
        ),
        y=alt.Y("Approach:N", sort=[name for _, name in SYSTEMS], title=None),
        color=alt.Color(
            "Outcome:N",
            sort=list(OUTCOME_ORDER),
            scale=alt.Scale(
                domain=list(OUTCOME_ORDER),
                range=[outcome_colors[outcome] for outcome in OUTCOME_ORDER],
            ),
            legend=alt.Legend(title=None, orient="bottom"),
        ),
        order=alt.Order("Outcome:N", sort="ascending"),
        tooltip=["Approach:N", "Outcome:N", "Count:Q"],
    )
    labels = (
        alt.Chart(outcome_data)
        .transform_filter("datum.Count > 0")
        .mark_text(color="white", size=11)
        .encode(
            x=alt.X("Count:Q", stack="center"),
            y=alt.Y("Approach:N", sort=[name for _, name in SYSTEMS]),
            detail="Outcome:N",
            text=alt.Text("Count:Q"),
        )
    )
    st.subheader("Outcome breakdown")
    st.caption(
        "Each bar totals the loaded scenarios. Outcome labels come from the project's "
        "keyword-based scorer and are a rough measure. Hover over a segment for its count."
    )
    st.altair_chart(alt.layer(bars, labels), use_container_width=True)

    scenario_type_rows = []
    result_types = results.apply(_scenario_type, axis=1)
    for scenario_type, type_label in SCENARIO_TYPES_DISPLAY:
        type_results = results[result_types == scenario_type]
        for system_key, system_name in SYSTEMS:
            statuses = [
                _status(row, system_key, scenario_type)
                for _, row in type_results.iterrows()
            ]
            correct_count = statuses.count("correct")
            partial_count = statuses.count("cause_only")
            wrong_count = statuses.count("wrong") + statuses.count("false_cause")
            no_answer_count = statuses.count("no_answer")
            error_count = statuses.count("error")
            unsuccessful_count = sum(
                (wrong_count, no_answer_count, error_count)
            )
            scenario_type_rows.append(
                {
                    "Scenario type": type_label,
                    "Approach": system_name,
                    "Cases": len(type_results),
                    "Roughly correct": correct_count,
                    "Cause found; entity missing": partial_count,
                    "Unsuccessful": unsuccessful_count,
                    "Unsuccessful breakdown": (
                        f"Wrong {wrong_count} · no answer {no_answer_count} · "
                        f"errors {error_count}"
                    ),
                }
            )

    st.subheader("Results by scenario type")
    st.caption(
        "Each neutral bar shows the count out of the cases in that group, not a performance "
        "grade. Unsuccessful combines wrong answers, no final answers, and run errors; the "
        "text column breaks those cases out. Outcome labels use the project's rough "
        "keyword-based scorer."
    )
    scenario_type_table = pd.DataFrame(scenario_type_rows)
    max_cases_per_type = max(
        (len(results[result_types == scenario_type]) for scenario_type, _ in SCENARIO_TYPES_DISPLAY),
        default=1,
    )
    st.dataframe(
        scenario_type_table,
        column_config={
            "Roughly correct": st.column_config.ProgressColumn(
                "Roughly correct",
                help="Count of roughly correct answers. Bar length shows count, not a grade.",
                min_value=0,
                max_value=max_cases_per_type,
                format="%d",
                color="#8793A0",
            ),
            "Cause found; entity missing": st.column_config.ProgressColumn(
                "Cause found; entity missing",
                help="Count of partial answers. Bar length shows count, not a grade.",
                min_value=0,
                max_value=max_cases_per_type,
                format="%d",
                color="#8793A0",
            ),
            "Unsuccessful": st.column_config.ProgressColumn(
                "Unsuccessful",
                help="Wrong answers, no final answers, and run errors. Bar length shows count, not a grade.",
                min_value=0,
                max_value=max_cases_per_type,
                format="%d",
                color="#8793A0",
            ),
        },
        use_container_width=True,
        hide_index=True,
    )

with review_tab:
    st.header("Review a scenario")
    st.caption(
        "The standard benchmark includes scenarios 1–20 and 71–80. "
        "Scenarios 21–70 are excluded."
    )
    type_labels = dict(SCENARIO_TYPES_DISPLAY)
    scenario_type = st.radio(
        "Choose a scenario group",
        options=[key for key, _ in SCENARIO_TYPES_DISPLAY],
        format_func=lambda value: type_labels[value],
        horizontal=True,
        key="scenario_type",
    )

    result_types = results.apply(_scenario_type, axis=1)
    scenario_ids = sorted(
        results.loc[result_types == scenario_type, "scenario_id"]
        .dropna()
        .astype(int)
        .tolist()
    )
    if not scenario_ids:
        st.info("There are no scenarios in this group in the loaded results file.")
    else:
        selector_key = f"scenario_selector_{scenario_type}"
        if st.session_state.get(selector_key) not in scenario_ids:
            st.session_state[selector_key] = scenario_ids[0]

        st.caption(f"{len(scenario_ids)} scenarios in this group")
        nav_left, selector_column, nav_right = st.columns([1, 5, 1])
        current_index = scenario_ids.index(st.session_state[selector_key])
        with nav_left:
            st.button(
                "← Previous",
                disabled=current_index == 0,
                on_click=_move_scenario,
                args=(selector_key, scenario_ids, -1),
                use_container_width=True,
            )
        with selector_column:
            selected_id = st.selectbox(
                "Scenario",
                options=scenario_ids,
                format_func=lambda value: _scenario_label(value, results),
                key=selector_key,
                label_visibility="collapsed",
            )
        with nav_right:
            st.button(
                "Next →",
                disabled=current_index == len(scenario_ids) - 1,
                on_click=_move_scenario,
                args=(selector_key, scenario_ids, 1),
                use_container_width=True,
            )

        scenario = results[results["scenario_id"] == selected_id].iloc[0]
        st.caption(
            f"Scenario {int(selected_id):02d} · {current_index + 1} of {len(scenario_ids)}"
        )

        st.subheader("Question")
        question = scenario_questions.get(int(selected_id))
        if question:
            with st.container(border=True):
                st.write(question)
        else:
            st.warning(
                f"Question unavailable. Could not find it in {LABELS_PATH.relative_to(PROJECT_ROOT)}."
            )

        st.subheader("Ground truth")
        truth_values = (
            ("ground_truth_cause", "Cause", _value(scenario, "ground_truth_cause", "Unavailable")),
            ("ground_truth_entity", "Affected entity", _value(scenario, "ground_truth_entity", "Not applicable")),
            ("ground_truth_dollar_impact", "Dollar impact", _value(scenario, "ground_truth_dollar_impact", "Unavailable")),
        )
        with st.container(border=True):
            truth_columns = st.columns([1, 1.5, 1])
            for column, (field, label, value) in zip(truth_columns, truth_values):
                with column:
                    st.caption(label)
                    st.markdown(f"**{_format_ground_truth(field, value)}**")

        scenario_statuses = {}
        for system_key, system_name in SYSTEMS:
            status = _status(scenario, system_key, _scenario_type(scenario))
            scenario_statuses[system_key] = (
                system_name,
                status,
                STATUS_LABELS.get(status, status.replace("_", " ").title()),
            )

        st.subheader("Approach status")
        status_columns = st.columns(3)
        for column, (system_key, _) in zip(status_columns, SYSTEMS):
            system_name, status, status_label = scenario_statuses[system_key]
            with column:
                st.markdown(f"**{system_name}**")
                if status == "correct":
                    st.success(status_label)
                elif status in {"error", "wrong", "false_cause"}:
                    st.error(status_label)
                else:
                    st.warning(status_label)

        st.subheader("Answers and evidence")
        for system_key, system_name in SYSTEMS:
            _, _, status_label = scenario_statuses[system_key]
            answer = _value(scenario, f"{system_key}_answer", "No answer recorded")
            error = _value(scenario, f"{system_key}_error", "")

            with st.container(border=True):
                st.markdown(f"#### {system_name}")
                st.caption(f"Scorer label: {status_label}")
                if error:
                    st.error(str(error))
                else:
                    st.markdown(str(answer))

                with st.expander("Run details"):
                    if system_key == "oneshot":
                        sql = _value(scenario, "oneshot_sql", "No SQL recorded")
                        st.code(str(sql), language="sql")
                    elif system_key == "react":
                        st.write(f"Steps: {_value(scenario, 'react_steps', 'Unavailable')}")
                        st.write(f"Queries: {_value(scenario, 'react_query_count', 'Unavailable')}")
                    else:
                        st.write(
                            "Analyst steps: "
                            f"{_value(scenario, 'multi_agent_analyst_steps', 'Unavailable')}"
                        )
                        st.write(
                            f"Queries: {_value(scenario, 'multi_agent_query_count', 'Unavailable')}"
                        )
                        st.write(
                            f"Audit rounds: {_value(scenario, 'multi_agent_audit_rounds', 'Unavailable')}"
                        )

