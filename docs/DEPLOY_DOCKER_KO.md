# Docker 배포 가이드

명령은 저장소 루트에서 실행합니다. 배포 전에 Docker Engine/Desktop, Compose v2와 OpenSSL을 준비합니다. 이미 사용 중인 기본 데모 컨테이너를 이 문서 검증 과정에서 재시작할 필요는 없습니다.

## 1. 배포 방식 선택

| 방식 | 구성 | 사용 목적 |
| --- | --- | --- |
| 루트 `compose.yaml` | Workbench + 전용 Source/Target Redis | 기능 확인·데모 |
| `deploy/docker/compose.yaml` | 토큰 인증이 적용된 Workbench만 | 실제 Redis 연결 |

두 Compose는 서로 다른 프로젝트 이름과 데이터 볼륨을 사용합니다. 다만 기본 웹 포트는 둘 다 8088이므로 동시에 띄우려면 운영 `.env`의 `WORKBENCH_PORT`를 8089 등으로 바꾸세요. 데모 자료가 운영 볼륨으로 자동 복사되지 않습니다.

## 2. 로컬 데모

```sh
docker compose up -d --build
docker compose ps
```

브라우저: <http://127.0.0.1:8088>. Source/Target Redis는 호스트의 6401/6402에만 바인딩됩니다. Workbench 연결에는 Docker 내부 주소를 사용합니다.

추가 데이터 생성이 필요한 경우에만 별도 오버레이를 실행합니다. 이 명령은 소스에 실제 테스트 키를 씁니다.

```sh
docker compose -f compose.yaml -f compose.load.yaml up -d ttl-generator
```

생성기는 5초 동안 랜덤 키 10,000개를 생성하고 60초 쉬는 과정을 반복합니다. TTL은 60초이고 별도 접두사·NX를 사용합니다. 멈추려면 같은 파일 조합으로 `stop ttl-generator`를 실행합니다. 전체 Redis를 비우지 마세요.

## 3. 운영용 초기화

```sh
./scripts/deploy/docker.sh init
```

`deploy/docker/.env`를 새로 만들고 64자리 랜덤 토큰과 권한 0600을 설정합니다. 기존 파일은 덮어쓰지 않습니다. 파일을 편집해 바인딩·포트·동시 실행 수·이미지 태그를 조절합니다.

```dotenv
WORKBENCH_BIND=127.0.0.1
WORKBENCH_PORT=8088
WORKBENCH_TOKEN=<생성된 값을 유지>
MAX_CONCURRENT_RUNS=1
RIOTX_IMAGE=riotx-workbench:prod
```

`WORKBENCH_TOKEN`은 최소 32자 이상으로 설정합니다. 이 파일과 Docker의 `inspect`/`compose config` 출력에는 비밀값이 들어갈 수 있으므로 공유하지 마세요. 이 예시의 `<...>`를 실제 설정으로 쓰면 안 됩니다.

```sh
./scripts/deploy/docker.sh up
./scripts/deploy/docker.sh status
curl -fsS http://127.0.0.1:8088/api/health
```

첫 빌드에는 인터넷 접속이 필요합니다. 공식 RIOTX ZIP을 체크섬으로 검증하고 Python/UI 의존성을 설치합니다. 기본 빌드는 실행 호스트의 플랫폼을 사용합니다. ECS용 교차 빌드는 [ECS 가이드](DEPLOY_ECS_KO.md)를 사용하세요.

웹 접근 토큰을 입력한 뒤 실제 Redis 프로필과 작업을 생성합니다. 운영 배포는 데모 연결·데모 잡·Redis 서버를 자동 생성하지 않습니다. 사용 방법은 [사용 가이드](USER_GUIDE_KO.md)에 있습니다.

## 4. 외부 웹 접근

기본값은 localhost입니다. 서버 외부에 제공하려면 인증된 HTTPS 리버스 프록시를 앞에 두고, 프록시가 8088에 접근하도록 네트워크를 구성합니다. 직접 HTTP 포트를 인터넷 전체에 공개하지 마세요.

- 같은 호스트의 프록시: localhost:8088로 전달합니다.
- 별도 컨테이너 프록시: 전용 Docker 네트워크 또는 허용된 호스트 주소로 전달합니다.
- Docker 호스트 방화벽/클라우드 보안 그룹에서 접속자를 제한합니다.
- 원본 Host와 Origin을 정상 전달해야 API의 교차 사이트 변경 요청 검사가 통과합니다.
- Redis URL과 비밀번호는 UI에서 등록하고 배포 파일에 하드코딩하지 않습니다.

TLS 프록시의 구체적인 인증서·DNS 설정은 사용하는 프록시 환경에 맞춰 구성하세요. AWS에서는 제공한 HTTPS ALB 구성을 사용할 수 있습니다.

## 5. 로그와 업데이트

```sh
./scripts/deploy/docker.sh logs
```

Workbench UI의 로그는 실행별 RIOTX 로그이며, 위 명령은 컨테이너 운영 로그입니다. 업데이트 전에 실행 중인 잡을 확인하고 정상 종료한 뒤 필요한 경우 결과 비교를 수행합니다.

```sh
git pull --ff-only
./scripts/deploy/docker.sh up
```

`up`은 이미지를 재빌드하고 필요하면 컨테이너를 교체합니다. 현재 실행은 이어지지 않으며 UNKNOWN으로 남을 수 있습니다. 빌드 실패 시 기존 볼륨을 삭제하거나 Redis를 초기화하지 마세요. 이전 Git 커밋/이미지로 돌아가 같은 데이터 볼륨을 연결해 복구하되, 향후 스키마 변경이 있는 버전은 호환성을 확인합니다.

RIOTX 바이너리 업데이트는 `scripts/update-riotx.py`와 기술 설명을 참고합니다. 포함 버전과 최소 지원 버전을 구분해 검증해야 합니다.

## 6. 중지와 데이터 백업

```sh
./scripts/deploy/docker.sh stop
```

컨테이너·네트워크만 내려가고 이름 있는 데이터 볼륨은 남습니다. **`down -v`는 사용하지 마세요.** 다시 `up`하면 같은 프로젝트의 볼륨을 재사용합니다.

운영 Compose의 기본 데이터 볼륨 이름은 `riotx-workbench-prod_app-data`입니다. Compose 프로젝트 이름을 바꾼 경우 실제 이름을 `docker volume ls`로 확인합니다.

일관된 백업은 잡을 종료하고 컨테이너를 중지한 뒤 수행합니다. 토큰은 `.env`/비밀번호 저장소에도 따로 보관합니다.

```sh
mkdir -p backup
chmod 700 backup
# scripts/deploy/docker.sh stop 실행 후, 로컬 백업 디렉터리에 기록합니다.
docker run --rm --user 0 --entrypoint sh \
  -v riotx-workbench-prod_app-data:/data:ro \
  -v "$PWD/backup:/backup" riotx-workbench:prod \
  -c 'umask 077; tar -czf /backup/workbench-data.tgz -C /data .'
```

백업에는 Redis 프로필 복호화에 필요한 `secret.key`, SQLite와 WAL 파일, 업로드·버전 사본이 포함됩니다. 소스/타깃 Redis 데이터 자체는 포함하지 않습니다. 백업 파일을 암호화된 별도 저장소로 이동하세요. 기존 데이터를 덮어쓰기보다 새로운 볼륨에 복원하고 동작을 확인하는 방식을 권장합니다.

로컬 실행 데이터는 Git 저장소에 업로드하지 않습니다. `.env`, SQLite, 키, Terraform 상태, 이미지 아카이브는 ignore 규칙으로 제외됩니다.
