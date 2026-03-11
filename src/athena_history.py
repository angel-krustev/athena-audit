import concurrent.futures
import gzip
import json
import logging
import os
import shutil
import tempfile
from datetime import date, datetime
from typing import List, Generator, Dict

import boto3

from common_utils import (
    get_day_back,
    get_days,
    clear_folder,
    obj_exists,
    get_yesterday,
    get_latest_day_in_s3,
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


def create_history_days_range(
    from_day: str, to_day: str, workgroup: str = None, clear: bool = False
) -> Dict[str, any]:
    if clear:
        for day in get_days(from_day, to_day):
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

    exists = 0
    total_records = 0
    skipped = 0
    for w in workgroups:
        key = get_history_key(from_day, w)
        data_exists = obj_exists(get_bucket(), key)
        logger.info(f"Current workgroup: {w}. Data Exists: {data_exists}")
        if data_exists:
            exists += 1
        else:
            try:
                records = create_history_day_for_workgroup(
                    from_day, to_day, workgroup=w, athena_client=None
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
                        from_day, to_day, workgroup=w, athena_client=idc_client
                    )
                    logger.info(f"Queries for workgroup {w} written (via TIP): {records}")
                    total_records += records
                except Exception as tip_err:
                    logger.warning(f"Skipping workgroup {w}: IAM error: {iam_err} | TIP error: {tip_err}")
                    skipped += 1
    if exists > 0:
        logger.info(f"Data existed for {exists} workgroups")
    if skipped > 0:
        logger.warning(f"Skipped {skipped} workgroups due to access errors")
    return {
        "from_day": from_day,
        "to_day": to_day,
        "workgroups": len(workgroups),
        "data-exists-workgroups": exists,
        "skipped-workgroups": skipped,
        "records": total_records,
    }


def get_query_exec_day(query_exe: dict) -> str:
    query_date = query_exe["Status"]["CompletionDateTime"]
    return query_date.strftime("%Y-%m-%d")


def get_query_executions_data(athena, ids: List[str]) -> dict:
    return athena.batch_get_query_execution(QueryExecutionIds=ids)


def get_query_executions_for_workgroup(
    workgroup: str, from_day: str, athena_client=None
) -> Generator[dict, None, None]:
    athena = athena_client or boto3.client("athena")
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
            # Wait for all futures to complete, in the same order they were created
            for future in futures:
                query_executions = future.result()
                for query in query_executions["QueryExecutions"]:
                    if query["Status"]["State"] in ["SUCCEEDED", "FAILED", "CANCELLED"]:
                        query_day = get_query_exec_day(query)
                        if query_day >= from_day:
                            yield query
                        else:
                            return


def upload_history_file(file_name: str, day: str, workgroup: str):
    with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f_out:
        with (
            open(file_name, "rb") as json_file_in,
            gzip.open(f_out.name, "wb") as gzip_fie,
        ):
            # noinspection PyTypeChecker
            shutil.copyfileobj(json_file_in, gzip_fie)
        key = get_history_key(day, workgroup)
        s3_client = boto3.client("s3")
        s3_client.upload_file(f_out.name, get_bucket(), key)
    logger.info(f"uploaded key: {key}")


def create_history_day_for_workgroup(from_day: str, to_day: str, workgroup: str, athena_client=None) -> int:
    current_day = to_day
    current_day_rows = 0
    total_rows = 0
    json_file = None

    for query in get_query_executions_for_workgroup(workgroup, from_day, athena_client=athena_client):
        query_day = get_query_exec_day(query)
        if query_day < current_day or query_day < from_day:
            if json_file:
                json_file.close()
                logger.info(f"Day: {current_day}, Total: {current_day_rows} rows")
                total_rows += current_day_rows
                current_day_rows = 0
                upload_history_file(json_file.name, current_day, workgroup)
                os.remove(json_file.name)
                json_file = None
                current_day = query_day
        if current_day == query_day:
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
                "workgroup": workgroup,
            }
            json_file.write(json.dumps(record))
            json_file.write("\n")
            current_day_rows += 1
            if current_day_rows % 1000 == 0:
                logger.info(f"Day: {current_day}, Written {current_day_rows} rows")
    if json_file:
        logger.info(f"Day: {current_day}, Total: {current_day_rows} rows")
        total_rows += current_day_rows
        json_file.close()
        upload_history_file(json_file.name, current_day, workgroup)
    return current_day_rows


def validate_day_range(from_day: str, to_day: str):
    if from_day > to_day:
        raise ValueError(f"from_day: {from_day} is greater than to_day: {to_day}")
    if (date.today() - datetime.strptime(from_day, "%Y-%m-%d").date()).days >= 45:
        raise ValueError(
            f"from_day: {from_day} is older than 45 days. Only 45 days are stored in Athena"
        )


def lambda_handler(event, context):
    if "day" in event:
        from_day = event["day"]
        to_day = event["day"]
    elif "days_back" in event:
        from_day = get_day_back(int(event["days_back"]))
        to_day = get_yesterday()
    elif event.get("resume", False):
        latest = get_latest_day_in_s3(get_bucket(), get_location() + "/")
        if latest:
            # Re-process latest day (overlap) in case it was partial
            from_day = latest
            logger.info(f"Resuming from latest day in S3: {latest}")
        else:
            from_day = get_yesterday()
            logger.info("No existing data found in S3, starting from yesterday")
        to_day = get_yesterday()
    else:
        from_day = event.get("from_day", get_yesterday())
        to_day = event.get("to_day", get_yesterday())

    validate_day_range(from_day, to_day)

    logger.info(f"START. from day: {from_day}, to day: {to_day}")
    result = create_history_days_range(
        from_day, to_day, event.get("workgroup"), event.get("force", False)
    )
    logger.info(result)
    return result
