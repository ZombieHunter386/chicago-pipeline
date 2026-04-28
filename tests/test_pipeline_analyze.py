"""Tests for pipeline/analyze.py — the historical-analysis script that derives
initial scoring weights from permit history."""
from pipeline import analyze


def test_signals_registry_shape():
    """SIGNALS is the single source of truth for what features the model sees.
    Every entry must be a 3-tuple of (column_name, kind, source_table) where
    kind is 'continuous' or 'binary'."""
    assert len(analyze.SIGNALS) > 0
    for entry in analyze.SIGNALS:
        assert len(entry) == 3
        col, kind, source = entry
        assert isinstance(col, str) and col
        assert kind in ("continuous", "binary")
        assert source == "parcels", \
            f"{col}: only the parcels table is supported in v1"


def test_signals_excludes_known_bad_columns():
    """tax_delinquent has 0% population on data/full.db (CSV is a stub).
    has_vacancy_report uses a defunct legacy dataset. Both must NOT be in
    the v1 feature set."""
    cols = [s[0] for s in analyze.SIGNALS]
    assert "tax_delinquent" not in cols
    assert "has_vacancy_report" not in cols


def test_analyze_entry_point_exists():
    """The orchestrator must accept (db_path, geo, scoring_yaml_path, report_md_path)."""
    import inspect
    sig = inspect.signature(analyze.analyze)
    assert list(sig.parameters) == ["db_path", "geo", "scoring_yaml_path", "report_md_path"]
