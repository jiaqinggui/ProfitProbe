from typing import Any

from src.llm_client import generate_completion, load_agent_limits
from src.system_react import run_single_react_agent
from src.tools import search_schema


def _parse_hypotheses(response: str) -> list[str]:
    hypotheses = []
    for line in response.splitlines():
        cleaned = line.strip().lstrip("-* ")
        if cleaned:
            hypotheses.append(cleaned)
    return hypotheses[:5]


def run_multi_agent_team(db_path: str, question: str, llm_client=None) -> dict:
    """Run the configured planner, analyst, auditor, and reporter agents."""
    del llm_client
    max_audit_rounds = int(load_agent_limits()["max_audit_rounds"])
    schema = search_schema("full schema")
    state: dict[str, Any] = {
        "question": question,
        "schema": schema,
        "hypotheses": [],
        "queries_run": [],
        "observations": [],
        "analyst_conclusion": None,
        "auditor_feedback": None,
        "final_report": None,
    }

    planner_prompt = f"""
You are the planning analyst for an e-commerce anomaly investigation.
Schema and formulas:
{schema}
Generate 3-5 ordered, testable hypotheses for this question:
{question}
Return one hypothesis per line, with no introduction.
"""
    planner_response = generate_completion(
        planner_prompt,
        question,
        role="planner",
    )
    state["hypotheses"] = _parse_hypotheses(planner_response)
    if not state["hypotheses"]:
        raise ValueError("The planner returned no usable hypotheses.")

    audit_rounds = 0
    audit_verdict = "FAIL"
    while audit_rounds < max_audit_rounds and audit_verdict != "PASS":
        analyst_question = (
            f"{question}\n\nPrioritized hypotheses:\n"
            + "\n".join(f"{index + 1}. {item}" for index, item in enumerate(state["hypotheses"]))
        )
        if state["auditor_feedback"]:
            analyst_question += f"\n\nAuditor feedback to address:\n{state['auditor_feedback']}"

        analyst_result = run_single_react_agent(db_path, analyst_question)
        state["analyst_conclusion"] = analyst_result["final_answer"]
        state["queries_run"].extend(analyst_result["queries_executed"])
        state["observations"].extend(analyst_result["observations"])

        auditor_prompt = f"""
You are an independent auditor for a data investigation.
Determine whether the analyst's conclusion is supported by the SQL results and
whether the root cause and affected entity match the question. Do not require
or evaluate a dollar-impact calculation.
Return exactly:
VERDICT: PASS
FEEDBACK: <brief justification>
or:
VERDICT: FAIL
FEEDBACK: <specific correction required>

Question: {question}
Analyst conclusion: {state['analyst_conclusion']}
SQL queries executed: {state['queries_run']}
SQL observations: {state['observations']}
"""
        audit_response = generate_completion(
            auditor_prompt,
            "Audit the investigation now.",
            role="auditor",
        )
        audit_rounds += 1
        audit_verdict = "PASS" if "VERDICT: PASS" in audit_response.upper() else "FAIL"
        state["auditor_feedback"] = audit_response

        if audit_verdict == "FAIL" and audit_rounds < max_audit_rounds:
            planner_response = generate_completion(
                planner_prompt,
                f"{question}\n\nPrevious audit feedback:\n{audit_response}",
                role="planner",
            )
            state["hypotheses"] = _parse_hypotheses(planner_response)
            if not state["hypotheses"]:
                raise ValueError("The planner returned no usable hypotheses after audit feedback.")

    reporter_prompt = """
You are a senior manager receiving a database investigation report.
Write a concise plain-text report stating the verified root cause, affected
entity, supporting explanation, and whether the auditor passed it. Do not
calculate or report dollar amounts. Do not invent facts.
If the audit failed or evidence is incomplete, clearly say the conclusion is
unverified.
"""
    report = generate_completion(
        reporter_prompt,
        (
            f"Question: {question}\n"
            f"Hypotheses: {state['hypotheses']}\n"
            f"Analyst conclusion: {state['analyst_conclusion']}\n"
            f"Auditor verdict: {audit_verdict}\n"
            f"Auditor feedback: {state['auditor_feedback']}\n"
            f"Queries: {state['queries_run']}\n"
            f"SQL observations: {state['observations']}"
        ),
        role="reporter",
    )
    state["final_report"] = report

    return {
        "system": "Multi-Agent",
        "hypotheses_tested": state["hypotheses"],
        "queries_executed": state["queries_run"],
        "audit_rounds": audit_rounds,
        "final_answer": state["final_report"],
    }
