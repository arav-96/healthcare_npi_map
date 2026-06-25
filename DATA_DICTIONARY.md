# Provider Master ETL — Data Dictionary

Reference for every output table and column produced by `provider_master_etl.py`.

The **same tables and columns** are produced by both engines:
- **pandas engine** (default for small/demo runs) writes `.csv` files **and** the single
  workbook `provider_master.xlsx` (first tab = `provider_master`, denormalized one-row-
  per-NPI view; remaining tabs = the normalized tables).
- **DuckDB engine** (`--engine duckdb`, for the national ~8M-NPI universe) writes `.csv`
  and/or `.parquet` via `--format`. No Excel: ~8M rows exceeds Excel's 1,048,576-row cap.

**Conventions**
- All identifier columns (`npi`, `zip`, `ccn`, enrollment IDs) are stored as **strings**
  to preserve leading zeros. Do not cast them to integers.
- Empty / not-applicable cells are written as blank (an empty string), not `NA`.
- Source files: NPPES monthly V.2, Medicare FFS Public Provider Enrollment (PPEF) +
  its reassignment sub-file, and the Provider of Services (POS) file.

---

## Spine & keys

| Key | Meaning | Source |
|-----|---------|--------|
| `npi` | 10-digit National Provider Identifier. The spine of the whole model. | NPPES |
| `ccn` | CMS Certification Number (6-char facility/provider number). | POS |
| `enrollment_id` | PECOS Medicare enrollment ID (`ENRLMT_ID`). Individual IDs start `I`, org IDs start `O`. | PPEF |

---

## `provider_master` (denormalized — one row per NPI)

The "map everything to an NPI" view. Starts from `dim_npi` and left-joins aggregates of
every other table, so **every NPI is kept** even when it has no taxonomy/reassignment/CCN.

