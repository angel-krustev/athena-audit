import concurrent.futures
import gzip
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, date, timedelta
from typing import List, Generator, Dict

import boto3

from common_utils import (
    clear_folder,
    get_yesterday,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Cached TIP athena client (initialised once per Lambda invocation)
_idc_athena_client = None


def get_idc_athena_client():
    """
    Return an Athena client authenticated via TIP (Trusted Identity Propagation)
    for IDC workgroup access.  Returns None if IDC_SECRET_ARN is not configured.
    """
    global _idc_athena_client
    if _idc_athena_client is not None:
        return _idc_athena_client

    secret_arn = os.environ.get("IDC_SECRET_ARN", "").strip()
    if not secret_arn:
        return None

    logger.info("Initialising TIP session for IDC workgroup access...")
    sm = boto3.client("secretsmanager")
    secret = json.loads(sm.get_secret_value(SecretId=secret_arn)["SecretString"])

    # Write cihi_auth config files to /tmp (Lambda writable area)
    config_dir = "/tmp/.aws_cihi_auth"
    creds_dir = "/tmp/.aws_secure"
    aws_dir = "/tmp/.aws"
    for d in [config_dir, creds_dir, aws_dir]:
        os.makedirs(d, exist_ok=True)

    config = secret["config"]
    config["creds_dir"] = creds_dir
    with open(os.path.join(config_dir, "config.json"), "w") as f:
        json.dump(config, f)

    with open(os.path.join(creds_dir, "idp_token.json"), "w") as f:
        json.dump(secret["tokens"], f)

    # Write minimal AWS config so cihi_auth can write credentials
    with open(os.path.join(aws_dir, "config"), "w") as f:
        f.write("[default]\nregion = {}\noutput = json\n".format(
            os.environ.get("AWS_REGION", "us-east-1")
        ))
    with open(os.path.join(aws_dir, "credentials"), "w") as f:
        f.write("[default]\n")

    # Point cihi_auth to /tmp as HOME so it finds ~/.aws_cihi_auth and ~/.aws_secure
    os.environ["HOME"] = "/tmp"

    # Create cihi-auth CLI wrapper (pip install -t doesn't create console scripts)
    cli_path = "/tmp/cihi-auth"
    with open(cli_path, "w") as f:
        f.write("#!/usr/bin/env python3\nimport sys\nsys.path.insert(0, '/var/task')\nfrom cihi_auth.main import cli\ncli()\n")
    os.chmod(cli_path, 0o755)
    os.environ["PATH"] = "/tmp:" + os.environ.get("PATH", "")

    try:
        from cihi_auth.jupyter_helper import authenticate, get_session
        authenticate()
        session = get_session(profile="default")
        if session is None:
            for d in [config_dir, creds_dir, aws_dir]:
                logger.warning(f"Contents of {d}: {os.listdir(d)}")
            logger.warning("cihi_auth get_session() returned None — IDC workgroups will be skipped")
            return None
    except Exception as e:
        logger.warning(f"TIP authentication failed: {e} — IDC workgroups will be skipped")
        return None

    _idc_athena_client = session.client(
        "athena", region_name=os.environ.get("AWS_REGION", "us-east-1")
    )
    logger.info("TIP session initialised successfully")
    return _idc_athena_client


def get_bucket() -> str:
    return os.environ["AUDIT_BUCKET"]


def get_region():
    return os.environ["AWS_REGION"]


def get_location() -> str:
    location = os.environ.get("HISTORY_FOLDER", "athena_audit/history")
    return location[:-1] if location.endswith("/") else location


def get_daily_location(day: str) -> str:
    return f"{get_location()}/region={get_region()}/day={day}"


def get_daily_location_workgroup(day: str, workgroup: str) -> str:
    return f"{get_location()}/region={get_region()}/day={day}/workgroup={workgroup}"


def get_history_key(day: str, workgroup: str) -> str:
    return f"{get_daily_location_workgroup(day, workgroup)}/data.json.gz"


def create_history_day(
    day: str, workgroup: str = None, clear: bool = False
) -> Dict[str, any]:
    if clear:
        if workgroup:
            path = get_daily_location_workgroup(day, workgroup)
        else:
            path = get_daily_location(day)
        clear_folder(get_bucket(), path)
    if workgroup is None:
        client = boto3.client("athena")
        workgroups: List[str] = [
            w["Name"] for w in client.list_work_groups()["WorkGroups"]
        ]
        wg_filter = os.environ.get("WORKGROUPS_FILTER", "").strip().lower()
        if wg_filter:
            workgroups = [w for w in workgroups if wg_filter in w.lower()]
            logger.info(f"Filtered workgroups (filter='{wg_filter}'): {workgroups}")
    else:
        workgroups = [workgroup]

    # Initialise TIP client for IDC workgroup access (if configured)
    idc_client = get_idc_athena_client()
    logger.info(f"TIP client available: {idc_client is not None}")

    total_records = 0
    skipped = 0
    for w in workgroups:
        try:
            records = create_history_day_for_workgroup(
                day, workgroup=w, athena_client=None
            )
            logger.info(f"Queries for workgroup {w} written: {records}")
            total_records += records
        except Exception as iam_err:
            if idc_client is None:
                logger.warning(f"Skipping workgroup {w}: {iam_err}")
                skipped += 1
                continue
            # IAM access failed — retry with TIP client
            logger.info(f"IAM access failed for workgroup {w} ({iam_err}), retrying with TIP client...")
            try:
                records = create_history_day_for_workgroup(
                    day, workgroup=w, athena_client=idc_client
                )
                logger.info(f"Queries for workgroup {w} written (via TIP): {records}")
                total_records += records
            except Exception as tip_err:
                logger.warning(f"Skipping workgroup {w}: IAM error: {iam_err} | TIP error: {tip_err}")
                skipped += 1
    if skipped > 0:
        logger.warning(f"Skipped {skipped} workgroups due to access errors")
    return {
        "day": day,
        "workgroups": len(workgroups),
        "skipped-workgroups": skipped,
        "records": total_records,
    }


def get_query_exec_day(query_exe: dict) -> date:
    dt = query_exe["Status"].get("SubmissionDateTime")
    if dt is None:
        dt = query_exe["Status"].get("CompletionDateTime")
    if dt is None:
        return None
    return dt.date() if isinstance(dt, datetime) else None


def get_query_executions_data(athena, ids: List[str]) -> dict:
    return athena.batch_get_query_execution(QueryExecutionIds=ids)


def get_query_executions_for_workgroup(
    workgroup: str, from_day: str, athena_client=None
) -> Generator[dict, None, None]:
    athena = athena_client or boto3.client("athena")
    from_date = datetime.strptime(from_day, "%Y-%m-%d").date()
    max_workers = 3
    paginator = iter(
        athena.get_paginator("list_query_executions").paginate(WorkGroup=workgroup)
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as threat_pool:
        while True:
            futures = []
            for _ in range(max_workers):
                try:
                    page = next(paginator)
                except StopIteration:
                    break
                if len(page["QueryExecutionIds"]) > 0:
                    futures.append(
                        threat_pool.submit(
                            get_query_executions_data, athena, page["QueryExecutionIds"]
                        )
                    )
            if len(futures) == 0:
                return
            # Process all futures in this round before deciding to stop
            found_older = False
            for future in futures:
                query_executions = future.result()
                for query in query_executions["QueryExecutions"]:
                    if query["Status"]["State"] in ["SUCCEEDED", "FAILED", "CANCELLED"]:
                        query_day = get_query_exec_day(query)
                        if query_day is None:
                            continue
                        if query_day >= from_date:
                            yield query
                        else:
                            found_older = True
            if found_older:
                return


def upload_history_file(file_name: str, day: str, workgroup: str):
    gz_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".gz") as f_out:
            gz_path = f_out.name
        with open(file_name, "rb") as json_file_in, gzip.open(gz_path, "wb") as gzip_file:
            shutil.copyfileobj(json_file_in, gzip_file)
        key = get_history_key(day, workgroup)
        s3_client = boto3.client("s3")
        s3_client.upload_file(gz_path, get_bucket(), key)
        logger.info(f"uploaded key: {key}")
    finally:
        if gz_path and os.path.exists(gz_path):
            os.remove(gz_path)


def create_history_day_for_workgroup(day: str, workgroup: str, athena_client=None) -> int:
    rows = 0
    json_file = None
    target_date = datetime.strptime(day, "%Y-%m-%d").date()
    try:
        # Pass the day before target to the generator so it pages past all of
        # yesterday's queries even if batch_get_query_execution returns them
        # out of order across page boundaries.
        day_before = str(target_date - timedelta(days=1))
        for query in get_query_executions_for_workgroup(workgroup, day_before, athena_client=athena_client):
            query_day = get_query_exec_day(query)
            if query_day is None or query_day != target_date:
                continue
            if json_file is None:
                json_file = tempfile.NamedTemporaryFile(mode="w", delete=False)
            if "Statistics" in query and "DataScannedInBytes" in query["Statistics"]:
                data_scanned = query["Statistics"]["DataScannedInBytes"]
            else:
                data_scanned = 0
            record = {
                "query_id": query["QueryExecutionId"],
                "query": query["Query"],
                "data_scanned": data_scanned,
                "status": query["Status"]["State"],
                "state_change_reason": query["Status"].get("StateChangeReason", ""),
                "submission_time": query["Status"].get("SubmissionDateTime", "").isoformat() if isinstance(query["Status"].get("SubmissionDateTime"), datetime) else "",
                "workgroup": workgroup,
            }
            json_file.write(json.dumps(record))
            json_file.write("\n")
            rows += 1
            if rows % 1000 == 0:
                logger.info(f"Day: {day}, Written {rows} rows")
        if json_file:
            logger.info(f"Day: {day}, Total: {rows} rows")
            json_file.close()
            upload_history_file(json_file.name, day, workgroup)
    finally:
        if json_file:
            if not json_file.closed:
                json_file.close()
            if os.path.exists(json_file.name):
                os.remove(json_file.name)
    return rows


def lambda_handler(event, context):
    day = event.get("day", get_yesterday())
    logger.info(f"START. day: {day}")
    result = create_history_day(
        day, event.get("workgroup"), event.get("force", False)
    )
    logger.info(result)
    return result
