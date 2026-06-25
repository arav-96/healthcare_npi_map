"""Generate tiny synthetic CMS-shaped files so the ETL can be demonstrated.
Column NAMES match the real public files; the rows are fabricated.
Scenarios covered:
  - Type 2 hospital that matches a POS CCN by name+ZIP
  - Type 2 distinct-part rehab unit (subpart) under that hospital
  - Type 2 cardiology group (billing entity) with two employed Type 1 physicians reassigning to it
  - A sole-proprietor Type 1 (bills under own NPI; no reassignment -> billing == rendering)
"""
import os
import pandas as pd

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_cms")
os.makedirs(OUT, exist_ok=True)


def tax_cols():
    c = {}
    for i in range(1, 16):
        c[f"Healthcare Provider Taxonomy Code_{i}"] = ""
        c[f"Healthcare Provider Primary Taxonomy Switch_{i}"] = ""
    return c


def npi_row(npi, etype, org="", first="", last="", addr="", city="", state="", zip_="",
            sole="", subpart="", parent="", tax=None):
    r = {
        "NPI": npi, "Entity Type Code": etype, "Replacement NPI": "",
        "Employer Identification Number (EIN)": "<UNAVAIL>",  # always suppressed in real file
        "Provider Organization Name (Legal Business Name)": org,
        "Provider Last Name (Legal Name)": last, "Provider First Name": first,
        "Provider First Line Business Practice Location Address": addr,
        "Provider Business Practice Location Address City Name": city,
        "Provider Business Practice Location Address State Name": state,
        "Provider Business Practice Location Address Postal Code": zip_,
        "Is Sole Proprietor": sole, "Is Organization Subpart": subpart,
        "Parent Organization LBN": parent,
    }
    r.update(tax_cols())
    for slot, (code, prim) in enumerate((tax or []), start=1):
        r[f"Healthcare Provider Taxonomy Code_{slot}"] = code
        r[f"Healthcare Provider Primary Taxonomy Switch_{slot}"] = prim
    return r


nppes = pd.DataFrame([
    npi_row("1700000001", "2", org="Riverside Regional Hospital",
            addr="100 River Rd", city="Chicago", state="IL", zip_="606010000", subpart="N",
            tax=[("282N00000X", "Y")]),                       # General Acute Care Hospital
    npi_row("1700000002", "2", org="Riverside Regional Hospital Rehab Unit",
            addr="100 River Rd", city="Chicago", state="IL", zip_="606010000",
            subpart="Y", parent="Riverside Regional Hospital",
            tax=[("283X00000X", "Y")]),                       # Rehabilitation Hospital (DPU)
    npi_row("1700000010", "2", org="Lakeside Cardiology Group LLC",
            addr="55 Lake St", city="Chicago", state="IL", zip_="606020000",
            tax=[("207RC0000X", "Y")]),                       # billing org (group)
    npi_row("1700000020", "1", first="Anna", last="Stone",
            addr="55 Lake St", city="Chicago", state="IL", zip_="606020000", sole="N",
            tax=[("207RC0000X", "Y"), ("207RI0011X", "N")]),  # cardiologist, multi-taxonomy
    npi_row("1700000021", "1", first="Ben", last="Ng",
            addr="55 Lake St", city="Chicago", state="IL", zip_="606020000", sole="N",
            tax=[("207RC0000X", "Y")]),
    npi_row("1700000030", "1", first="Carol", last="Diaz",
            addr="9 Main St", city="Peoria", state="IL", zip_="616020000", sole="Y",
            tax=[("207Q00000X", "Y")]),                       # sole proprietor: bills under own NPI
])
nppes.to_csv(f"{OUT}/npidata_sample.csv", index=False)

# PECOS enrollment file (PPEF). Enrollment IDs: I... individual, O... organization.
enroll = pd.DataFrame([
    {"NPI": "1700000010", "PECOS_ASCT_CNTL_ID": "PAC1000010", "ENRLMT_ID": "O20000000000010",
     "PROVIDER_TYPE_DESC": "CLINIC/GROUP PRACTICE", "STATE_CD": "IL",
     "ORG_NAME": "Lakeside Cardiology Group LLC", "FIRST_NAME": "", "LAST_NAME": ""},
    {"NPI": "1700000020", "PECOS_ASCT_CNTL_ID": "PAC1000020", "ENRLMT_ID": "I20000000000020",
     "PROVIDER_TYPE_DESC": "PHYSICIAN/CARDIOLOGY", "STATE_CD": "IL",
     "ORG_NAME": "", "FIRST_NAME": "Anna", "LAST_NAME": "Stone"},
    {"NPI": "1700000021", "PECOS_ASCT_CNTL_ID": "PAC1000021", "ENRLMT_ID": "I20000000000021",
     "PROVIDER_TYPE_DESC": "PHYSICIAN/CARDIOLOGY", "STATE_CD": "IL",
     "ORG_NAME": "", "FIRST_NAME": "Ben", "LAST_NAME": "Ng"},
    {"NPI": "1700000030", "PECOS_ASCT_CNTL_ID": "PAC1000030", "ENRLMT_ID": "I20000000000030",
     "PROVIDER_TYPE_DESC": "PHYSICIAN/FAMILY PRACTICE", "STATE_CD": "IL",
     "ORG_NAME": "", "FIRST_NAME": "Carol", "LAST_NAME": "Diaz"},
])
enroll.to_csv(f"{OUT}/ppef_enrollment_sample.csv", index=False)

# Reassignment: both cardiologists reassign benefits to the cardiology group.
# Sole proprietor Carol does NOT reassign -> she bills under her own Type 1.
reasgn = pd.DataFrame([
    {"REASGN_BNFT_ENRLMT_ID": "I20000000000020", "RCV_BNFT_ENRLMT_ID": "O20000000000010"},
    {"REASGN_BNFT_ENRLMT_ID": "I20000000000021", "RCV_BNFT_ENRLMT_ID": "O20000000000010"},
])
reasgn.to_csv(f"{OUT}/ppef_reassignment_sample.csv", index=False)

# Provider of Services (POS) file -> facility CCNs. Note name has 'INC' noise + 5-digit zip.
pos = pd.DataFrame([
    {"PRVDR_NUM": "140123", "FAC_NAME": "RIVERSIDE REGIONAL HOSPITAL INC", "ST_ADR": "100 RIVER RD",
     "CITY_NAME": "CHICAGO", "STATE_CD": "IL", "ZIP_CD": "60601", "PRVDR_CTGRY_CD": "01"},
    {"PRVDR_NUM": "14T123", "FAC_NAME": "RIVERSIDE REGIONAL HOSP REHABILITATION UNIT", "ST_ADR": "100 RIVER RD",
     "CITY_NAME": "CHICAGO", "STATE_CD": "IL", "ZIP_CD": "60601", "PRVDR_CTGRY_CD": "06"},
    {"PRVDR_NUM": "140999", "FAC_NAME": "ORPHAN COUNTY MEDICAL CENTER", "ST_ADR": "1 NOWHERE LN",
     "CITY_NAME": "SPRINGFIELD", "STATE_CD": "IL", "ZIP_CD": "62700", "PRVDR_CTGRY_CD": "01"},
])
pos.to_csv(f"{OUT}/pos_sample.csv", index=False)

print("wrote sample files to", OUT)
for f in sorted(os.listdir(OUT)):
    print("  ", f)
