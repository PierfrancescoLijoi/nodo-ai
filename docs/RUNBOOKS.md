# NODO AI - runbooks

Status: written before the first deployment, from the design. Every command below is untested against a live subscription; the first rehearsal on a throwaway environment corrects them, and the date and duration of each rehearsal go in section 9. Names such as `rg-nodo-prod` follow the Terraform naming (`rg-<project>-<environment>`).

Severity: **SEV1** users cannot use the service or data is at risk (page now); **SEV2** degraded, a promise is being missed (within the hour); **SEV3** no user impact yet (working hours).

## 0. First five minutes of any incident

1. Open the workbook "NODO AI overview": requests, errors, latency, error budget.
2. Is it the edge (WAF blocks, Front Door origin health), the gateway (capacity, backend breakers) or the app? Take a failing request id from the client or the gateway log and search it in Log Analytics: one id joins the gateway, the API and the worker.
3. Decide severity and write down the time. Everything after is a timeline.
4. Roll back before you debug if a deploy happened in the last hour (section 4).

## 1. Alert: availability test failing / fast burn (SEV1)

- Check Front Door origin health. Both origins red: the problem is upstream of regions (certificate, WAF rule, DNS). Primary red, standby green: Front Door is already routing to the standby; go to section 2.
- Check the last deploy and the last Terraform apply in the activity log. Roll back (section 4) if either is recent.
- `az containerapp revision list -n ca-nodo-prod-api-primary -g rg-nodo-prod -o table` shows replica health. `/readyz` failing means the database; see section 5.

## 2. Regional failover (SEV1, human decision)

When: the primary region is down or unusable, the alert evidence shows it is not a deploy or a configuration problem, and the expected outage is longer than the recovery time. A brief blip is not a reason: promotion cannot be undone.

