from __future__ import annotations

import pandas as pd


def load_sample_reports() -> dict[str, pd.DataFrame]:
    """Return small Amazon-like reports for first-time product walkthroughs."""
    returns = pd.DataFrame(
        [
            {
                "amazon-order-id": "701-1000000-0000001",
                "asin": "B0SAMPLE01",
                "sku": "KITCHEN-S-BLK",
                "product-name": "Kitchen Storage Rack - Small Black",
                "return-date": "2026-04-10",
                "return-reason": "QUALITY_UNACCEPTABLE",
                "disposition": "SELLABLE",
                "quantity": 1,
            },
            {
                "amazon-order-id": "701-1000000-0000002",
                "asin": "B0SAMPLE02",
                "sku": "CUTTING-L-WHT",
                "product-name": "Cutting Board Set - Large White",
                "return-date": "2026-03-28",
                "return-reason": "DAMAGED_BY_CARRIER",
                "disposition": "CUSTOMER_DAMAGED",
                "quantity": 1,
            },
            {
                "amazon-order-id": "701-1000000-0000003",
                "asin": "B0SAMPLE03",
                "sku": "BAG-M-GRY",
                "product-name": "Travel Bag - Medium Gray",
                "return-date": "2026-04-18",
                "return-reason": "NOT_COMPATIBLE",
                "disposition": "SELLABLE",
                "quantity": 1,
            },
        ]
    )
    payments = pd.DataFrame(
        [
            {
                "order id": "701-1000000-0000001",
                "posted date": "2026-04-08",
                "transaction type": "Refund",
                "description": "Refund for return",
                "sku": "KITCHEN-S-BLK",
                "product name": "Kitchen Storage Rack - Small Black",
                "marketplace": "Amazon.com",
                "total": "-32.99",
                "buyer name": "Sample Buyer A",
                "buyer email": "sample-a@example.com",
            },
            {
                "order id": "701-1000000-0000002",
                "posted date": "2026-03-20",
                "transaction type": "Refund",
                "description": "Customer refund",
                "sku": "CUTTING-L-WHT",
                "product name": "Cutting Board Set - Large White",
                "marketplace": "Amazon.com",
                "total": "-118.50",
                "buyer name": "Sample Buyer B",
                "buyer email": "sample-b@example.com",
            },
            {
                "order id": "701-1000000-0000004",
                "posted date": "2026-03-05",
                "transaction type": "Refund",
                "description": "Refund issued",
                "sku": "BOTTLE-XL-RED",
                "product name": "Sports Bottle - XL Red",
                "marketplace": "Amazon.com",
                "total": "-76.20",
                "buyer name": "Sample Buyer C",
                "buyer email": "sample-c@example.com",
            },
            {
                "order id": "701-1000000-0000005",
                "posted date": "2026-05-02",
                "transaction type": "Refund",
                "description": "Refund issued",
                "sku": "KITCHEN-S-BLK",
                "product name": "Kitchen Storage Rack - Small Black",
                "marketplace": "Amazon.com",
                "total": "-29.99",
                "buyer name": "Sample Buyer A",
                "buyer email": "sample-a@example.com",
            },
        ]
    )
    ledger = pd.DataFrame(
        [
            {
                "date": "2026-04-11",
                "disposition": "SELLABLE",
                "event-type": "Customer Return",
                "reference-id": "701-1000000-0000001",
                "fnsku": "X0SAMPLE01",
                "msku": "KITCHEN-S-BLK",
                "quantity": 1,
                "fulfillment-center-id": "ONT8",
                "reason": "Customer return received",
            },
            {
                "date": "2026-03-30",
                "disposition": "CUSTOMER_DAMAGED",
                "event-type": "Customer Return",
                "reference-id": "701-1000000-0000002",
                "fnsku": "X0SAMPLE02",
                "msku": "CUTTING-L-WHT",
                "quantity": 1,
                "fulfillment-center-id": "LGB8",
                "reason": "Customer damaged",
            },
            {
                "date": "2026-05-05",
                "disposition": "SELLABLE",
                "event-type": "Adjustment",
                "reference-id": "",
                "fnsku": "X0SAMPLE05",
                "msku": "KITCHEN-S-BLK",
                "quantity": 1,
                "fulfillment-center-id": "ONT8",
                "reason": "Inventory adjustment after return",
            },
        ]
    )
    reimbursements = pd.DataFrame(
        [
            {
                "amazon-order-id": "701-1000000-0000002",
                "approval-date": "2026-04-05",
                "amount-total": "118.50",
                "reason": "CustomerReturn",
            }
        ]
    )
    return {
        "returns": returns,
        "payments": payments,
        "inventory_ledger": ledger,
        "reimbursements": reimbursements,
    }
