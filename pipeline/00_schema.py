"""Task 1: create schema, volumes, and the base CCMS case table (provided schema).

Run: python -m pipeline.00_schema   (or import and call main())
"""
from pipeline import dbsql
from pipeline.config import FQ

# The provided CCMS schema, verbatim. All STRING except the audit datetime.
COLUMNS = [
    ("Do_Not_Modify_Case", "STRING"),
    ("Do_Not_Modify_Row_Checksum", "STRING"),
    ("Do_Not_Modify_Modified_On", "TIMESTAMP"),
    ("Title_Name", "STRING"),
    ("MFA_Ref", "STRING"),
    ("Created_On", "STRING"),
    ("Origin", "STRING"),
    ("Additional_Information", "STRING"),
    ("Advice_Provided___Follow_up", "STRING"),
    ("Allow_Case_History_Access", "STRING"),
    ("Allow_LRS_Access", "STRING"),
    ("Assigned_HCG", "STRING"),
    ("Assistance_Required", "STRING"),
    ("Caller___Informant", "STRING"),
    ("Case_Description", "STRING"),
    ("Case_Handler", "STRING"),
    ("Case_Location", "STRING"),
    ("Case_Ref", "STRING"),
    ("Case_Status", "STRING"),
    ("Case_Subject", "STRING"),
    ("Case_Title", "STRING"),
    ("Case_Type", "STRING"),
    ("Category_Multiselect", "STRING"),
    ("Citizenship", "STRING"),
    ("Contact_Tracing_Status", "STRING"),
    ("Covering_MP", "STRING"),
    ("Current_Location", "STRING"),
    ("Evacuation_Status", "STRING"),
    ("Feedback_Date", "STRING"),
    ("Feedback_Message", "STRING"),
    ("Feedback_Provider", "STRING"),
    ("Feedback_Type", "STRING"),
    ("Handled_By", "STRING"),
    ("HCG_Assigned_Date", "STRING"),
    ("Incident_ID", "STRING"),
    ("LRS_Communication", "STRING"),
    ("LRS_Portal_User_Modify_By", "STRING"),
    ("LRS_Portal_User_Modify_On", "STRING"),
    ("LRS_Update", "STRING"),
    ("Media_Query", "STRING"),
    ("Media_Response", "STRING"),
    ("Modified_On", "STRING"),
    ("MP_Constituency_Division", "STRING"),
    ("MP_Name", "STRING"),
    ("New_Case", "STRING"),
    ("New_Email", "STRING"),
    ("No_of_Open_Child_Cases", "STRING"),
    ("Parent_Case", "STRING"),
    ("Recipient_of_Feedback", "STRING"),
    ("Registrant", "STRING"),
    ("Sub_Category_Multiselect", "STRING"),
    ("Tag", "STRING"),
]


def main():
    dbsql.run(
        f"CREATE SCHEMA IF NOT EXISTS {FQ} "
        "COMMENT 'MFA CCMS Consular Case Intelligence demo'"
    )
    dbsql.run(f"CREATE VOLUME IF NOT EXISTS {FQ}.raw_emails")
    dbsql.run(f"CREATE VOLUME IF NOT EXISTS {FQ}.sop_corpus")

    cols_ddl = ",\n  ".join(f"{name} {typ}" for name, typ in COLUMNS)
    dbsql.run(f"CREATE TABLE IF NOT EXISTS {FQ}.case_extracted (\n  {cols_ddl}\n)")

    # Verify
    tables = [r["tableName"] for r in dbsql.run(f"SHOW TABLES IN {FQ}")]
    desc = dbsql.run(f"DESCRIBE {FQ}.case_extracted")
    print("tables:", tables)
    print("case_extracted columns:", len([d for d in desc if d.get("col_name")]))


if __name__ == "__main__":
    main()
