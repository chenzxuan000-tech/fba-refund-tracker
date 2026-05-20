from io import BytesIO

import pandas as pd

from src.matcher import detect_columns, validate_required_columns
from src.parser import parse_date_series, read_report_file, read_report_files


class NamedBytesIO(BytesIO):
    def __init__(self, content: bytes, name: str):
        super().__init__(content)
        self.name = name


def test_detect_columns_normalizes_real_amazon_order_id_variants():
    variants = ["amazon-order-id", "order-id", "order id", "Amazon Order ID"]

    for variant in variants:
        df = pd.DataFrame(columns=[variant, "posted-date", "total"])
        columns = detect_columns(df, "payments")
        assert columns.order_id == variant


def test_detect_columns_supports_inventory_ledger_required_fields():
    ledger = pd.DataFrame(
        columns=[
            "Date",
            "Disposition",
            "Event-Type",
            "Reference-ID",
            "FNSKU",
            "Quantity",
            "Fulfillment-Center-ID",
            "Reason",
        ]
    )

    columns = detect_columns(ledger, "inventory_ledger")
    missing = validate_required_columns(columns, "inventory_ledger")

    assert missing == []
    assert columns.disposition == "Disposition"
    assert columns.event_type == "Event-Type"
    assert columns.reference_id == "Reference-ID"
    assert columns.fnsku == "FNSKU"
    assert columns.quantity == "Quantity"
    assert columns.fulfillment_center_id == "Fulfillment-Center-ID"
    assert columns.reason == "Reason"


def test_read_report_file_handles_utf16_tab_delimited_txt():
    content = "amazon-order-id\treturn-date\treason\n111-1\t03/15/2026\tToo small\n"
    upload = NamedBytesIO(content.encode("utf-16"), "returns.txt")

    df = read_report_file(upload)

    assert list(df.columns) == ["amazon-order-id", "return-date", "reason"]
    assert df.loc[0, "amazon-order-id"] == "111-1"


def test_parse_date_series_handles_common_amazon_and_excel_formats():
    values = pd.Series(
        [
            "2026-03-15",
            "03/16/2026",
            "2026-03-17T08:30:00+00:00",
            46100,
        ]
    )

    parsed = parse_date_series(values)

    assert parsed.iloc[0].isoformat() == "2026-03-15"
    assert parsed.iloc[1].isoformat() == "2026-03-16"
    assert parsed.iloc[2].isoformat() == "2026-03-17"
    assert parsed.iloc[3].year >= 2026


def test_read_report_files_combines_multiple_payment_exports_and_tracks_source_file():
    file_one = NamedBytesIO(
        "order-id,posted-date,total\n111-1,2026-03-01,-10.00\n".encode("utf-8"),
        "payments-part-1.csv",
    )
    file_two = NamedBytesIO(
        "order-id,posted-date,total\n111-2,2026-03-02,-20.00\n".encode("utf-8"),
        "payments-part-2.csv",
    )

    df = read_report_files([file_one, file_two], source_column="Source File")

    assert df["order-id"].tolist() == ["111-1", "111-2"]
    assert df["Source File"].tolist() == ["payments-part-1.csv", "payments-part-2.csv"]
