from pathlib import Path
from webapp.filter_schema import build_filter_schema

CONFIG = Path(__file__).resolve().parent.parent / "config" / "ui_filters.yaml"


def test_schema_has_expected_groups(populated_db_path):
    schema = build_filter_schema(populated_db_path, CONFIG)
    group_names = [g["group"] for g in schema["filter_groups"]]
    assert group_names == ["Score", "Location", "Owner", "Condo", "Property",
                           "Zoning", "Motivation Signals", "Financial"]


def test_range_filter_emits_min_max(populated_db_path):
    schema = build_filter_schema(populated_db_path, CONFIG)
    prop = next(g for g in schema["filter_groups"] if g["group"] == "Property")
    lot = next(f for f in prop["filters"] if f["column"] == "lot_size_sf")
    assert lot["type"] == "range"
    # smoke.db has some lot_size_sf values; min/max should be present
    assert "min" in lot and "max" in lot


def test_dropdown_filter_has_distinct_values(populated_db_path):
    schema = build_filter_schema(populated_db_path, CONFIG)
    prop = next(g for g in schema["filter_groups"] if g["group"] == "Property")
    pc = next(f for f in prop["filters"] if f["column"] == "property_class")
    assert pc["type"] == "dropdown"
    assert isinstance(pc["options"], list)
    assert len(pc["options"]) > 0


def test_checkbox_filter_has_no_extras(populated_db_path):
    schema = build_filter_schema(populated_db_path, CONFIG)
    owner = next(g for g in schema["filter_groups"] if g["group"] == "Owner")
    abs_f = next(f for f in owner["filters"] if f["column"] == "is_absentee")
    assert abs_f["type"] == "checkbox"
    assert "options" not in abs_f


def test_stage_pills_in_schema(populated_db_path):
    schema = build_filter_schema(populated_db_path, CONFIG)
    assert schema["stage_pills"]["column"] == "stage"
    assert "scored" in schema["stage_pills"]["values"]
