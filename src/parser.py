from __future__ import annotations

import re
from typing import Iterable

import pandas as pd


TEXT_ENCODINGS = ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp1252", "latin1")


def read_report_file(uploaded_file) -> pd.DataFrame:
    name = getattr(uploaded_file, "name", "").lower()
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded_file)

    raw = uploaded_file.read()
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)

    text = _decode_text(raw, TEXT_ENCODINGS)
    delimiter = _detect_delimiter(text)
    return pd.read_csv(
        pd.io.common.StringIO(text),
        sep=delimiter,
        engine="python",
    )


def read_report_files(uploaded_files, source_column: str | None = None) -> pd.DataFrame:
    if uploaded_files is None:
        return pd.DataFrame()
    if not isinstance(uploaded_files, list):
        uploaded_files = [uploaded_files]

    frames = []
    for uploaded_file in uploaded_files:
        if uploaded_file is None:
            continue
        frame = read_report_file(uploaded_file)
        if source_column:
            frame[source_column] = getattr(uploaded_file, "name", "")
        frames.append(frame)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def parse_date_series(series: pd.Series) -> pd.Series:
    return series.apply(_parse_one_date)


def _decode_text(raw: bytes, encodings: Iterable[str]) -> str:
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if _looks_like_table(text):
            return text
    return raw.decode("latin1", errors="replace")


def _looks_like_table(text: str) -> bool:
    first_line = text.splitlines()[0] if text.splitlines() else ""
    return bool(first_line.strip()) and "\ufffd" not in first_line


def _detect_delimiter(text: str) -> str | None:
    first_lines = "\n".join(text.splitlines()[:5])
    counts = {
        "\t": first_lines.count("\t"),
        ",": first_lines.count(","),
        ";": first_lines.count(";"),
    }
    delimiter, count = max(counts.items(), key=lambda item: item[1])
    if count > 0:
        return delimiter
    return None


def _parse_one_date(value):
    if pd.isna(value) or str(value).strip() == "":
        return pd.NaT

    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.notna(numeric) and 20000 <= numeric <= 80000:
        parsed_excel = pd.to_datetime(numeric, unit="D", origin="1899-12-30", errors="coerce")
        return parsed_excel.date() if pd.notna(parsed_excel) else pd.NaT

    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        parsed = pd.to_datetime(str(value), errors="coerce")
    return parsed.date() if pd.notna(parsed) else pd.NaT
