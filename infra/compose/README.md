# Local POC stack

This stack runs PostgreSQL with pgvector, Neo4j Community, and MinIO. Every published port is bound to `127.0.0.1`. The `minio-init` service creates `nodo-audio` and applies a 30-day expiration rule.

Copy the example environment file before starting the stack. Its values are fake and intended only for local development.

```powershell
Copy-Item .env.example .env
docker compose config
docker compose up -d
docker compose ps
docker compose down
```

Validate the Compose model without pulling images:

```powershell
docker compose -f docker-compose.yml --env-file .env.example config -q
```

To remove the local data as well, run `docker compose down --volumes`. This permanently deletes the named development volumes.

