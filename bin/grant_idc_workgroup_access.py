#!/usr/bin/env python3
"""
Grant the athena-audit Lambda roles read-only access to IDC (Identity Center)
managed Athena workgroups.

IDC workgroups enforce their own resource-level policies and do not honour
normal IAM policies alone.  This script uses the Athena PutWorkGroupPolicy
API (via boto3) so it works regardless of AWS CLI version.

Usage:
    python grant_idc_workgroup_access.py                 # auto-detect IDC workgroups
    python grant_idc_workgroup_access.py idc-wg other-wg # explicit workgroup names
"""

import json
import sys

import boto3


REGION = "us-east-1"
ACCOUNT_ID = boto3.client("sts").get_caller_identity()["Account"]

# Actions the history Lambda needs on each workgroup
ACTIONS = [
    "athena:ListQueryExecutions",
    "athena:BatchGetQueryExecution",
    "athena:GetQueryExecution",
    "athena:GetWorkGroup",
]


def get_idc_workgroups() -> list[str]:
    """Return names of workgroups whose IdentityProviderType is not 'IAM'."""
    athena = boto3.client("athena", region_name=REGION)
    wg_names = [w["Name"] for w in athena.list_work_groups()["WorkGroups"]]
    idc_wgs = []
    for name in wg_names:
        detail = athena.get_work_group(WorkGroup=name)["WorkGroup"]
        cfg = detail.get("Configuration", {})
        if cfg.get("IdentityCenterConfiguration") or \
           detail.get("IdentityProviderType", "IAM") != "IAM":
            idc_wgs.append(name)
    return idc_wgs


def put_workgroup_policy(workgroup: str):
    athena = boto3.client("athena", region_name=REGION)
    wg_arn = f"arn:aws:athena:{REGION}:{ACCOUNT_ID}:workgroup/{workgroup}"

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": f"arn:aws:iam::{ACCOUNT_ID}:root"},
                "Action": ACTIONS,
                "Resource": wg_arn,
            }
        ],
    }

    athena.put_resource_policy(
        PolicyInJson=json.dumps(policy),
        ResourceArn=wg_arn,
    )
    print(f"✓ Policy applied to workgroup: {workgroup}")


def main():
    if len(sys.argv) > 1:
        workgroups = sys.argv[1:]
    else:
        print("Auto-detecting IDC workgroups...")
        workgroups = get_idc_workgroups()
        if not workgroups:
            print("No IDC workgroups found.")
            return
        print(f"Found IDC workgroups: {', '.join(workgroups)}")

    for wg in workgroups:
        try:
            put_workgroup_policy(wg)
        except Exception as e:
            print(f"✗ Failed to apply policy to {wg}: {e}")


if __name__ == "__main__":
    main()
