#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_DIR="${PROJECT_ROOT}/config"

###############################################################################
# Usage
###############################################################################
usage() {
  echo "Usage: $0 <environment> [version]"
  echo ""
  echo "  environment  Name of the config file (without .json) under config/"
  echo "  version      Docker image tag (default: read from config Version field)"
  echo ""
  echo "This script builds the Docker image for the Fargate history task"
  echo "and pushes it to the ECR repository."
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

###############################################################################
# Read parameters from JSON config
###############################################################################
cfg() { python -c "import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])" "${CONFIG_FILE}" "$1"; }

REGION="$(cfg Region)"
VERSION="${2:-$(cfg Version)}"
FARGATE_STACK_NAME="$(cfg FargateStackName 2>/dev/null || echo '')"

if [[ -z "${FARGATE_STACK_NAME}" ]]; then
  echo "ERROR: FargateStackName is not set in ${CONFIG_FILE}"
  echo "Set FargateStackName in your config file to enable Fargate deployment."
  exit 1
fi

###############################################################################
# Build and push
###############################################################################
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_REPO="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${FARGATE_STACK_NAME}-history"
IMAGE_URI="${ECR_REPO}:${VERSION}"

echo "=== Config: ${CONFIG_FILE} ==="
echo "  Region:    ${REGION}"
echo "  Version:   ${VERSION}"
echo "  ECR Repo:  ${ECR_REPO}"
echo "  Image URI: ${IMAGE_URI}"
echo ""

# Create ECR repo if it doesn't exist
echo "=== Ensuring ECR repository exists ==="
aws ecr describe-repositories \
  --repository-names "${FARGATE_STACK_NAME}-history" \
  --region "${REGION}" 2>/dev/null || \
aws ecr create-repository \
  --repository-name "${FARGATE_STACK_NAME}-history" \
  --region "${REGION}"

echo ""
echo "=== Logging in to ECR ==="
aws ecr get-login-password --region "${REGION}" | \
  docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

echo ""
echo "=== Building Docker image (linux/arm64) ==="
docker build --platform linux/arm64 -t "${IMAGE_URI}" "${PROJECT_ROOT}"

echo ""
echo "=== Pushing image ==="
docker push "${IMAGE_URI}"

echo ""
echo "=== Done ==="
echo "Image URI: ${IMAGE_URI}"
echo ""
echo "To deploy the Fargate stack with this image, run:"
echo "  ./bin/deploy.sh ${ENV}"
