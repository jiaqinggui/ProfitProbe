# Running TeleTask

TeleTask compares three LLM-based data-analysis architectures:

1. One-shot Text-to-SQL
2. Single Groq ReAct analyst
3. Multi-agent workflow with Groq planner, analyst, auditor, and reporter

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
OPENROUTER_API_KEY=your_openrouter_key
```

The application loads `.env` automatically through
[src/llm_client.py](src/llm_client.py).

## Edit models and hyperparameters

Edit [llm_settings.json](llm_settings.json). Settings are configured per role:

- `oneshot`: Groq SQL generation and answer summarization
- `analyst`: Groq ReAct loop
- `planner`: Groq hypothesis generation
- `auditor`: Groq verification
- `reporter`: Groq final report

Example:

```json
{
  "analyst": {
    "provider": "groq",
    "model_name": "qwen/qwen3.8-27b",
    "temperature": 0.0,
    "top_p": 0.1,
    "frequency_penalty": 0.3,
    "stop_sequences": [],
    "max_tokens": 400
  }
}
```

The editable shared limits are in the same file:

```json
{
  "agent_limits": {
    "max_react_steps": 3,
    "max_audit_rounds": 1,
    "sql_timeout_seconds": 3,
    "sql_row_limit": 25,
    "api_timeout_seconds": 20,
    "gemini_min_interval_seconds": 13,
    "gemini_rate_limit_retries": 1,
    "openrouter_min_interval_seconds": 4,
    "openrouter_rate_limit_retries": 1,
    "groq_min_interval_seconds": 65,
    "groq_rate_limit_retries": 0
  }
}
```

These are set to a fast evaluation profile. Increase `max_react_steps` or
`max_audit_rounds` if you prefer deeper analysis over shorter runtimes.
Groq retries are disabled so a failed request is not silently repeated and
counted again.

All current agent roles use Groq with a deliberately small response budget.
If Gemini is selected later, its requests are paced at approximately four per
minute to stay below the free-tier five-requests-per-minute limit. A single
quota retry is allowed when the API provides a retry delay.
Groq requests are paced at 65 seconds apart and SDK retries are disabled.
This intentionally prioritizes staying below the reported 1,000
output-tokens-per-minute budget over speed. The larger response budgets and
three ReAct steps give the agents enough room to complete their required
formats. The agents report the cause and affected entity; scenario dollar
values remain ground-truth evaluation fields rather than model predictions.

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

The injector creates exactly 80 scenarios:

- Scenarios 1–10: wrong discount code
- Scenarios 11–20: shipping delay
- Scenarios 21–30: returns/cancellation spike
- Scenarios 31–40: price error
- Scenarios 41–50: cost increase
- Scenarios 51–60: lost payment channel
- Scenarios 61–70: bad reviews and future-order loss
- Scenarios 71–80: clean controls

The source CSVs in `archive/` remain unchanged.

The standard evaluation uses a focused 30-scenario benchmark through
`evaluations.py`:

- Scenarios 1–10: wrong discount code
- Scenarios 11–20: shipping delay
- Scenarios 71–80: clean controls

These are 30 unique scenarios: ten cases from each selected group. The
remaining generated scenarios are retained for an optional extended
evaluation.

The `discount_code` and `discount_amount` columns exist in the baseline
`order_items` table for every scenario. They start empty and zero-valued.
Scenarios 1–10 populate them with the main wrong-discount anomaly. The first
two scenarios in each of the first five anomaly groups include a secondary
distractor, for exactly 10 distractor scenarios. Scenarios 71–80 are controls
and do not receive a planted data modification.

## Run the evaluation

Run the focused benchmark from the repository root:

```powershell
python evaluations.py
```

Run this command from the project root. It automatically:

1. Builds `olist_base.db` if it does not exist.
2. Generates the complete 80-scenario set if the scenario set is missing or
   incomplete.
3. Runs:

- `run_oneshot_sql`
- `run_single_react_agent`
- `run_multi_agent_team`

The terminal shows the current scenario, active system, elapsed heartbeat
messages while waiting for an API response, and a completed-scenario progress
bar. Results are saved after each completed scenario.

It writes results to:

```text
evaluations_results.csv
```

The output includes ground-truth labels, generated answers, generated SQL,
ReAct step counts, query counts, audit rounds, and per-system errors.
The standard run evaluates 30 unique scenarios: ten wrong-discount scenarios,
ten shipping-delay scenarios, and ten clean controls.

## Split the benchmark across three people

Each person uses their own Groq key in their own `.env`, because Groq limits
are per account. Each person runs one share of 10 cases:

```bash
python evaluations.py --part 1   # person 1
python evaluations.py --part 2   # person 2
python evaluations.py --part 3   # person 3
```

Each share mixes discount, shipping, and control scenarios and writes to
`evaluations_results_part<N>.csv`. Rerunning the same command resumes: cases
that already completed without errors are skipped.

To run a second share on the same machine with another key, override the key
for that terminal only:

```bash
GROQ_API_KEY=other_key python evaluations.py --part 2
```

Once all three part files are in the repository root, combine them:

```bash
python merge_results.py
```

This writes `evaluations_results.csv` for the Streamlit app and lists any
scenarios that are still missing.

## Run the three-scenario trial

To run only scenarios 1, 2, and 3, use:

```powershell
python run_trial.py
```

The trial writes its output to `trial_results_1_3.csv` and saves each completed
scenario immediately. It does not overwrite `evaluations_results.csv`. Close
the CSV in Excel or another editor before running. If Windows locks the main
file, the trial automatically writes to `trial_results_1_3_recovery.csv`
instead.

The command-line runners are the supported evaluation workflow. Use
`python evaluations.py` for the current 30-case benchmark.

## Compare results

The evaluation script writes results to:

```text
evaluations_results.csv
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
python evaluations.py
```

The existing database and complete 80-scenario set are reused, so changing
hyperparameters does not rebuild the CSV-derived data. The new agent answers
overwrite `evaluations_results.csv`.

If the scenario set is incomplete, `evaluations.py` regenerates it before
running the agents.

If a provider request fails for one scenario, the evaluator records the error
in that system's `*_error` column and continues with the other systems and
scenarios. The Streamlit app displays those errors alongside any successful
answers. After correcting a model or API setting, rerun
`python evaluations.py` to replace the failed results.

## Provider routing

The current routing is:

| Role | Provider |
|---|---|
| `oneshot` | Groq |
| `analyst` | Groq |
| `planner` | Groq |
| `auditor` | Groq |
| `reporter` | Groq |

Change a role's `provider` and `model_name` in `llm_settings.json` if you want
to use a different supported provider/model combination.
