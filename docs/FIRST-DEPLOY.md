# NODO AI - first deployment to Azure

Status: written before any deployment, from the design and from everything that could be checked without an Azure account (AZURE-ARCHITECTURE.md section 14). The commands are the standard Azure CLI and Terraform ones; a flag that your version spells differently is a correction to this page, not a reason to skip the step.

The idea is simple. Each phase ends in a **gate**: a command whose output tells you the phase worked. You go on only when the gate is green. When a gate is red, the table at the end of the page says what is most likely wrong and which switch or fix applies. Nothing in this page needs you to hand credentials to anyone else: you run it with your own `az login`.

| Phase | Result | Costs money |
|---|---|---|
| 0 | the subscription is ready, the identity provider exists | no |
| 1 | Terraform state exists | cents |
| 2 | the **basic** tier runs, the first connection is proved | little (see the calculator) |
| 3 | the **professional** tier runs on a throwaway subscription | yes, a lot per day: destroy it when done |
| 4 | the recovery procedures were rehearsed | the cost of phase 3 for a few hours |
| 5 | go-live checklist | - |

Go through phase 2 first even if the goal is the professional tier: it proves the application against real Azure services with far fewer moving parts, and every problem it finds is one the professional tier would also have.

## Phase 0 - prepare the subscription and the identity provider

1. **Tools:** Azure CLI 2.60 or newer, Terraform 1.9 or newer, the GitHub CLI. `az login`, then `az account set --subscription <id>`.
2. **Region and models.** The default region is `swedencentral`. Check that the models you want exist there and see the quota (tokens per minute) you can have:
   ```bash
   az cognitiveservices model list --location swedencentral --query "[?model.format=='OpenAI'].{name:model.name,version:model.version,sku:model.skus[].name}" -o table
   az cognitiveservices usage list --location swedencentral -o table
   ```
   Pick a chat model that supports **structured outputs** (the service calls it with a strict JSON schema) and an embedding model. Put them in `openai_deployments` (see `envs/prod/terraform.tfvars.example`). If a quota is too small, request more now: it is the slowest thing in the whole process (CAPACITY-MODEL.md section 4).
3. **Resource providers.** Register once per subscription (a few minutes):
   ```bash
   for p in Microsoft.App Microsoft.ApiManagement Microsoft.Authorization Microsoft.Cdn Microsoft.CognitiveServices \
            Microsoft.Consumption Microsoft.ContainerRegistry Microsoft.DBforPostgreSQL Microsoft.Insights \
            Microsoft.KeyVault Microsoft.ManagedIdentity Microsoft.Network Microsoft.OperationalInsights \
            Microsoft.PolicyInsights Microsoft.Search Microsoft.Security Microsoft.ServiceBus Microsoft.Storage; do
     az provider register --namespace $p
   done
   az provider list --query "[?registrationState!='Registered'].namespace" -o tsv   # gate: prints nothing
   ```
