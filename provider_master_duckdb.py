"""
provider_master_duckdb.py
=========================
National-scale engine for provider_master_etl. Same model, same outputs, but the
heavy ingest/joins run in DuckDB so the ~8M-row / ~6GB NPPES monthly file is never
loaded into pandas. Only the rapidfuzz CCN<->NPI bridge runs in Python, and only its
small inputs (Type-2 org NPIs + facilities, blocked on ZIP5) come into memory.

Outputs are identical in shape to the pandas engine and are streamed straight to disk
as Parquet and/or CSV via COPY. No Excel here: the national provider_master (~8M rows)
exceeds Excel's 1,048,576-row-per-sheet limit, so use the pandas engine for filtered
Excel extracts.

Reused conventions: all identifier columns are read as VARCHAR (leading zeros), column
maps still live in the config dicts in provider_master_etl.py, and the fuzzy bridge stays
in Python.
"""
from __future__ import annotations
import os
import pandas as pd
import duckdb

from provider_master_etl import (
    NPPES_COLS, PPEF_ENROLL_COLS, PPEF_REASGN_COLS, POS_COLS,
    N_TAXONOMY_SLOTS, TIN_SENTINELS, _norm_name,
)

try:
    from rapidfuzz import process, fuzz
    HAVE_RAPIDFUZZ = True
except ImportError:
    HAVE_RAPIDFUZZ = False


# --------------------------------------------------------------------------
# small SQL helpers
# --------------------------------------------------------------------------
def _q(path: str) -> str:
    """Quote a path for inlining into SQL (local CLI; escape single quotes)."""
    return "'" + path.replace("'", "''") + "'"


def _read_csv_sql(path: str) -> str:
    """DuckDB scan of a CSV (or glob) with every column as VARCHAR to keep leading
    zeros. union_by_name tolerates column drift across multiple monthly files."""
    return (f"read_csv({_q(path)}, all_varchar=true, header=true, "
            f"sample_size=-1, union_by_name=true)")


def _present(con, scan: str) -> set:
    """Columns actually present in a CSV scan (one cheap header read)."""
    return {d[0] for d in con.execute(f"SELECT * FROM {scan} LIMIT 0").description}


def _col(name_key: str, cols: dict, present: set | None = None) -> str:
    """Quoted raw source column for one of our canonical keys. If `present` is given
    and the configured column is absent (CMS renamed/removed it between releases),
    fall back to NULL so the build degrades gracefully instead of hard-failing —
    matching the pandas loader, which simply skips absent columns."""
    raw = cols[name_key]
    if present is not None and raw not in present:
        return "NULL"
    return '"' + raw.replace('"', '""') + '"'


def _tin_status_sql(ein_expr: str) -> str:
    sentinels = ", ".join("'" + s.replace("'", "''") + "'" for s in sorted(TIN_SENTINELS))
    return (f"CASE WHEN upper(trim(coalesce({ein_expr}, ''))) IN ({sentinels}) "
            f"THEN 'suppressed_unavail' ELSE 'present_unexpected' END")


def _zip5_sql(zip_expr: str) -> str:
    return f"left(regexp_replace(coalesce({zip_expr}, ''), '-', ''), 5)"


