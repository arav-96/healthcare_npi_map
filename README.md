# Medicare provider master ETL

Builds a provider master + relationship tables from **public** CMS sources, keyed on NPI.

## Files
- `provider_master_etl.py` — the pipeline (loaders, transforms, CCN<->NPI bridge, CLI).
- `make_sample_data.py` — generates tiny synthetic CMS-shaped files for a smoke test.
- `DATA_DICTIONARY.md` — every output table and column, documented.
- `out/` — example outputs from the synthetic run (CSVs + `provider_master.xlsx`).

## Public sources (download on infra with internet access)
| Source | What it gives | Where |
|---|---|---|
| NPPES full monthly file (**V.2**) | NPI, entity type, name, address, ZIP, taxonomy (15 slots) | https://download.cms.gov/nppes/NPI_Files.html |
| Medicare FFS Public Provider Enrollment (PPEF) | enrollment IDs, PAC ID, reassignment edges | https://data.cms.gov/provider-characteristics/medicare-provider-supplier-enrollment/medicare-fee-for-service-public-provider-enrollment |
| Provider of Services (POS) file | CCN, facility name/address/ZIP, category | https://www.cms.gov/research-statistics-data-and-systems/downloadable-public-use-files/provider-of-services |
| Medicare Inpatient Hospitals by Provider & Service | CCN -> MS-DRG utilization/payment (optional enrichment) | https://data.cms.gov/provider-summary-by-type-of-service/medicare-inpatient-hospitals |

> NPPES V.1 monthly/weekly files are retired as of 2026-03-03. Build against V.2.

## Run
```bash
pip install pandas rapidfuzz openpyxl
# smoke test on synthetic data:
python make_sample_data.py
python provider_master_etl.py \
  --nppes sample_cms/npidata_sample.csv \
  --ppef-enroll sample_cms/ppef_enrollment_sample.csv \
  --ppef-reasgn sample_cms/ppef_reassignment_sample.csv \
  --pos sample_cms/pos_sample.csv --out out

# real data: point the same flags at the unzipped CMS files
```

## Outputs
All tables are written as CSVs **and** combined into one workbook `provider_master.xlsx`.
See `DATA_DICTIONARY.md` for full column definitions.
- `provider_master` — denormalized, one row per NPI: identity + all taxonomies +
  reassignment billing orgs (both directions) + linked CCNs. The first workbook tab.
- `dim_npi` — NPI, type (1/2), name, address, ZIP, primary taxonomy, `tin_status`
- `bridge_npi_taxonomy` — NPI -> taxonomy code(s), primary flag (long form)
- `rel_reassignment` — individual NPI -> billing org NPI (billing<->rendering proxy)
- `dim_ccn` — CCN facility dimension
- `bridge_ccn_npi` — CCN <-> NPI with `match_score` + `match_method` (triage low scores)
- `coverage_report` — counts + explicit GAP flags

Override the workbook path with `--excel <path>` (default `<out>/provider_master.xlsx`).

## Known gaps (do not chase in public data)
- **TIN/EIN per NPI**: confidential; NPPES column is always `<UNAVAIL>`. Fill from your
  own enrollment/claims data internally. Partial workaround for nonprofits: IRS Form 990
  EINs (ProPublica Nonprofit Explorer) fuzzy-matched by name — incomplete.
- **Claim-level rendering/attending/billing relationships**: claims are PHI, not public.
  Reassignment is the closest public proxy (who *may* bill under whom, not who did on a claim).

## Scaling to the national file (~8M NPIs, ~5GB)
The DuckDB engine is implemented in `provider_master_duckdb.py` and selected with
`--engine duckdb` (the default `--engine auto` uses it whenever duckdb is installed).
It reads the CSVs directly with `all_varchar=true` (leading zeros preserved), builds
`dim_npi` / `bridge_npi_taxonomy` / `rel_reassignment` / `dim_ccn` / `provider_master`
in SQL, and streams every table to disk via `COPY` — the 5GB file never enters pandas.
The CCN<->NPI fuzzy bridge stays in Python (rapidfuzz), blocked on ZIP5 so candidate
sets stay small, with `match_score` persisted for threshold tuning.

```bash
python provider_master_etl.py --engine duckdb --format parquet \
  --nppes /data/nppes/npidata_pfile_*.csv \
  --ppef-enroll /data/ppef/enrollment.csv \
  --pos /data/pos/pos.csv \
  --out out_full --threads 4 --memory-limit 9GB --nppes-one-row-per-npi
```

- `--nppes-one-row-per-npi` — the monthly NPPES universe is already unique per NPI, so
  this skips the `dim_npi` dedup and the `bridge_npi_taxonomy` `ORDER BY`, avoiding a temp
  spill on a tight disk. The taxonomy bridge is built in a **single pass** over the source
  (positional `unnest` of the 15 code/switch slots) instead of a 15× CSV re-scan.
- `--ppef-reasgn` is **optional**: the PPEF Reassignment sub-file is not currently
  published in the CMS public catalog, so if omitted, `rel_reassignment` is built empty.

**Output at national scale: Parquet, not Excel.** The full `provider_master`
(~9.6M rows) exceeds Excel's 1,048,576-row-per-sheet limit. Use `--format parquet` for
the universe; for an Excel deliverable, run the pandas engine on a filtered slice (e.g.
one state). DuckDB reads/writes parquet natively; `pyarrow` is only needed to read the
`.parquet` outputs back into pandas.

## Prebuilt national dataset (GitHub Release)
A full national build is published as **Release assets** (Parquet) — see the repo's
**Releases** page, or pull with the GitHub CLI:

```bash
gh release download national-2026.06 --repo arav-96/healthcare_npi_map --dir out_full
```

Built from the 2026-06 monthly NPPES + 2026.04 PPEF enrollment + Q1-2026 POS:

| Table | Rows | |
|---|---|---|
| provider_master | 9,606,683 | denormalized, one row per NPI (25 cols) |
| dim_npi | 9,606,683 | type1 7,323,142 / type2 1,937,362 |
| bridge_npi_taxonomy | 12,020,505 | NPI → taxonomy edges |
| dim_ccn | 44,429 | facility CCNs |
| bridge_ccn_npi | 12,154 | fuzzy CCN↔NPI links (match_score) |
| rel_reassignment | 0 | sub-file unavailable |

See [`how_to_use.md`](how_to_use.md) for query snippets and analysis caveats.
