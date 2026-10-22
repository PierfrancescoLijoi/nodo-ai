# NODO AI - Azure infrastructure (Terraform)

Design: [docs/AZURE-ARCHITECTURE.md](../../docs/AZURE-ARCHITECTURE.md), ADR-010 and ADR-011. Status: the configuration passes `terraform validate`; it has never been planned or applied against a real subscription.

```text
modules/nodo/   one module: resource group, identities, registry, Key Vault, Storage, Service Bus,
                PostgreSQL, AI Search, Speech and OpenAI accounts, Container Apps (api + worker job),
                monitoring, budget, role assignments
envs/dev/       root for one environment (staging and prod are copies with their own state and sizes)
bootstrap-state.sh   one-time creation of the state Storage account
```

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
- Create the database roles for the two managed identities (use `identity_client_ids`). Until then the API cannot read Postgres.

## Cost

The always-on parts are the API (one replica), PostgreSQL, Service Bus Standard and Log Analytics ingestion; AI Search is free in dev. Speech and model tokens are pay per use. The module creates a monthly budget with alerts at 50, 80 and 100 percent when you give it an email.

## Known limits of this first version

- Public endpoints on every data service (Entra-only, TLS, shared keys off). Private endpoints and a VNet are the next stage.
- One module for everything. Split it when a second team or a second stack needs its own lifecycle.
- Entra External ID is not managed here because it is a separate tenant.
