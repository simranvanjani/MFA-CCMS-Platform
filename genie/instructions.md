# Genie Space — Consular Case Intelligence

**Target table:** `kauvey_poc.mfa_ccms.gold_case_intelligence` (+ `kauvey_poc.mfa_ccms.tag_accuracy`)
**Warehouse:** `3ca7ddd9d10dbbac`
**Space id:** 01f1b2809936166da1a732dc78d8162f

## Description for Genie

This space answers operational questions about Singapore MFA consular cases. Each row is one
case, keyed by `Case_Ref` / `Case_Id`. Structured CCMS fields sit alongside AI-derived Layer 1
tags (prefixed `L1_`) and Layer 2 analytical free-text (prefixed `L2_`).

## Key columns

- `Case_Ref`, `Case_Id` — case identifier.
- `Case_Type` — CCMS-recorded classification (Arrest & Detention, Serious Illness/Death,
  Victim of Crime, Lost/Stolen Document, Evacuation, Scam).
- `L1_Case_Type` — AI-derived classification (compare to `Case_Type` for accuracy).
- `Case_Status` — Pending / Closed.
- `Case_Handler` — HQ unit (CRC or CON/OPS). `Assigned_HCG` / `L1_Mission` — responsible mission.
- `Case_Location`, `L1_Country` — where events occurred.
- `L1_Agencies` — ARRAY of external SG agencies (ICA, SPF, MHA, MOH). Use `array_contains`.
- `L1_Situational_Flags` — ARRAY of flags (scam, MP/POH escalation, language barrier,
  welfare concern). Use `array_contains`.
- `Resolution_Days` — days to resolution for closed cases.
- `Created_On` — case creation date (string, YYYY-MM-DD).
- `L2_Summary_Pathway`, `L2_Assistance_Req_vs_Provided`, `L2_Complications_Delays`,
  `L2_External_Resources`, `L2_Lessons_Learnt` — analytical narrative fields.

## Synonyms

- "unit" / "handler" / "team" → `Case_Handler`; "mission" / "post" / "HCG" → `Assigned_HCG` / `L1_Mission`.
- "agency" → `L1_Agencies`; "flag" → `L1_Situational_Flags`; "country" → `L1_Country`.
- "resolution time" / "time to close" → `Resolution_Days`.

## Example questions → SQL

**Arrest & detention cases in Thailand where ICA was involved in the last 12 months**
```sql
SELECT Case_Ref, Case_Location, Case_Status, Created_On
FROM kauvey_poc.mfa_ccms.gold_case_intelligence
WHERE Case_Type = 'Arrest & Detention'
  AND L1_Country = 'Thailand'
  AND array_contains(L1_Agencies, 'ICA')
  AND to_date(Created_On) >= add_months(current_date(), -12);
```

**Handling volume by unit**
```sql
SELECT Case_Handler, count(*) AS cases
FROM kauvey_poc.mfa_ccms.gold_case_intelligence
GROUP BY Case_Handler ORDER BY cases DESC;
```

**Case-type distribution by country**
```sql
SELECT L1_Country, Case_Type, count(*) AS n
FROM kauvey_poc.mfa_ccms.gold_case_intelligence
GROUP BY L1_Country, Case_Type ORDER BY L1_Country, n DESC;
```

**Average resolution time by case type**
```sql
SELECT Case_Type, round(avg(Resolution_Days),1) AS avg_days, count(*) AS closed
FROM kauvey_poc.mfa_ccms.gold_case_intelligence
WHERE Resolution_Days IS NOT NULL
GROUP BY Case_Type ORDER BY avg_days DESC;
```

**Scam cases with an MP/POH escalation**
```sql
SELECT Case_Ref, Case_Location, Case_Status
FROM kauvey_poc.mfa_ccms.gold_case_intelligence
WHERE array_contains(L1_Situational_Flags, 'scam')
  AND array_contains(L1_Situational_Flags, 'MP/POH escalation');
```

**How often does the AI case type match the CCMS-recorded type?**
```sql
SELECT * FROM kauvey_poc.mfa_ccms.tag_accuracy;
```

**Which countries have the most welfare-concern cases?**
```sql
SELECT L1_Country, count(*) AS n
FROM kauvey_poc.mfa_ccms.gold_case_intelligence
WHERE array_contains(L1_Situational_Flags, 'welfare concern')
GROUP BY L1_Country ORDER BY n DESC;
```
