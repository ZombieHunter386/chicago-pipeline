# Chicago Multifamily Pipeline

Off-market deal sourcing pipeline for Chicago multifamily properties. Fetches parcel data from Cook County Assessor + Chicago Data Portal + Cook County Clerk, scores parcels by development potential and motivation signals, and supports outreach to property owners.

## Setup

1. `python -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. Register at https://dev.socrata.com for a free app token
4. `cp .env.example .env` and fill in `SOCRATA_APP_TOKEN`
5. Edit `config/geography.yaml` if you want a different target area

## Usage

```bash
# Fetch all data for target geography (~5-10 min)
python -m pipeline.fetch_all

# Force refresh (ignore staleness check)
python -m pipeline.fetch_all --refresh
```

## Tests

```bash
pytest -v
```

## Review UI

Read-only Flask app showing all parcels in the database with filter panel, map, and detail view.

```bash
# Run against smoke.db (641 parcels, default)
python -m webapp

# Run against another DB
python -m webapp --db data/full.db --port 5000
```

Open `http://127.0.0.1:5000/`.

**Scope limits:** scoring is not yet implemented — parcels are sorted by `last_updated_date` and the Score Breakdown panel is a stub. Outreach UI is hidden behind `--outreach` (not yet functional).
