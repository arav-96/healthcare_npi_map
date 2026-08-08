-- ============================================================================
-- ADDITIONAL FY2027 PROCEDURE CONCEPTS (C42-C47)
-- Extends C37-C41 beyond the five headline DRGs into every other FY2027
-- procedure-gated exposure: the full burn family, BMT qualifier, the new
-- uterine-malignancy family, antepartum OR gates, burn-specific debridement,
-- and graft device-character validation.
-- ============================================================================


-- ============================================================================
-- C42 | FULL BURN-FAMILY GRAFT GATE (928/929 vs 934/935)
-- Intent: C38 covered 927 vs 933. The SAME graft gate splits the rest of
--         MDC 22: 928/929 (full-thickness burn WITH skin graft, w & w/o
--         CC/MCC) vs 934/935 (without graft). Validate the 0HR graft on every
--         with-graft burn claim: true Replacement vs temporary covering or
--         dressing application coded up.
-- ============================================================================
SELECT claim_id, drg, los, paid_amount, 'C42_BURN_FAMILY_GRAFT' AS concept_id,
       CASE WHEN NOT proc_concat rlike '(^|,)0HR'   THEN 'GRAFT_CODE_ABSENT'
            WHEN proc_concat rlike '(^|,)0HR..[KJ]' THEN 'TEMP_COVERING_VERIFY'
            ELSE 'GRAFT_DOC_REVIEW' END AS review_leg
FROM claims_universe
WHERE inclusionflag = 1
  AND drg IN ('928','929')
  AND discharge_date >= '2026-10-01';


-- ============================================================================
-- C43 | AUTOLOGOUS vs ALLOGENEIC BMT QUALIFIER (017 sibling steering)
-- Intent: DRG 017 (autologous BMT w/o CC/MCC) took a -39.2% pre-cap cut. The
--         escape routes are (a) CC/MCC capture into 016 (dx side) and
--         (b) the PROCEDURE qualifier: stem-cell transfusion codes carry an
--         autologous vs allogeneic/nonautologous qualifier character, and
--         allogeneic routes to the higher-weighted allogeneic family (014).
--         Validate the qualifier against the transplant source documented -
--         autologous cells coded allogeneic is a one-character family jump.
-- ============================================================================
SELECT claim_id, drg, los, paid_amount, 'C43_BMT_QUALIFIER' AS concept_id
FROM claims_universe
WHERE inclusionflag = 1
  AND drg IN ('014','016','017')
  AND proc_concat rlike '(^|,)302[34]3'              -- PBSC/bone marrow transfusion
  AND discharge_date >= '2026-10-01';
-- Audit leg: qualifier char of the 302 code vs transplant note (donor vs self);
-- verify allogeneic-coded claims have donor documentation.


-- ============================================================================
-- C44 | RADICAL vs TOTAL HYSTERECTOMY GATE - NEW 731-733 FAMILY
-- Intent: FY2027 merged 736-741 into new 731-733 (uterine/adnexa procedures
--         for malignancy) while 734 (pelvic evisceration/radical hyst w/
--         CC/MCC) took a -25.4% pre-cap cut. The "radical" designation is a
--         procedure-code gate: radical hysterectomy requires documented
--         removal of parametrium/upper vagina beyond total hysterectomy
--         (0UT9 + the radical extent), and lymphadenectomy codes ride along.
--         Validate radical-family claims: total hysterectomy documented but
--         radical coded = family overstatement; also year-one routing errors
--         in the brand-new 731-733 with no historical pattern.
-- ============================================================================
SELECT claim_id, drg, principal_dx, los, paid_amount,
       'C44_RADICAL_HYST' AS concept_id,
       CASE WHEN proc_concat rlike '(^|,)0UT[9C]' AND NOT proc_concat rlike '(^|,)0UT[24G]'
                 THEN 'RADICAL_EXTENT_VERIFY'      -- uterus/cervix out, no vagina/parametrial leg
            WHEN NOT dx_concat rlike '(^|,)C5[3-8]|(^|,)C79'
                 THEN 'MALIGNANCY_PDX_VERIFY'      -- 731-733 requires malignancy
            ELSE 'ROUTING_REVIEW' END AS review_leg
