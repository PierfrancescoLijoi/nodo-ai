# ADR-010: Azure as the cloud target, Terraform as infrastructure as code

Status: accepted (region to confirm)

## Context
The owner wants Azure as the cloud stack consulted by the app, and wants the whole architecture defined as code so it can be reviewed, reproduced and scaled.

## Decision
- The app talks only to the NODO API on Azure Container Apps. All other services are reached by managed identities (no keys, no connection strings in config).
- Terraform (azurerm provider) defines every resource, with one state per environment (dev, staging, prod). The Entra External ID tenant is created by hand because it is a separate tenant.
- GitHub Actions deploy with OIDC federated credentials.
- Default region `swedencentral`, held in one variable, to be confirmed against service and model availability before the first apply.
- The ports and adapters of ADR-006 stay the seam: the in-memory adapters remain for tests and local work, Azure adapters implement the same protocols.

## Consequences
- A change of region, SKU or private networking is a variable change and a plan.
- The first deployment needs an Azure subscription, a signed-in `az` session and the Terraform CLI; none of that exists on the dev machine yet.
- Details: [AZURE-ARCHITECTURE.md](../AZURE-ARCHITECTURE.md).
