# NODO AI - Azure infrastructure (Terraform)

Design: [docs/AZURE-ARCHITECTURE.md](../../docs/AZURE-ARCHITECTURE.md), ADR-010, ADR-011 and ADR-016 to ADR-019. Status: the configuration passes `terraform validate` and `terraform test` (plan-time assertions with mocked providers); it has never been planned or applied against a real subscription.

```text
modules/nodo/        one module, two tiers (variable `tier`)
  main.tf            resource groups, identities, registry, Key Vault and keys, storage, queue, database, search, models
  network.tf         virtual networks per region, subnets, NSG, private DNS, private endpoints   (professional)
  edge.tf            Front Door Premium, WAF, origin group with failover                        (professional)
  gateway.tf         API Management (two regions), policies, backends, AI gateway               (professional)
  apps.tf            Container Apps environments, API, worker, migrate and retention jobs
  dr.tf              standby region: replica, queue pairing, search, models
  monitoring.tf      diagnostics, availability test, SLO burn-rate and resource alerts, workbook (professional)
  security.tf        policy (residency, benchmark), Defender, activity log, locks               (professional)
  rbac.tf            least-privilege role assignments
  policies/          API Management policy templates
  tests/             terraform test: what each tier must create and never regress
envs/dev/            basic tier: cheap, one region, public endpoints guarded by Entra
envs/prod/           professional tier, launch size from docs/CAPACITY-MODEL.md
bootstrap-state.sh   one-time creation of the state Storage account
../azure/setup-github-oidc.sh   owner-run GitHub Actions OIDC and RBAC setup
```

Check without an Azure account (this is what CI does):

```bash
cd infra/terraform
terraform fmt -check -recursive
terraform -chdir=envs/prod init -backend=false && terraform -chdir=envs/prod validate
terraform -chdir=modules/nodo init -backend=false && terraform -chdir=modules/nodo test
```

The professional tier costs real money (Front Door Premium, a Premium gateway unit per region, Premium Service Bus, General Purpose database with HA and a replica, Search Standard with three replicas). Price it in the Azure calculator from the resources in `envs/prod` before the first apply, and set the budget in `cost_budget_amount`.

## What you need first

1. An Azure subscription and the Azure CLI signed in (`az login`).
2. Terraform 1.9 or newer.
3. Permission to create resources and role assignments in the subscription (Owner, or Contributor plus User Access Administrator).

## First deployment

```bash
cd infra/terraform
./bootstrap-state.sh                         # creates the state storage, writes envs/dev/backend.hcl
cd envs/dev
cp terraform.tfvars.example terraform.tfvars # set subscription_id and budget email
terraform init -backend-config=backend.hcl
terraform plan -out plan.tfplan              # read it before applying
terraform apply plan.tfplan
```

After the apply:

- `terraform output api_url` is the value for `EXPO_PUBLIC_API_BASE_URL` in the app.
- The API will refuse to start until `oidc_issuer` and `oidc_audience` are set; this is deliberate (fail closed). Create the Entra External ID tenant, then set both variables and apply again.
- The container images are placeholders. Push the real API image to the registry and roll a revision; Terraform ignores the image after creation.
- No OpenAI model is deployed by default. Check availability for your region, then fill `openai_deployments`.
The migration job now creates the API and worker database roles and the search index idempotently. It runs as the PostgreSQL Entra administrator before each application rollout.

## Deploy pipeline

The deployment workflow builds one image from `services/api`, scans it before push, runs the bootstrap job, updates the worker and API, and smoke-tests `/healthz`. Pushes to `main` deploy to `dev`; manual runs select `dev`, `staging`, or `prod` and use the matching GitHub environment approval rules.

After Terraform creates an environment, the subscription owner runs the OIDC setup script locally after `az login`. `ENV_NAME` defaults to `dev`, `RG` defaults to `rg-nodo-$ENV_NAME`, and `ACR` defaults to the first registry found in that resource group. Set both names explicitly if the resource group has more than one registry.

```bash
ENV_NAME=dev RG=rg-nodo-dev ACR=<terraform-output-acr_name> bash infra/azure/setup-github-oidc.sh
```

The script creates or reuses an Entra application and service principal, adds the environment-specific GitHub federated credential, assigns Contributor on the resource group and AcrPush on the registry, and prints commands for these repository variables without running them:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`
- `ACR_NAME`
- `API_APP_NAME`
- `WORKER_JOB_NAME`
- `MIGRATE_JOB_NAME`

For the initial Terraform apply, set `api_target_port = 80` because the placeholder image listens on port 80. To establish the first real image before enabling the workflow:

```bash
az acr login --name <acr_name>
docker build -t <acr_name>.azurecr.io/nodo-api:first services/api
docker push <acr_name>.azurecr.io/nodo-api:first
az containerapp update --resource-group <resource_group_name> --name <api_app_name> --image <acr_name>.azurecr.io/nodo-api:first
terraform -chdir=infra/terraform/envs/dev apply -var api_target_port=8000
```

Then dispatch the deploy workflow. Its first run updates and starts the migration job before updating the worker and API. Keep `api_target_port` at 8000 for all real NODO images.

## Cost

The always-on parts are the API (one replica), PostgreSQL, Service Bus Standard and Log Analytics ingestion; AI Search is free in dev. Speech and model tokens are pay per use. The module creates a monthly budget with alerts at 50, 80 and 100 percent when you give it an email.

## Known limits of this first version

- Public endpoints on every data service (Entra-only, TLS, shared keys off). Private endpoints and a VNet are the next stage.
- One module for everything. Split it when a second team or a second stack needs its own lifecycle.
- Entra External ID is not managed here because it is a separate tenant.
