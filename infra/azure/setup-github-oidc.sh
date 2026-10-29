#!/usr/bin/env bash
set -euo pipefail

ENV_NAME="${ENV_NAME:-dev}"
RG="${RG:-rg-nodo-${ENV_NAME}}"
REPO="PierfrancescoLijoi/nodo-ai"
APP_DISPLAY_NAME="nodo-github-${ENV_NAME}"

if [[ -z "${ACR:-}" ]]; then
  ACR="$(az acr list --resource-group "$RG" --query '[0].name' --output tsv)"
fi

if [[ -z "$ACR" ]]; then
  echo "No registry found in ${RG}. Set ACR explicitly." >&2
  exit 1
fi

subscription_id="$(az account show --query id --output tsv)"
tenant_id="$(az account show --query tenantId --output tsv)"
resource_group_id="$(az group show --name "$RG" --query id --output tsv)"
registry_id="$(az acr show --name "$ACR" --resource-group "$RG" --query id --output tsv)"

client_id="$(az ad app list --display-name "$APP_DISPLAY_NAME" --query '[0].appId' --output tsv)"
if [[ -z "$client_id" ]]; then
  client_id="$(az ad app create --display-name "$APP_DISPLAY_NAME" --query appId --output tsv)"
fi

app_object_id="$(az ad app show --id "$client_id" --query id --output tsv)"
principal_id="$(az ad sp list --filter "appId eq '$client_id'" --query '[0].id' --output tsv)"
if [[ -z "$principal_id" ]]; then
  principal_id="$(az ad sp create --id "$client_id" --query id --output tsv)"
fi

credential_name="github-${ENV_NAME}"
credential_count="$(az ad app federated-credential list \
  --id "$app_object_id" \
  --query "[?name=='${credential_name}'] | length(@)" \
  --output tsv)"

if [[ "$credential_count" == "0" ]]; then
  credential_file="$(mktemp)"
  trap 'rm -f "$credential_file"' EXIT
  cat > "$credential_file" <<EOF
{
  "name": "${credential_name}",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:${REPO}:environment:${ENV_NAME}",
  "description": "GitHub Actions ${ENV_NAME} environment",
  "audiences": ["api://AzureADTokenExchange"]
}
EOF
  az ad app federated-credential create \
    --id "$app_object_id" \
    --parameters "@$credential_file" \
    --output none
fi

az role assignment create \
  --assignee-object-id "$principal_id" \
  --assignee-principal-type ServicePrincipal \
  --role Contributor \
  --scope "$resource_group_id" \
  --output none

az role assignment create \
  --assignee-object-id "$principal_id" \
  --assignee-principal-type ServicePrincipal \
  --role AcrPush \
  --scope "$registry_id" \
  --output none

cat <<EOF

Run these commands from a machine authenticated to GitHub CLI:

gh variable set AZURE_CLIENT_ID --repo ${REPO} --body '${client_id}'
gh variable set AZURE_TENANT_ID --repo ${REPO} --body '${tenant_id}'
gh variable set AZURE_SUBSCRIPTION_ID --repo ${REPO} --body '${subscription_id}'
gh variable set AZURE_RESOURCE_GROUP --repo ${REPO} --body '${RG}'
gh variable set ACR_NAME --repo ${REPO} --body '${ACR}'
gh variable set API_APP_NAME --repo ${REPO} --body 'ca-nodo-${ENV_NAME}-api-primary'
gh variable set WORKER_JOB_NAME --repo ${REPO} --body 'caj-nodo-${ENV_NAME}-worker-primary'
gh variable set MIGRATE_JOB_NAME --repo ${REPO} --body 'caj-nodo-${ENV_NAME}-migrate'
gh variable set RETENTION_JOB_NAME --repo ${REPO} --body 'caj-nodo-${ENV_NAME}-retention'
gh variable set INDEX_VERIFY_JOB_NAME --repo ${REPO} --body 'caj-nodo-${ENV_NAME}-index-verify'
gh variable set REINDEX_JOB_NAME --repo ${REPO} --body 'caj-nodo-${ENV_NAME}-reindex-primary'
gh variable set EXTRA_JOB_NAMES --repo ${REPO} --body 'caj-nodo-${ENV_NAME}-sweep caj-nodo-${ENV_NAME}-audit-archive caj-nodo-${ENV_NAME}-pf-p-api caj-nodo-${ENV_NAME}-pf-p-worker'
# Professional tier only (terraform output api_url is its public address):
#   gh variable set API_APP_NAME_DR --repo ${REPO} --body 'ca-nodo-${ENV_NAME}-api-dr'  (and the resource group of the standby region)
#   gh variable set REINDEX_JOB_NAME_DR --repo ${REPO} --body 'caj-nodo-${ENV_NAME}-reindex-dr'
#   gh variable set PUBLIC_HOST --repo ${REPO} --body '<host of terraform output api_url>'
EOF
