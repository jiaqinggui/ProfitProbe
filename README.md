# ProfitProbe

ProfitProbe (SMU IS469, group G1T2) tests whether LLM agents can explain why an
e-commerce business's profit fell. We plant known problems in copies of the
Olist store database, ask the same 30 questions to three designs, and score
every answer against the planted ground truth:

1. One-shot Text-to-SQL
2. Single Groq ReAct analyst
3. Multi-agent workflow with Groq planner, analyst, auditor, and reporter

All roles use the same model (`qwen/qwen3.8-27b` on Groq) at temperature 0.

## Reproduce our results (no API key needed)

Our final answers are committed in `evaluations_results_part1.csv` to `part3.csv`,
and the hand corrections in `manual_review.csv`. To see the reported scores
(One-shot 9/30, ReAct 4/30, Multi-agent 3/30) and browse every answer:

```bash
python -m venv .venv && source .venv/bin/activate   # Python 3.10+; Windows: see below
pip install -r requirements.txt
python build_base_db.py          # about 10 seconds; needed for the questions shown in the app
python injector.py               # same 80 scenarios on every machine
python merge_results.py          # -> evaluations_results.csv
python score_results.py          # prints the scores
python -m streamlit run app.py   # use the venv's Streamlit, not a global/Anaconda one
```

