from __future__ import annotations

import pandas as pd


ORDER_OPERATION_STATUSES = [
    "待处理",
    "人工复核",
    "待观察",
    "已开Case",
    "已核查",
    "已忽略",
    "已确认正常",
]


def normalize_order_operation_status(status: object) -> str:
    text = "" if pd.isna(status) else str(status).strip()
    return text if text in ORDER_OPERATION_STATUSES else "待处理"


def apply_order_statuses(
    orders_df: pd.DataFrame,
    status_by_order_id: dict[str, str] | None,
) -> pd.DataFrame:
    result = orders_df.copy()
    status_by_order_id = status_by_order_id or {}
    if "Order ID" not in result.columns:
        result["Operation Status"] = "待处理"
        return result

    result["Operation Status"] = result["Order ID"].apply(
        lambda order_id: normalize_order_operation_status(
            status_by_order_id.get(str(order_id), "待处理")
        )
    )
    return result