# --------------------------------------------------------------------------
# table builders (SQL)
# --------------------------------------------------------------------------
def _create_dim_npi(con, nppes_scan: str, dedup: bool = True) -> None:
    p = _present(con, nppes_scan)
    code = lambda i: f'"Healthcare Provider Taxonomy Code_{i}"'
    sw = lambda i: f'"Healthcare Provider Primary Taxonomy Switch_{i}"'
    # primary = first slot whose switch = 'Y'; fallback to slot 1's code.
    # Only include slots whose code column is actually present in this file.
    slots = [i for i in range(1, N_TAXONOMY_SLOTS + 1)
             if f"Healthcare Provider Taxonomy Code_{i}" in p]
    if slots:
        primary = "COALESCE(" + ", ".join(
            f"CASE WHEN {sw(i)} = 'Y' THEN {code(i)} END" for i in slots
            if f"Healthcare Provider Primary Taxonomy Switch_{i}" in p
        ) + (", " if any(f"Healthcare Provider Primary Taxonomy Switch_{i}" in p for i in slots) else "") \
            + f"{code(slots[0])})"
    else:
        primary = "NULL"
    npi = _col('npi', NPPES_COLS, p)
    # NPPES npidata_pfile is already one-row-per-NPI, so for the whole-universe
    # national build we skip the QUALIFY row_number() dedup, which would otherwise
    # force a full hash/sort over ~8M rows and spill to temp on a tight disk.
    dedup_clause = (f"\n        QUALIFY row_number() OVER (PARTITION BY {npi} "
                    f"ORDER BY {npi}) = 1" if dedup else "")
    con.execute(f"""
        CREATE OR REPLACE TABLE dim_npi AS
        SELECT
            {npi}                              AS npi,
            {_col('entity_type', NPPES_COLS, p)}  AS entity_type,
            CASE {_col('entity_type', NPPES_COLS, p)}
                 WHEN '1' THEN 'Individual (Type 1)'
                 WHEN '2' THEN 'Organization (Type 2)'
                 ELSE 'Unknown' END            AS type_label,
            {_col('org_name', NPPES_COLS, p)}     AS org_name,
            trim(coalesce({_col('first_name', NPPES_COLS, p)}, '') || ' ' ||
                 coalesce({_col('last_name', NPPES_COLS, p)}, '')) AS individual_name,
            {_col('is_sole_prop', NPPES_COLS, p)} AS is_sole_prop,
            {_col('is_subpart', NPPES_COLS, p)}   AS is_subpart,
            {_col('parent_lbn', NPPES_COLS, p)}   AS parent_lbn,
            {_col('addr1', NPPES_COLS, p)}        AS addr1,
            {_col('city', NPPES_COLS, p)}         AS city,
            {_col('state', NPPES_COLS, p)}        AS state,
            {_col('zip', NPPES_COLS, p)}          AS zip,
            {_zip5_sql(_col('zip', NPPES_COLS, p))} AS zip5,
            {primary}                          AS primary_taxonomy,
            {_tin_status_sql(_col('ein', NPPES_COLS, p))} AS tin_status
        FROM {nppes_scan}{dedup_clause}
    """)


def _create_bridge_taxonomy(con, nppes_scan: str, ordered: bool = True) -> None:
    p = _present(con, nppes_scan)
    npi = _col('npi', NPPES_COLS, p)
    code = lambda i: f'"Healthcare Provider Taxonomy Code_{i}"'
    sw = lambda i: (f'"Healthcare Provider Primary Taxonomy Switch_{i}"'
                    if f"Healthcare Provider Primary Taxonomy Switch_{i}" in p else "NULL")
    present_slots = [i for i in range(1, N_TAXONOMY_SLOTS + 1)
                     if f"Healthcare Provider Taxonomy Code_{i}" in p]
    # Single pass over the source: positionally-zipped unnest() of the per-slot
    # code/switch/index lists. A UNION ALL of N per-slot SELECTs would re-scan the
    # whole ~11.5GB CSV N times; this scans it once.
    codes = ", ".join(code(i) for i in present_slots)
    prims = ", ".join(f"({sw(i)} = 'Y')" for i in present_slots)
    idxs = ", ".join(str(i) for i in present_slots)
    # ORDER BY npi, slot forces a full external sort (~spill) over every taxonomy
    # edge of the ~8M-NPI universe. unnest emits slots 1..N in order per source row,
    # so for the national build we skip the global sort.
    order_clause = "\n        ORDER BY npi, slot" if ordered else ""
    con.execute(f"""
        CREATE OR REPLACE TABLE bridge_npi_taxonomy AS
        SELECT npi, taxonomy_code, is_primary, slot FROM (
            SELECT {npi} AS npi,
                   unnest([{codes}]) AS taxonomy_code,
                   unnest([{prims}]) AS is_primary,
                   unnest([{idxs}])  AS slot
            FROM {nppes_scan}
        ) WHERE taxonomy_code IS NOT NULL AND taxonomy_code <> ''{order_clause}
    """)


