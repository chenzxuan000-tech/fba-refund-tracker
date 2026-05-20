# Amazon FBA Return Refund Analyzer

## Project Goal
Build a local MVP tool for Amazon sellers to compare refund transactions with FBA return records, identify refunded orders that have not returned to FBA within an observation window, and summarize return reasons by ASIN/SKU.

## Scope Rules
- MVP only: no Amazon API, no database, no login, no background jobs.
- Inputs are Seller Central CSV / Excel exports uploaded by the user.
- Keep logic easy to inspect and maintain for operations users.
- Prefer explicit, small modules over clever abstractions.

## Tech Stack
- Python
- Streamlit
- pandas
- openpyxl for Excel export
- pytest for core logic tests

## Directory Rules
- `app.py`: Streamlit UI only.
- `src/`: parsing, field matching, analysis, and export helpers.
- `tests/`: pytest tests for matching and risk logic.
- `data/raw/`: sample or manually placed raw files, never overwrite.
- `data/processed/`: generated intermediate files if needed.
- `exports/`: local export output if needed.
- `docs/`: notes and usage docs.

## Implementation Rules
- Keep all business logic out of `app.py` when it can be tested in `src/`.
- Add or update tests when changing field matching, status logic, risk logic, or export columns.
- Do not add APIs, databases, or cloud services unless explicitly requested.
- Do not commit real Seller Central reports or buyer/order-sensitive data.

## Evidence Language Rules
- Highest priority: the system must clearly distinguish confirmed facts, high-probability inferences, and AI guesses.
- Never package an inference as a fact. Missing matches mean "the system has not matched clear evidence", not "the product was not returned".
- UI, exports, case text, and AI report prompts must use cautious operational language for inferred states.
- When changing risk logic or report wording, include or preserve fields that explain evidence type, confidence, and the boundary of the conclusion.
