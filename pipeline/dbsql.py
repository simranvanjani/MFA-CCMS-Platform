"""Thin SQL runner over the demo SQL warehouse using the Databricks SDK."""
from __future__ import annotations

import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

from . import config

_w = WorkspaceClient(profile=config.PROFILE)


def run(sql: str, warehouse: str = config.WAREHOUSE) -> list[dict]:
    """Execute a SQL statement and return rows as a list of dicts.

    Polls until the statement finishes. Raises RuntimeError on failure.
    """
    resp = _w.statement_execution.execute_statement(
        warehouse_id=warehouse, statement=sql, wait_timeout="50s"
    )
    stmt_id = resp.statement_id
    status = resp.status
    # Poll while pending/running.
    while status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(2)
        resp = _w.statement_execution.get_statement(stmt_id)
        status = resp.status

    if status.state != StatementState.SUCCEEDED:
        msg = status.error.message if status.error else str(status.state)
        raise RuntimeError(f"SQL failed ({status.state}): {msg}\nSQL: {sql[:500]}")

    result = resp.result
    schema = resp.manifest.schema if resp.manifest else None
    if not result or not result.data_array or not schema:
        return []
    cols = [c.name for c in schema.columns]
    return [dict(zip(cols, row)) for row in result.data_array]


if __name__ == "__main__":
    print(run("SELECT 1 AS x"))
