"""Task 6: PII column masks on gold + tag_accuracy view (PRD S9).

Masks name/email PII unless the viewer is in the 'consular_privileged' account group,
so analysts see trends without raw identifiers. tag_accuracy compares the AI-derived
Layer 1 tags against the CCMS-recorded values.

Run: python -m pipeline.05_governance
"""
from pipeline import dbsql
from pipeline.config import FQ

PII_COLUMNS = ["Title_Name", "Caller___Informant", "Registrant", "New_Email"]


def main():
    # Masking function: redact unless the viewer is privileged.
    dbsql.run(f"""
    CREATE OR REPLACE FUNCTION {FQ}.mask_pii(v STRING)
    RETURN CASE WHEN is_account_group_member('consular_privileged')
                THEN v ELSE '***REDACTED***' END
    """)

    for col in PII_COLUMNS:
        dbsql.run(f"ALTER TABLE {FQ}.gold_case_intelligence "
                  f"ALTER COLUMN {col} SET MASK {FQ}.mask_pii")

    # Accuracy view: derived L1 tags vs CCMS-recorded values.
    dbsql.run(f"""
    CREATE OR REPLACE VIEW {FQ}.tag_accuracy AS
    SELECT
      round(avg(CASE WHEN lower(trim(L1_Case_Type)) = lower(trim(Case_Type))
                     THEN 1.0 ELSE 0.0 END), 3) AS case_type_agreement,
      round(avg(CASE WHEN L1_Mission IS NOT NULL AND Assigned_HCG IS NOT NULL
                      AND lower(trim(L1_Mission)) = lower(trim(Assigned_HCG))
                     THEN 1.0 ELSE 0.0 END), 3) AS mission_agreement,
      count(*) AS n_cases
    FROM {FQ}.gold_case_intelligence
    WHERE Case_Type IS NOT NULL AND L1_Case_Type IS NOT NULL
    """)

    print("accuracy:", dbsql.run(f"SELECT * FROM {FQ}.tag_accuracy")[0])
    print("masked sample (current user):", dbsql.run(
        f"SELECT Title_Name FROM {FQ}.gold_case_intelligence LIMIT 3"))


if __name__ == "__main__":
    main()
