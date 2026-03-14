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
"""Tests for JsonValidatorOperator."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add dev directory to path
sys.path.insert(0, str(Path(__file__).parent))

from json_validator_operator import JsonValidatorOperator


class TestJsonValidatorOperator:
    """Test suite for JsonValidatorOperator."""

    def test_basic_dict_validation(self):
        """Test validation with dict input."""
        operator = JsonValidatorOperator(
            task_id="test",
            json_data={"id": 1, "name": "John"},
        )
        result = operator.execute({})
        assert result == {"id": 1, "name": "John"}

    def test_string_json_parsing(self):
        """Test parsing JSON from string."""
        operator = JsonValidatorOperator(
            task_id="test",
            json_data='{"id": 1, "name": "John"}',
        )
        result = operator.execute({})
        assert result == {"id": 1, "name": "John"}

    def test_required_keys_present(self):
        """Test validation passes when all required keys are present."""
        operator = JsonValidatorOperator(
            task_id="test",
            json_data={"id": 1, "name": "John", "email": "john@example.com"},
            required_keys=["id", "name", "email"],
        )
        result = operator.execute({})
        assert result["id"] == 1
        assert result["name"] == "John"
        assert result["email"] == "john@example.com"

    def test_missing_required_keys_fails(self):
        """Test validation fails when required keys are missing."""
        from airflow.providers.common.compat.sdk import AirflowException

        operator = JsonValidatorOperator(
            task_id="test",
            json_data={"id": 1, "name": "John"},
            required_keys=["id", "name", "email"],
            fail_on_invalid=True,
        )
        with pytest.raises(AirflowException, match="Missing required keys"):
            operator.execute({})

    def test_missing_keys_skip_mode(self):
        """Test validation skips task when fail_on_invalid is False."""
        from airflow.providers.common.compat.sdk import AirflowSkipException

        operator = JsonValidatorOperator(
            task_id="test",
            json_data={"id": 1, "name": "John"},
            required_keys=["id", "name", "email"],
            fail_on_invalid=False,
        )
        with pytest.raises(AirflowSkipException, match="Missing required keys"):
            operator.execute({})

    def test_extract_keys_from_dict(self):
        """Test extracting specific keys from dict."""
        operator = JsonValidatorOperator(
            task_id="test",
            json_data={"id": 1, "name": "John", "age": 30, "city": "SF"},
            extract_keys=["id", "name"],
        )
        result = operator.execute({})
        assert result == {"id": 1, "name": "John"}
        assert "age" not in result
        assert "city" not in result

    def test_extract_keys_missing_key_warning(self):
        """Test extraction with missing key logs warning."""
        operator = JsonValidatorOperator(
            task_id="test",
            json_data={"id": 1, "name": "John"},
            extract_keys=["id", "name", "email"],
        )
        result = operator.execute({})
        # Should extract available keys only
        assert result == {"id": 1, "name": "John"}
        assert "email" not in result

    def test_extract_keys_from_list(self):
        """Test extracting keys from list of dicts."""
        operator = JsonValidatorOperator(
            task_id="test",
            json_data=[
                {"id": 1, "name": "John", "age": 30},
                {"id": 2, "name": "Jane", "age": 25},
            ],
            extract_keys=["id", "name"],
        )
        result = operator.execute({})
        assert result == [{"id": 1, "name": "John"}, {"id": 2, "name": "Jane"}]

    def test_transform_remove_nulls(self):
        """Test transformation to remove null values."""
        operator = JsonValidatorOperator(
            task_id="test",
            json_data={"a": 1, "b": None, "c": 3, "d": None},
            transform_func="lambda x: {k: v for k, v in x.items() if v is not None}",
        )
        result = operator.execute({})
        assert result == {"a": 1, "c": 3}

    def test_transform_uppercase_values(self):
        """Test transformation to uppercase string values."""
        operator = JsonValidatorOperator(
            task_id="test",
            json_data={"name": "john", "city": "san francisco"},
            transform_func="lambda x: {k: v.upper() if isinstance(v, str) else v for k, v in x.items()}",
        )
        result = operator.execute({})
        assert result == {"name": "JOHN", "city": "SAN FRANCISCO"}

    def test_invalid_json_string_fails(self):
        """Test that invalid JSON string raises error."""
        from airflow.providers.common.compat.sdk import AirflowException

        operator = JsonValidatorOperator(
            task_id="test",
            json_data='{"invalid": json}',
        )
        with pytest.raises(AirflowException, match="Invalid JSON data"):
            operator.execute({})

    def test_list_json_data(self):
        """Test validation with list data."""
        operator = JsonValidatorOperator(
            task_id="test",
            json_data=[{"id": 1}, {"id": 2}],
        )
        result = operator.execute({})
        assert result == [{"id": 1}, {"id": 2}]

    def test_combined_validation_and_extraction(self):
        """Test combining required keys validation and extraction."""
        operator = JsonValidatorOperator(
            task_id="test",
            json_data={"id": 1, "name": "John", "email": "john@example.com", "age": 30},
            required_keys=["id", "name", "email"],
            extract_keys=["id", "email"],
        )
        result = operator.execute({})
        # Should validate all required keys are present, then extract only specified keys
        assert result == {"id": 1, "email": "john@example.com"}

    def test_empty_dict(self):
        """Test validation with empty dict."""
        operator = JsonValidatorOperator(
            task_id="test",
            json_data={},
        )
        result = operator.execute({})
        assert result == {}

    def test_empty_list(self):
        """Test validation with empty list."""
        operator = JsonValidatorOperator(
            task_id="test",
            json_data=[],
        )
        result = operator.execute({})
        assert result == []

    def test_nested_json_structure(self):
        """Test validation with nested JSON."""
        nested_data = {
            "user": {
                "id": 1,
                "name": "John",
                "address": {"city": "SF", "zip": "94102"},
            }
        }
        operator = JsonValidatorOperator(
            task_id="test",
            json_data=nested_data,
        )
        result = operator.execute({})
        assert result == nested_data

    def test_extract_from_nested_structure(self):
        """Test extracting top-level keys from nested structure."""
        operator = JsonValidatorOperator(
            task_id="test",
            json_data={
                "id": 1,
                "user": {"name": "John"},
                "metadata": {"created": "2024-01-01"},
            },
            extract_keys=["id", "user"],
        )
        result = operator.execute({})
        assert result == {"id": 1, "user": {"name": "John"}}

    @pytest.mark.parametrize(
        "json_input,expected",
        [
            ('{"key": "value"}', {"key": "value"}),
            ({"key": "value"}, {"key": "value"}),
            ('["item1", "item2"]', ["item1", "item2"]),
            (["item1", "item2"], ["item1", "item2"]),
        ],
    )
    def test_various_input_formats(self, json_input, expected):
        """Test operator handles various input formats."""
        operator = JsonValidatorOperator(
            task_id="test",
            json_data=json_input,
        )
        result = operator.execute({})
        assert result == expected


class TestJsonSchemaValidation:
    """Test JSON schema validation (requires jsonschema library)."""

    def test_schema_validation_success(self):
        """Test successful schema validation."""
        pytest.importorskip("jsonschema")

        operator = JsonValidatorOperator(
            task_id="test",
            json_data={"id": 1, "name": "John"},
            schema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                },
            },
        )
        result = operator.execute({})
        assert result == {"id": 1, "name": "John"}

    def test_schema_validation_failure(self):
        """Test schema validation failure."""
        pytest.importorskip("jsonschema")
        from airflow.providers.common.compat.sdk import AirflowException

        operator = JsonValidatorOperator(
            task_id="test",
            json_data={"id": "not_an_int", "name": "John"},
            schema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                },
            },
        )
        with pytest.raises(AirflowException, match="Schema validation failed"):
            operator.execute({})

    def test_schema_with_required_fields(self):
        """Test schema validation with required fields."""
        pytest.importorskip("jsonschema")
        from airflow.providers.common.compat.sdk import AirflowException

        operator = JsonValidatorOperator(
            task_id="test",
            json_data={"name": "John"},  # Missing 'id'
            schema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                },
                "required": ["id", "name"],
            },
        )
        with pytest.raises(AirflowException, match="Schema validation failed"):
            operator.execute({})


class TestJsonValidatorOperatorAttributes:
    """Test operator attributes and configuration."""

    def test_operator_has_template_fields(self):
        """Test that operator has correct template fields."""
        operator = JsonValidatorOperator(
            task_id="test",
            json_data={},
        )
        assert "json_data" in operator.template_fields
        assert "schema" in operator.template_fields

    def test_operator_has_ui_color(self):
        """Test that operator has UI customization."""
        operator = JsonValidatorOperator(
            task_id="test",
            json_data={},
        )
        assert operator.ui_color == "#e8f5f7"

    def test_operator_inherits_from_base_operator(self):
        """Test that operator properly inherits from BaseOperator."""
        from airflow.providers.standard.version_compat import BaseOperator

        operator = JsonValidatorOperator(
            task_id="test",
            json_data={},
        )
        assert isinstance(operator, BaseOperator)

    def test_task_id_is_set(self):
        """Test that task_id is properly set."""
        operator = JsonValidatorOperator(
            task_id="my_validation_task",
            json_data={},
        )
        assert operator.task_id == "my_validation_task"
