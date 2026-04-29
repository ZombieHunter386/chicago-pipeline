# Chicago Multifamily Pipeline

Off-market deal sourcing pipeline for Chicago multifamily properties. Fetches parcel data from Cook County Assessor + Chicago Data Portal + Cook County Clerk, scores parcels by development potential and motivation signals, and supports outreach to property owners.

## Setup

1. `python -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. Register at https://dev.socrata.com for a free app token
4. `cp .env.example .env` and fill in `SOCRATA_APP_TOKEN`
5. Edit `config/geography.yaml` if you want a different target area
6. Provide the Cook County Clerk delinquent-tax CSV — see below

### Cook County Clerk delinquent tax CSV

This source is not on Socrata. Download the current delinquent-tax parcel list
from the Cook County Clerk's office and save it to `data/delinquent.csv`. The
pipeline raises `FileNotFoundError` until this file is in place. Required
columns: `pin`, `tax_year`, `amount_owed`. PINs may be in dashed
(`14-21-001-001-0000`) or undashed form.

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

## Working with alternative scoring weights

The standard pipeline produces `config/scoring.yaml` from the historical-analysis
script. You can also create alternative YAMLs to experiment with different
weights, score sub-populations differently, or compare hypothetical weight sets
against the analysis-derived ones.

### Creating an alternative YAML

The scoring YAML format:

```yaml
version: "experimental-2026-04-28-no-tax"
generated_at: "2026-04-28T12:00:00+00:00"
top_n: 20
signals:
  lot_size_sf:
    kind: continuous
    weight: 0.5
    direction: positive
    normalization: { min: 1500.0, max: 50000.0 }
    insignificant: false
  is_llc:
    kind: binary
    weight: 0.5
    direction: positive
    normalization: { min: 0.0, max: 1.0 }
    insignificant: false
```

**Required per signal:** `kind` (`continuous`|`binary`), `weight` (float, sums to 1.0
across non-insignificant signals), `direction` (`positive`|`negative`),
`normalization.min` and `.max`, `insignificant` (boolean).

**Required top-level:** `version` (string — used as `score_version` on every
scored row), `top_n` (int), `signals` (mapping).

### Running with an alternative YAML

```bash
.venv/bin/python -m pipeline.score \
    --db data/full.db \
    --scoring-yaml path/to/alternative.yaml
```

This overwrites the `score` and `score_version` columns on every eligible
parcel and on every consolidation group. The score_version reflects the YAML
you used, so you can tell which weights produced any given score.

### Convention

Alternative YAMLs go in `config/scoring_alternatives/<name>.yaml` so they're
visible alongside the canonical one but don't get confused with it. Example
names: `no_tax_signals.yaml`, `motivation_only.yaml`, `consolidation_focus.yaml`.

### Comparing two scored DBs

To compare two weight sets without losing the previous run's scores, copy the
DB first:

```bash
cp data/full.db data/full.alt.db
.venv/bin/python -m pipeline.score --db data/full.alt.db --scoring-yaml config/scoring_alternatives/foo.yaml
.venv/bin/python -c "
import sqlite3
a = sqlite3.connect('data/full.db')
b = sqlite3.connect('data/full.alt.db')
# top 20 from canonical
canonical = {r[0]: r[1] for r in a.execute('SELECT pin, score FROM parcels ORDER BY score DESC LIMIT 20').fetchall()}
alternative = {r[0]: r[1] for r in b.execute('SELECT pin, score FROM parcels ORDER BY score DESC LIMIT 20').fetchall()}
print('In canonical top-20 but not alternative:', set(canonical) - set(alternative))
print('In alternative top-20 but not canonical:', set(alternative) - set(canonical))
"
```