FROM claims_universe
WHERE inclusionflag = 1
  AND drg IN ('731','732','733','734','735')
  AND discharge_date >= '2026-10-01';


-- ============================================================================
-- C45 | ANTEPARTUM OR-PROCEDURE GATE (817-819 collapse to medical)
-- Intent: 817-819 (antepartum diagnosis WITH an OR procedure) took the
--         largest pre-cap cuts in the table (-39.2%/-22.9%). The OR procedure
--         is the only thing separating these from the medical antepartum
--         DRGs - and cerclage, D&C, and adnexal procedures on pregnant
--         patients are exactly the codes your C03 no-op-report and C04
--         aborted-procedure patterns hit. An unsupported OR code collapses
--         the claim to the medical antepartum family. (Loser DRGs still
--         overpay when the gate code is false - the cut makes the FAMILY
--         cheaper, not the miscode legitimate.)
-- ============================================================================
SELECT claim_id, drg, los, paid_amount, 'C45_ANTEPARTUM_OR' AS concept_id
FROM claims_universe
WHERE inclusionflag = 1
  AND drg IN ('817','818','819')
  AND los <= 2                                       -- short-stay surgical antepartum
  AND discharge_date >= '2026-10-01';
-- Audit leg: op-report existence (C03) + completion (C04) for the OR code.


-- ============================================================================
-- C46 | BURN-WOUND EXCISIONAL DEBRIDEMENT (0HB) - BURNS VERSION OF C01
-- Intent: burn claims carry their own debridement gate: excision of burn
--         wound (0HB/0JB at burn sites) vs nonexcisional debridement or
--         dressing changes. In MDC 22 the excisional code contributes to
--         surgical routing alongside the graft gate; the C01 language test
--         (excision documented vs washout/curettage) applies verbatim, and
--         with the burn-family weight moves the same finding now re-routes
--         between diverged siblings.
-- ============================================================================
SELECT claim_id, drg, los, paid_amount, 'C46_BURN_DEBRIDEMENT' AS concept_id
FROM claims_universe
WHERE inclusionflag = 1
  AND drg IN ('927','928','929','933','934','935')
  AND proc_concat rlike '(^|,)0[HJ]B'
  AND discharge_date >= '2026-10-01';


-- ============================================================================
-- C47 | GRAFT DEVICE-CHARACTER VALIDATION (autograft without harvest)
-- Intent: on any 0HR skin graft, device char 7 (autologous tissue substitute)
--         requires a harvest - a donor site. A claim coding autograft with NO
--         donor-site excision code (0HB..X harvest pattern) and no donor-site
--         documentation supports either a device-char swap (nonautologous K /
--         synthetic J - temporary coverings) or a root-op downgrade to
--         application. In the up-weighted 463-465 and diverged burn families,
--         the autograft character is now the money character.
-- Guideline: PCS B3.9 (autograft harvest from a different procedure site is
--         coded separately); device key; B6.1.
-- ============================================================================
SELECT claim_id, drg, los, paid_amount, 'C47_AUTOGRAFT_NO_HARVEST' AS concept_id
FROM claims_universe
WHERE inclusionflag = 1
  AND proc_concat rlike '(^|,)0HR..7'                -- autologous device char
  AND NOT proc_concat rlike '(^|,)0HB...X|(^|,)0HB...Z' -- no skin excision/harvest leg
  AND drg IN ('463','464','465','927','928','929')
  AND discharge_date >= '2026-10-01';


-- ============================================================================
-- COMBINED RUN ORDER (C37-C47):
--   C38 -> C42 (sibling gaps, small populations, 100% reviewable)
--   C47 (mechanical device-char check inside the winners)
--   C37 (volume engine) -> C46 (burn debridement layer on same claims)
--   C43, C44 (family-jump gates, low volume high swing)
--   C40, C39, C45 (routing/residual validation)
--   C41 (standing quarterly monitor feeding all of the above)
-- ============================================================================