def _create_dim_ccn(con, pos_scan: str) -> None:
    p = _present(con, pos_scan)
    ccn = _col('ccn', POS_COLS, p)
    con.execute(f"""
        CREATE OR REPLACE TABLE dim_ccn AS
        SELECT
            {ccn}   AS ccn,
            {_col('name', POS_COLS, p)}  AS name,
            {_col('addr', POS_COLS, p)}  AS addr,
            {_col('city', POS_COLS, p)}  AS city,
            {_col('state', POS_COLS, p)} AS state,
            {_col('zip', POS_COLS, p)}   AS zip,
            {_zip5_sql(_col('zip', POS_COLS, p))} AS zip5,
            {_col('category', POS_COLS, p)} AS category
        FROM {pos_scan}
        QUALIFY row_number() OVER (PARTITION BY {ccn}
                                   ORDER BY {_col('name', POS_COLS, p)}) = 1
    """)


def _create_empty_rel_reassignment(con) -> None:
    """No PPEF Reassignment sub-file is published in the current CMS public catalog,
    so build an empty rel_reassignment with the right schema. provider_master still
    left-joins it (every reassignment column comes back empty/0)."""
    con.execute("""
        CREATE OR REPLACE TABLE rel_reassignment (
            ind_npi VARCHAR, ind_name VARCHAR, reasgn_enrollment_id VARCHAR,
            bill_npi VARCHAR, bill_org_name VARCHAR, rcv_enrollment_id VARCHAR
        )
    """)


def _create_rel_reassignment(con, enroll_scan: str, reasgn_scan: str) -> None:
    pe = _present(con, enroll_scan)
    pr = _present(con, reasgn_scan)
    e_npi = _col('npi', PPEF_ENROLL_COLS, pe)
    e_id = _col('enrollment_id', PPEF_ENROLL_COLS, pe)
    e_org = _col('org_name', PPEF_ENROLL_COLS, pe)
    e_first = _col('first_name', PPEF_ENROLL_COLS, pe)
    e_last = _col('last_name', PPEF_ENROLL_COLS, pe)
    r_reasgn = _col('reasgn_enrollment_id', PPEF_REASGN_COLS, pr)
    r_rcv = _col('rcv_enrollment_id', PPEF_REASGN_COLS, pr)
    con.execute(f"""
        CREATE OR REPLACE TABLE rel_reassignment AS
        SELECT DISTINCT
            ind.{e_npi} AS ind_npi,
            trim(coalesce(ind.{e_first}, '') || ' ' || coalesce(ind.{e_last}, '')) AS ind_name,
            r.{r_reasgn} AS reasgn_enrollment_id,
            bill.{e_npi} AS bill_npi,
            bill.{e_org} AS bill_org_name,
            r.{r_rcv} AS rcv_enrollment_id
        FROM {reasgn_scan} r
        LEFT JOIN {enroll_scan} ind  ON r.{r_reasgn} = ind.{e_id}
        LEFT JOIN {enroll_scan} bill ON r.{r_rcv}    = bill.{e_id}
    """)


# --------------------------------------------------------------------------
# CCN <-> NPI bridge (Python / rapidfuzz, blocked on ZIP5)
# --------------------------------------------------------------------------
def _build_bridge_ccn_npi(con, threshold: int = 88) -> pd.DataFrame:
    """Pull only Type-2 org NPIs + facilities into memory, block on ZIP5, and
    fuzzy-match normalized names with rapidfuzz.process.extractOne (C-level)."""
    orgs = con.execute(
        "SELECT npi, org_name, zip5 FROM dim_npi WHERE entity_type = '2'"
    ).df()
    fac = con.execute("SELECT ccn, name, zip5 FROM dim_ccn").df()

    orgs["match_name"] = orgs["org_name"].fillna("").map(_norm_name)
    fac["match_name"] = fac["name"].fillna("").map(_norm_name)
    orgs_by_zip = {z: g.reset_index(drop=True) for z, g in orgs.groupby("zip5")}

    method = "name+zip5_fuzzy" if HAVE_RAPIDFUZZ else "name+zip5_exact"
    results = []
    for f in fac.itertuples(index=False):
        grp = orgs_by_zip.get(f.zip5)
        if grp is None or grp.empty or not f.match_name:
            continue
        if HAVE_RAPIDFUZZ:
            hit = process.extractOne(f.match_name, grp["match_name"].tolist(),
                                     scorer=fuzz.token_sort_ratio, score_cutoff=threshold)
            if hit is None:
                continue
            _, score, idx = hit
        else:
            exact = grp.index[grp["match_name"] == f.match_name]
            if len(exact) == 0:
                continue
            idx, score = int(exact[0]), 100.0
        results.append({"ccn": f.ccn, "facility_name": f.name,
                        "npi": grp.at[idx, "npi"], "match_score": float(score),
                        "match_method": method})

    bridge = pd.DataFrame(results,
                          columns=["ccn", "facility_name", "npi", "match_score", "match_method"])
    con.register("_bridge_ccn_df", bridge)
    con.execute("CREATE OR REPLACE TABLE bridge_ccn_npi AS SELECT * FROM _bridge_ccn_df")
    con.unregister("_bridge_ccn_df")
    return bridge