1. Confirm: `OriginHealthPercentage` low for the primary origin, the availability test failing, the platform status page for the region. Write the decision and who made it.
2. Make the standby serve: in `envs/prod`, set `dr_active = true` and apply with the state of the surviving resources only:
   `terraform apply -var dr_active=true -target=module.nodo.azurerm_container_app.api -target=module.nodo.azurerm_container_app_job.worker`
   (the primary region's resources cannot be refreshed while it is down; use `-refresh=false` if the plan hangs on them). This scales the standby API to three replicas and creates the standby worker.
3. Fail over the queue alias: `az servicebus georecovery-alias fail-over --alias sbdr-<suffix> -g rg-nodo-prod-dr --namespace-name sb-<suffix>-dr`. The alias name in the application does not change.
4. Promote the database (**irreversible**). With the primary region down, promote from the CLI, because only a forced promotion works without the source: `az postgres flexible-server replica promote --resource-group rg-nodo-prod-dr --name psql-<suffix>-dr --promote-mode standalone --promote-option forced`. Wait for `Ready`. Then reconcile Terraform with `dr_promote_database = true` (this also gives the promoted server its Entra administrator); high availability and the maintenance window are added to it by a later apply once the primary is gone from the configuration. Writes work from the CLI step on.
5. Rebuild search in the standby region: run the migrate job (`python -m nodo_api.admin bootstrap`) to create the index, then re-index every transcript from PostgreSQL. **The re-index command does not exist yet** (AZURE-ARCHITECTURE.md section 15, item 3): it needs a cross-workspace read role like the retention one. Until it is built and rehearsed, this step is the weakest in the runbook, and until it completes questions return `503` while lists and details work.
6. Storage: if the audio account's primary region is also unavailable, initiate the account failover (`az storage account failover`). It is also irreversible in the sense that the account becomes locally redundant until re-geo-enabled.
7. Smoke test through the public door: sign in, list meetings, ask a question, upload a short recording, see it reach `review`.
8. Tell users (status page, in-app message). Record the recovery point (the last replicated transaction time) and the recovery time.

The queue pairing is also one-way: after the alias fails over, break the pairing and pair again from the new primary before relying on it, and expect Terraform to show drift on the pairing until then. Messages in flight at the moment of failover are not replicated (Geo-DR copies metadata only; the managed alternative that copies messages is Service Bus Geo-Replication, to be considered if losing them is not acceptable).

Failback is a new project, not a button: rebuild the old primary as a replica of the new one, wait for lag to reach zero, switch back in a quiet window, and rehearse it before you need it.

## 3. Alert: Front Door sees fewer than half of its origins healthy (SEV1)

The origin group probes `/readyz` through the gateway. One of the two regions is failing readiness: follow section 1, then 2 if it is the primary. If both fail, check the gateway's network security group and the `front-door-id` named value (a changed profile means a changed id).

## 4. Bad deploy: roll back

`az containerapp revision activate -n ca-nodo-prod-api-primary -g rg-nodo-prod --revision <previous>` then `az containerapp ingress traffic set ... --revision-weight <previous>=100`. The database migrations are forward-only and additive by rule (no destructive change in the same release as the code that stops needing it), so the previous revision runs against the new schema.

## 5. PostgreSQL

- **Readiness failing, connections exhausted:** `az postgres flexible-server show` for state; look at `active_connections`. The API pools ten connections per replica; a surge of replicas is the usual cause. Lower `api_max_replicas` or raise the SKU.
- **CPU or storage alert:** move the SKU up one step or raise storage (online). Look at the slow-statement log (statements over one second are logged).
- **Replica lag alert (SEV2):** the recovery point objective is at risk. Check for a long transaction or a bulk write on the primary; if it persists, the standby needs attention before the next incident.
- **Restore a point in time** (data damage, not a region failure): `az postgres flexible-server restore --source-server psql-<suffix> --restore-time <UTC> --name psql-<suffix>-restore` into a new server in the same network; verify; copy the rows needed back, or re-point the app after review. Never restore over the live server.

## 6. Queue: dead letters or backlog (SEV2/SEV3)

- **Dead letters:** a recording failed five times. Read the message's dead-letter reason and the worker logs for its trace id. If it is a bad file, the meeting is already marked failed and the owner sees it. If it is a defect, fix, then resubmit from the dead-letter queue (`peek` first; never purge without reading).
- **Backlog:** workers are not keeping up. Look at Speech throttling and model 429s at the AI gateway first; raise `worker_max_executions` only if those are clean.

## 7. Compliance events

- **Retention failure (SEV1, page):** the job logs `retention erased=N failed=M`; M above zero means audio older than its policy still exists. The log names no content; find the meeting ids in the audit events of the failed run, check why deletion failed (a lock on the blob? a permission change?), erase by hand with the retention identity's role, rerun the job, write down the exposure window.
- **Data export request taking too long:** the job is on the same queue; check backlog (section 6). The download link expires after 24 hours; the person can request again.
- **Suspected breach:** 72 hours to notify the authority run from when you become aware. Preserve: activity log, WAF and gateway logs, Key Vault logs, database audit events; export them to the immutable account. Rotate: the attestation key, the telemetry salt, the break-glass password. Revoke: the affected identity's role assignments. Then the decision on notification belongs to the data protection officer, not to the on-call.

## 8. Routine

- **Rotate secrets** (every 90 days or on suspicion): `terraform apply -replace=random_password.attestation_key` (the attestation key; old attestations stay verifiable only if the old key is kept in Key Vault under a version, which the application does not read: plan the cutover), `-replace=random_password.telemetry_salt`, `-replace=random_password.postgres_admin`. The storage encryption key rotates itself.
- **Move the WAF to Prevention:** read a week of Detection logs for rules that would have blocked legitimate traffic, add exclusions, set `waf_mode = "Prevention"`, apply.
- **Add capacity:** follow the signal table in CAPACITY-MODEL.md section 5; change the number in `envs/prod`, read the plan, apply.
- **Lock the audit policy:** only after compliance confirms the period; `audit_policy_locked = true` is irreversible.

## 9. Rehearsal log

| Date | What | Duration | What went wrong | What changed |
|---|---|---|---|---|
| | first apply to a throwaway subscription | | | |
| | restore of a point in time into a scratch server | | | |
| | regional failover of staging, with promotion | | | |
