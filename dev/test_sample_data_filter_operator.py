# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""
Tests for the sample DataFilterOperator.

This demonstrates:
- Using pytest patterns (not unittest.TestCase)
- Parametrized tests for multiple scenarios
- Testing error conditions
- Using proper fixtures
"""

from __future__ import annotations

import json

import pytest

import sys
from pathlib import Path

# Add dev directory to path so we can import the sample operator
sys.path.insert(0, str(Path(__file__).parent))

from sample_data_filter_operator import DataAggregationOperator, DataFilterOperator


class TestDataFilterOperator:
    """Test suite for DataFilterOperator."""

    def test_basic_gte_filter(self):
        """Test basic greater-than-or-equal filtering."""
        operator = DataFilterOperator(
            task_id="test_task",
            data=[10, 20, 30, 40, 50],
            threshold=30,
            operator="gte",
        )

        # Create a minimal context (can be empty for this operator)
        context = {}

        result = operator.execute(context)

        assert result == [30, 40, 50]

    def test_lte_filter(self):
        """Test less-than-or-equal filtering."""
        operator = DataFilterOperator(
            task_id="test_task",
            data=[10, 20, 30, 40, 50],
            threshold=30,
            operator="lte",
        )

        result = operator.execute({})
        assert result == [10, 20, 30]

    def test_exact_match_filter(self):
        """Test exact equality filtering."""
        operator = DataFilterOperator(
            task_id="test_task",
            data=[10, 20, 30, 30, 40, 50],
            threshold=30,
            operator="eq",
        )

        result = operator.execute({})
        assert result == [30, 30]

    @pytest.mark.parametrize(
        "operator_type,expected",
        [
            ("gte", [30, 40, 50]),
            ("gt", [40, 50]),
            ("lte", [10, 20, 30]),
            ("lt", [10, 20]),
            ("eq", [30]),
        ],
    )
    def test_all_operators(self, operator_type, expected):
        """Test all operator types using parametrize."""
        operator = DataFilterOperator(
            task_id="test_task",
            data=[10, 20, 30, 40, 50],
            threshold=30,
            operator=operator_type,
        )

        result = operator.execute({})
        assert result == expected

    def test_return_count_only(self):
        """Test returning count instead of full list."""
        operator = DataFilterOperator(
            task_id="test_task",
            data=[10, 20, 30, 40, 50],
            threshold=30,
            operator="gte",
            return_count_only=True,
        )

        result = operator.execute({})
        assert result == 3
        assert isinstance(result, int)

    def test_float_values(self):
        """Test filtering with float values."""
        operator = DataFilterOperator(
            task_id="test_task",
            data=[1.5, 2.7, 3.2, 4.8, 5.1],
            threshold=3.0,
            operator="gte",
        )

        result = operator.execute({})
        assert result == [3.2, 4.8, 5.1]

    def test_string_threshold_conversion(self):
        """Test that string threshold values are converted to numeric."""
        operator = DataFilterOperator(
            task_id="test_task",
            data=[10, 20, 30, 40, 50],
            threshold="30",  # String threshold (from templating)
            operator="gte",
        )

        result = operator.execute({})
        assert result == [30, 40, 50]

    def test_json_string_data(self):
        """Test that JSON string data is parsed correctly."""
        operator = DataFilterOperator(
            task_id="test_task",
            data=json.dumps([10, 20, 30, 40, 50]),  # JSON string (from templating)
            threshold=30,
            operator="gte",
        )

        result = operator.execute({})
        assert result == [30, 40, 50]

    def test_comma_separated_string_data(self):
        """Test that comma-separated string data is parsed."""
        operator = DataFilterOperator(
            task_id="test_task",
            data="10, 20, 30, 40, 50",  # Comma-separated string
            threshold=30,
            operator="gte",
        )

        result = operator.execute({})
        assert result == [30, 40, 50]

    def test_empty_data_list(self):
        """Test behavior with empty data list."""
        operator = DataFilterOperator(
            task_id="test_task",
            data=[],
            threshold=30,
            operator="gte",
        )

        result = operator.execute({})
        assert result == []

    def test_all_values_filtered_out(self):
        """Test when all values are filtered out."""
        operator = DataFilterOperator(
            task_id="test_task",
            data=[10, 20, 30],
            threshold=100,
            operator="gte",
        )

        result = operator.execute({})
        assert result == []

    def test_invalid_operator_raises_error(self):
        """Test that invalid operator raises ValueError."""
        with pytest.raises(ValueError, match="Invalid operator"):
            DataFilterOperator(
                task_id="test_task",
                data=[10, 20, 30],
                threshold=20,
                operator="invalid",
            )

    def test_mixed_int_and_float(self):
        """Test filtering with mixed int and float values."""
        operator = DataFilterOperator(
            task_id="test_task",
            data=[10, 20.5, 30, 40.7, 50],
            threshold=25,
            operator="gte",
        )

        result = operator.execute({})
        assert result == [30, 40.7, 50]


class TestDataAggregationOperator:
    """Test suite for DataAggregationOperator."""

    def test_basic_sum_and_mean(self):
        """Test basic sum and mean operations."""
        operator = DataAggregationOperator(
            task_id="test_task",
            data=[10, 20, 30, 40, 50],
            operations=["sum", "mean"],
        )

        result = operator.execute({})

        assert result["sum"] == 150
        assert result["mean"] == 30.0

    def test_all_operations(self):
        """Test all aggregation operations."""
        operator = DataAggregationOperator(
            task_id="test_task",
            data=[10, 20, 30, 40, 50],
            operations=["sum", "mean", "min", "max", "count"],
        )

        result = operator.execute({})

        assert result["sum"] == 150
        assert result["mean"] == 30.0
        assert result["min"] == 10
        assert result["max"] == 50
        assert result["count"] == 5

    def test_median_odd_count(self):
        """Test median with odd number of values."""
        operator = DataAggregationOperator(
            task_id="test_task",
            data=[10, 20, 30, 40, 50],
            operations=["median"],
        )

        result = operator.execute({})
        assert result["median"] == 30

    def test_median_even_count(self):
        """Test median with even number of values."""
        operator = DataAggregationOperator(
            task_id="test_task",
            data=[10, 20, 30, 40],
            operations=["median"],
        )

        result = operator.execute({})
        assert result["median"] == 25.0  # (20 + 30) / 2

    def test_empty_data(self):
        """Test aggregation with empty data."""
        operator = DataAggregationOperator(
            task_id="test_task",
            data=[],
            operations=["sum", "mean"],
        )

        result = operator.execute({})

        # Should return 0 for all operations with empty data
        assert result["sum"] == 0
        assert result["mean"] == 0

    def test_single_value(self):
        """Test aggregation with single value."""
        operator = DataAggregationOperator(
            task_id="test_task",
            data=[42],
            operations=["sum", "mean", "min", "max", "count", "median"],
        )

        result = operator.execute({})

        assert result["sum"] == 42
        assert result["mean"] == 42.0
        assert result["min"] == 42
        assert result["max"] == 42
        assert result["count"] == 1
        assert result["median"] == 42

    def test_invalid_operation_raises_error(self):
        """Test that invalid operation raises ValueError."""
        with pytest.raises(ValueError, match="Invalid operations"):
            DataAggregationOperator(
                task_id="test_task",
                data=[10, 20, 30],
                operations=["sum", "invalid_op"],
            )

    def test_default_operations(self):
        """Test that default operations are applied when none specified."""
        operator = DataAggregationOperator(
            task_id="test_task",
            data=[10, 20, 30],
        )

        result = operator.execute({})

        # Default operations are ["sum", "mean"]
        assert "sum" in result
        assert "mean" in result
        assert result["sum"] == 60
        assert result["mean"] == 20.0


# ============================================================================
# Integration test example (would require actual Airflow environment)
# ============================================================================


class TestDataFilterOperatorIntegration:
    """
    Integration tests that would run with full Airflow environment.

    Note: These tests would typically require @pytest.mark.db_test and
    proper Airflow test fixtures. This is a simplified example.
    """

    def test_operator_attributes(self):
        """Test that operator has correct attributes set."""
        operator = DataFilterOperator(
            task_id="test_task",
            data=[1, 2, 3],
            threshold=2,
        )

        # Check template fields are defined
        assert "data" in operator.template_fields
        assert "threshold" in operator.template_fields

        # Check UI color is set
        assert operator.ui_color == "#e8f7f0"

        # Check task_id is set correctly
        assert operator.task_id == "test_task"

    def test_operator_inherits_from_base_operator(self):
        """Test that operator properly inherits from BaseOperator."""
        from airflow.providers.standard.version_compat import BaseOperator

        operator = DataFilterOperator(
            task_id="test_task",
            data=[1, 2, 3],
            threshold=2,
        )

        assert isinstance(operator, BaseOperator)
