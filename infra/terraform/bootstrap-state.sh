#!/usr/bin/env bash
# One-time bootstrap of the Terraform state storage. Run it yourself after `az login`.
# It creates a resource group and a private Storage account (Entra-only, versioned) for the state
# files, and writes envs/dev/backend.hcl. It does not create any NODO resources.
set -euo pipefail

LOCATION="${LOCATION:-swedencentral}"
RG="${RG:-rg-nodo-tfstate}"
CONTAINER="tfstate"
ENV_NAME="${ENV_NAME:-dev}"

SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
SUFFIX="$(openssl rand -hex 3)"
ACCOUNT="stnodotf${SUFFIX}"
USER_ID="$(az ad signed-in-user show --query id -o tsv)"

echo "Subscription: ${SUBSCRIPTION_ID}"
echo "Creating ${RG} and ${ACCOUNT} in ${LOCATION}"

az group create --name "${RG}" --location "${LOCATION}" --output none
az storage account create \
  --name "${ACCOUNT}" --resource-group "${RG}" --location "${LOCATION}" \
  --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 \
  --allow-blob-public-access false --allow-shared-key-access false --output none
az storage account blob-service-properties update \
  --account-name "${ACCOUNT}" --resource-group "${RG}" \
  --enable-versioning true --enable-delete-retention true --delete-retention-days 14 --output none

ACCOUNT_ID="$(az storage account show --name "${ACCOUNT}" --resource-group "${RG}" --query id -o tsv)"
az role assignment create --assignee-object-id "${USER_ID}" --assignee-principal-type User \
  --role "Storage Blob Data Contributor" --scope "${ACCOUNT_ID}" --output none

# Role assignments take a short while to propagate before the container can be created.
for attempt in 1 2 3 4 5 6; do
  if az storage container create --name "${CONTAINER}" --account-name "${ACCOUNT}" --auth-mode login --output none 2>/dev/null; then
    break
  fi
  echo "Waiting for the role assignment to propagate (${attempt}/6)"
  sleep 20
done

cat > "envs/${ENV_NAME}/backend.hcl" <<EOF
resource_group_name  = "${RG}"
storage_account_name = "${ACCOUNT}"
container_name       = "${CONTAINER}"
key                  = "${ENV_NAME}.tfstate"
use_azuread_auth     = true
EOF

echo "Wrote envs/${ENV_NAME}/backend.hcl. Next:"
echo "  cd envs/${ENV_NAME} && cp terraform.tfvars.example terraform.tfvars   # set subscription_id = ${SUBSCRIPTION_ID}"
echo "  terraform init -backend-config=backend.hcl && terraform plan"
