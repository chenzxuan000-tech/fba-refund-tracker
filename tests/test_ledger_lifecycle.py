from datetime import date

import pandas as pd

from src.analysis import analyze_refunds_returns
from src.analyzer import build_order_lifecycle


def test_inventory_ledger_marks_refunded_order_as_received_by_amazon():
    returns = pd.DataFrame(columns=["amazon-order-id"])
    payments = pd.DataFrame(
        [
            {
                "order-id": "111-1",
                "posted-date": "2026-03-01",
                "transaction-type": "Refund",
                "total": "-25.00",
            }
        ]
    )
    ledger = pd.DataFrame(
        [
            {
                "date": "2026-03-18T10:00:00+00:00",
                "disposition": "SELLABLE",
                "event-type": "CustomerReturns",
                "reference-id": "111-1",
                "fnsku": "X001",
                "quantity": "1",
                "fulfillment-center-id": "ONT8",
                "reason": "Customer return",
            }
        ]
    )

    result = analyze_refunds_returns(
        returns,
        payments,
        observation_window_days=60,
        as_of=date(2026, 5, 18),
        ledger_df=ledger,
    )

    row = result.iloc[0]
    assert row["Amazon Warehouse Received"] == "是"
    assert row["Ledger Disposition"] == "SELLABLE"
    assert row["Status"] == "亚马逊已收货且可售"
    assert row["Risk Level"] == "Low"


def test_build_order_lifecycle_returns_timeline_steps():
    order = pd.Series(
        {
            "Order ID": "111-1",
            "Refund Date": date(2026, 3, 1),
            "Return Date": pd.NaT,
            "Amazon Warehouse Received": "是",
            "Ledger Date": date(2026, 3, 18),
            "Ledger Disposition": "UNSELLABLE",
            "Days Since Refund": 78,
            "Status": "Amazon已收货但不可售",
        }
    )

    lifecycle = build_order_lifecycle(order, overdue_days=60)

    assert [step["label"] for step in lifecycle] == [
        "Order Created",
        "Refunded",
        "Return Requested",
        "Return Received",
        "Inventory Ledger Event",
        "Sellable / Unsellable",
        "Reimbursement",
    ]
    assert lifecycle[3]["status"] == "done"
    assert lifecycle[5]["status"] == "done"
    assert lifecycle[6]["status"] == "missing"
