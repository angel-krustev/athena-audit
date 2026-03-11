#!/usr/bin/env bash
set -euo pipefail
set -x

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

VERSION="${1:-latest}"
OUTPUT_DIR="${PROJECT_ROOT}/bin"
OUTPUT_FILE="${OUTPUT_DIR}/athena_audit.zip"

echo "Packaging version: ${VERSION}"

# Clean previous build
rm -f "${OUTPUT_FILE}"

# Build in a temp directory so we can include pip-installed packages
BUILD_DIR=$(mktemp -d)
trap "rm -rf ${BUILD_DIR}" EXIT

# Copy source files
cp "${PROJECT_ROOT}/src/"*.py "${BUILD_DIR}/"
cp -r "${PROJECT_ROOT}/src/resources" "${BUILD_DIR}/resources"

# Install cihi_auth whl if present (for IDC workgroup TIP authentication)
# boto3, botocore, requests, setuptools, awscli are excluded — provided by Lambda runtime or not needed
WHL=$(find "${PROJECT_ROOT}/bin" -maxdepth 1 -name 'cihi_auth*.whl' 2>/dev/null | head -1)
if [[ -n "${WHL}" ]]; then
  echo "Installing cihi_auth + dependencies from: $(basename "${WHL}")"
  pip install "${WHL}" -t "${BUILD_DIR}" --quiet \
    --no-deps
  pip install "click>=8.1.7,<8.2" "PyJWT>=2.8.0,<3" "chardet>=5.2.0,<6" "requests>=2.31.0,<3" \
    -t "${BUILD_DIR}" --quiet --upgrade
fi

# Create zip
cd "${BUILD_DIR}/*"
7z a -tzip "${OUTPUT_FILE}" .

# If using zip instead of 7z, uncomment the line below and comment out the 7z line above:
# (cd "${BUILD_DIR}" && zip -r "${OUTPUT_FILE}" .)

echo ""
echo "Package created: ${OUTPUT_FILE}"
