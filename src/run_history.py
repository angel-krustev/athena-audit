"""Fargate entrypoint for athena_history — runs the same logic as the Lambda handler."""
import json
import logging
import os
import sys

from athena_history import create_history_day
from common_utils import get_yesterday

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger()


def main():
    day = os.environ.get("TARGET_DAY", get_yesterday())
    workgroup = os.environ.get("TARGET_WORKGROUP") or None
    force = os.environ.get("FORCE", "false").lower() == "true"

    logger.info(f"START. day: {day}, workgroup: {workgroup}, force: {force}")
    result = create_history_day(day, workgroup, force)
    logger.info(f"RESULT: {json.dumps(result)}")
    return result


if __name__ == "__main__":
    main()
