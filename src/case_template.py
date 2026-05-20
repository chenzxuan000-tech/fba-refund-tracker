from __future__ import annotations

import pandas as pd


def build_amazon_case_text(row: pd.Series) -> str:
    order_id = _text(row.get("Order ID")) or "[Order ID]"
    refund_amount = _money(row.get("Refund Amount"))
    refund_date = _text(row.get("Refund Date")) or "待确认"
    days_since_refund = _days_text(row.get("Days Since Refund"))
    risk_type = _text(row.get("Risk Diagnosis")) or _text(row.get("Risk Level With Confidence")) or "需要人工确认"
    reason = _case_reason(row)

    return "\n".join(
        [
            "您好，",
            "",
            f"请协助核查订单 {order_id} 的退货入仓及商品状态。",
            "",
            f"订单号：{order_id}",
            f"退款金额：{refund_amount}",
            f"退款日期：{refund_date}",
            f"距今：{days_since_refund}",
            f"当前判断：{risk_type}",
            "",
            f"核查原因：{reason}",
            "",
            "请确认：",
            "1. 商品是否已退回 FBA；",
            "2. 当前状态是否可售 / 不可售；",
            "3. 如不符合赔偿条件，请告知原因。",
            "",
            "谢谢。",
        ]
    )


def build_case_filename(row: pd.Series) -> str:
    order_id = _text(row.get("Order ID")) or "amazon-order"
    safe_order_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in order_id)
    return f"amazon_case_{safe_order_id}.txt"


def _case_reason(row: pd.Series) -> str:
    if _is_suspected_not_returned(row):
        return "订单已退款，但当前报表暂未匹配到明确退货入仓证据，需要人工确认。"
    if _has_buyer_damage_signal(row):
        return "系统发现商品可能存在买家损坏或不可售状态，请协助确认最终状态。"
    if _has_inventory_shortage_signal(row):
        return "当前未匹配到明确 FBA 入仓流水，需要协助核对库存记录。"
    return "退货、退款或库存证据不完整，需要人工确认订单状态。"


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
        return "待确认"
    return f"{int(days)} 天"


def _number(value: object) -> float:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return 0.0 if pd.isna(number) else float(number)


def _text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()
