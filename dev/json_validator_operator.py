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
JSON Validator Operator.

This operator validates JSON data against a schema and optionally transforms it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Sequence

from airflow.providers.common.compat.sdk import AirflowException, AirflowSkipException
from airflow.providers.standard.version_compat import BaseOperator

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context


class JsonValidatorOperator(BaseOperator):
    """
    Validate JSON data against a schema and optionally transform it.

    This operator is useful for:
    - Validating API responses
    - Checking data quality in pipelines
    - Ensuring data contracts between systems
    - Transforming and filtering JSON data

    :param json_data: JSON data to validate (string or dict). Can be templated.
    :param schema: Optional JSON schema to validate against (dict). Can be templated.
    :param required_keys: List of keys that must be present in the JSON data.
    :param extract_keys: If provided, extract only these keys from the JSON data.
    :param fail_on_invalid: If True, fail the task on validation errors.
        If False, log warnings and skip the task. Default is True.
    :param transform_func: Optional Python expression to transform the data.
        Example: "lambda x: {k: v for k, v in x.items() if v is not None}"

    **Example usage:**

    .. code-block:: python

        # Basic validation
        validate_api_response = JsonValidatorOperator(
            task_id="validate_response",
            json_data="{{ ti.xcom_pull(task_ids='fetch_api_data') }}",
            required_keys=["id", "name", "status"],
        )

        # With schema validation
        validate_with_schema = JsonValidatorOperator(
            task_id="validate_schema",
            json_data="{{ ti.xcom_pull(task_ids='fetch_data') }}",
            schema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                    "active": {"type": "boolean"}
                },
                "required": ["id", "name"]
            },
        )

        # Extract specific fields
        extract_fields = JsonValidatorOperator(
            task_id="extract_user_info",
            json_data="{{ ti.xcom_pull(task_ids='get_user') }}",
            extract_keys=["user_id", "email", "created_at"],
        )
    """

    template_fields: Sequence[str] = ("json_data", "schema")
    template_fields_renderers = {"json_data": "json", "schema": "json"}
    ui_color = "#e8f5f7"
    ui_fgcolor = "#000"

    def __init__(
        self,
        *,
        json_data: str | dict[str, Any],
        schema: dict[str, Any] | None = None,
        required_keys: list[str] | None = None,
        extract_keys: list[str] | None = None,
        fail_on_invalid: bool = True,
        transform_func: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.json_data = json_data
        self.schema = schema
        self.required_keys = required_keys or []
        self.extract_keys = extract_keys
        self.fail_on_invalid = fail_on_invalid
        self.transform_func = transform_func

    def execute(self, context: Context) -> dict[str, Any] | list[Any]:
        """
        Execute the JSON validation and transformation.

        :param context: Airflow context
        :return: Validated and optionally transformed JSON data
        """
        self.log.info("Starting JSON validation")

        # Parse JSON data if it's a string
        data = self._parse_json(self.json_data)

        # Validate required keys
        if self.required_keys:
            self._validate_required_keys(data)

        # Validate against schema if provided
        if self.schema:
            self._validate_schema(data)

        # Extract specific keys if requested
        if self.extract_keys:
            data = self._extract_keys(data)

        # Apply transformation function if provided
        if self.transform_func:
            data = self._apply_transformation(data)

        self.log.info("JSON validation completed successfully")
        return data

    def _parse_json(self, json_data: str | dict[str, Any]) -> dict[str, Any] | list[Any]:
        """Parse JSON data from string or dict."""
        if isinstance(json_data, dict):
            return json_data

        if isinstance(json_data, list):
            return json_data

        if isinstance(json_data, str):
            try:
                parsed = json.loads(json_data)
                self.log.info("Successfully parsed JSON string (type: %s)", type(parsed).__name__)
                return parsed
            except json.JSONDecodeError as e:
                error_msg = f"Invalid JSON data: {e}"
                self._handle_validation_error(error_msg)
        else:
            error_msg = f"json_data must be string, dict, or list, got: {type(json_data)}"
            self._handle_validation_error(error_msg)

    def _validate_required_keys(self, data: dict[str, Any] | list[Any]) -> None:
        """Validate that required keys are present in the data."""
        if isinstance(data, list):
            self.log.warning("Cannot validate required keys on list data")
            return

        if not isinstance(data, dict):
            error_msg = "Cannot validate required keys on non-dict data"
            self._handle_validation_error(error_msg)
            return

        missing_keys = [key for key in self.required_keys if key not in data]

        if missing_keys:
            error_msg = f"Missing required keys: {missing_keys}"
            self._handle_validation_error(error_msg)
        else:
            self.log.info("All required keys present: %s", self.required_keys)

    def _validate_schema(self, data: dict[str, Any] | list[Any]) -> None:
        """Validate data against JSON schema."""
        try:
            # Try to import jsonschema (it's an optional dependency)
            import jsonschema
        except ImportError:
            self.log.warning(
                "jsonschema library not available. Install it with: pip install jsonschema"
            )
            return

        try:
            jsonschema.validate(instance=data, schema=self.schema)
            self.log.info("JSON schema validation passed")
        except jsonschema.exceptions.ValidationError as e:
            error_msg = f"Schema validation failed: {e.message}"
            self._handle_validation_error(error_msg)
        except jsonschema.exceptions.SchemaError as e:
            raise AirflowException(f"Invalid schema provided: {e.message}")

    def _extract_keys(self, data: dict[str, Any] | list[Any]) -> dict[str, Any]:
        """Extract specific keys from the data."""
        if isinstance(data, list):
            self.log.info("Extracting keys from list of %d items", len(data))
            return [
                {key: item.get(key) for key in self.extract_keys if isinstance(item, dict)}
                for item in data
            ]

        if not isinstance(data, dict):
            error_msg = "Cannot extract keys from non-dict data"
            self._handle_validation_error(error_msg)
            return data

        extracted = {}
        for key in self.extract_keys:
            if key in data:
                extracted[key] = data[key]
            else:
                self.log.warning("Key '%s' not found in data", key)

        self.log.info("Extracted %d keys: %s", len(extracted), list(extracted.keys()))
        return extracted

    def _apply_transformation(self, data: dict[str, Any] | list[Any]) -> Any:
        """Apply transformation function to the data."""
        try:
            # Evaluate the transformation function
            transform_fn = eval(self.transform_func)
            transformed = transform_fn(data)
            self.log.info("Transformation applied successfully")
            return transformed
        except Exception as e:
            error_msg = f"Transformation failed: {e}"
            raise AirflowException(error_msg)

    def _handle_validation_error(self, error_msg: str) -> None:
        """Handle validation errors based on fail_on_invalid setting."""
        if self.fail_on_invalid:
            self.log.error(error_msg)
            raise AirflowException(error_msg)
        else:
            self.log.warning(error_msg)
            raise AirflowSkipException(f"Skipping due to validation error: {error_msg}")
