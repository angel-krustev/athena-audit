import logging
import re
from datetime import datetime, timedelta, date
from typing import Generator, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()


def get_day_back(back: int) -> str:
    return str(date.today() - timedelta(back))


def get_yesterday() -> str:
    return get_day_back(1)


def get_latest_day_in_s3(bucket: str, prefix: str) -> Optional[str]:
    """Scan S3 prefix for the latest day= partition and return it (YYYY-MM-DD), or None."""
    s3_client = boto3.client("s3")
    day_pattern = re.compile(r"day=(\d{4}-\d{2}-\d{2})")
    latest = None
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            match = day_pattern.search(cp["Prefix"])
            if match:
                day_val = match.group(1)
                if latest is None or day_val > latest:
                    latest = day_val
    if latest is not None:
        return latest
    # No top-level day= found — look one level deeper (e.g. region=.../day=...)
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            for inner_page in paginator.paginate(Bucket=bucket, Prefix=cp["Prefix"], Delimiter="/"):
                for inner_cp in inner_page.get("CommonPrefixes", []):
                    match = day_pattern.search(inner_cp["Prefix"])
                    if match:
                        day_val = match.group(1)
                        if latest is None or day_val > latest:
                            latest = day_val
    return latest


def get_days(from_day: str, to_day: str) -> Generator[str, None, None]:
    date_object = datetime.strptime(from_day, "%Y-%m-%d").date()
    while True:
        date_str = str(date_object)[:10]
        yield date_str
        if date_str == to_day:
            break
        date_object += timedelta(1)


def obj_exists(bucket: str, key: str):
    s3_client = boto3.client("s3")
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        else:
            raise e


def clear_folder(bucket: str, s3_folder: str) -> int:
    s3 = boto3.resource("s3")
    bucket = s3.Bucket(bucket)
    res = bucket.objects.filter(Prefix=s3_folder).delete()
    deleted = 0 if len(res) == 0 else len(res[0]["Deleted"])
    logger.info(f"{deleted} objects deleted from under {s3_folder}")
    return deleted
