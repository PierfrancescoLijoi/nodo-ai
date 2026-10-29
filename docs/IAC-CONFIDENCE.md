# IaC confidence: from the repository to the first apply

The aim is that `terraform apply` on a real account fails for reasons we could not have known, not for
reasons a check could have found. Each rung removes a class of failure; a rung starts when the one
below is green. Nothing here applies anything.

```sh
python tools/iac/verify.py          # rungs 1-4 in Docker
python tools/iac/verify.py --scan   # + trivy config (HIGH and CRITICAL fail)
```

| Rung | What it proves | Where | State |
|---|---|---|---|
| 1 Pin | Terraform and providers have version constraints, lock files are committed | `verify.py` | passes |
| 2 Format | `terraform fmt -check -recursive` | `verify.py` | passes |
| 3 Validate | `init -backend=false` and `validate` in `envs/dev`, `envs/prod` and the module | `verify.py` | passes |
| 4 Plan-time tests | `terraform test`: both tiers, each feature flag on and off, rendered policies, scale floors, private-only flags | `modules/nodo/tests/` | 22 runs pass |
| 5 Security scan | misconfigurations in the plan inputs | `verify.py --scan` | **2 findings, below** |
| 6 Preflight | the account is ready (registrations, quotas, roles, model availability) and the deployment works (tokens, database roles, queue, index, signed link) | the `preflight` job and `docs/FIRST-DEPLOY.md` | written, needs an account |
| 7 Staged apply | sandbox first, second `plan` must be empty, then promote the same images by digest | `docs/FIRST-DEPLOY.md` | needs an account |
| 8 Keep it true | scheduled drift `plan`, policy as code, budgets in the IaC | `security.tf`, `monitoring.tf` | partly in code |

Rungs 6 to 8 are exactly what `docs/AZURE-ARCHITECTURE.md` section 14 lists as still to prove on a
real subscription; each has a switch or a check in the code.

## Open findings of the scan

| Id | Where | Reading |
|---|---|---|
| AZU-0012 | storage accounts in `modules/nodo/main.tf` (audio, audit) | no explicit `network_rules` default `Deny`. The audit account already has `public_network_access_enabled = false`; the audio account must stay reachable by short-lived signed links from the browser, so it needs a decision and a written reason, not a blind `Deny` |
| AZU-0013 | `azurerm_key_vault.main` | no `network_acls` block. Adding `default_action = "Deny"` interacts with Container Apps reading Key Vault references (`secrets_from_key_vault`), which is on the list of things to prove on a real subscription |

Decide each, then either fix it or write the reason next to the resource and suppress the rule with a Trivy inline ignore.

## When you change infrastructure

1. Edit; the editor hook reviews the file as you save it (secrets, TLS, public access, tags, deletion guards).
2. `python tools/iac/verify.py` before the commit. Add a `terraform test` run for every new flag or tier
   branch: "flag off changes nothing" and "flag on creates what is expected".
3. New input a human can get wrong gets a `validation {}`. New cross-resource rule gets a `precondition`.
4. A provider behaviour you could not run goes into section 14 of `AZURE-ARCHITECTURE.md` with a switch.