# --------------------------------------------------------------------------
# provider_master (denormalized) + coverage_report (SQL)
# --------------------------------------------------------------------------
def _create_provider_master(con) -> None:
    con.execute("""
        CREATE OR REPLACE TABLE provider_master AS
        WITH tax AS (
            SELECT npi,
                   string_agg(CASE WHEN is_primary THEN taxonomy_code || '*'
                                   ELSE taxonomy_code END, '; ' ORDER BY slot) AS all_taxonomies,
                   count(DISTINCT taxonomy_code) AS taxonomy_count
            FROM bridge_npi_taxonomy GROUP BY npi
        ),
        bills AS (
            SELECT ind_npi AS npi,
                   string_agg(DISTINCT bill_npi, '; ' ORDER BY bill_npi) AS reassigns_to_billing_npis,
                   string_agg(DISTINCT bill_org_name, '; ' ORDER BY bill_org_name) AS reassigns_to_billing_orgs,
                   count(DISTINCT bill_npi) AS reassignment_out_count
            FROM rel_reassignment WHERE bill_npi IS NOT NULL GROUP BY ind_npi
        ),
        recv AS (
            SELECT bill_npi AS npi,
                   string_agg(DISTINCT ind_npi, '; ' ORDER BY ind_npi) AS receives_reassignment_from_npis,
                   count(DISTINCT ind_npi) AS reassignment_in_count
            FROM rel_reassignment WHERE ind_npi IS NOT NULL GROUP BY bill_npi
        ),
        ccn AS (
            SELECT npi,
                   string_agg(ccn, '; ' ORDER BY ccn) AS linked_ccns,
                   string_agg(facility_name, '; ' ORDER BY ccn) AS linked_ccn_names,
                   string_agg(CAST(match_score AS VARCHAR), '; ' ORDER BY ccn) AS ccn_match_scores
            FROM bridge_ccn_npi GROUP BY npi
        )
        SELECT d.*,
            coalesce(tax.all_taxonomies, '')               AS all_taxonomies,
            coalesce(tax.taxonomy_count, 0)                AS taxonomy_count,
            coalesce(bills.reassigns_to_billing_npis, '')  AS reassigns_to_billing_npis,
            coalesce(bills.reassigns_to_billing_orgs, '')  AS reassigns_to_billing_orgs,
            coalesce(bills.reassignment_out_count, 0)      AS reassignment_out_count,
            coalesce(recv.receives_reassignment_from_npis, '') AS receives_reassignment_from_npis,
            coalesce(recv.reassignment_in_count, 0)        AS reassignment_in_count,
            coalesce(ccn.linked_ccns, '')                  AS linked_ccns,
            coalesce(ccn.linked_ccn_names, '')             AS linked_ccn_names,
            coalesce(ccn.ccn_match_scores, '')             AS ccn_match_scores
        FROM dim_npi d
        LEFT JOIN tax   USING (npi)
        LEFT JOIN bills USING (npi)
        LEFT JOIN recv  USING (npi)
        LEFT JOIN ccn   USING (npi)
    """)


