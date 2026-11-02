# Running TeleTask

TeleTask compares three LLM-based data-analysis architectures:

1. One-shot Text-to-SQL
2. Single Gemini ReAct analyst
3. Multi-agent workflow with a Groq planner, analyst, and auditor, followed by a Gemini reporter

## Install dependencies

From the repository root, create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, use the Python executable directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Configure API keys

Create or edit the root-level `.env` file:

```text
TeleTask\
  .env
  config.json
  llm_settings.json
```

Add the provider keys using these exact variable names:

```env
GROQ_API_KEY=your_groq_key
GEMINI_API_KEY=your_gemini_key
```

The application loads `.env` automatically through
[src/llm_client.py](src/llm_client.py).

## Edit models and hyperparameters

Edit [llm_settings.json](llm_settings.json). Settings are configured per role:

- `oneshot`: Gemini SQL generation and answer summarization
- `analyst`: Gemini ReAct loop
- `planner`: Gemini hypothesis generation
- `auditor`: Gemini verification
- `reporter`: Gemini final report

Example:

```json
{
  "analyst": {
    "provider": "gemini",
    "model_name": "gemini-3.8-flash",
    "temperature": 0.0,
    "top_p": 0.1,
    "frequency_penalty": 0.3,
    "stop_sequences": [],
    "max_tokens": 300
  }
}
```

The editable shared limits are in the same file:

```json
{
  "agent_limits": {
    "max_react_steps": 2,
    "max_audit_rounds": 1,
    "sql_timeout_seconds": 3,
    "sql_row_limit": 25,
    "api_timeout_seconds": 15,
    "gemini_min_interval_seconds": 13,
    "gemini_rate_limit_retries": 1
  }
}
```

These are set to a fast evaluation profile. Increase `max_react_steps` or
`max_audit_rounds` if you prefer deeper analysis over shorter runtimes.
Groq retries are disabled so a failed request is not silently repeated and
counted again.

All current agent roles use Gemini to avoid the Groq output-token rate limit.
The reporter also uses Gemini.
Gemini requests are paced at approximately four per minute to stay below the
free-tier five-requests-per-minute limit. A single quota retry is allowed when
the API provides a retry delay.

`config.json` identifies the settings file and stores database-level settings.
Normally, edit `llm_settings.json` rather than `config.json` for model behavior.

## Build the baseline database

The original CSV files are kept in [archive/](archive/) and are read without
modification. Run:

```powershell
python build_base_db.py
```

This creates:

```text
olist_base.db
```

The script reads all source CSVs from `archive/` and writes the generated
database to the repository root.

## Generate isolated scenarios

After creating `olist_base.db`, run:

```powershell
python injector.py
```

This creates:

```text
scenarios\
  labels.json
  scenario_01.db
  scenario_02.db
  ...
```

The injector creates exactly 40 scenarios:

- Scenarios 1–5: wrong discount code
- Scenarios 6–10: shipping delay
- Scenarios 11–15: returns/cancellation spike
- Scenarios 16–20: price error
- Scenarios 21–25: cost increase
- Scenarios 26–30: lost payment channel
- Scenarios 31–35: bad reviews and future-order loss
- Scenarios 36–40: clean controls

The source CSVs in `archive/` remain unchanged.

The `discount_code` and `discount_amount` columns exist in the baseline
`order_items` table for every scenario. They start empty and zero-valued.
Scenarios 1–5 populate them with the main wrong-discount anomaly. Scenarios
6–7 also set a small distractor discount amount on a few items. The first two
scenarios in each of the first five anomaly groups include a secondary
distractor, for exactly 10 distractor scenarios. Scenarios 36–40 are controls
and do not receive a planted data modification.

## Run the evaluation

Run one command from the repository root:

```powershell
python run_evaluation.py
```

Run this command from the project root. It automatically:

1. Builds `olist_base.db` if it does not exist.
2. Generates exactly 40 scenario databases if the scenario set is missing,
   incomplete, or still from the older 12-scenario version.
3. Runs:

- `run_oneshot_sql`
- `run_single_react_agent`
- `run_multi_agent_team`

The terminal shows the current scenario, active system, elapsed heartbeat
messages while waiting for an API response, and a completed-scenario progress
bar. Results are saved after each completed scenario.

It writes results to:

```text
evaluation_results.csv
```

The output includes ground-truth labels, generated answers, generated SQL,
ReAct step counts, query counts, audit rounds, and per-system errors.

## Run the three-scenario trial

To run only scenarios 1, 2, and 3, use:

```powershell
python run_trial.py
```

The trial writes its output to `trial_results_1_3.csv` and saves each completed
scenario immediately. It does not overwrite `evaluation_results.csv`. Close
the CSV in Excel or another editor before running. If Windows locks the main
file, the trial automatically writes to `trial_results_1_3_recovery.csv`
instead.

The original [evaluation_runner.ipynb](evaluation_runner.ipynb) remains
available as an optional interactive notebook, but it is not required for the
normal workflow.

## Compare results

The evaluation script writes results to:

```text
evaluation_results.csv
```

The output includes each scenario's ground-truth labels, generated answers,
generated SQL, ReAct step counts, query counts, audit rounds, and errors.

Start the basic comparison app with:

```powershell
python -m streamlit run app.py
```

The app lets you select a scenario and compare the One-shot, Single ReAct, and
Multi-Agent answers side by side. It also shows the ground-truth cause, entity,
and dollar impact when those fields are available.

## Run an individual system from Python

The agent functions accept a scenario database path and a question:

```python
from src.system_oneshot import run_oneshot_sql
from src.system_react import run_single_react_agent
from src.system_multi import run_multi_agent_team

db_path = "scenarios/scenario_01.db"
question = "Why did profit fall sharply in week 32 of 2017?"

oneshot_result = run_oneshot_sql(db_path, question)
react_result = run_single_react_agent(db_path, question)
multi_result = run_multi_agent_team(db_path, question)
```

The first live run makes external API calls and may consume provider quota.

## Changing hyperparameters

After editing [llm_settings.json](llm_settings.json), run:

```powershell
python run_evaluation.py
```

The existing database and complete 40-scenario set are reused, so changing
hyperparameters does not rebuild the CSV-derived data. The new agent answers
overwrite `evaluation_results.csv`.

If the scenario set is incomplete, `run_evaluation.py` regenerates it before
running the agents.

If a provider request fails for one scenario, the evaluator records the error
in that system's `*_error` column and continues with the other systems and
scenarios. The Streamlit app displays those errors alongside any successful
answers. After correcting a model or API setting, rerun
`python run_evaluation.py` to replace the failed results.

## Provider routing

The current routing is:

| Role | Provider |
|---|---|
| `oneshot` | Groq |
| `analyst` | Groq |
| `planner` | Groq |
| `auditor` | Groq |
| `reporter` | Gemini |

Change a role's `provider` and `model_name` in `llm_settings.json` if you want
to use a different supported provider/model combination.
