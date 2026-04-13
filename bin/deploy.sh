#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_DIR="${PROJECT_ROOT}/config"

###############################################################################
# Usage
###############################################################################
usage() {
  echo "Usage: $0 <environment>"
  echo ""
  echo "  environment  Name of the config file (without .json) under config/"
  echo "               e.g. sandbox, dev, prod"
  echo ""
  echo "Available configs:"
  for f in "${CONFIG_DIR}"/*.json; do
    echo "  - $(basename "${f}" .json)"
  done
  exit 1
}

[[ $# -lt 1 ]] && usage
ENV="$1"
CONFIG_FILE="${CONFIG_DIR}/${ENV}.json"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "ERROR: Config file not found: ${CONFIG_FILE}"
  usage
fi

echo "=== Loading config: ${CONFIG_FILE} ==="

###############################################################################
# Read parameters from JSON config (uses python – no jq dependency)
###############################################################################
cfg() { python -c "import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])" "${CONFIG_FILE}" "$1"; }

CODE_BUCKET="$(cfg CodeBucket)"
VERSION="${VERSION:-$(cfg Version)}"
REGION="$(cfg Region)"

HISTORY_STACK_NAME="$(cfg HistoryStackName)"
EVENTS_STACK_NAME="$(cfg EventsStackName)"

AUDIT_BUCKET="$(cfg AuditBucket)"
CLOUDTRAIL_BUCKET="$(cfg CloudTrailBucket)"
CLOUDTRAIL_FOLDER="$(cfg CloudTrailFolder)"
DATABASE_NAME="$(cfg DatabaseName)"
EVENTS_FOLDER="$(cfg EventsFolder)"
HISTORY_FOLDER="$(cfg HistoryFolder)"
REGIONS="$(cfg REGIONS)"
WORKGROUP="$(cfg Workgroup)"
ATHENA_OUTPUT_FOLDER="$(cfg AthenaOutputFolder)"
ATHENA_OUTPUT_BUCKET="$(cfg AthenaOutputBucket)"
KMS_KEY_ARN="$(cfg KmsKeyArn)"
WORKGROUPS_FILTER="$(cfg WorkgroupsFilter)"
IDC_SECRET_ARN="$(cfg IdcSecretArn)"

# Fargate config (optional — only deployed if FargateStackName is set)
FARGATE_STACK_NAME="$(cfg FargateStackName 2>/dev/null || echo '')"
SUBNET_IDS="$(cfg SubnetIds 2>/dev/null || echo '')"
SECURITY_GROUP_ID="$(cfg SecurityGroupId 2>/dev/null || echo '')"

###############################################################################
# Package
###############################################################################
echo "=== Packaging Lambda code ==="
bash "${SCRIPT_DIR}/package.sh" "${VERSION}"

###############################################################################
# Upload code to S3
###############################################################################
S3_KEY="versions/${VERSION}/athena_audit.zip"
echo ""
echo "=== Uploading package to s3://${CODE_BUCKET}/${S3_KEY} ==="
aws s3 cp "${PROJECT_ROOT}/bin/athena_audit.zip" "s3://${CODE_BUCKET}/${S3_KEY}"

###############################################################################
# Deploy History stack
###############################################################################
echo ""
echo "=== Deploying ${HISTORY_STACK_NAME} (${ENV}) ==="
aws cloudformation deploy \
  --template-file "${PROJECT_ROOT}/cloudformation/athena_history_cloudformation.yaml" \
  --stack-name "${HISTORY_STACK_NAME}" \
  --region "${REGION}" \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    AuditBucket="${AUDIT_BUCKET}" \
    HistoryFolder="${HISTORY_FOLDER}" \
    Version="${VERSION}" \
    CodeBucket="${CODE_BUCKET}" \
    KmsKeyArn="${KMS_KEY_ARN}" \
    WorkgroupsFilter="${WORKGROUPS_FILTER}" \
    IdcSecretArn="${IDC_SECRET_ARN}"

###############################################################################
# Deploy Events stack
###############################################################################
echo ""
echo "=== Deploying ${EVENTS_STACK_NAME} (${ENV}) ==="
aws cloudformation deploy \
  --template-file "${PROJECT_ROOT}/cloudformation/athena_events_cloudformation.yaml" \
  --stack-name "${EVENTS_STACK_NAME}" \
  --region "${REGION}" \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    AuditBucket="${AUDIT_BUCKET}" \
    CloudTrailBucket="${CLOUDTRAIL_BUCKET}" \
    CloudTrailFolder="${CLOUDTRAIL_FOLDER}" \
    DatabaseName="${DATABASE_NAME}" \
    REGIONS="${REGIONS}" \
    Workgroup="${WORKGROUP}" \
    HistoryFolder="${HISTORY_FOLDER}" \
    EventsFolder="${EVENTS_FOLDER}" \
    Version="${VERSION}" \
    AthenaOutputFolder="${ATHENA_OUTPUT_FOLDER}" \
    AthenaOutputBucket="${ATHENA_OUTPUT_BUCKET}" \
    CodeBucket="${CODE_BUCKET}" \
    KmsKeyArn="${KMS_KEY_ARN}" \
    WorkgroupsFilter="${WORKGROUPS_FILTER}"

###############################################################################
# Force-update Lambda code (picks up new zip even when template is unchanged)
###############################################################################
echo ""
echo "=== Updating Lambda function code ==="

HISTORY_FUNCTION="${HISTORY_STACK_NAME}-AthenaHistoryLambdaFunction"
EVENTS_FUNCTION="${EVENTS_STACK_NAME}-AthenaEventsLambdaFunction"
set -x
aws lambda update-function-code \
  --function-name "${HISTORY_FUNCTION}" \
  --s3-bucket "${CODE_BUCKET}" \
  --s3-key "${S3_KEY}" \
  --region "${REGION}" 

aws lambda update-function-code \
  --function-name "${EVENTS_FUNCTION}" \
  --s3-bucket "${CODE_BUCKET}" \
  --s3-key "${S3_KEY}" \
  --region "${REGION}"

echo ""
echo "=== Deployment complete (${ENV}) ==="

###############################################################################
# Deploy Fargate stack (optional)
###############################################################################
if [[ -n "${FARGATE_STACK_NAME}" ]]; then
  echo ""
  echo "=== Building and pushing Docker image ==="
  ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
  ECR_REPO="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${FARGATE_STACK_NAME}-history"
  IMAGE_URI="${ECR_REPO}:${VERSION}"

  # Create ECR repo if it doesn't exist (CloudFormation may not have run yet)
  aws ecr describe-repositories --repository-names "${FARGATE_STACK_NAME}-history" --region "${REGION}" 2>/dev/null || \
    aws ecr create-repository --repository-name "${FARGATE_STACK_NAME}-history" --region "${REGION}"

  aws ecr get-login-password --region "${REGION}" | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
  docker build --platform linux/arm64 -t "${IMAGE_URI}" "${PROJECT_ROOT}"
  docker push "${IMAGE_URI}"

  echo ""
  echo "=== Deploying ${FARGATE_STACK_NAME} (${ENV}) ==="
  aws cloudformation deploy \
    --template-file "${PROJECT_ROOT}/cloudformation/athena_history_fargate_cloudformation.yaml" \
    --stack-name "${FARGATE_STACK_NAME}" \
    --region "${REGION}" \
    --capabilities CAPABILITY_IAM \
    --parameter-overrides \
      AuditBucket="${AUDIT_BUCKET}" \
      HistoryFolder="${HISTORY_FOLDER}" \
      KmsKeyArn="${KMS_KEY_ARN}" \
      WorkgroupsFilter="${WORKGROUPS_FILTER}" \
      IdcSecretArn="${IDC_SECRET_ARN}" \
      ImageUri="${IMAGE_URI}" \
      SubnetIds="${SUBNET_IDS}" \
      SecurityGroupId="${SECURITY_GROUP_ID}"

  echo ""
  echo "=== Fargate deployment complete (${ENV}) ==="
fi
