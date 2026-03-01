#!/usr/bin/env bash
set -euo pipefail
###############################################################################
# Creates (or updates) the Secrets Manager secret that the history Lambda uses
# to authenticate via TIP (Trusted Identity Propagation) for IDC workgroups.
#
# Prerequisites:
#   - cihi_auth must have been run at least once so the token files exist.
#   - ~/.aws_cihi_auth/config.json  (module configuration)
#   - ~/.aws_secure/idp_token.json  (OIDC + refresh tokens)
#
# Usage:
#   ./create_idc_secret.sh [secret-name] [region]
#
#   secret-name  defaults to: athena-audit/idc-tokens
#   region       defaults to: us-east-1
###############################################################################

SECRET_NAME="${1:-athena-audit/idc-tokens}"
REGION="${2:-us-east-1}"

CONFIG_FILE="${HOME}/.aws_cihi_auth/config.json"
TOKENS_FILE="${HOME}/.aws_secure/idp_token.json"

# Validate files exist
for f in "${CONFIG_FILE}" "${TOKENS_FILE}"; do
  if [[ ! -f "${f}" ]]; then
    echo "ERROR: Required file not found: ${f}"
    echo "Run cihi_auth authenticate first."
    exit 1
  fi
done

# Build the secret JSON: { "config": {...}, "tokens": {...} }
SECRET_VALUE=$(python -c "
import json, sys
config = json.load(open(sys.argv[1]))
tokens = json.load(open(sys.argv[2]))
# Remove local creds_dir — Lambda will use /tmp
config.pop('creds_dir', None)
print(json.dumps({'config': config, 'tokens': tokens}))
" "${CONFIG_FILE}" "${TOKENS_FILE}")

# Create or update the secret
if aws secretsmanager describe-secret --secret-id "${SECRET_NAME}" --region "${REGION}" >/dev/null 2>&1; then
  echo "Updating existing secret: ${SECRET_NAME}"
  aws secretsmanager put-secret-value \
    --secret-id "${SECRET_NAME}" \
    --secret-string "${SECRET_VALUE}" \
    --region "${REGION}" 
else
  echo "Creating new secret: ${SECRET_NAME}"
  aws secretsmanager create-secret \
    --name "${SECRET_NAME}" \
    --description "TIP tokens for athena-audit history Lambda (IDC workgroup access)" \
    --secret-string "${SECRET_VALUE}" \
    --region "${REGION}" 
fi

# Print the ARN for use in config
SECRET_ARN=$(aws secretsmanager describe-secret \
  --secret-id "${SECRET_NAME}" \
  --region "${REGION}" \
  --query "ARN" --output text )

echo ""
echo "=== Done ==="
echo "Secret ARN: ${SECRET_ARN}"
echo ""
echo "Add this to your config/<env>.json:"
echo "  \"IdcSecretArn\": \"${SECRET_ARN}\""
