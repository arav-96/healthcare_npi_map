# Provider Master — Final Result & How to Use It

National Medicare provider master, keyed on **NPI**, built from 3 public CMS sources
(NPPES monthly + PPEF enrollment + POS). Output is **Parquet** in `out_full/`.

## What you have

| File | Rows | What it is |
|---|---|---|
| **provider_master.parquet** | 9,606,683 | The main deliverable — one row per NPI, everything denormalized (identity, all taxonomies, linked CCNs, reassignment cols) |
| dim_npi.parquet | 9,606,683 | One row per NPI (identity / address / primary taxonomy / tin_status) |
| bridge_npi_taxonomy.parquet | 12,020,505 | NPI → each taxonomy code (long form, primary flag, slot) |
| dim_ccn.parquet | 44,429 | One row per facility CCN |
| bridge_ccn_npi.parquet | 12,154 | CCN ↔ NPI fuzzy links (with match_score) |
| rel_reassignment.parquet | 0 | Empty — PPEF Reassignment sub-file not published by CMS |
| coverage_report.parquet | 12 | Counts + gap flags |

For most analysis you only need **provider_master.parquet**.

## Coverage report (the build's own summary)

```
npi_total            9,606,683     ccn_total          44,429
npi_type1            7,323,142     ccn_npi_bridged    12,154
npi_type2            1,937,362     ccn_unbridged      32,275
taxonomy_edges      12,020,505     reassignment_edges      0
npi_tin_suppressed   9,606,683
GAP_tin_per_npi                     NOT PUBLIC (confidential / <UNAVAIL>)
GAP_claim_level_rendering_attending NOT PUBLIC (claims are PHI)
```

## How to use it

Parquet isn't double-clickable — query it with DuckDB or pandas. Both read it lazily,
so you don't load 9.6M rows into RAM unless you ask for them.

### DuckDB (fastest, SQL, no full load)

```python
import duckdb
con = duckdb.connect()
pm = "out_full/provider_master.parquet"

# count orgs by state
con.execute(f"""
  SELECT state, count(*) FROM read_parquet('{pm}')
  WHERE entity_type='2' GROUP BY state ORDER BY 2 DESC
""").df()

# all providers with a given primary taxonomy
con.execute(f"""
  SELECT npi, org_name, city, state
  FROM read_parquet('{pm}') WHERE primary_taxonomy='282N00000X'
""").df()

# only NPIs that bridged to a facility CCN
con.execute(f"""
  SELECT npi, org_name, linked_ccns, ccn_match_scores
  FROM read_parquet('{pm}') WHERE linked_ccns <> ''
""").df()
```

### pandas (filter on read so you don't pull all 9.6M rows)

```python
import pandas as pd
df = pd.read_parquet(
    "out_full/provider_master.parquet",
    filters=[("state", "==", "NY"), ("entity_type", "==", "2")],
)
```

## Key columns in provider_master

- **Identity:** `npi`, `entity_type` (1=individual, 2=org), `org_name`,
  `individual_name`, `city`, `state`, `zip5`
- **Taxonomy:** `primary_taxonomy`, `all_taxonomies` (`*` marks primary), `taxonomy_count`
- **Facilities:** `linked_ccns`, `linked_ccn_names`, `ccn_match_scores`
  (`;`-separated; **filter `match_score >= 95`** for high-confidence links — these come
  from a name+ZIP fuzzy match, not an official crosswalk)
- **`tin_status`** is always `suppressed_unavail` (TIN/EIN is confidential — never populated)
- Reassignment columns (`reassigns_to_billing_*`, `receives_reassignment_from_*`) are all
  empty (no public sub-file)

Full column list (25): `npi, entity_type, type_label, org_name, individual_name,
is_sole_prop, is_subpart, parent_lbn, addr1, city, state, zip, zip5, primary_taxonomy,
tin_status, all_taxonomies, taxonomy_count, reassigns_to_billing_npis,
reassigns_to_billing_orgs, reassignment_out_count, receives_reassignment_from_npis,
reassignment_in_count, linked_ccns, linked_ccn_names, ccn_match_scores`

## Two caveats for analysis

1. **CCN bridge is fuzzy** — 12,154 of 44,429 facilities matched (Type-2 orgs only,
   ZIP5-blocked, threshold 88). Treat `ccn_match_scores` as a confidence and threshold
   accordingly. There is no authoritative public CCN↔NPI crosswalk.
2. **346,179 NPIs have blank `entity_type`** (deactivated). Add
   `WHERE entity_type IN ('1','2')` to exclude them.

## Want a clickable Excel slice?

`provider_master` (9.6M rows) exceeds Excel's 1,048,576-row sheet limit, so the national
output is Parquet only. For an Excel deliverable, run the pandas engine on a filtered
slice (e.g. one state) — ask and a small `excel_extract.py` can be added.
