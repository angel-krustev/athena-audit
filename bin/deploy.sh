#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_DIR="${PROJECT_ROOT}/config"

###############################################################################
# Usage
###############################################################################
usage() {
  echo "Usage: $0 <environment> [target]"
  echo ""
  echo "  environment  Name of the config file (without .json) under config/"
  echo "               e.g. sandbox, dev, prod"
  echo "  target       Which lambda to deploy: all | history | events | identity-sync"
  echo "               Default: all"
  echo ""
  echo "Available configs:"
  for f in "${CONFIG_DIR}"/*.json; do
    echo "  - $(basename "${f}" .json)"
  done
  exit 1
}

[[ $# -lt 1 ]] && usage
ENV="$1"
TARGET="${2:-all}"
CONFIG_FILE="${CONFIG_DIR}/${ENV}.json"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "ERROR: Config file not found: ${CONFIG_FILE}"
  usage
fi

echo "=== Loading config: ${CONFIG_FILE} ==="

case "${TARGET}" in
  all|history|events|identity-sync)
    ;;
  *)
    echo "ERROR: Invalid target '${TARGET}'. Expected: all | history | events | identity-sync"
    usage
    ;;
esac

DEPLOY_HISTORY=false
DEPLOY_EVENTS=false
DEPLOY_IDENTITY_SYNC=false
case "${TARGET}" in
  all)
    DEPLOY_HISTORY=true
    DEPLOY_EVENTS=true
    DEPLOY_IDENTITY_SYNC=true
    ;;
  history)
    DEPLOY_HISTORY=true
    ;;
  events)
    DEPLOY_EVENTS=true
    ;;
  identity-sync)
    DEPLOY_IDENTITY_SYNC=true
    ;;
esac

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
IDENTITY_SYNC_STACK_NAME="$(cfg IdentitySyncStackName 2>/dev/null || echo '')"
IDENTITY_SYNC_WINDOW_HOURS="$(cfg IdentitySyncWindowHours 2>/dev/null || echo '4')"
IDENTITY_SYNC_DB_NAME="$(cfg IdentitySyncDatabaseName 2>/dev/null || echo 's3_access_logs_db')"
IDENTITY_SYNC_STAGE_VIEW="$(cfg IdentitySyncStageViewName 2>/dev/null || echo 's3_access_events_stage_view')"
IDENTITY_SYNC_LOOKUP_TABLE="$(cfg IdentitySyncLookupTableName 2>/dev/null || echo 'identity_center_users')"
IDENTITY_SYNC_LOOKUP_FOLDER="$(cfg IdentitySyncLookupTableFolder 2>/dev/null || echo 'athena_audit/identity_center_users')"
IDENTITY_STORE_ID="$(cfg IdentityStoreId 2>/dev/null || echo '')"

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

if [[ "${DEPLOY_HISTORY}" == true ]]; then
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
fi

if [[ "${DEPLOY_EVENTS}" == true ]]; then
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
fi

if [[ "${DEPLOY_IDENTITY_SYNC}" == true ]]; then
  ###############################################################################
  # Deploy Identity Center Sync stack (optional)
  ###############################################################################
  if [[ -z "${IDENTITY_SYNC_STACK_NAME}" ]]; then
    echo "ERROR: target=identity-sync but IdentitySyncStackName is empty in ${CONFIG_FILE}"
    exit 1
  fi
  echo ""
  echo "=== Deploying ${IDENTITY_SYNC_STACK_NAME} (${ENV}) ==="
  aws cloudformation deploy \
    --template-file "${PROJECT_ROOT}/cloudformation/athena_identity_center_sync_cloudformation.yaml" \
    --stack-name "${IDENTITY_SYNC_STACK_NAME}" \
    --region "${REGION}" \
    --capabilities CAPABILITY_IAM \
    --parameter-overrides \
      AuditBucket="${AUDIT_BUCKET}" \
      CloudTrailBucket="${CLOUDTRAIL_BUCKET}" \
      CloudTrailFolder="${CLOUDTRAIL_FOLDER}" \
      DatabaseName="${IDENTITY_SYNC_DB_NAME}" \
      StageViewName="${IDENTITY_SYNC_STAGE_VIEW}" \
      LookupTableName="${IDENTITY_SYNC_LOOKUP_TABLE}" \
      LookupTableFolder="${IDENTITY_SYNC_LOOKUP_FOLDER}" \
      Workgroup="${WORKGROUP}" \
      WindowHours="${IDENTITY_SYNC_WINDOW_HOURS}" \
      IdentityStoreId="${IDENTITY_STORE_ID}" \
      Version="${VERSION}" \
      CodeBucket="${CODE_BUCKET}" \
      AthenaOutputFolder="${ATHENA_OUTPUT_FOLDER}" \
      AthenaOutputBucket="${ATHENA_OUTPUT_BUCKET}" \
      KmsKeyArn="${KMS_KEY_ARN}"
fi

###############################################################################
# Force-update Lambda code (picks up new zip even when template is unchanged)
###############################################################################
echo ""
echo "=== Updating Lambda function code (target=${TARGET}) ==="

HISTORY_FUNCTION="${HISTORY_STACK_NAME}-AthenaHistoryLambdaFunction"
EVENTS_FUNCTION="${EVENTS_STACK_NAME}-AthenaEventsLambdaFunction"
IDENTITY_SYNC_FUNCTION="${IDENTITY_SYNC_STACK_NAME}-IdentityCenterSyncLambdaFunction"
#set -x
if [[ "${DEPLOY_HISTORY}" == true ]]; then
  aws lambda update-function-code \
    --function-name "${HISTORY_FUNCTION}" \
    --s3-bucket "${CODE_BUCKET}" \
    --s3-key "${S3_KEY}" \
    --region "${REGION}" 
fi

if [[ "${DEPLOY_EVENTS}" == true ]]; then
  aws lambda update-function-code \
    --function-name "${EVENTS_FUNCTION}" \
    --s3-bucket "${CODE_BUCKET}" \
    --s3-key "${S3_KEY}" \
    --region "${REGION}"
fi

if [[ "${DEPLOY_IDENTITY_SYNC}" == true && -n "${IDENTITY_SYNC_STACK_NAME}" ]]; then
  aws lambda update-function-code \
    --function-name "${IDENTITY_SYNC_FUNCTION}" \
    --s3-bucket "${CODE_BUCKET}" \
    --s3-key "${S3_KEY}" \
    --region "${REGION}"
fi

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
