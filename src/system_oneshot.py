import re

from src.llm_client import generate_completion
from src.tools import run_sql, search_schema


def _strip_sql_markdown(response: str) -> str:
    fenced_match = re.search(r"```(?:sql)?\s*(.*?)```", response, re.IGNORECASE | re.DOTALL)
    sql = fenced_match.group(1).strip() if fenced_match else response.strip()
    sql_match = re.search(r"\bSELECT\b.*", sql, re.IGNORECASE | re.DOTALL)
    if not sql_match:
        raise ValueError("The LLM response did not contain a SELECT query.")
    return sql_match.group(0).strip().rstrip(";") + ";"


def run_oneshot_sql(db_path: str, question: str, llm_client=None) -> dict:
    """Generate one SQL query with Groq, execute it, and summarize the result."""
    del llm_client
    schema = search_schema("full schema")
    sql_system_prompt = f"""
You are a precise SQLite data analyst.
Use only the schema and business formulas below:
{schema}

Return exactly one read-only SQLite SELECT query. Do not return Markdown,
explanations, comments, or more than one statement.
"""
    generated_response = generate_completion(
        sql_system_prompt,
        f"Question: {question}",
        role="oneshot",
    )
    generated_sql = _strip_sql_markdown(generated_response)
    sql_result = run_sql(generated_sql, db_path)

    answer_system_prompt = """
You are a data analyst reporting a database investigation.
Answer concisely in plain text. State the root cause, the specific entity,
and the exact dollar impact when the query result supports it. If the result
does not support a reliable cause or amount, say that explicitly.
Do not invent values.
"""
    final_answer = generate_completion(
        answer_system_prompt,
        f"Question: {question}\nSQL query:\n{generated_sql}\nSQL result:\n{sql_result}",
        role="oneshot",
    )

    return {
        "system": "One-shot",
        "generated_sql": generated_sql,
        "sql_result": sql_result,
        "final_answer": final_answer,
    }
