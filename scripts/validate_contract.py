#!/usr/bin/env python3
"""
零依赖的 JSON Schema 子集校验器。
用法: python validate_contract.py --schema specs/contracts/xxx.json --data data/xxx.json

实现 JSON Schema draft-07 子集:
- type 校验 (string, number, integer, boolean, array, object, null, 以及联合类型如 ["number", "null"])
- required 字段校验
- enum 校验
- pattern 校验 (正则表达式)
- properties 定义
- items 校验 (array 元素)
- additionalProperties

输出结构化 JSON 到 stdout:
{
  "valid": true/false,
  "errors": ["具体错误信息"],
  "warnings": ["非关键警告"],
  "critical_missing": ["缺失的 required 字段名"]
}

退出码:
  0 = 校验通过
  1 = 校验失败（有 errors 或 critical_missing）
  2 = schema 文件不存在或解析失败
"""

import argparse
import json
import os
import re
import sys


def _python_type_name(value):
    """Return the JSON Schema type name for a Python value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _type_matches(value, expected_type):
    """Check if a value matches a JSON Schema type (or list of types)."""
    if isinstance(expected_type, list):
        return any(_type_matches(value, t) for t in expected_type)
    actual = _python_type_name(value)
    if expected_type == "number" and actual == "integer":
        return True
    return actual == expected_type


def validate(schema, data, path=""):
    """
    Validate data against a JSON Schema subset.
    Returns (errors, warnings, critical_missing).
    """
    errors = []
    warnings = []
    critical_missing = []

    # type check
    schema_type = schema.get("type")
    if schema_type is not None:
        if not _type_matches(data, schema_type):
            actual = _python_type_name(data)
            errors.append(f"{path or '(root)'}: expected type {schema_type}, got {actual}")
            return errors, warnings, critical_missing

    # enum check
    if "enum" in schema:
        if data not in schema["enum"]:
            errors.append(f"{path or '(root)'}: value {data!r} not in enum {schema['enum']}")

    # pattern check (strings only)
    if "pattern" in schema and isinstance(data, str):
        if not re.search(schema["pattern"], data):
            errors.append(f"{path or '(root)'}: value {data!r} does not match pattern {schema['pattern']!r}")

    # object validation
    if isinstance(data, dict):
        # required fields
        for field in schema.get("required", []):
            if field not in data:
                field_path = f"{path}.{field}" if path else field
                critical_missing.append(field)
                errors.append(f"{field_path}: required field is missing")

        # properties
        properties = schema.get("properties", {})
        for key, sub_schema in properties.items():
            if key in data:
                sub_path = f"{path}.{key}" if path else key
                sub_errors, sub_warnings, sub_missing = validate(sub_schema, data[key], sub_path)
                errors.extend(sub_errors)
                warnings.extend(sub_warnings)
                critical_missing.extend(sub_missing)

        # additionalProperties
        additional = schema.get("additionalProperties")
        if additional is not None and isinstance(additional, dict):
            for key in data:
                if key not in properties:
                    sub_path = f"{path}.{key}" if path else key
                    sub_errors, sub_warnings, sub_missing = validate(additional, data[key], sub_path)
                    errors.extend(sub_errors)
                    warnings.extend(sub_warnings)
                    critical_missing.extend(sub_missing)

    # array validation
    if isinstance(data, list) and "items" in schema:
        items_schema = schema["items"]
        for i, item in enumerate(data):
            item_path = f"{path}[{i}]"
            sub_errors, sub_warnings, sub_missing = validate(items_schema, item, item_path)
            errors.extend(sub_errors)
            warnings.extend(sub_warnings)
            critical_missing.extend(sub_missing)

    return errors, warnings, critical_missing


def main():
    parser = argparse.ArgumentParser(
        description="零依赖 JSON Schema 子集校验器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python validate_contract.py --schema specs/contracts/stock-basic-info.json --data data/stock.json\n"
            "\n"
            "退出码:\n"
            "  0 = 校验通过\n"
            "  1 = 校验失败\n"
            "  2 = schema/data 文件错误\n"
        ),
    )
    parser.add_argument("--schema", required=True, help="JSON Schema 文件路径")
    parser.add_argument("--data", required=True, help="待校验的 JSON 数据文件路径")
    args = parser.parse_args()

    # Load schema
    if not os.path.isfile(args.schema):
        result = {"valid": False, "errors": [f"Schema file not found: {args.schema}"], "warnings": [], "critical_missing": []}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(2)

    try:
        with open(args.schema, "r", encoding="utf-8") as f:
            schema = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        result = {"valid": False, "errors": [f"Failed to parse schema: {e}"], "warnings": [], "critical_missing": []}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(2)

    # Load data
    if not os.path.isfile(args.data):
        result = {"valid": False, "errors": [f"Data file not found: {args.data}"], "warnings": [], "critical_missing": []}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(2)

    try:
        with open(args.data, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        result = {"valid": False, "errors": [f"Failed to parse data: {e}"], "warnings": [], "critical_missing": []}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(2)

    # Validate
    errors, warnings, critical_missing = validate(schema, data)

    valid = len(errors) == 0 and len(critical_missing) == 0
    result = {
        "valid": valid,
        "errors": errors,
        "warnings": warnings,
        "critical_missing": critical_missing,
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if valid else 1)


if __name__ == "__main__":
    main()