4. **Permissions.** The person who runs Terraform needs **Owner** on the subscription (or Contributor plus User Access Administrator): the module creates role and policy assignments.
5. **Entra External ID (the users' identity provider).** Created by hand in the portal; Terraform does not own it.
   - Create an *external* tenant, a user flow for sign-up and sign-in, and one app registration for the **API** (its application id is `oidc_audience`) and one per client app (mobile, desktop). The token issuer is `https://<tenant>.ciamlogin.com/<tenant-id>/v2.0` (`oidc_issuer`).
   - The API expects an access token with `aud` = the API application id and a `workspace_id` claim minted by a custom claims provider. The client application ids go into `oidc_allowed_client_ids`. Until the claims provider exists, nothing can sign in; everything else in this page still works with a test token.
   - Gate: you can obtain an access token for the API with the client's PKCE flow and decode it (`aud`, `iss`, `azp`, `workspace_id`).

## Phase 1 - Terraform state

```bash
cd infra/terraform
./bootstrap-state.sh          # creates the state storage account and writes envs/dev/backend.hcl
```
Gate: `az storage account show` of the state account shows `allowSharedKeyAccess: false` and `publicNetworkAccess` as set by the script, and `terraform -chdir=envs/dev init -backend-config=backend.hcl` ends with "Terraform has been successfully initialized".

## Phase 2 - the basic tier (environment `dev`)

1. `cp envs/dev/terraform.tfvars.example envs/dev/terraform.tfvars`; set `subscription_id`, `budget_contact_emails`; leave `api_target_port = 80` for now (the placeholder image).
2. `terraform -chdir=envs/dev plan -out plan.tfplan` and **read it**. Expect: resource group, identities, registry, Key Vault, storage, queue, Postgres, Search, two cognitive accounts, Container Apps environment, one API app and the jobs. Nothing with "Front Door", "api_management", "virtual_network" or "private_endpoint" belongs in this plan.
3. `terraform -chdir=envs/dev apply plan.tfplan`. If the model deployments fail with a conflict, apply again with `-parallelism=1`.
   Gate: `terraform output` prints the registry, the API address and the job names.
4. **Push the real image.** Run the deploy once from your machine or let the workflow do it (`infra/azure/setup-github-oidc.sh` prepares the GitHub side; it prints the repository variables to set). With the image in place, set `api_target_port = 8000` and apply again.
5. **Bootstrap the database and the index:**
   ```bash
   az containerapp job start -n caj-nodo-dev-migrate -g rg-nodo-dev
   az containerapp job execution list -n caj-nodo-dev-migrate -g rg-nodo-dev -o table      # gate: Succeeded
   ```
   The log shows the nine migrations, the roles granted and `search index ready`. If it fails on `pgaadauth_create_principal`, see the table at the end.
6. **Preflight, as each identity** (this is the gate that matters most):
   ```bash
   az containerapp job start -n caj-nodo-dev-pf-p-api    -g rg-nodo-dev
   az containerapp job start -n caj-nodo-dev-pf-p-worker -g rg-nodo-dev
   az containerapp job logs show -n caj-nodo-dev-pf-p-api -g rg-nodo-dev --container preflight --follow
   ```
   Gate: every line is `[ OK ]` or `[skip]`, and the last line says `0 failed`. Each failed line carries a `try:` hint. The checks, and what each proves, are listed in ADR-023.
7. **Smoke test through the front door** (needs an access token; see phase 0, step 5):
   ```bash
   export NODO_SMOKE_TOKEN=<token>
   python tools/smoke/smoke.py "$(terraform -chdir=infra/terraform/envs/dev output -raw api_url)"
   ```
   With no audio it uploads generated silence and the right result is a **failed** meeting ("no speech"): that proves upload, queue, worker, Speech and the failure path. Then run it again with a real recording of a few minutes (`--audio talk.wav`): the right result is `review`, with a transcript, an answer to a question, a graph and an export. Gate: `0 failed`.
8. Leave it running for a day. The weekly index check, the sweep and the retention job all run on their own schedules; open the Log Analytics workspace and confirm each logged its summary line.

## Phase 3 - the professional tier (environment `prod`, on a throwaway subscription first)

Everything in phase 2 passed? Then:

1. `cp envs/prod/terraform.tfvars.example envs/prod/terraform.tfvars` and fill it in: `oidc_*`, `operator_cidrs` (the address you run Terraform and the deploy from; **a single address as a plain IP**, Key Vault refuses `/32`), `alert_emails`, `budget_contact_emails`, and `waf_mode = "Detection"`.
2. Price it: put the resources of `envs/prod` into the Azure pricing calculator. Set `cost_budget_amount` to a number you accept per month.
3. `terraform -chdir=envs/prod plan -out plan.tfplan`; read it for the things the tests assert: two virtual networks, Front Door, API Management with two regions, a Postgres replica, Service Bus Premium and its geo-pairing, an audit storage account with an immutability policy.
4. `terraform apply`. **API Management takes about an hour.** If the apply stops with an error, read it, fix the cause and apply again: Terraform resumes. Errors that name a *name* (too long, already taken) are fixed by changing `project` or the environment suffix.
5. **Secrets from Key Vault.** If the API app or a job shows `ContainerAppSecretRefFailed`-style errors in the portal (the platform could not read a Key Vault reference while the vault has a firewall), set `secrets_from_key_vault = false` and apply: the same values are handed over inline and nothing else changes. Record which setting worked.
6. **Token budget policy.** If API Management refuses the AI gateway policy (the error names `llm-token-limit`), set `ai_token_limit_enabled = false` and apply. The models are then limited by their own quotas only; the per-caller budget returns when the policy is accepted (for example after importing the Azure OpenAI API definition into the gateway).
7. Run the bootstrap job (phase 2, step 5), then **all four preflights** (`pf-p-api`, `pf-p-worker`, and the two for the standby region, `pf-d-api`, `pf-d-worker`). The professional preflight adds `gateway` (the public front door reaches the API through Front Door and API Management).
8. The custom domain: create the DNS records Front Door asks for (a CNAME and a validation TXT), wait for the certificate, then `terraform apply` again.
9. Smoke test through the public host, silence first, then a real recording.
10. Watch WAF logs in Detection mode for a week. Add exclusions for any rule that matches legitimate traffic, then set `waf_mode = "Prevention"`.

## Phase 4 - rehearse the recoveries

The recovery procedures in RUNBOOKS.md are written, not proved. Rehearse them on the throwaway environment, with a stopwatch, and write the result in the table at the end of RUNBOOKS.md:

1. **Restore a point in time** into a scratch server (RUNBOOKS.md section 5) and open it with the maintenance identity.
2. **Index loss:** delete the search index, run the weekly check by hand (read-only, expect failures), run the reindex job, run the check again (expect clean). Section 5b.
3. **Regional failover:** `dr_active = true`, queue alias failover, forced promotion of the replica, standby reindex, smoke test. Section 2. Afterwards rebuild: this is also the rehearsal of failback.
4. **A lost message:** purge the queue while a recording is processing; within an hour the sweep restarts it and the meeting reaches review.

## Phase 5 - before real users

The compliance table in COMPLIANCE.md section 4 is the checklist: the data protection assessment, the processor agreements, the retention periods, the participant notices in each language, the incident owner, an independent penetration test. Decide the audit retention period and only then set `audit_policy_locked = true`: it can not be undone.

## When a gate is red

| What you see | Most likely cause | What to do |
|---|---|---|
| `terraform apply`: a name "already exists" or "is invalid" | names for Key Vault, storage, registry and Search are global or length-limited | change `project` (short, lower case) or retry (the random suffix changes only when the state is recreated) |
| apply: `AuthorizationFailed` on a role or policy assignment | the person running Terraform is not Owner | grant Owner (or User Access Administrator) on the subscription |
| apply: a provider "is not registered" | phase 0, step 3 was skipped | register it, apply again |
| apply: `SkuNotAvailable`, `LocationNotAvailableForResourceType`, zone errors | the region lacks a SKU or availability zones | choose another region with zones for `location` and `dr_location`; check the models there |
| apply: OpenAI deployment fails: `InsufficientQuota` or `409` | quota, or parallel deployments on one account | request quota; `-parallelism=1` |
| apply: the API Management policy is rejected | a policy element the service does not accept | read the error: if it names `llm-token-limit` use `ai_token_limit_enabled = false`; otherwise correct `policies/*.tftpl` (the tests render them but only the service validates semantics) |
| bootstrap: `pgaadauth_create_principal` fails | the job's identity is not the database's Entra administrator yet, or the identity name has characters the function rejects | wait a minute and rerun; check `azurerm_postgresql_flexible_server_active_directory_administrator.migrate`; keep identity names to letters, digits and hyphens |
| preflight `identity` fails | the identity is not attached to the app or job | `az containerapp show ... --query identity` |
| preflight `postgres_connect` fails | private DNS, firewall, or the role is missing (the bootstrap job did not run) | professional tier: check the Postgres private DNS zone link; basic tier: check the firewall rule |
| preflight `postgres_isolation` fails | a table lacks forced row level security | a migration stopped half way: rerun the bootstrap job and read its log |
| preflight `blob_roundtrip` fails | missing *Storage Blob Data Contributor*; shared key still on; private endpoint DNS | see the hint printed with the failure |
| preflight `servicebus_send` / `receive` fail | missing sender/receiver role; with the geo-pairing, the alias name | the app must use the alias; roles exist on both namespaces |
| preflight `search_index` fails | the index does not exist or has other dimensions | rerun bootstrap; the embedding dimensions setting must equal the model's |
| preflight `embedding` fails | wrong deployment name, no quota, missing role, or the AI gateway policy | call the account directly once with `az rest` to separate model problems from gateway problems |
| preflight `model_answer` / `model_extraction` fail | the deployed model or API version does not support structured outputs | change the model or `openai_api_version` |
| preflight `speech` fails with 401/403 | missing *Cognitive Services Speech User* on the account | role assignment `worker_speech_*` |
| smoke `process` ends `failed` with real speech | Speech error, or no speech detected | read the worker execution log: the line says `speech_gave_up reason=...` |
| smoke `process` times out | the worker never ran: KEDA rule, queue permissions, or the secret reference problem above | `az containerapp job execution list` for the worker job |
| API app: replicas never become ready | `/readyz` fails: the database | run preflight `postgres_connect` as the API identity |
| Front Door returns 403 | the `X-Azure-FDID` named value does not match the profile, or the WAF is blocking | check the `front-door-id` named value against `terraform output front_door_id`; WAF logs |
| Front Door returns 502/504 | the gateway cannot reach the API (network rule, private DNS for the environment) | test `/readyz` from inside the network with a debug container; check the NSG |

## Undo

`terraform destroy` removes a throwaway environment, with these exceptions that you remove by hand: the Terraform state account; Key Vaults and Cognitive Services accounts, which stay soft-deleted (`az keyvault purge`, `az cognitiveservices account purge`); and anything under a `CanNotDelete` lock (`lock_resources`) until you remove the lock. The subscription-wide Defender plans are not removed by destroy of a single environment.
