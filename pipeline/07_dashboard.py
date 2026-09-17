"""Task 9: Layer 3 AI/BI (Lakeview) dashboard over gold_case_intelligence.

Builds five insight views (PRD S8) plus KPIs and a detail table, tests every dataset
query, then creates + publishes the dashboard via the Lakeview API.

Run: python -m pipeline.07_dashboard
"""
import json
import os
import subprocess
import sys

from pipeline import dbsql
from pipeline.config import FQ, PROFILE, WAREHOUSE

HELPER_DIR = os.path.expanduser(
    "~/.vibe/marketplace/plugins/fe-databricks-tools/skills/databricks-lakeview-dashboard/resources")
sys.path.insert(0, HELPER_DIR)
from lakeview_builder import LakeviewDashboard  # noqa: E402

G = f"{FQ}.gold_case_intelligence"

DATASETS = {
    "ds_cases": (
        f"SELECT Case_Ref, Case_Type, Case_Status, L1_Country, Assigned_HCG, "
        f"Resolution_Days, to_date(Created_On) AS created FROM {G}"),
    "ds_kpi": (
        f"SELECT count(*) AS total, "
        f"sum(CASE WHEN Case_Status='Closed' THEN 1 ELSE 0 END) AS closed, "
        f"round(avg(Resolution_Days),1) AS avg_days FROM {G}"),
    "ds_flags": (
        f"SELECT Case_Ref, to_date(Created_On) AS created, flag FROM {G} "
        f"LATERAL VIEW explode(L1_Situational_Flags) t AS flag"),
    "ds_agency": (
        f"SELECT a1, a2, count(*) AS pair_count FROM ("
        f"  SELECT Case_Ref, ag1 AS a1, ag2 AS a2 FROM {G} "
        f"  LATERAL VIEW explode(L1_Agencies) x AS ag1 "
        f"  LATERAL VIEW explode(L1_Agencies) y AS ag2 WHERE ag1 < ag2"
        f") GROUP BY a1, a2 ORDER BY pair_count DESC"),
}


def test_queries():
    for name, q in DATASETS.items():
        rows = dbsql.run(q + " LIMIT 3")
        print(f"OK {name}: {len(rows)} sample rows")


def build():
    d = LakeviewDashboard("MFA Consular Case Intelligence")
    d.add_page("Overview")
    for name, q in DATASETS.items():
        d.add_dataset(name, name, q)

    # KPIs (single-row ds_kpi; MAX returns the value).
    d.add_counter("ds_kpi", "total", "MAX", "Total cases", {"x": 0, "y": 0, "width": 2, "height": 3})
    d.add_counter("ds_kpi", "closed", "MAX", "Closed cases", {"x": 2, "y": 0, "width": 2, "height": 3})
    d.add_counter("ds_kpi", "avg_days", "MAX", "Avg resolution (days)", {"x": 4, "y": 0, "width": 2, "height": 3})

    # Case-type distribution by country (grouped bar).
    d.add_bar_chart("ds_cases", "L1_Country", "Case_Ref", "COUNT",
                    "Case-type distribution by country", {"x": 0, "y": 3, "width": 3, "height": 6},
                    color_field="Case_Type")
    # Handling volume by mission.
    d.add_bar_chart("ds_cases", "Assigned_HCG", "Case_Ref", "COUNT",
                    "Handling volume by mission", {"x": 3, "y": 3, "width": 3, "height": 6},
                    sort_descending=True)
    # Resolution timeline by case type.
    d.add_bar_chart("ds_cases", "Case_Type", "Resolution_Days", "AVG",
                    "Avg resolution days by case type", {"x": 0, "y": 9, "width": 3, "height": 6},
                    sort_descending=True)
    # Situational-flag trend over time.
    d.add_line_chart("ds_flags", "created", "Case_Ref", "COUNT", time_grain="MONTH",
                     title="Situational-flag trend over time",
                     position={"x": 3, "y": 9, "width": 3, "height": 6}, color_field="flag")
    # Agency co-involvement (table).
    d.add_table("ds_agency", [
        {"field": "a1", "title": "Agency A"}, {"field": "a2", "title": "Agency B"},
        {"field": "pair_count", "title": "Cases together", "type": "integer"},
    ], "Agency co-involvement", {"x": 0, "y": 15, "width": 3, "height": 6})
    # Case detail table.
    d.add_table("ds_cases", [
        {"field": "Case_Ref", "title": "Case Ref"}, {"field": "Case_Type", "title": "Type"},
        {"field": "L1_Country", "title": "Country"}, {"field": "Case_Status", "title": "Status"},
        {"field": "Resolution_Days", "title": "Resolution days", "type": "integer"},
    ], "Case detail", {"x": 3, "y": 15, "width": 3, "height": 6})
    return d


def deploy(d):
    me = subprocess.run(["databricks", "current-user", "me", "--profile", PROFILE],
                        capture_output=True, text=True, check=True)
    user = json.loads(me.stdout)["userName"]
    payload = d.get_api_payload(WAREHOUSE, f"/Users/{user}")
    res = subprocess.run(
        ["databricks", "api", "post", "/api/2.0/lakeview/dashboards", "--profile", PROFILE,
         "--json", json.dumps(payload)], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(res.stderr or res.stdout)
    out = json.loads(res.stdout)
    did = out["dashboard_id"]
    subprocess.run(["databricks", "api", "post",
                    f"/api/2.0/lakeview/dashboards/{did}/published", "--profile", PROFILE,
                    "--json", json.dumps({"warehouse_id": WAREHOUSE})], check=True,
                   capture_output=True, text=True)
    print("dashboard_id:", did)
    print("path:", out.get("path"))
    return did


if __name__ == "__main__":
    test_queries()
    if "--test-only" not in sys.argv:
        d = build()
        deploy(d)
