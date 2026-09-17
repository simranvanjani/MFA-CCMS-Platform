"""Task 3: parse case-email PDFs with ai_parse_document -> bronze_email_parsed.

Run: python -m pipeline.02_parse            # full run
     python -m pipeline.02_parse --sample   # 3-file preview only
"""
import sys

from pipeline import dbsql
from pipeline.config import FQ, VOL_EMAILS

# Flatten ai_parse_document output: elements[] -> content, joined into one text blob.
# Case_Ref comes from the subject line token "[SGCON/YYYY/NNNNN]".
PARSE_SELECT = r"""
SELECT
  path,
  regexp_extract(txt, '\\[(SGCON/[0-9]{{4}}/[0-9]+)\\]', 1) AS case_ref,
  txt AS parsed_text,
  err AS parse_error
FROM (
  SELECT path,
    concat_ws('\n', transform(
      variant_get(parsed, '$.document.elements', 'ARRAY<VARIANT>'),
      e -> variant_get(e, '$.content', 'STRING'))) AS txt,
    parsed:error_status AS err
  FROM (
    SELECT path, ai_parse_document(content) AS parsed
    FROM read_files('{vol}/', format => 'binaryFile'){limit}
  )
)
"""


def sample():
    sql = "SELECT case_ref, length(parsed_text) n, substr(parsed_text,1,220) head, parse_error FROM (" \
          + PARSE_SELECT.format(vol=VOL_EMAILS, limit=" LIMIT 3") + ")"
    for r in dbsql.run(sql):
        print("CASE_REF:", r["case_ref"], "| len:", r["n"], "| err:", r["parse_error"])
        print("HEAD:", r["head"][:200].replace("\n", " / "))
        print("---")


def full():
    dbsql.run(
        f"CREATE OR REPLACE TABLE {FQ}.bronze_email_parsed AS "
        + PARSE_SELECT.format(vol=VOL_EMAILS, limit="")
    )
    stats = dbsql.run(
        f"SELECT count(*) total, count(nullif(parsed_text,'')) with_text, "
        f"count(case_ref) with_ref, count(nullif(to_json(parse_error),'null')) errors "
        f"FROM {FQ}.bronze_email_parsed"
    )
    print("bronze_email_parsed:", stats[0])


if __name__ == "__main__":
    if "--sample" in sys.argv:
        sample()
    else:
        full()
