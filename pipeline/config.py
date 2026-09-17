"""Shared configuration for the MFA CCMS Consular Case Intelligence demo."""

PROFILE = "DEFAULT"
CAT = "kauvey_poc"
SCHEMA = "mfa_ccms"
FQ = f"{CAT}.{SCHEMA}"

WAREHOUSE = "<warehouse-id>"
LLM = "databricks-claude-sonnet-4-5"
EMB = "databricks-gte-large-en"

# Volumes
VOL_EMAILS = f"/Volumes/{CAT}/{SCHEMA}/raw_emails"
VOL_SOP = f"/Volumes/{CAT}/{SCHEMA}/sop_corpus"

# Vector search
VS_ENDPOINT = "mfa_ccms_vs"
IDX_EMAIL = f"{FQ}.case_email_index"
IDX_SOP = f"{FQ}.sop_index"
