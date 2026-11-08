import re

from src.llm_client import generate_completion, load_agent_limits
from src.tools import run_sql, search_schema

# Recorded with each result so evaluations.py --react-only reruns cases
# produced by an older version of the prompt.
PROMPT_VERSION = "step-budget"


def _load_limits() -> int:
    return int(load_agent_limits()["max_react_steps"])


def _parse_action(response: str) -> tuple[str, str] | None:
    action_match = re.search(
        r"^\s*Action\s*:\s*(run_sql|search_schema)\s*$",
        response,
        re.IGNORECASE | re.MULTILINE,
    )
    if not action_match:
        return None

    input_match = re.search(
        r"^\s*Action\s*Input\s*:\s*(.*?)(?=^\s*(?:Observation|Thought|Action|FINAL ANSWER)\s*:|\Z)",
        response,
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    if not input_match:
        raise ValueError("The LLM returned an action without Action Input.")
    action_input = input_match.group(1).strip()
    fenced_match = re.search(
        r"```(?:sql)?\s*(.*?)```", action_input, re.IGNORECASE | re.DOTALL
    )
    if fenced_match:
        action_input = fenced_match.group(1).strip()
    return action_match.group(1).lower(), action_input


def run_single_react_agent(db_path: str, question: str, llm_client=None) -> dict:
    """Investigate a question through a bounded Thought/Action loop."""
    del llm_client
    schema = search_schema("full schema")
    max_steps = _load_limits()
    system_prompt = f"""
You are an autonomous SQLite data analyst.
Schema and business formulas:
{schema}

Investigate the user's question using this exact loop format:
Thought: <your reasoning>
Action: run_sql
Action Input: <one read-only SQLite SELECT query>

You may use Action: search_schema with Action Input: full schema if needed.
After observations, either take another action or return:
FINAL ANSWER: <concise verified root cause, affected entity, and explanation>
You have at most {max_steps} steps. Each response uses one step. Leave
enough steps to give a FINAL ANSWER; on the last step you must give it.
Do not calculate or report dollar amounts.
Never use INSERT, UPDATE, DELETE, PRAGMA, or multiple SQL statements.
"""
    transcript = f"User question: {question}"
    queries_executed: list[str] = []
    observations: list[str] = []
    final_answer = None
    steps_taken = 0

    while steps_taken < max_steps:
        step_note = f"\n\nStep {steps_taken + 1} of {max_steps}."
        if steps_taken + 1 == max_steps:
            step_note += (
                " This is your last step. Do not take another action; respond "
                "with FINAL ANSWER based on the observations so far."
            )
        response = generate_completion(
            system_prompt, transcript + step_note, role="analyst"
        )
        steps_taken += 1
        final_match = re.search(r"FINAL ANSWER\s*:\s*(.*)", response, re.IGNORECASE | re.DOTALL)
        if final_match:
            final_answer = final_match.group(1).strip()
            break

        action = _parse_action(response)
        if action is None:
            raise ValueError("The LLM response contained neither FINAL ANSWER nor a valid action.")

        action_name, action_input = action
        if action_name == "run_sql":
            observation = run_sql(action_input, db_path)
            queries_executed.append(action_input)
        else:
            observation = search_schema(action_input)
        observations.append(observation)
        transcript += (
            f"\n\nAssistant:\n{response}\n"
            f"Observation:\n{observation}"
        )

    if final_answer is None:
        final_answer = (
            f"The analyst reached the {max_steps}-step limit without a verified final answer."
        )

    return {
        "system": "Single-ReAct",
        "steps_taken": steps_taken,
        "queries_executed": queries_executed,
        "observations": observations,
        "final_answer": final_answer,
    }
