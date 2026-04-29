from __future__ import annotations
import argparse
from pathlib import Path
from webapp.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Chicago Pipeline Review UI")
    parser.add_argument("--db", type=Path, default=Path("data/smoke.db"),
                        help="Path to SQLite database (default: data/smoke.db)")
    parser.add_argument("--scoring-yaml", type=Path, default=None,
                        help="Path to scoring YAML for the score-breakdown panel "
                             "(default: config/scoring.yaml relative to project root)")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--outreach", action="store_true",
                        help="Enable outreach UI (Plan 4 — not implemented)")
    parser.add_argument("--debug", action="store_true",
                        help="Enable Flask debug mode (exposes Werkzeug debugger; localhost only)")
    args = parser.parse_args()

    if not args.db.exists():
        raise SystemExit(f"Database not found: {args.db}")

    app = create_app(db_path=args.db, feature_outreach=args.outreach,
                     scoring_yaml_path=args.scoring_yaml)
    app.run(host="127.0.0.1", port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
