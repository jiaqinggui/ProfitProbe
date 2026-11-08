import re

from src.llm_client import generate_completion, load_agent_limits
from src.tools import run_sql, search_schema


def _load_limits() -> int:
    return int(load_agent_limits()["max_react_steps"])


# Groq's free tier rejects any request above 7,000 input tokens per minute.
# Prompts are kept under this many characters (about 3 characters per token,
# with room left for the reply) by shortening the oldest SQL results first.
PROMPT_CHAR_BUDGET = 16_000
TRIMMED_OBSERVATION_LINES = 6

FORMAT_REMINDER = (
    "Your last reply could not be parsed. Reply with either\n"
    "Thought: <reasoning>\nAction: run_sql\nAction Input: <one SELECT query>\n"
    "or\nFINAL ANSWER: <root cause, affected entity, and explanation>"
)
LAST_STEP_REMINDER = (
    "This is your last step. Do not run another query. Reply now with\n"
    "FINAL ANSWER: <the most likely root cause and affected entity based on the "
    "evidence so far, and say if it is uncertain>"
)


def _normalise(response: str) -> str:
    """Remove markdown emphasis and code markers that break the line format."""
    text = response.replace("**", "").replace("__", "")
    return re.sub(r"^\s*#+\s*", "", text, flags=re.MULTILINE)


def _parse_action(response: str) -> tuple[str, str] | None:
    text = _normalise(response)
    action_match = re.search(
        r"^\s*Action\s*:\s*`?(run_sql|search_schema)`?\s*(?:\((.*?)\))?\s*$",
        text,
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    if not action_match:
        return None
    action_name = action_match.group(1).lower()

    input_match = re.search(
        r"^\s*Action\s*Input\s*:\s*(.*?)(?=^\s*(?:Observation|Thought|Action|FINAL ANSWER)\s*:|\Z)",
        text,
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    if input_match:
        action_input = input_match.group(1).strip()
    elif action_match.group(2):
        # Accept the call style: Action: run_sql("SELECT ...")
        action_input = action_match.group(2).strip()
    elif action_name == "search_schema":
        action_input = "full schema"
    else:
        # A SQL code block after the action line is also accepted.
        fenced = re.search(r"```(?:sql)?\s*(.*?)```", text[action_match.end():], re.IGNORECASE | re.DOTALL)
        if not fenced:
            return None
        action_input = fenced.group(1).strip()

    fenced_match = re.search(
        r"```(?:sql)?\s*(.*?)```", action_input, re.IGNORECASE | re.DOTALL
    )
    if fenced_match:
        action_input = fenced_match.group(1).strip()
    action_input = action_input.strip().strip("`").strip().strip("\"'").strip()
    if not action_input:
        return None
    return action_name, action_input


def _trim_observation(observation: str) -> str:
    lines = observation.splitlines()
    if len(lines) <= TRIMMED_OBSERVATION_LINES:
        return observation
    kept = "\n".join(lines[:TRIMMED_OBSERVATION_LINES])
    return f"{kept}\n[... {len(lines) - TRIMMED_OBSERVATION_LINES} more rows trimmed to fit the token limit]"


def _build_transcript(question: str, turns: list[list[str]], budget: int) -> str:
    """Join the turns, trimming the oldest observations until the text fits."""

    def render() -> str:
        text = f"User question: {question}"
        for response, observation in turns:
            text += f"\n\nAssistant:\n{response}\nObservation:\n{observation}"
        return text

    transcript = render()
    for turn in turns[:-1]:
        if len(transcript) <= budget:
            break
        turn[1] = _trim_observation(turn[1])
        transcript = render()
    return transcript


def fit_to_budget(text: str, budget: int) -> str:
    """Cut text that would push a request over the token limit."""
    if len(text) <= budget:
        return text
    return text[:budget] + "\n[... trimmed to fit the token limit]"


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
Do not calculate or report dollar amounts.
Never use INSERT, UPDATE, DELETE, PRAGMA, or multiple SQL statements.
"""
    turns: list[list[str]] = []
    queries_executed: list[str] = []
    observations: list[str] = []
    final_answer = None
    steps_taken = 0
    format_retries = 0
    transcript_budget = PROMPT_CHAR_BUDGET - len(system_prompt)

    while steps_taken < max_steps:
        transcript = _build_transcript(question, turns, transcript_budget)
        if steps_taken == max_steps - 1:
            transcript += f"\n\n{LAST_STEP_REMINDER}"
        response = generate_completion(system_prompt, transcript, role="analyst")
        steps_taken += 1
        final_match = re.search(r"FINAL ANSWER\s*:\s*(.*)", _normalise(response), re.IGNORECASE | re.DOTALL)
        if final_match and final_match.group(1).strip():
            final_answer = final_match.group(1).strip()
            break

        action = _parse_action(response)
        if action is None:
            # Ask the model to fix the format instead of discarding the run.
            # The retry uses up one of the step budget.
            format_retries += 1
            turns.append([response, FORMAT_REMINDER])
            continue

        action_name, action_input = action
        if action_name == "run_sql":
            observation = run_sql(action_input, db_path)
            queries_executed.append(action_input)
        else:
            observation = search_schema(action_input)
        observations.append(observation)
        turns.append([response, observation])

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
        "format_retries": format_retries,
    }