Run Streamlit through the virtual environment: an older global Streamlit
(for example Anaconda's 1.45) fails with `TextColumn() got an unexpected
keyword argument 'alignment'`.

## Quick start (rerun the evaluation)

Rerunning the agents calls Groq and needs your own key. Create a free key at
[console.groq.com/keys](https://console.groq.com/keys); the model
(`qwen/qwen3.8-27b`) runs on Groq's servers, so nothing is downloaded.

**Determinism.** `injector.py` uses no randomness: it picks each scenario's
week, category and seller with fixed SQL ordering rules, so every machine
builds identical scenario databases and `labels.json` (the `seed` in
`config.json` is not used). All model settings are fixed in `llm_settings.json`
(temperature 0, top-p 0.1). A hosted model can still vary slightly between
calls even at temperature 0, so a rerun may not match our answers word for
word; the committed part CSVs are the exact answers we scored.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: see below
pip install -r requirements.txt
cp .env.example .env             # then put your GROQ_API_KEY in .env
python build_base_db.py          # Olist CSVs in archive/ -> olist_base.db
python injector.py               # 80 scenario databases + labels.json
python evaluations.py --part 1   # your 10-case share (parts 1-3)
python merge_results.py          # combine the three part files
python score_results.py          # scores, with manual corrections applied
streamlit run app.py             # browse every answer beside the ground truth
```

The Olist data is included in `archive/` (source:
[Kaggle](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)). The
scenario injection is deterministic, so every machine generates the same
scenarios. The sections below explain each step.

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
ProfitProbe\
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
    "max_react_steps": 8,
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

These are set to a quota-conscious evaluation profile. Groq pacing makes the
benchmark slow, but reduces the chance of per-minute rate-limit failures.
Increase `max_react_steps` or `max_audit_rounds` only if you prefer deeper
analysis over shorter runtimes and have enough quota.
`max_react_steps` was raised from 3 to 8 because, with 3 steps, the ReAct
analyst used every step on queries and never reached a final answer in any of
the 30 benchmark cases. The multi-agent analyst is the same ReAct loop, so it
uses the same limit.
Groq retries are disabled so a failed request is not silently repeated and
counted again.

All current agent roles use Groq with a deliberately small response budget.
Gemini and OpenRouter support remains in `src/llm_client.py`, but neither
provider is active in the current role configuration. If Gemini is selected
later, its requests are paced at approximately four per minute to stay below
the free-tier five-requests-per-minute limit. A single quota retry is allowed
when the API provides a retry delay.
Groq requests are paced at 65 seconds apart and SDK retries are disabled.
This intentionally prioritizes staying below the reported 1,000
output-tokens-per-minute budget over speed. The larger response budgets give
the agents enough room to complete their required formats. The agents report the cause and affected entity; scenario dollar values remain
ground-truth evaluation fields rather than model predictions.

`config.json` identifies the settings file and stores database-level settings.
Normally, edit `llm_settings.json` rather than `config.json` for model behavior.

## Data flow

The read-only CSV files in `archive/` are converted by `build_base_db.py` into
`olist_base.db`. `injector.py` copies that baseline into isolated scenario
databases, applies controlled anomalies or leaves a clean control unchanged,
and writes the ground-truth metadata to `scenarios/labels.json`.

`evaluations.py` selects the 30 benchmark scenarios, sends prompts to the
Groq-configured roles, and executes SQL locally through `src/tools.py`; only
schema context, generated SQL, and bounded SQL observations are included in
LLM prompts. Results are saved to part CSVs or `evaluations_results.csv`,
`merge_results.py` combines split files, and `app.py` displays the results.

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
for that PowerShell process only:

```bash
$env:GROQ_API_KEY = "other_key"
python evaluations.py --part 2
```

### Rerun ReAct and multi-agent after a step-limit change

After changing `max_react_steps`, rerun the two systems that use the ReAct
loop on your saved share. One-shot answers in the part file are kept:

```bash
python evaluations.py --part 1 --rerun-agents   # person 1
python evaluations.py --part 2 --rerun-agents   # person 2
python evaluations.py --part 3 --rerun-agents   # person 3
```

Pull first so your part file is the latest committed version. For each case,
a system is rerun when its saved result used a different step limit
(`react_max_steps` or `multi_agent_analyst_steps`) or failed with a provider
error such as a rate limit or timeout. Other errors, such as a response in the
wrong format, are kept as that system's result. Each system's result is saved
as soon as it finishes, and rerunning the same command resumes. Commit and
push the updated part file when it finishes.

An 8-step ReAct run uses roughly 20,000 Groq tokens and a multi-agent run
roughly 25,000 to 30,000. The free tier allows 200,000 tokens per day per
account on a rolling window, so a 10-case share may not finish in one
session. A `RateLimitError` mentioning "tokens per day (TPD)" means the key is
used up for now; rerun the same command a few hours later to finish.

### Retry runs that ended without an answer

`--retry-failed` reruns every ReAct or multi-agent run in your share that has
no answer, whatever the error (format, request too large, daily limit,
timeout) or because it hit the step limit. Answers already given are kept.
`--only react` or `--only multi` limits it to one system:

```bash
python evaluations.py --part 1 --rerun-agents --retry-failed --only multi
```

Our final multi-agent results used this after the ReAct loop was fixed to
accept small format variations, ask the model to retry an unreadable reply,
request a final answer on the last step, and trim the oldest query results so
prompts stay under Groq's 7,000-token input limit.

Once all three part files are in the repository root, combine them:

```bash
python merge_results.py
```

This writes `evaluations_results.csv` for the Streamlit app and lists any
scenarios that are still missing.

## Score the results

After merging, score every system's answers against the ground truth:

```bash
python score_results.py
```

It prints a correct-answer count and a breakdown by scenario type for each
system, and writes per-scenario statuses to `evaluation_scores.csv`:
`correct`, `cause_only` (right cause, wrong or missing entity), `wrong`,
`false_cause` (a control answer that blames a specific cause), `no_answer`
(ReAct ran out of steps), and `error`.

Scoring uses keyword rules, so treat it as a first pass and review the
answers in the Streamlit app before reporting. Labels corrected by hand are
recorded, with a reason, in `manual_review.csv`; `score_results.py` and the
Streamlit app both apply them. A correction is applied only while the answer
still gets the keyword label that was reviewed, so a rerun answer is scored
afresh and the script asks for it to be reviewed again. An answer that reports no
cause scores as correct on a control, so a system that rarely commits to a
cause can look strong on controls; compare the control scores with the
discount and shipping scores.

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
