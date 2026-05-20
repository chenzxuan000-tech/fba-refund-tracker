from __future__ import annotations

import pandas as pd


def build_amazon_case_text(row: pd.Series) -> str:
    order_id = _text(row.get("Order ID")) or "[Order ID]"
    refund_amount = _money(row.get("Refund Amount"))
    refund_date = _text(row.get("Refund Date")) or "not available"
    days_since_refund = _days_text(row.get("Days Since Refund"))
    risk_type = _text(row.get("Risk Diagnosis")) or _text(row.get("Risk Level With Confidence")) or "Manual review"
    reason_lines = _case_reason_lines(row)

    return "\n".join(
        [
            "Dear Amazon Seller Support,",
            "",
            f"Please verify the FBA return and inventory status for order {order_id}.",
            "",
            "Order details:",
            f"- Amazon Order ID: {order_id}",
            f"- Refund amount: {refund_amount}",
            f"- Refund date: {refund_date}",
            f"- Time since refund: {days_since_refund}",
            f"- Current review type: {risk_type}",
            "",
            "Reason for this case:",
            *[f"- {line}" for line in reason_lines],
            "",
            "Requested action:",
            "- Please confirm whether the returned unit was received by FBA.",
            "- Please confirm the final item disposition, including sellable, unsellable, buyer-damaged, carrier-damaged, or missing inventory status.",
            "- If the unit was not returned, lost, damaged, or otherwise eligible under Amazon policy, please advise whether reimbursement or further investigation is available.",
            "- If this order is not eligible for reimbursement, please provide the reason so we can close our internal review.",
            "",
            "Thank you.",
        ]
    )


def build_case_filename(row: pd.Series) -> str:
    order_id = _text(row.get("Order ID")) or "amazon-order"
    safe_order_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in order_id)
    return f"amazon_case_{safe_order_id}.txt"


def _case_reason_lines(row: pd.Series) -> list[str]:
    reasons = []
    if _is_suspected_not_returned(row):
        reasons.append(
            "The order was refunded, but our uploaded reports do not show a clear return received confirmation."
        )
    if _has_buyer_damage_signal(row):
        reasons.append(
            "The available return or inventory data indicates a possible buyer-damaged or unsellable condition; please verify condition and reimbursement eligibility."
        )
    if _has_inventory_shortage_signal(row):
        reasons.append(
            "We found no confirmed FBA receiving or inventory movement record, so we need help with inventory reconciliation."
        )
    if not reasons:
        reasons.append(
            "The order requires manual verification because the return, refund, or inventory evidence is incomplete."
        )
    return reasons


def _is_suspected_not_returned(row: pd.Series) -> bool:
    matched_returns = _text(row.get("Matched Returns")) == "是"
    matched_ledger = _text(row.get("Matched Ledger")) == "是"
    warehouse_received = _text(row.get("Amazon Warehouse Received")) == "是"
    days = _number(row.get("Days Since Refund"))
    return days >= 30 and not (matched_returns or matched_ledger or warehouse_received)


def _has_buyer_damage_signal(row: pd.Series) -> bool:
    text = " ".join(
        _text(row.get(column)).lower()
        for column in [
            "Return Reason",
            "Ledger Reason",
            "Ledger Disposition",
            "Disposition / Sellable Status",
        ]
    )
    return any(
        keyword in text
        for keyword in ["buyer", "customer_damaged", "customer damaged", "damaged", "unsellable"]
    )


def _has_inventory_shortage_signal(row: pd.Series) -> bool:
    matched_ledger = _text(row.get("Matched Ledger")) == "是"
    warehouse_received = _text(row.get("Amazon Warehouse Received")) == "是"
    ledger_event = _text(row.get("Ledger Event Type"))
    return not matched_ledger and not warehouse_received and not ledger_event


def _money(value: object) -> str:
    number = _number(value)
    return f"${number:,.2f}"


def _days_text(value: object) -> str:
    days = _number(value)
    if days <= 0:
        return "not available"
    return f"{int(days)} days"


def _number(value: object) -> float:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return 0.0 if pd.isna(number) else float(number)


def _text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()
