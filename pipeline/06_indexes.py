"""Task 7: Vector Search endpoint + PII-scrubbed email index + SOP index.

- gold_email_chunks: one row per case, email text with citizen name + NRIC scrubbed.
- gold_sop_chunks: one row per SOP procedure document.
Both feed Delta Sync indexes with managed GTE embeddings on the endpoint mfa_ccms_vs.

Run: python -m pipeline.06_indexes
"""
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.vectorsearch import (
    DeltaSyncVectorIndexSpecRequest, EmbeddingSourceColumn, EndpointType,
    PipelineType, VectorIndexType)

from pipeline import dbsql
from pipeline.config import (EMB, FQ, IDX_EMAIL, IDX_SOP, PROFILE, VOL_SOP,
                             VS_ENDPOINT)

w = WorkspaceClient(profile=PROFILE)


def build_chunk_tables():
    # Email chunks: scrub NRIC-format strings and the citizen's name from the thread text.
    dbsql.run(f"""
    CREATE OR REPLACE TABLE {FQ}.gold_email_chunks AS
    SELECT
      ce.Case_Ref AS chunk_id,
      ce.Case_Ref AS case_ref,
      ce.Case_Type AS case_type,
      ce.Case_Location AS case_location,
      replace(
        regexp_replace(l.parsed_text, '[STFG][0-9]{{7}}[A-Z]', '[NRIC]'),
        ce.Title_Name, '[NAME]'
      ) AS chunk
    FROM {FQ}.case_extracted ce
    JOIN {FQ}.bronze_email_parsed l ON ce.Case_Ref = l.case_ref
    """)
    dbsql.run(f"ALTER TABLE {FQ}.gold_email_chunks "
              f"SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")

    # SOP chunks: parse the SOP volume, one chunk per procedure doc.
    dbsql.run(f"""
    CREATE OR REPLACE TABLE {FQ}.gold_sop_chunks AS
    SELECT
      regexp_extract(path, '([^/]+)\\\\.pdf$', 1) AS chunk_id,
      regexp_extract(path, 'SOP_(.+)\\\\.pdf$', 1) AS sop_title,
      concat_ws('\\n', transform(
        variant_get(ai_parse_document(content), '$.document.elements', 'ARRAY<VARIANT>'),
        e -> variant_get(e, '$.content', 'STRING'))) AS chunk
    FROM read_files('{VOL_SOP}/', format => 'binaryFile')
    """)
    dbsql.run(f"ALTER TABLE {FQ}.gold_sop_chunks "
              f"SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")
    print("email chunks:", dbsql.run(f"SELECT count(*) c FROM {FQ}.gold_email_chunks")[0]["c"])
    print("sop chunks:", dbsql.run(f"SELECT count(*) c FROM {FQ}.gold_sop_chunks")[0]["c"])
    print("NRIC leak check:", dbsql.run(
        f"SELECT count(*) c FROM {FQ}.gold_email_chunks WHERE chunk RLIKE '[STFG][0-9]{{7}}[A-Z]'")[0]["c"])


def ensure_endpoint():
    names = [e.name for e in w.vector_search_endpoints.list_endpoints()]
    if VS_ENDPOINT not in names:
        w.vector_search_endpoints.create_endpoint(name=VS_ENDPOINT, endpoint_type=EndpointType.STANDARD)
        print("creating endpoint", VS_ENDPOINT)
    # Wait until ONLINE.
    for _ in range(60):
        ep = w.vector_search_endpoints.get_endpoint(VS_ENDPOINT)
        state = ep.endpoint_status.state.value if ep.endpoint_status else "?"
        if state == "ONLINE":
            print("endpoint ONLINE")
            return
        print("endpoint state:", state, "- waiting")
        time.sleep(20)
    raise RuntimeError("endpoint not ONLINE in time")


def create_index(index_name, source_table):
    existing = [i.name for i in w.vector_search_indexes.list_indexes(VS_ENDPOINT)]
    if index_name not in existing:
        w.vector_search_indexes.create_index(
            name=index_name,
            endpoint_name=VS_ENDPOINT,
            primary_key="chunk_id",
            index_type=VectorIndexType.DELTA_SYNC,
            delta_sync_index_spec=DeltaSyncVectorIndexSpecRequest(
                source_table=source_table,
                pipeline_type=PipelineType.TRIGGERED,
                embedding_source_columns=[
                    EmbeddingSourceColumn(name="chunk", embedding_model_endpoint_name=EMB)
                ],
            ),
        )
        print("creating index", index_name)


def wait_index(index_name):
    for _ in range(60):
        idx = w.vector_search_indexes.get_index(index_name)
        st = idx.status
        ready = getattr(st, "ready", None)
        detail = getattr(st, "detailed_state", None) or getattr(st, "index_status", None)
        if ready:
            print(index_name, "READY")
            return
        print(index_name, "detail:", detail, "- waiting")
        time.sleep(20)
    raise RuntimeError(f"{index_name} not ready in time")


def main():
    build_chunk_tables()
    ensure_endpoint()
    create_index(IDX_EMAIL, f"{FQ}.gold_email_chunks")
    create_index(IDX_SOP, f"{FQ}.gold_sop_chunks")
    wait_index(IDX_EMAIL)
    wait_index(IDX_SOP)

    print("\n=== query test: case_email_index ===")
    r = w.vector_search_indexes.query_index(
        index_name=IDX_EMAIL, columns=["case_ref", "case_type", "chunk"],
        query_text="Singaporean arrested and detained in Bangkok, ICA involved", num_results=3)
    for row in r.result.data_array:
        print(row[0], "|", row[1], "| score", round(float(row[-1]), 3))

    print("\n=== query test: sop_index ===")
    r = w.vector_search_indexes.query_index(
        index_name=IDX_SOP, columns=["sop_title", "chunk"],
        query_text="what is the procedure for a serious illness or death overseas", num_results=2)
    for row in r.result.data_array:
        print(row[0], "| score", round(float(row[-1]), 3))


if __name__ == "__main__":
    main()
