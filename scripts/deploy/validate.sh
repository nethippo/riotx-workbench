#!/usr/bin/env bash
# Downloads Terraform providers if needed, but never calls AWS apply or Docker up.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
for tool in terraform docker bash; do command -v "$tool" >/dev/null || { echo "$tool is required." >&2; exit 1; }; done
bash -n "$ROOT/scripts/deploy/docker.sh" "$ROOT/scripts/deploy/ecs.sh" "$ROOT/infra/ecs/user-data.sh.tftpl"
docker compose --env-file "$ROOT/deploy/docker/.env.example" -f "$ROOT/deploy/docker/compose.yaml" config --quiet
terraform fmt -check -recursive "$ROOT/infra"
for stack in ecr ecs; do
  terraform -chdir="$ROOT/infra/$stack" init -backend=false -input=false
  terraform -chdir="$ROOT/infra/$stack" validate
  terraform -chdir="$ROOT/infra/$stack" test
 done
