import logging
import os
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _get_env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def get_db_name() -> str:
    return _get_env("DB_NAME", "s3_access_logs_db")


def get_workgroup() -> str:
    return _get_env("WORKGROUP", "primary")


def get_query_timeout() -> int:
    return int(_get_env("DEFAULT_QUERY_TIMEOUT", "300"))


def get_window_hours(event: Dict) -> int:
    if isinstance(event, dict) and "window_hours" in event:
        return int(event["window_hours"])
    return int(_get_env("WINDOW_HOURS", "4"))


def get_stage_view_name() -> str:
    return _get_env("STAGE_VIEW_NAME", "s3_access_events_stage_view")


def get_lookup_table_name() -> str:
    return _get_env("LOOKUP_TABLE_NAME", "identity_center_users")


def get_lookup_table_location() -> str:
    return _get_env(
        "LOOKUP_TABLE_LOCATION",
        f"s3://{os.environ['AUDIT_BUCKET']}/athena_audit/identity_center_users",
    )


def _escape_sql(value: str) -> str:
    return value.replace("'", "''")


def _chunk(values: List[str], size: int) -> Iterable[List[str]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def run_query(query: str) -> Dict:
    client = boto3.client("athena")
    response = client.start_query_execution(
        QueryString=query,
        ResultConfiguration={
            "OutputLocation": f"s3://{_get_env('ATHENA_OUTPUT_BUCKET', os.environ['AUDIT_BUCKET'])}/{_get_env('ATHENA_OUTPUT_FOLDER', 'athena_audit/query_results')}"
        },
        WorkGroup=get_workgroup(),
    )

    execution_id = response["QueryExecutionId"]
    sleep_in_interval = 5
    intervals = int(get_query_timeout() / sleep_in_interval)
    result = {"execution_id": execution_id}

    for _ in range(intervals):
        stats = client.get_query_execution(QueryExecutionId=execution_id)
        status = stats["QueryExecution"]["Status"]["State"]
        if status in ["FAILED", "CANCELLED", "SUCCEEDED"]:
            result["status"] = status
            if "StateChangeReason" in stats["QueryExecution"]["Status"]:
                result["error"] = stats["QueryExecution"]["Status"]["StateChangeReason"]
            return result
        time.sleep(sleep_in_interval)

    timeout_msg = f"Timeout of {get_query_timeout()} seconds occurred. Query id: {execution_id}"
    logger.warning(timeout_msg)
    client.stop_query_execution(QueryExecutionId=execution_id)
    return {"execution_id": execution_id, "status": "CANCELLED", "error": timeout_msg}


def get_query_results(query: str) -> List[Dict[str, Optional[str]]]:
    result = run_query(query)
    if "error" in result and result["status"] != "SUCCEEDED":
        raise RuntimeError(f"Query failed: {result['error']}")

    client = boto3.client("athena")
    pages_it = client.get_paginator("get_query_results").paginate(
        QueryExecutionId=result["execution_id"]
    )

    rows: List[Dict[str, Optional[str]]] = []
    columns = None
    for page in pages_it:
        row_it = iter(page["ResultSet"]["Rows"])
        if columns is None:
            header = next(row_it)["Data"]
            columns = [header[i]["VarCharValue"] for i in range(len(header))]
        for row in row_it:
            row_dict: Dict[str, Optional[str]] = {}
            for i, col in enumerate(columns):
                value = row["Data"][i] if i < len(row["Data"]) else None
                row_dict[col] = value.get("VarCharValue") if value else None
            rows.append(row_dict)
    return rows


def ensure_lookup_table() -> None:
    file_name = os.path.join(
        Path(os.path.dirname(os.path.abspath(__file__))).absolute(),
        "resources/create_identity_center_users_table.sql",
    )
    with open(file_name, "r", encoding="utf-8") as file:
        sql = file.read()

    keywords = {
        "table": f"{get_db_name()}.{get_lookup_table_name()}",
        "location": get_lookup_table_location(),
    }
    for key in keywords:
        sql = sql.replace(f"{{{key}}}", keywords[key])

    result = run_query(sql)
    if "error" in result and result.get("status") != "SUCCEEDED":
        raise RuntimeError(f"Failed creating lookup table: {result['error']}")


def get_candidate_identitystore_users(window_hours: int) -> Set[str]:
    sql = f"""
SELECT DISTINCT identitystore_user
FROM {get_db_name()}.{get_stage_view_name()}
WHERE event_ts >= date_add('hour', -{window_hours}, current_timestamp)
  AND (source_identity IS NULL OR trim(source_identity) = '')
  AND identitystore_user IS NOT NULL
  AND trim(identitystore_user) <> ''
"""
    rows = get_query_results(sql)
    return {row["identitystore_user"] for row in rows if row.get("identitystore_user")}


def get_existing_identitystore_users(identitystore_users: List[str]) -> Set[str]:
    if not identitystore_users:
        return set()

    existing: Set[str] = set()
    for users_chunk in _chunk(identitystore_users, 200):
        quoted = ", ".join(f"'{_escape_sql(user)}'" for user in users_chunk)
        sql = (
            f"SELECT identitystore_user FROM {get_db_name()}.{get_lookup_table_name()} "
            f"WHERE identitystore_user IN ({quoted})"
        )
        rows = get_query_results(sql)
        existing.update(
            row["identitystore_user"] for row in rows if row.get("identitystore_user")
        )
    return existing


def parse_identitystore_user(identitystore_user: str) -> Tuple[Optional[str], Optional[str]]:
    if "/" in identitystore_user:
        parts = identitystore_user.split("/", 1)
        return parts[0], parts[1]

    fallback_store_id = _get_env("IDENTITY_STORE_ID", "")
    if fallback_store_id:
        return fallback_store_id, identitystore_user
    return None, None


def pick_email(user: Dict) -> Optional[str]:
    emails = user.get("Emails", [])
    if not emails:
        return None

    for email in emails:
        if email.get("Primary") and email.get("Value"):
            return email["Value"]

    first = emails[0]
    return first.get("Value")


def build_source_identity(email: str) -> str:
    local_part = email.split("@", 1)[0]
    return local_part.strip().lower()


def resolve_idc_user(identitystore_user: str) -> Optional[Dict[str, str]]:
    identity_store_id, user_id = parse_identitystore_user(identitystore_user)
    if not identity_store_id or not user_id:
        logger.warning(
            "Could not parse identitystore_user '%s'. Provide IDENTITY_STORE_ID if needed.",
            identitystore_user,
        )
        return None

    client = boto3.client("identitystore")
    try:
        user = client.describe_user(IdentityStoreId=identity_store_id, UserId=user_id)
    except ClientError as error:
        logger.warning("describe_user failed for %s: %s", identitystore_user, error)
        return None

    email = pick_email(user)
    if not email:
        logger.warning("No email found in IDC profile for %s", identitystore_user)
        return None

    email = email.strip().lower()
    return {
        "source_identity": build_source_identity(email),
        "identitystore_user": identitystore_user,
        "display_name": (user.get("DisplayName") or "").strip(),
        "email": email,
    }


def upsert_lookup_rows(rows: List[Dict[str, str]]) -> None:
    if not rows:
        return

    for rows_chunk in _chunk(rows, 50):
        values_sql = ",\n      ".join(
            "('{source_identity}', '{identitystore_user}', '{display_name}', '{email}')".format(
                source_identity=_escape_sql(row["source_identity"]),
                identitystore_user=_escape_sql(row["identitystore_user"]),
                display_name=_escape_sql(row["display_name"]),
                email=_escape_sql(row["email"]),
            )
            for row in rows_chunk
        )

        sql = f"""
MERGE INTO {get_db_name()}.{get_lookup_table_name()} AS t
USING (
  SELECT *
  FROM (
    VALUES
      {values_sql}
  ) AS v(source_identity, identitystore_user, display_name, email)
) AS s
ON t.identitystore_user = s.identitystore_user
WHEN MATCHED THEN UPDATE SET
  source_identity = s.source_identity,
  display_name = s.display_name,
  email = s.email,
  last_update = current_timestamp
WHEN NOT MATCHED THEN INSERT (
  source_identity, identitystore_user, display_name, email, last_update
) VALUES (
  s.source_identity, s.identitystore_user, s.display_name, s.email, current_timestamp
)
"""
        result = run_query(sql)
        if "error" in result and result.get("status") != "SUCCEEDED":
            raise RuntimeError(f"Failed MERGE into lookup table: {result['error']}")


def lambda_handler(event, context):
    window_hours = get_window_hours(event if isinstance(event, dict) else {})
    logger.info("Identity sync started. window_hours=%d", window_hours)

    ensure_lookup_table()

    candidate_users = sorted(get_candidate_identitystore_users(window_hours))
    logger.info("Found %d candidate identitystore users", len(candidate_users))
    if not candidate_users:
        return {"window_hours": window_hours, "candidates": 0, "resolved": 0, "upserted": 0}

    existing = get_existing_identitystore_users(candidate_users)
    missing = [user for user in candidate_users if user not in existing]
    logger.info("Existing in lookup: %d, missing: %d", len(existing), len(missing))

    resolved_rows: List[Dict[str, str]] = []
    for identitystore_user in missing:
        resolved = resolve_idc_user(identitystore_user)
        if resolved:
            resolved_rows.append(resolved)

    upsert_lookup_rows(resolved_rows)

    logger.info("Identity sync completed. resolved=%d upserted=%d", len(resolved_rows), len(resolved_rows))
    return {
        "window_hours": window_hours,
        "candidates": len(candidate_users),
        "existing": len(existing),
        "missing": len(missing),
        "resolved": len(resolved_rows),
        "upserted": len(resolved_rows),
    }
