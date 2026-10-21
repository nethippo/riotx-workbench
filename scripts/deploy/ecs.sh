#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
ECR="$ROOT/infra/ecr"
ECS="$ROOT/infra/ecs"
command -v terraform >/dev/null || { echo 'Terraform >= 1.10 is required.' >&2; exit 1; }
tf() { terraform -chdir="$1" "${@:2}"; }
case "${1:-help}" in
  ecr-plan|plan)
    dir="$ECS"; [[ "$1" != ecr-plan ]] || dir="$ECR"
    # Invalidate any older plan before validation, so failures cannot leave a stale apply target.
    rm -f -- "$dir/deployment.tfplan"
    [[ -f "$dir/terraform.tfvars" ]] || { echo "Copy $dir/terraform.tfvars.example first." >&2; exit 1; }
    tf "$dir" init -input=false
    tf "$dir" validate
    shift
    if ! tf "$dir" plan "$@" -out=deployment.tfplan; then
      rm -f -- "$dir/deployment.tfplan"
      exit 1
    fi
    ;;
  ecr-apply|apply)
    dir="$ECS"; [[ "$1" != ecr-apply ]] || dir="$ECR"
    [[ -f "$dir/deployment.tfplan" ]] || { echo 'Create and review a saved plan first.' >&2; exit 1; }
    tf "$dir" apply deployment.tfplan
    ;;
  image)
    for tool in aws docker python3; do command -v "$tool" >/dev/null || { echo "$tool is required." >&2; exit 1; }; done
    repository=$(tf "$ECR" output -raw repository_url)
    region=$(tf "$ECR" output -raw region)
    registry=${repository%%/*}
    platform=${PLATFORM:-linux/amd64}
    case "$platform" in
      linux/amd64) architecture=X86_64 ;;
      linux/arm64) architecture=ARM64 ;;
      *) echo 'PLATFORM must be linux/amd64 or linux/arm64.' >&2; exit 1 ;;
    esac
    if [[ -n "${IMAGE_TAG:-}" ]]; then
      tag=$IMAGE_TAG
    else
      [[ -z "$(git -C "$ROOT" status --porcelain)" ]] || { echo 'Commit changes or explicitly set a unique IMAGE_TAG.' >&2; exit 1; }
      tag=$(git -C "$ROOT" rev-parse --short=12 HEAD)
    fi
    [[ "$tag" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]] || { echo 'Invalid image tag.' >&2; exit 1; }
    aws ecr get-login-password --region "$region" | docker login --username AWS --password-stdin "$registry"
    docker buildx build --platform "$platform" --tag "$repository:$tag" --push "$ROOT"
    digest=$(aws ecr describe-images --region "$region" --repository-name "${repository#*/}" --image-ids "imageTag=$tag" --query 'imageDetails[0].imageDigest' --output text)
    [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo 'Invalid ECR digest.' >&2; exit 1; }
    umask 077
    python3 - "$ECS/image.auto.tfvars.json" "$repository@$digest" "$architecture" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({'container_image':sys.argv[2], 'architecture':sys.argv[3]},indent=2)+'\n')
PY
    echo 'Created infra/ecs/image.auto.tfvars.json. Review instance_type, then run plan.'
    ;;
  status)
    region=$(tf "$ECS" output -raw region)
    aws ecs describe-services --region "$region" --cluster "$(tf "$ECS" output -raw cluster_name)" --services "$(tf "$ECS" output -raw service_name)" --query 'services[].{name:serviceName,desired:desiredCount,running:runningCount,pending:pendingCount,events:events[:5]}'
    ;;
  *) echo 'Usage: scripts/deploy/ecs.sh {ecr-plan|ecr-apply|image|plan|apply|status}' ;;
esac