| Column | Description |
|--------|-------------|
| `npi` | National Provider Identifier (string). |
| `entity_type` | `1` = individual, `2` = organization. |
| `type_label` | Human label: `Individual (Type 1)` / `Organization (Type 2)` / `Unknown`. |
| `org_name` | Legal business name (Type-2 only). |
| `individual_name` | `first last` (Type-1 only). |
| `is_sole_prop` | NPPES "Is Sole Proprietor" flag (`Y`/`N`/blank). |
| `is_subpart` | NPPES "Is Organization Subpart" flag. |
| `parent_lbn` | Parent organization legal business name (for subparts). |
| `addr1`, `city`, `state`, `zip` | Practice location address; `zip` may be ZIP+4. |
| `zip5` | First 5 digits of `zip` (used for CCN match blocking). |
| `primary_taxonomy` | The taxonomy code whose NPPES primary switch = `Y` (fallback: first code). |
| `all_taxonomies` | Every taxonomy for the NPI, `; `-joined, slot order; primary marked with `*`. |
| `taxonomy_count` | Distinct taxonomy codes for the NPI. |
| `tin_status` | TIN/EIN availability — see [Gaps](#documented-gaps). `suppressed_unavail` normally. |
| `reassigns_to_billing_npis` | Billing-org NPIs this (individual) NPI reassigns benefits to, `; `-joined. |
| `reassigns_to_billing_orgs` | Names of those billing orgs, `; `-joined. |
| `reassignment_out_count` | Distinct billing orgs this NPI reassigns to. |
| `receives_reassignment_from_npis` | Individual NPIs that bill under this (org) NPI, `; `-joined. |
| `reassignment_in_count` | Distinct individuals reassigning to this NPI. |
| `linked_ccns` | Facility CCNs fuzzy-matched to this (Type-2) NPI, `; `-joined. |
| `linked_ccn_names` | Names of those facilities, `; `-joined. |
| `ccn_match_scores` | rapidfuzz `token_sort_ratio` score per linked CCN (0–100); triage low scores. |

---

## `dim_npi` (one row per NPI)

Identity dimension. Columns: `npi`, `entity_type`, `type_label`, `org_name`,
`individual_name`, `is_sole_prop`, `is_subpart`, `parent_lbn`, `addr1`, `city`, `state`,
`zip`, `zip5`, `primary_taxonomy`, `tin_status` — all as defined in `provider_master` above.

---

## `bridge_npi_taxonomy` (long form — one row per NPI × taxonomy)

| Column | Description |
|--------|-------------|
| `npi` | National Provider Identifier. |
| `taxonomy_code` | A single Healthcare Provider Taxonomy code. |
| `is_primary` | `True` if this is the NPI's primary taxonomy (NPPES switch = `Y`). |
| `slot` | NPPES taxonomy slot number 1–15 the code came from. |

---

## `rel_reassignment` (one row per reassignment link)

Public proxy for the billing↔rendering relationship: an individual NPI reassigns its
Medicare benefits to a billing organization NPI. Resolved through enrollment IDs.

| Column | Description |
|--------|-------------|
| `ind_npi` | NPI of the individual reassigning benefits. |
| `ind_name` | Individual's `first last`. |
| `reasgn_enrollment_id` | Enrollment ID of the reassigning individual (`REASGN_BNFT_ENRLMT_ID`). |
| `bill_npi` | NPI of the receiving / billing organization. |
| `bill_org_name` | Billing organization name. |
| `rcv_enrollment_id` | Enrollment ID of the receiving org (`RCV_BNFT_ENRLMT_ID`). |

---

## `dim_ccn` (one row per facility CCN)

| Column | Description |
|--------|-------------|
| `ccn` | CMS Certification Number (`PRVDR_NUM`). |
| `name` | Facility name (`FAC_NAME`). |
| `addr`, `city`, `state`, `zip` | Facility address. |
| `zip5` | First 5 digits of `zip`. |
| `category` | Provider category code (`PRVDR_CTGRY_CD`). |

---

## `bridge_ccn_npi` (one row per matched CCN → NPI)

No authoritative CCN↔NPI crosswalk exists in public data. This bridge is **derived** by
blocking facilities and Type-2 org NPIs on `zip5` and fuzzy-matching normalized names.
Always review `match_score` before trusting a link.

| Column | Description |
|--------|-------------|
| `ccn` | Facility CMS Certification Number. |
| `facility_name` | Facility name from `dim_ccn`. |
| `npi` | Best-matching Type-2 organization NPI. |
| `match_score` | rapidfuzz `token_sort_ratio` (0–100). Only matches ≥ 88 are emitted. |
| `match_method` | `name+zip5_fuzzy` (rapidfuzz present) or `name+zip5_exact` (fallback). |

---

## `coverage_report` (metric / value)

Row counts plus explicit gap flags. Notable rows:

| Metric | Meaning |
|--------|---------|
| `npi_total`, `npi_type1`, `npi_type2` | NPI counts by entity type. |
| `npi_with_tin` | NPIs with an unexpected non-suppressed TIN (should be 0 for public data). |
| `npi_tin_suppressed` | NPIs whose TIN is confidential / `<UNAVAIL>`. |
| `taxonomy_edges`, `reassignment_edges` | Row counts of the two relationship tables. |
| `ccn_total`, `ccn_npi_bridged`, `ccn_unbridged` | Facility match coverage. |
| `GAP_tin_per_npi` | Flag: TIN/EIN is confidential, not buildable from public data. |
| `GAP_claim_level_rendering_attending` | Flag: claim-level relationships are PHI, not public. |

---

## Documented gaps

These are **data limitations of public CMS sources, surfaced not silently dropped**.
Both are filled later from internal enrollment / claims data.

1. **TIN/EIN per NPI is confidential.** The NPPES EIN column is always `<UNAVAIL>`. It is
   never fabricated; instead `tin_status` carries:
   - `suppressed_unavail` — value is blank / `<UNAVAIL>` / `NA` (the normal case).
   - `present_unexpected` — a non-sentinel value appeared (investigate the source file).
2. **Claim-level rendering ↔ attending ↔ billing relationships are PHI** and not public.
   `rel_reassignment` (who *may* bill under whom) is the closest public proxy.
