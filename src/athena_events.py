import logging
import os
import re
import time
from enum import Enum
from pathlib import Path
from typing import Dict, Generator, List, Set, Tuple

import boto3
from botocore.exceptions import ClientError

from common_utils import (
    get_day_back,
    get_yesterday,
    get_days,
    clear_folder,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def get_db_name():
    return os.environ.get("DB_NAME", "default")


def get_workgroup():
    return os.environ.get("WORKGROUP", "primary")


def get_query_timeout() -> int:
    return int(os.environ.get("DEFAULT_QUERY_TIMEOUT", "300"))


def get_athena_output_bucket():
    return os.environ.get("ATHENA_OUTPUT_BUCKET", os.environ["AUDIT_BUCKET"])


def get_athena_output_folder():
    return os.environ.get("ATHENA_OUTPUT_FOLDER", "athena_audit/query_results")


def get_cost_per_tb() -> float:
    return float(os.environ.get("COST_PER_TB", "5.00"))


def get_regions() -> List[str]:
    return os.environ.get("REGIONS", os.environ["AWS_REGION"]).split(",")


class TableType(Enum):
    CLOUD_TRAIL = (0,)
    HISTORY = (1,)
    EVENTS = (2,)
    EVENTS_STAGING = (3,)

    @property
    def table_name(self):
        return (
            f"{get_db_name()}.{os.environ.get(self.name + '_TABLE', self.name.lower())}"
        )

    @property
    def bucket(self):
        return os.environ.get(f"{self.name}_BUCKET", os.environ["AUDIT_BUCKET"])

    @property
    def folder(self):
        return os.environ.get(
            f"{self.name}_FOLDER", f"athena_audit/{self.name.lower()}"
        )


def run_query(query: str):
    client = boto3.client("athena")
    response = client.start_query_execution(
        QueryString=query,
        ResultConfiguration={
            "OutputLocation": f"s3://{get_athena_output_bucket()}/{get_athena_output_folder()}"
        },
        WorkGroup=get_workgroup(),
    )
    time.sleep(2)
    execution_id = response["QueryExecutionId"]
    sleep_in_interval = 10
    intervals = int(get_query_timeout() / sleep_in_interval)
    result = {"execution_id": execution_id}
    for wait_index in range(intervals):
        stats = client.get_query_execution(QueryExecutionId=execution_id)
        status = stats["QueryExecution"]["Status"]["State"]
        if status in ["FAILED", "CANCELLED", "SUCCEEDED"]:
            result["status"] = status
            if status == "SUCCEEDED":
                result["seconds"] = stats["QueryExecution"]["Statistics"][
                    "EngineExecutionTimeInMillis"
                ]
                result["data_scanned_mb"] = (
                    int(stats["QueryExecution"]["Statistics"]["DataScannedInBytes"])
                    / 1024.0
                    / 1024.0
                )
            if "StateChangeReason" in stats["QueryExecution"]["Status"]:
                error_message = stats["QueryExecution"]["Status"]["StateChangeReason"]
                result["error"] = error_message
            return result
        if wait_index % 6 == 0 and wait_index > 0:
            logger.info("Waiting to query for %d minutes", wait_index / 6)
        time.sleep(sleep_in_interval)
    err_msg = f"Timeout of {get_query_timeout()} seconds occurred. Canceling query execution. Query id: {execution_id}"
    logger.warning(err_msg)
    result["error"] = err_msg
    client.stop_query_execution(QueryExecutionId=execution_id)
    return result


def get_query_results(query: str) -> Generator[Dict, None, None]:
    result = run_query(query)
    if "error" in result:
        raise RuntimeError(f"Query failed: {result['error']}")
    client = boto3.client("athena")
    pages_it = client.get_paginator("get_query_results").paginate(
        QueryExecutionId=result["execution_id"]
    )
    columns = None
    for page in pages_it:
        row_it = iter(page["ResultSet"]["Rows"])
        if columns is None:
            header = next(row_it)["Data"]
            columns = [header[i]["VarCharValue"] for i in range(len(header))]
        for row in row_it:
            row_dict = {}
            for i, col in enumerate(columns):
                value = row["Data"][i]
                row_dict[col] = value["VarCharValue"] if value else None
            yield row_dict


def get_workgroups_filter() -> str:
    return os.environ.get("WORKGROUPS_FILTER", "").strip().lower()


TABLE_PATTERN = re.compile(
    r'\b(?:FROM|JOIN|INTO)\s+`?(\w+)`?(?:\s*\.\s*`?(\w+)`?)?',
    re.IGNORECASE,
)


def extract_tables_from_query(query: str) -> Set[Tuple[str, str]]:
    """Extract (database, table) pairs from a SQL query string.

    Returns a set of (database, table) tuples.  When the query references
    a table without a database qualifier the first element is None.
    """
    if not query:
        return set()
    tables = set()
    for match in TABLE_PATTERN.finditer(query):
        part1, part2 = match.group(1), match.group(2)
        if part2:
            tables.add((part1, part2))
        else:
            tables.add((None, part1))
    return tables


def get_tiered_tables(lf_client) -> Dict[Tuple[str, str], str]:
    """Return a mapping of (database, table) -> tier for all classification_tier tagged tables."""
    tiered: Dict[Tuple[str, str], str] = {}
    try:
        account_id = boto3.client("sts").get_caller_identity()["Account"]

        try:
            tag_info = lf_client.get_lf_tag(
                CatalogId=account_id,
                TagKey="classification_tier",
            )
            tier_values = tag_info["TagValues"]
        except ClientError:
            logger.warning("Could not get LF tag definition; using default tier list")
            tier_values = ["tier_1", "tier_2", "tier_3"]

        logger.info(f"Searching for tables with classification_tier in {tier_values}")

        paginator = lf_client.get_paginator("search_tables_by_lf_tags")
        for page in paginator.paginate(
            CatalogId=account_id,
            Expression=[
                {
                    "TagKey": "classification_tier",
                    "TagValues": tier_values,
                }
            ]
        ):
            for item in page.get("TableList", []):
                db = item["Table"]["DatabaseName"]
                tbl = item["Table"]["Name"]
                tier = None
                for tag in item.get("LFTagsOnTable", []):
                    if tag["TagKey"] == "classification_tier" and tag.get("TagValues"):
                        tier = tag["TagValues"][0]
                if tier:
                    tiered[(db, tbl)] = tier
                    logger.info(f"Tiered table found: {db}.{tbl} = {tier}")
    except ClientError as e:
        logger.warning(f"Could not search LF tags: {e}")
    return tiered


def classify_and_promote(day: str, tiered_tables: Dict[Tuple[str, str], str]):
    """Read events from staging, classify by tier, and insert into the events table."""
    events_table = TableType.EVENTS.table_name
    staging_table = TableType.EVENTS_STAGING.table_name
    db_name = get_db_name()

    if not tiered_tables:
        # No tiered tables — promote all staging records without classification
        insert_sql = f"""
INSERT INTO {events_table} (query_id, event_time, source_identity,
  on_behalf_of_user_id, user_identity_type, user_identity_principal,
  user_identity_arn, user_agent, source_ip, query, database,
  status, state_change_reason, data_scanned, cost, workgroup,
  classification_tier, region, day)
SELECT query_id, event_time, source_identity, on_behalf_of_user_id,
  user_identity_type, user_identity_principal, user_identity_arn,
  user_agent, source_ip, query, database, status, state_change_reason,
  data_scanned, cost, workgroup,
  CAST(NULL AS VARCHAR) AS classification_tier,
  region, day
FROM {staging_table}
WHERE day = '{day}'
"""
        result = run_query(insert_sql)
        if "error" in result:
            logger.error(f"Promote (no classification) failed for {day}: {result['error']}")
        else:
            logger.info(f"Promoted staging to events (no classification) for {day}")
        return 0

    # Read events with queries from staging to determine tiers
    records = list(
        get_query_results(
            f"SELECT query_id, query, database FROM {staging_table} "
            f"WHERE day = '{day}' AND query IS NOT NULL"
        )
    )

    # Determine the most sensitive tier for each query_id
    tier_to_query_ids: Dict[str, List[str]] = {}
    for record in records:
        sql_query = record.get("query", "")
        record_db = record.get("database") or db_name
        tables = extract_tables_from_query(sql_query)

        best_tier = None
        for db, table in tables:
            if db is None:
                db = record_db
            tier = tiered_tables.get((db, table))
            if tier and (best_tier is None or tier < best_tier):
                best_tier = tier

        if best_tier:
            tier_to_query_ids.setdefault(best_tier, []).append(record["query_id"])

    classified_count = sum(len(ids) for ids in tier_to_query_ids.values())
    logger.info(f"Classified {classified_count} of {len(records)} events for {day}")

    # Build CASE expression for tier assignment
    if tier_to_query_ids:
        case_parts = []
        for tier, query_ids in sorted(tier_to_query_ids.items()):
            ids_str = ", ".join(f"'{qid}'" for qid in query_ids)
            case_parts.append(f"WHEN query_id IN ({ids_str}) THEN '{tier}'")
        case_expr = "CASE " + " ".join(case_parts) + " ELSE NULL END"
    else:
        case_expr = "CAST(NULL AS VARCHAR)"

    # Insert from staging into events with classification
    insert_sql = f"""
INSERT INTO {events_table} (query_id, event_time, source_identity,
  on_behalf_of_user_id, user_identity_type, user_identity_principal,
  user_identity_arn, user_agent, source_ip, query, database,
  status, state_change_reason, data_scanned, cost, workgroup,
  classification_tier, region, day)
SELECT query_id, event_time, source_identity, on_behalf_of_user_id,
  user_identity_type, user_identity_principal, user_identity_arn,
  user_agent, source_ip, query, database, status, state_change_reason,
  data_scanned, cost, workgroup,
  {case_expr} AS classification_tier,
  region, day
FROM {staging_table}
WHERE day = '{day}'
"""
    result = run_query(insert_sql)
    if "error" in result:
        logger.error(f"Promote with classification failed for {day}: {result['error']}")
    else:
        logger.info(f"Promoted staging to events with classification for {day}")
    return classified_count


def insert_data(full_day_str: str, region: str):
    year = full_day_str[:4]
    month = full_day_str[5:7]
    day = full_day_str[-2:]

    alter_sql = f"""ALTER TABLE {TableType.CLOUD_TRAIL.table_name} ADD IF NOT EXISTS 
PARTITION (region= '{region}', year= '{year}', month= '{month}', day= '{day}') 
LOCATION 's3://{TableType.CLOUD_TRAIL.bucket}/{TableType.CLOUD_TRAIL.folder}/{region}/{year}/{month}/{day}/'"""
    run_query(alter_sql)

    wg_filter = get_workgroups_filter()
    wg_filter_sql = f"\n      AND LOWER(json_extract_scalar(requestParameters, '$.workGroup')) LIKE '%{wg_filter}%'" if wg_filter else ""

    insert_sql = f"""
INSERT INTO {TableType.EVENTS_STAGING.table_name} (query_id, event_time, source_identity,
  on_behalf_of_user_id, user_identity_type, user_identity_principal,
  user_identity_arn, user_agent, source_ip, query, database,
  status, state_change_reason, data_scanned, cost, workgroup, region, day)
SELECT 
  json_extract_scalar(responseelements, '$.queryExecutionId') AS query_id,
  CAST(From_iso8601_timestamp(eventtime) AS TIMESTAMP) AS event_time,
  useridentity.sessioncontext.sourceidentity AS source_identity,
  useridentity.onbehalfof.userid AS on_behalf_of_user_id,
  useridentity.type AS user_identity_type, 
  useridentity.principalid AS user_identity_principal,
  useridentity.arn AS user_identity_arn,   
  useragent AS user_agent, 
  sourceipaddress AS source_ip,  
  COALESCE(h.query, NULLIF(json_extract_scalar(requestParameters, '$.queryString'), '***OMITTED***')) AS query, 
  json_extract_scalar(requestParameters, '$.queryExecutionContext.database') AS database,
  h.status AS status,
  h.state_change_reason AS state_change_reason,
  COALESCE(h.data_scanned, 0) AS data_scanned,
  CAST(COALESCE(h.data_scanned, 0) / 1099511627776.0 * {get_cost_per_tb()} AS DECIMAL(12,8)) AS cost,
  json_extract_scalar(requestParameters, '$.workGroup') AS workgroup,
  ct.region,
  '{full_day_str}' AS day
FROM {TableType.CLOUD_TRAIL.table_name} AS ct
     LEFT OUTER JOIN {TableType.HISTORY.table_name} AS h 
     ON json_extract_scalar(responseelements, '$.queryExecutionId') = h.query_id
        AND h.day = '{full_day_str}' AND h.region = '{region}'
WHERE eventsource = 'athena.amazonaws.com'
      AND eventname IN ('StartQueryExecution')
      AND useridentity.arn NOT LIKE '%AthenaEventsLambda%'
      AND useridentity.arn NOT LIKE '%AthenaHistoryLambda%'
      AND ct.region = '{region}'
      AND year = '{year}'
      AND month = '{month}'
      AND ct.day = '{day}'{wg_filter_sql}
      AND COALESCE(h.query, NULLIF(json_extract_scalar(requestParameters, '$.queryString'), '***OMITTED***')) IS NOT NULL
"""
    result = run_query(insert_sql)
    if "error" in result:
        logger.warning(f"INSERT (CloudTrail) failed for {full_day_str}, region: {region}: {result['error']}")
    else:
        logger.info(f"Inserted CloudTrail data for {full_day_str}, region: {region}. Result: {result}")

    # Second pass: insert history-only records not found in CloudTrail
    # (IDC/TIP queries often don't generate StartQueryExecution CloudTrail events)
    wg_history_filter_sql = f"\n      AND LOWER(h.workgroup) LIKE '%{wg_filter}%'" if wg_filter else ""
    history_only_sql = f"""
INSERT INTO {TableType.EVENTS_STAGING.table_name} (query_id, event_time, source_identity,
  on_behalf_of_user_id, user_identity_type, user_identity_principal,
  user_identity_arn, user_agent, source_ip, query, database,
  status, state_change_reason, data_scanned, cost, workgroup, region, day)
SELECT
  h.query_id,
  CAST(from_iso8601_timestamp(h.submission_time) AS TIMESTAMP) AS event_time,
  CAST(NULL AS VARCHAR) AS source_identity,
  CAST(NULL AS VARCHAR) AS on_behalf_of_user_id,
  CAST(NULL AS VARCHAR) AS user_identity_type,
  CAST(NULL AS VARCHAR) AS user_identity_principal,
  CAST(NULL AS VARCHAR) AS user_identity_arn,
  CAST(NULL AS VARCHAR) AS user_agent,
  CAST(NULL AS VARCHAR) AS source_ip,
  h.query,
  CAST(NULL AS VARCHAR) AS database,
  h.status,
  h.state_change_reason,
  COALESCE(h.data_scanned, 0) AS data_scanned,
  CAST(COALESCE(h.data_scanned, 0) / 1099511627776.0 * {get_cost_per_tb()} AS DECIMAL(12,8)) AS cost,
  h.workgroup,
  h.region,
  '{full_day_str}' AS day
FROM {TableType.HISTORY.table_name} AS h
WHERE h.day = '{full_day_str}'
      AND h.region = '{region}'
      AND h.query IS NOT NULL{wg_history_filter_sql}
      AND h.query_id NOT IN (
        SELECT json_extract_scalar(responseelements, '$.queryExecutionId')
        FROM {TableType.CLOUD_TRAIL.table_name}
        WHERE eventsource = 'athena.amazonaws.com'
              AND eventname IN ('StartQueryExecution')
              AND region = '{region}'
              AND year = '{year}'
              AND month = '{month}'
              AND day = '{day}'
      )
"""
    result2 = run_query(history_only_sql)
    if "error" in result2:
        logger.warning(f"INSERT (history-only) failed for {full_day_str}, region: {region}: {result2['error']}")
    else:
        logger.info(f"Inserted history-only data for {full_day_str}, region: {region}. Result: {result2}")


def repair_events_table(days_back: int):
    sql = f"ALTER TABLE {TableType.EVENTS.table_name} ADD IF NOT EXISTS"
    for i in range(days_back, 0, -1):
        day = get_day_back(i)
        for region in get_regions():
            sql += f"\nPARTITION (region='{region}', day='{day}')"
    run_query(sql)


def tables_exist(tables: List[str]) -> bool:
    sql = "EXPLAIN "
    for idx, table in enumerate(tables):
        sql += f"(SELECT 1 FROM {table} LIMIT 1)"
        if idx < len(tables) - 1:
            sql += " UNION ALL "
    result = run_query(sql)
    return "error" not in result


def create_table(table_type: TableType):
    run_query(f"DROP TABLE IF EXISTS {table_type.table_name}")
    file_name = os.path.join(
        Path(os.path.dirname(os.path.abspath(__file__))).absolute(),
        f"resources/create_{table_type.name.lower()}_table.sql",
    )
    with open(file_name, "r") as file:
        sql = file.read()
    keywords = {
        "table": table_type.table_name,
        "bucket": table_type.bucket,
        "prefix": table_type.folder,
    }
    for key in keywords:
        sql = sql.replace(f"{{{key}}}", keywords[key])
    result = run_query(sql)
    logger.info(f"Table {table_type.table_name} created. Result: {result}")


def init_database(repair_days_back: int, force: bool = False):
    if force or not tables_exist([t.table_name for t in TableType]):
        logger.info("Creating tables..." if not force else "Force-recreating tables...")
        db_name = get_db_name()
        if db_name != "default":
            bucket = os.environ["AUDIT_BUCKET"]
            run_query(f"CREATE DATABASE IF NOT EXISTS {db_name} LOCATION 's3://{bucket}/athena_audit/'")
        for table_type in TableType:
            create_table(table_type)
        repair_events_table(repair_days_back)
        logger.info(f"Finished creating tables")
        return True
    return False


INIT_DAYS_BACK = 14


def lambda_handler(event, context):
    force = event.get("force_recreate", False)
    init_database(INIT_DAYS_BACK, force=force)
    if force:
        # On table recreation, backfill the last INIT_DAYS_BACK days
        from_day = get_day_back(INIT_DAYS_BACK)
        to_day = get_yesterday()
        logger.info(f"Force recreate: backfilling {INIT_DAYS_BACK} days")
    else:
        day = event.get("day", get_yesterday())
        from_day = day
        to_day = day

    result = {}
    logger.info(f"START. from day: {from_day}, to day: {to_day}")

    # Discover all history partitions (including workgroup= subdirectories)
    logger.info("Repairing history table partitions...")
    run_query(f"MSCK REPAIR TABLE {TableType.HISTORY.table_name}")

    # Fetch classification tiers from Lake Formation
    lf_client = boto3.client("lakeformation")
    tiered_tables = get_tiered_tables(lf_client)
    logger.info(f"Found {len(tiered_tables)} tiered tables in Lake Formation")

    regions = event["regions"].split(",") if "regions" in event else get_regions()
    for day in get_days(from_day, to_day):
        # Stage: insert raw events into staging table
        for region in regions:
            logger.info(f"Current region: {region}, day: {day}")
            clear_folder(
                TableType.EVENTS_STAGING.bucket,
                f"{TableType.EVENTS_STAGING.folder}/region={region}/day={day}",
            )
            insert_data(day, region)

        # Promote: classify and move from staging to events
        for region in regions:
            clear_folder(
                TableType.EVENTS.bucket,
                f"{TableType.EVENTS.folder}/region={region}/day={day}",
            )
        classified = classify_and_promote(day, tiered_tables)
        logger.info(f"Day {day}: classified {classified} events")

        # Clean up staging for this day
        for region in regions:
            clear_folder(
                TableType.EVENTS_STAGING.bucket,
                f"{TableType.EVENTS_STAGING.folder}/region={region}/day={day}",
            )

    events_count = int(
        list(
            get_query_results(
                f"SELECT COUNT() AS events FROM {TableType.EVENTS.table_name} "
                f"WHERE day BETWEEN '{from_day}' AND '{to_day}'"
            )
        )[0]["events"]
    )

    logger.info(
        f"FINISH. from day: {from_day}, to day: {to_day}. Events: {events_count}"
    )
    result["from_day"] = from_day
    result["to_day"] = to_day
    result["events"] = events_count

    return result
