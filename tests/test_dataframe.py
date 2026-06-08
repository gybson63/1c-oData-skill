#!/usr/bin/env python3
"""Тесты bot_lib/dataframe.py."""

from __future__ import annotations

import pandas as pd
import pytest

from bot_lib.dataframe import (
    aggregate_dataframe,
    dataframe_to_csv,
    merge_dataframes,
    records_to_dataframe,
)


def test_records_to_dataframe_empty():
    df = records_to_dataframe([])
    assert df.empty


def test_records_to_dataframe_flat():
    records = [{"Name": "A", "Value": 10}, {"Name": "B", "Value": 20}]
    df = records_to_dataframe(records, normalize=False)
    assert len(df) == 2
    assert list(df.columns) == ["Name", "Value"]


def test_records_to_dataframe_nested_expand():
    records = [{"Item": {"Description": "Товар", "Code": "001"}}]
    df = records_to_dataframe(records, normalize=True)
    assert "Item.Description" in df.columns or "Item_Description" in df.columns


def test_merge_dataframes_single_alias():
    dfs = {"sales": pd.DataFrame({"A": [1, 2]})}
    result = merge_dataframes(dfs, [])
    assert len(result) == 2


def test_merge_dataframes_join():
    left = pd.DataFrame({"Product": ["A", "B"], "Sum": [100, 200]})
    right = pd.DataFrame({"Product": ["A", "B"], "Code": ["01", "02"]})
    dfs = {"sales": left, "products": right}
    joins = [
        {
            "left": "sales",
            "right": "products",
            "left_on": "Product",
            "right_on": "Product",
            "how": "inner",
        }
    ]
    result = merge_dataframes(dfs, joins)
    assert len(result) == 2
    assert "Code" in result.columns


def test_aggregate_dataframe_sum():
    df = pd.DataFrame(
        {
            "Category": ["A", "A", "B"],
            "Amount": [10, 20, 5],
        }
    )
    result = aggregate_dataframe(df, ["Category"], {"Amount": "sum"})
    assert len(result) == 2
    row_a = result[result["Category"] == "A"].iloc[0]
    assert row_a["Amount"] == 30


def test_aggregate_dataframe_synthetic_count():
    df = pd.DataFrame(
        {
            "Org": ["A", "A", "B", "B", "B"],
        }
    )
    result = aggregate_dataframe(
        df,
        ["Org"],
        {"КоличествоСотрудников": "count"},
    )
    assert list(result.columns) == ["Org", "КоличествоСотрудников"]
    assert result[result["Org"] == "A"]["КоличествоСотрудников"].iloc[0] == 2
    assert result[result["Org"] == "B"]["КоличествоСотрудников"].iloc[0] == 3


def test_dataframe_to_csv():
    df = pd.DataFrame({"X": [1]})
    csv_text = dataframe_to_csv(df)
    assert "X" in csv_text
    assert "1" in csv_text


def test_merge_without_joins_multiple_raises():
    dfs = {
        "a": pd.DataFrame({"x": [1]}),
        "b": pd.DataFrame({"y": [2]}),
    }
    with pytest.raises(ValueError, match="без joins"):
        merge_dataframes(dfs, [])
