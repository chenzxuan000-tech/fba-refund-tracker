from datetime import date

from src.analysis import analyze_refunds_returns, summarize_return_reasons
from src.priority import apply_operation_priorities
from src.sample_data import load_sample_reports


def test_sample_reports_include_all_supported_report_types():
    reports = load_sample_reports()

    assert set(reports) == {"returns", "payments", "inventory_ledger", "reimbursements"}
    assert all(not frame.empty for frame in reports.values())


def test_sample_reports_run_through_real_analysis_pipeline():
    reports = load_sample_reports()

    result = analyze_refunds_returns(
        reports["returns"],
        reports["payments"],
        observation_window_days=60,
        as_of=date(2026, 5, 20),
        ledger_df=reports["inventory_ledger"],
        reimbursement_df=reports["reimbursements"],
    )
    result = apply_operation_priorities(result)
    reasons = summarize_return_reasons(reports["returns"])

    assert len(result) == 4
    assert "Operation Priority" in result.columns
    assert "Risk Level With Confidence" in result.columns
    assert not reasons.empty
