import sqlite3
import pandas as pd
from pathlib import Path

from src.llm_client import load_agent_limits

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = str(PROJECT_ROOT / "olist_base.db")

def run_sql(query: str, db_path: str = DB_PATH) -> str:
    """
    Executes a read-only SQL query against the specified SQLite database.
    Enforces a strict 50-row limit to prevent massive payload returns.
    """
    if not query.strip().upper().startswith("SELECT"):
        return "Error: Only SELECT queries are permitted."
    
    # Enforce row limit
    limits = load_agent_limits()
    row_limit = int(limits["sql_row_limit"])
    timeout = float(limits["sql_timeout_seconds"])
    if "LIMIT" not in query.upper():
        query = query.rstrip(';') + f" LIMIT {row_limit};"
        
    try:
        with sqlite3.connect(db_path, timeout=timeout) as conn:
            conn.execute("PRAGMA query_only = ON;")
            df = pd.read_sql_query(query, conn)
        
        if df.empty:
            return "Query returned 0 rows."
        return df.to_string(index=False)
        
    except Exception as e:
        return f"SQL Error: {str(e)}"

def search_schema(query: str) -> str:
    """
    Retrieves the database schema and business definitions.
    Since the schema is small, we return the full schema directly to the agent.
    """
    import json
    
    schema_path = PROJECT_ROOT / "schema_dict.json"
        
    try:
        with schema_path.open(encoding="utf-8") as f:
            schema_data = json.load(f)
        return json.dumps(schema_data, indent=2)
    except Exception as e:
        return f"Error loading schema: {str(e)}"
