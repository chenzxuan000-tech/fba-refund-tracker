# Amazon FBA Return Refund Analyzer

Local Streamlit MVP for comparing Amazon refund transactions with FBA return reports.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## MVP Inputs

- FBA Customer Returns Report / Returns Report (`.csv`, `.txt`, `.xlsx`, `.xls`)
- Payments / Transaction report containing refund deductions (`.csv`, `.txt`, `.xlsx`, `.xls`)
- Inventory Ledger Report (`.csv`, `.txt`, `.xlsx`, `.xls`) for warehouse receipt checks
- Reimbursements Report (`.csv`, `.txt`, `.xlsx`, `.xls`) as a reserved compatibility input

The parser is designed for UTF-8, UTF-16, ANSI-like encodings, comma-separated files, and tab-delimited text exports.

## Output

The app produces:

- Suspicious refunded order table
- Risk scoring fields: risk level, risk score, risk reasons, and potential return abuse flag
- Top Risk Orders dashboard sortable by ASIN, SKU, Buyer, refund amount, or risk score
- ASIN / SKU refund and return summary
- Return reason share and reason category summary
- ASIN-level optimization recommendations
- Optional AI operations report using OpenAI / MiniMax / DeepSeek compatible chat APIs
- Excel export with all analysis sheets

## Code Layout

- `src/parser.py`: file encoding, delimiter, and date parsing.
- `src/matcher.py`: Amazon report field matching and missing-field checks.
- `src/analyzer.py`: Inventory Ledger receipt matching and order lifecycle.
- `src/risk.py`: refund risk scoring and buyer abuse detection.
- `src/analysis.py`: refund/return analysis and export assembly.
- `src/dashboard.py`: Streamlit data preview and lifecycle display helpers.

AI API keys are entered in the Streamlit page at runtime and are not stored by the app.