def _build_coverage_report(con) -> pd.DataFrame:
    scalar = lambda sql: con.execute(sql).fetchone()[0]
    ccn_total = scalar("SELECT count(*) FROM dim_ccn")
    ccn_bridged = scalar("SELECT count(*) FROM bridge_ccn_npi")
    rows = [
        ("npi_total", scalar("SELECT count(*) FROM dim_npi")),
        ("npi_type1", scalar("SELECT count(*) FROM dim_npi WHERE entity_type = '1'")),
        ("npi_type2", scalar("SELECT count(*) FROM dim_npi WHERE entity_type = '2'")),
        ("npi_with_tin", scalar("SELECT count(*) FROM dim_npi WHERE tin_status = 'present_unexpected'")),
        ("npi_tin_suppressed", scalar("SELECT count(*) FROM dim_npi WHERE tin_status = 'suppressed_unavail'")),
        ("taxonomy_edges", scalar("SELECT count(*) FROM bridge_npi_taxonomy")),
        ("reassignment_edges", scalar("SELECT count(*) FROM rel_reassignment")),
        ("ccn_total", ccn_total),
        ("ccn_npi_bridged", ccn_bridged),
        ("ccn_unbridged", ccn_total - ccn_bridged),
        ("GAP_tin_per_npi", "NOT PUBLIC (confidential / <UNAVAIL>)"),
        ("GAP_claim_level_rendering_attending", "NOT PUBLIC (claims are PHI)"),
    ]
    report = pd.DataFrame(rows, columns=["metric", "value"])
    report["value"] = report["value"].astype(str)
    con.register("_coverage_df", report)
    con.execute("CREATE OR REPLACE TABLE coverage_report AS SELECT * FROM _coverage_df")
    con.unregister("_coverage_df")
    return report


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------
TABLES = ["provider_master", "dim_npi", "bridge_npi_taxonomy",
          "rel_reassignment", "dim_ccn", "bridge_ccn_npi", "coverage_report"]


def _write_outputs(con, out_dir: str, formats) -> None:
    formats = {formats} if isinstance(formats, str) else set(formats)
    if "both" in formats:
        formats = {"csv", "parquet"}
    for name in TABLES:
        if "parquet" in formats:
            con.execute(f"COPY {name} TO {_q(os.path.join(out_dir, name + '.parquet'))} "
                        f"(FORMAT parquet)")
        if "csv" in formats:
            con.execute(f"COPY {name} TO {_q(os.path.join(out_dir, name + '.csv'))} "
                        f"(FORMAT csv, HEADER, NULLSTR '')")


# --------------------------------------------------------------------------
# pipeline
# --------------------------------------------------------------------------
def run_duckdb(nppes_path, ppef_enroll_path, ppef_reasgn_path, pos_path, out_dir,
               formats=("csv", "parquet"), threads=None, memory_limit=None,
               nppes_one_row_per_npi=False):
    """National-scale build. Returns {table_name: count or DataFrame for small tables}.

    nppes_one_row_per_npi: set True for the full monthly NPPES universe, which is
    already unique per NPI. It skips the dim_npi QUALIFY dedup and the
    bridge_npi_taxonomy ORDER BY so neither forces a temp spill on a tight disk."""
    os.makedirs(out_dir, exist_ok=True)
    con = duckdb.connect()
    if threads:
        con.execute(f"PRAGMA threads={int(threads)}")
    if memory_limit:
        con.execute(f"PRAGMA memory_limit='{memory_limit}'")
    # spill large intermediates to the output dir rather than the OS temp drive
    con.execute(f"PRAGMA temp_directory={_q(os.path.join(out_dir, '_duckdb_tmp'))}")

    nppes_scan = _read_csv_sql(nppes_path)
    enroll_scan = _read_csv_sql(ppef_enroll_path)
    pos_scan = _read_csv_sql(pos_path)

    _create_dim_npi(con, nppes_scan, dedup=not nppes_one_row_per_npi)
    _create_bridge_taxonomy(con, nppes_scan, ordered=not nppes_one_row_per_npi)
    _create_dim_ccn(con, pos_scan)
    have_reasgn = bool(ppef_reasgn_path) and os.path.exists(ppef_reasgn_path)
    if have_reasgn:
        _create_rel_reassignment(con, enroll_scan, _read_csv_sql(ppef_reasgn_path))
    else:
        _create_empty_rel_reassignment(con)
    _build_bridge_ccn_npi(con)
    _create_provider_master(con)
    report = _build_coverage_report(con)

    _write_outputs(con, out_dir, formats)
    con.close()
    return {"coverage_report": report}
