import pandas as pd

from src.order_workflow import (
    ORDER_OPERATION_STATUSES,
    apply_order_statuses,
    normalize_order_operation_status,
)


def test_apply_order_statuses_defaults_orders_to_pending():
    orders = pd.DataFrame([{"Order ID": "111-1"}, {"Order ID": "111-2"}])

    result = apply_order_statuses(orders, {})

    assert result["Operation Status"].tolist() == ["待处理", "待处理"]


def test_apply_order_statuses_uses_saved_operator_actions():
    orders = pd.DataFrame([{"Order ID": "111-1"}, {"Order ID": "111-2"}])

    result = apply_order_statuses(
        orders,
        {
            "111-1": "已开Case",
            "111-2": "已确认正常",
        },
    )

    assert result["Operation Status"].tolist() == ["已开Case", "已确认正常"]


def test_unknown_order_status_falls_back_to_pending():
    assert normalize_order_operation_status("随便写") == "待处理"
    assert "已忽略" in ORDER_OPERATION_STATUSES
