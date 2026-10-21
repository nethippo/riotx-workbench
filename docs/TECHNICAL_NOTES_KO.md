# RIOTX Workbench

업무 탐색·중앙 작업 화면·접이식 우측 패널을 갖춘 한국어 데이터 이전 워크벤치. 웹 UI·FastAPI·공식 RIOTX 1.15.1을 하나의 Docker 이미지에 포함합니다.

## 실행

```sh
docker compose up -d --build
```

기본 웹 주소: http://127.0.0.1:8088 .
기본 바인딩은 127.0.0.1입니다. 전용 데모 Redis는 아래 localhost 포트로 접근할 수 있습니다.

| 연결 | 호스트 주소 | 컨테이너 내부 주소 |
| --- | --- | --- |
| Source | `redis://127.0.0.1:6401/0` | `redis://source:6379/0` |
| Target | `redis://127.0.0.1:6402/0` | `redis://target:6379/0` |

```sh
redis-cli -h 127.0.0.1 -p 6401
redis-cli -h 127.0.0.1 -p 6402
```

Workbench는 Docker 내부 주소를 계속 사용하며, 키 수와 활동 관측도 같은 Redis에 연결됩니다.

소스 저장소에는 Docker 이미지 아카이브가 포함되지 않습니다. 위 명령으로 현재 플랫폼에 맞게 빌드합니다.
별도로 제공받은 `riotx-workbench-docker-arm64.tar.gz`는 Linux ARM64 이미지입니다.
다른 Docker 호스트에서 `docker load -i riotx-workbench-docker-arm64.tar.gz`로 불러오고,
이 소스 폴더에서 `docker compose up -d --no-build`로 실행할 수 있습니다.
AMD64 서버에서는 제공한 Dockerfile로 다시 빌드하세요.

- 이미지: `riotx-workbench:1.0.0`
- 포함/최소 지원 RIOTX: **1.15.1** (실제 검증한 기준)
- 공식 배포 원본: https://github.com/redis/riotx-dist
- ZIP SHA-256: `109b7fc9cf9de3431f6559c3a35444feba3f8d781b3bb5038a3fa96f0b91e973`
- Python 3.12 / Eclipse Temurin JRE 21 / SQLite WAL / React 19 / Vite 6
- 의존성: `requirements.txt`, `ui/package-lock.json`, 소개 메뉴

## 사용

1. `실시간 복제`를 선택하고 실행합니다. 연필 버튼으로 옵션을 편집합니다.
2. `데모 활동 생성`은 이 프로젝트 전용 source Redis의 `workbench:demo:*` 키만 기록합니다.
3. 상단 소스·타깃 키 수와 실시간 활동을 확인합니다.
4. 설정에서 알림 탐지·활동 구독·서버 알림 설정을, 로그에서 실행별 로그를 확인합니다.
5. 소개에서 포함·최소 지원 버전, 의존성, 공식 최신 버전을 확인합니다.
6. 연결 관리에서 Redis 프로필을 추가하고 여러 업무/잡을 생성합니다.

## 구현 범위

업무별 잡, 설정 revision, 실행 당시 설정 사본, 이력과 로그를 보관합니다.
최대 3개 RIOTX 프로세스를 동시에 실행하고 나머지는 대기합니다 (활성·대기 한도 50).
동일 타깃 DB 동시 쓰기는 보수적으로 차단합니다. 같은 URL의 다른 프로필도 검사합니다.
취소는 TERM 후 8초 뒤 KILL을 사용하며 이미 기록된 데이터는 남을 수 있습니다.
컨테이너 재시작 시 이전 활성 실행은 UNKNOWN으로 표시하고 자동 재개하지 않습니다.
프로세스 종료와 데이터 검증을 구분합니다.

UI는 2초 API 조회로 갱신합니다. 실행별 SSE도 제공하며 API 문서는 `/docs`입니다.
메트릭은 RIOTX의 실행별 내부 `/metrics`를 직접 수집하여 외부 Prometheus 설치 없이 동작합니다.

전용 옵션 폼은 복제의 모드/키 패턴/배치/스레드/struct와 파일 입력을 제공합니다.
나머지 명령은 유형 선택과 실제 CLI 도움말에 근거한 고급 옵션 JSON을 사용합니다.
DB/Stream/Elasticsearch/Snowflake 등은 연결된 외부 시스템이 없어 실제 전송은 미검증입니다.
JDBC 등의 비밀 옵션을 일반 JSON에 저장하는 것은 차단하며 별도 인증 어댑터는 후속 확장 항목입니다.
Memcached는 도움말 탐지만 제공하고 UI 실행 유형에는 노출하지 않습니다.
완전한 시각적 필드 매핑·격리 샘플 변환 미리보기는 이번 버전에 포함하지 않습니다.

## LIVE와 keyspace notifications

- 소스·타깃 키 수는 **DBSIZE**로 각각 2초마다 조회합니다. 알림으로 키 수를 추정하지 않습니다.
- 선택한 전체 DB 기준이며 key-pattern만의 키 수가 아닙니다. 시각과 갱신 지연을 함께 표시합니다.
- RIOTX 1.15.1의 LIVE 초기화가 **KEA**를 요구함을 실제 실행으로 검증했습니다.
- 소스 알림은 LIVE 필수이고 타깃 알림은 타깃 활동 관측용입니다.
- CONFIG GET 권한이 없으면 비활성으로 단정하지 않고 확인 불가로 표시합니다. 이때 LIVE 시작은 차단합니다.
- 활동 구독 토글은 Workbench 관측 연결만 바꿉니다.
- 서버 알림은 `서버 설정 변경 허용` 후 각 연결의 `Redis에 적용`을 눌러야 변경됩니다.
- 켜기는 기존 플래그 보존 + KEA 추가, 끄기는 서버 알림 전체 비활성화입니다.
- DB가 아닌 서버 전체에 영향을 줍니다. 적용 직전 탐지값을 비교하며 LIVE 실행 중 소스 알림은 끌 수 없습니다.
- CONFIG REWRITE는 자동 실행하지 않습니다. 서버 재시작 후 지속성은 해당 운영 방식에 따릅니다.
- Cluster는 primary별 키 수 합산과 구독·설정을 사용합니다. 모든 primary에 직접 접근할 수 있어야 합니다.
  일부 노드 조회 실패 시 마지막 합계를 지연 상태로 남깁니다. 실제 다중 노드 검증은 아직 수행하지 않았습니다.
- Pub/Sub 알림은 유실될 수 있습니다. 소스·타깃 활동은 개별 관측값이며 1:1 인과 관계를 보장하지 않습니다.
- 키 수 일치는 값·자료형·TTL의 일치 보장이 아닙니다. 최종 비교를 별도로 수행하세요.

## 업데이트

소개는 지정된 공식 저장소의 GitHub latest stable release만 시작 시·6시간마다·수동으로 조회합니다.
조회 실패 시 최신 여부 미확정으로 표시하며 작업 데이터를 GitHub에 전송하지 않습니다.

```sh
python3 scripts/update-riotx.py
python3 scripts/update-riotx.py --apply
docker compose up -d --build
```

`--apply`는 공식 안정 릴리스 ZIP을 내려받아 SHA-256을 검증하고 Dockerfile/runtime.json을 갱신합니다.
컨테이너를 자동 재시작하거나 실행 중 바이너리를 교체하지 않습니다.
최소 지원 버전은 검증한 기준을 유지합니다. 업데이트 후 테스트를 다시 실행하세요.
컨테이너 시작 시 실제 RIOTX 버전과 manifest 일치를 확인합니다.

## 데이터와 배포

설정·이력·로그·업로드는 `workbench-data` 볼륨에 저장합니다.
연결 비밀번호는 볼륨의 0600 키로 암호화합니다. 볼륨 백업에는 키 파일도 포함하세요.
키 이름은 기본 마스킹합니다. 이벤트에는 데이터 값을 조회하지 않습니다.
실행당 로그 2,000줄, 메모리 활동 800개를 보관합니다.

SQL·SpEL 입력이 가능한 단일 운영자 도구입니다. 공개 다중 사용자 서비스로 간주하지 않습니다.
외부 서버 배포 시 인증된 리버스 프록시/TLS 뒤에 두고 `WORKBENCH_TOKEN`을 설정하세요.
`WORKBENCH_BIND`/`WORKBENCH_PORT`로 바인딩을 변경할 수 있습니다.
토큰을 설정하면 웹에서 입력해야 API에 접근할 수 있으며 브라우저 sessionStorage에 보관합니다.
이번 배포는 로컬 Docker이며 공인 도메인·외부 서버 배포는 수행하지 않았습니다.

## 검증

데이터를 변경하지 않는 UI 및 Python 단위 테스트:

```sh
(cd ui && npm ci && npm run build && node --test tests/*.test.mjs)
docker compose build workbench
docker run --rm --entrypoint python \
  -v "$PWD/tests:/app/tests:ro" \
  -v "$PWD/scripts:/app/scripts:ro" \
  -v "$PWD/deploy/docker/.env.example:/app/deploy/docker/.env.example:ro" \
  riotx-workbench:1.0.0 -m unittest discover -s tests -p 'test_*.py' -v
```

아래 통합 테스트는 데모 데이터와 알림 설정을 변경하고 기존 데모 실행을 취소합니다. 사용 중인 배포가 아닌 별도의 테스트 환경에서만 실행하세요.

`tests/integration.py`는 compose의 전용 데모 source/target만 대상으로 합니다.

```sh
docker cp tests/integration.py riotx-workbench-workbench-1:/tmp/integration.py
docker compose exec -T workbench python /tmp/integration.py
```

LIVE 값/Hash/TTL, 양쪽 활동/키 수, KEA off/on, 알림 없는 LIVE 차단,
실행 중 소스 알림 해제 차단, 타깃 충돌, 메트릭, 3프로세스/대기열/취소,
비교, JSON 30레코드 내보내기, CSV 업로드/숫자 변환/필터/HSET 출력, 소개와 공식 업데이트 조회를 검증합니다.

## 라이선스

RIOTX Business Source License 원문: `RIOTX-LICENSE.md` 및 이미지의 `/opt/riotx/LICENSE`.
https://github.com/redis/riotx-dist/blob/main/LICENSE.md
JRE 및 의존성 라이선스는 각 배포 패키지에 포함됩니다.


## 복제와 결과 비교 메뉴 (2026-10-20)

결과 비교는 복제의 하위 메뉴입니다. 복제 현황/결과 비교 탭 또는 왼쪽 복제 아래의 결과 비교 항목에서 이동합니다. 비교는 독립 RIOTX 프로세스로 실행하므로 LIVE 복제를 종료하지 않고 실행할 수 있고 자체 실행 이력을 보존합니다.

- 기존 비교 작업은 작업공간·소스·타깃이 일치하는 복제가 하나일 때 상위 ID를 연결합니다. 연결 후 상위의 연결/작업공간이 변경되어도 이 관계를 유지합니다. 모호한 미연결 비교는 독립 항목으로 표시합니다.
- 새 비교는 복제 화면의 결과 비교 탭에서 상위 ID를 참조하도록 만들어집니다. 하위 비교는 실행 시점의 상위 연결·옵션을 참조합니다. 과거 실행의 설정 스냅샷과 이력은 유지합니다.
- FULL은 값·자료형·TTL, QUICK은 자료형·TTL을 비교합니다. 상위 복제 설정에서 비교 방식과 패턴을 고릅니다. 실행 당시 설정과 실제 RIOTX 출력은 선택한 실행 기준입니다.
- LIVE 중 변화하는 데이터는 일시적인 차이가 생길 수 있습니다. 키 개수가 같거나 프로세스가 정상 종료했다는 사실만으로 전체 데이터 일치를 주장하지 않습니다.
- RedisInsight 공식 다크 테마 색상과 컨트롤을 적용했습니다. Graphik의 공개 대안으로 Inter, 코드에는 Source Code Pro를 포함합니다. 글꼴 OFL 고지는 `/licenses/Inter-OFL.txt`, `/licenses/SourceCodePro-OFL.txt`에서 제공합니다.
- 소스·타깃 키 수는 상단에 유지되고, 설정/로그/소개 메뉴는 오른쪽에 유지됩니다.

근거: [RIOTX 비교 문서](https://redis.github.io/riotx/replication/compare.html), [RedisInsight 공식 테마](https://github.com/RedisInsight/RedisInsight/tree/43bd288ed30dc6d2355723158dad5d7ecf4a280e/redisinsight/ui/src/styles/themes/dark_theme).

추가 검증: `cd ui && node --test tests/navigation.test.mjs` (연결 관계 추론/충돌/범위 보존). 기존 통합 테스트는 데모 데이터를 변경하므로 사용 중인 데이터가 있는 환경에서는 그대로 실행하지 마세요.


### 상위 설정 참조

하위 결과 비교의 설정 버튼과 편집 버튼은 상위 복제 설정을 엽니다. API도 자식의 독립 옵션/인자를 사용하지 않고 현재 상위 설정으로 비교 명령을 구성합니다.

- 소스/타깃 프로필, 키 패턴/타입/포함/제외/슬롯, TTL 허용 오차, 변환 및 성능 옵션 중 포함 RIOTX의 compare가 지원하는 옵션을 참조합니다.
- 상위 `compare=QUICK`(기본)은 값 비교 생략, `FULL`은 값 포함, `NONE`은 하위 비교 실행을 차단합니다. LIVE/struct 등 복제 전용 옵션과 metrics/progress 등 관리 옵션은 비교로 전달하지 않습니다.
- 제출 시 실제 적용 옵션과 상위 ID/수정본을 실행 스냅샷에 저장합니다. 대기 중이거나 과거 실행의 설정은 이후 상위 변경으로 바뀌지 않습니다.
- 명시적 상위가 사라지거나 유형이 변경된 경우 오래된 자식 연결로 실행하지 않고 오류로 표시합니다. 상위 없는 독립 비교는 기존 방식으로 동작합니다.
- 단위 검증: `python3 -m unittest discover -s tests -p test_inheritance.py -v` (7개).

실제 API·실행 검증은 `../riotx-inheritance-results.json`, 화면은 `../riotx-inheritance.png`를 참조하세요.

## 작업공간·작업 삭제 및 복원

왼쪽 작업공간 제목과 개별 작업 오른쪽의 `⋯` 옵션 메뉴에서 삭제합니다. 삭제 전 대상 작업 목록을 확인할 수 있습니다. 복제를 삭제하면 연결된 결과 비교도 함께 삭제되며, 개별 비교만 삭제할 수도 있습니다.

- 실행 중/시작 중/대기 중/취소 중인 작업이 삭제 대상에 있으면 전체 삭제를 차단합니다. 먼저 실행을 종료하세요.
- 삭제는 목록에서 숨기는 방식입니다. 상단 **삭제한 항목**에서 같은 작업 ID·설정·실행 이력·로그로 복원합니다. 상위를 별도로 삭제한 경우 상위 복제를 먼저 복원해야 합니다.
- Redis 데이터, 연결 프로필, 실행 이력·로그·결과 파일은 삭제하지 않습니다. 영구 삭제 기능은 제공하지 않습니다.
- 작업공간 안의 모든 작업을 삭제하면 해당 작업공간도 목록에서 사라집니다. 모두 삭제한 상태로 재시작해도 데모 작업이 자동으로 다시 생기지 않습니다.
- 확인 창을 연 뒤 삭제 대상이 추가/변경되면 다시 확인하도록 차단합니다. 삭제 직전 실행 상태를 확인하고 한 SQLite 트랜잭션으로 처리합니다. 실행 제출 중 삭제가 일어나도 삭제된 작업이 새로 대기열에 들어가지 않도록 재검사합니다.

API: `POST /api/deletions/preview`에 `{scope:"job",job_id:"..."}` 또는 `{scope:"workspace",workspace:"..."}`를 전달합니다. `POST /api/deletions`는 같은 본문에 확인한 `expected_ids` 목록을 포함합니다. `POST /api/deletions/{id}/restore`는 삭제 묶음을 복원합니다. 미복원 삭제 내역은 `/api/state`의 `deletions`에서 제공합니다.

단위 검증 (실제 서비스 DB 대신 메모리 SQLite 사용):

```sh
docker compose exec -T workbench python - < tests/test_deletion.py
```

9개 항목: 하위 비교 동반 삭제·복원, 개별 비교 삭제, 작업공간 분리, 실행 상태별 차단, 변경된 삭제 대상 차단, 상위 복원 순서, 데모 재생성 방지, 저장 실패 롤백, 실행 제출/삭제 동시성. 브라우저 검증과 기존 데이터 보존 결과는 `../riotx-deletion-results.json`을 참조하세요.

## TTL 60초 랜덤 데이터 반복 생성기

`compose.load.yaml`은 소스 Redis 전용 생성 서비스를 추가합니다. 기본 동작은 **5초간 10,000건(약 2,000건/초) → 60초 대기 → 반복**입니다. 따라서 생성 시작 간격은 약 65초입니다.

- 각 키는 `workbench:ttl-load:<session>:<cycle>:<index>`로 구분합니다. 값은 무작위 ID·문자열·점수·생성 시각이 담긴 JSON String입니다.
- `SET ... NX EX 60`으로 기존 키 덮어쓰기 방지와 TTL 적용을 한 명령으로 처리합니다. 100건씩 파이프라이닝하며 목표 시각에 맞춰 나누어 전송합니다. 실제 소요 시간은 Redis/시스템 부하에 따라 약간 달라질 수 있으며 로그에 기록합니다.
- 소스 `redis://source:6379/0`(호스트 `127.0.0.1:6401`)에 기록합니다. 타깃 복제 실행 여부는 Workbench에서 관리합니다.
- 별도 상태 볼륨과 컨테이너를 사용합니다. 기존 Redis·웹 서비스를 재시작할 필요가 없습니다. `restart: unless-stopped`로 Docker 재기동 시 계속 실행하며 수동 중지 상태는 유지합니다.
- Redis 오류가 나면 부분 완료를 실패로 표시하고 60초 후 새 주기를 시도합니다. 누락된 배치를 몰아서 보내지 않습니다. SIGTERM/중지 시 기존 키는 TTL에 따라 자연 만료됩니다.

실행/다시 시작 (프로젝트 폴더에서):

```sh
docker compose -f compose.yaml -f compose.load.yaml up -d --no-deps ttl-generator
```

중지 (어느 폴더에서나):

```sh
docker stop riotx-workbench-ttl-generator-1
```

상태/로그:

```sh
docker logs --tail 20 riotx-workbench-ttl-generator-1
docker exec riotx-workbench-ttl-generator-1 cat /data/ttl-load-status.json
```

설정은 overlay의 `LOAD_KEYS`, `LOAD_BURST_SECONDS`, `LOAD_PAUSE_SECONDS`, `LOAD_TTL_SECONDS`, `LOAD_BATCH_SIZE`, `LOAD_PREFIX`로 변경합니다. Docker 이미지는 기존 `riotx-workbench:1.0.0`을 재사용하며 생성 스크립트는 읽기 전용으로 마운트합니다.

실제 실행 검증: 첫 주기 10,000건/5.0026초, 두 번째 주기 10,000건/5.0032초, 주기 사이 대기 약 60초. 다음 주기에서 첫 주기의 키가 만료됨을 확인했습니다. `../riotx-load-first-burst.json`, `../riotx-load-repeat.json` 참조.


## 우측 플로팅 서랍 패널

우측 가장자리의 **모니터·설정·로그·소개** 버튼으로 해당 패널을 펼칩니다. 선택된 버튼을 다시 누르거나 패널의 접기 버튼/`Esc`로 닫습니다.

- 기본은 접힌 상태이며 마지막 열림 상태와 선택 메뉴를 브라우저에 기억합니다.
- 데스크톱에서는 패널을 접으면 중앙 작업 화면과 그래프가 자동으로 넓어지고, 펼치면 패널 너비만큼 공간을 조정합니다.
- 좁은 화면에서는 중앙 화면 위에 서랍이 겹쳐 표시됩니다. 우측 버튼은 계속 접근할 수 있고 가로 넘침을 만들지 않습니다.
- 소스·타깃 키 수는 항상 서랍 위에 남습니다. 페이지를 스크롤해도 고정 키 수 영역 바로 아래로 서랍 위치가 조정됩니다.
- 패널 본문은 독립적으로 스크롤합니다. 본문에 키보드 포커스를 주고 PageUp/PageDown도 사용할 수 있습니다. 메뉴를 바꾸면 내용의 맨 위에서 시작합니다.
- 같은 메뉴를 접었다 펼칠 때는 설정 초안과 로그 검색을 유지합니다. 닫힌 패널은 키보드/접근성 탐색에서 제외되고 닫을 때 해당 우측 버튼으로 포커스를 돌려줍니다.
- 실행 이력이나 비교 로그 바로가기를 누르면 로그 서랍이 자동으로 열립니다.

이번 적용은 실행 중인 백엔드 컨테이너를 유지한 정적 UI 교체로 진행했습니다. Docker 배포 이미지에도 동일 UI를 포함했습니다.

### 파일 가져오기 소스 집계와 자동 재실행

파일 가져오기 상단의 **소스 입력 레코드 수**는 해당 작업의 `file_id`와 파서 옵션을 기준으로 집계합니다. 남아 있는 Redis `source_id`나 다른 LIVE 복제의 DB 키 수를 사용하지 않습니다. CSV/TSV/PSV는 헤더·건너뛸 줄을 반영하고, JSON 배열/객체와 JSONL도 집계합니다. 원본 레코드 수이므로 필터, 처리식, 중복 키 템플릿, `max` 등에 따른 최종 Redis 키 수와 다를 수 있습니다. 지원하지 않는 형식·사용자 지정 파서 옵션은 `—`와 설명을 표시합니다.

- 2초마다 서버 입력 파일의 device/inode, 크기, 나노초 단위 mtime/ctime을 비교합니다. 동일한 파일은 내용 읽기·복사·집계를 반복하지 않으며, 집계 캐시는 SQLite에 보관합니다.
- 메타데이터 변경이 연속 두 번 확인되면 한 번 읽어 버전 사본과 집계를 만듭니다. 복사 전후 메타데이터가 다르면 다음 조회로 미룹니다. 실행은 고정된 버전 사본을 사용하므로 파일 교체가 대기·실행 중 입력을 바꾸지 않습니다.
- **설정 → 입력 파일 → 파일 교체 시 자동 가져오기**가 켜져 있고 해당 작업을 한 번 수동 실행했다면 교체된 파일을 자동 import합니다. 기존 실행이 끝날 때까지 대기하며 같은 버전의 중복 자동 실행을 막습니다. 실패·취소된 버전은 자동 반복하지 않으므로 로그를 확인한 후 수동 재실행하거나 새 파일로 교체하세요.
- 자동 가져오기를 꺼도 메타데이터 확인과 레코드 수 갱신은 계속됩니다. 다시 켜면 대기 중인 변경을 실행합니다. 삭제한 작업은 감시·실행하지 않습니다. 서버 업그레이드 시 기존 파일을 무조건 다시 import하지 않습니다.
- **설정 → 입력 파일 교체**는 기존 파일 ID를 유지하며 `PUT /api/files/{id}`로 원자적으로 파일을 교체합니다. 같은 파일을 참조하는 작업 모두에 적용됩니다. `POST /api/files`는 독립된 새 입력을 만듭니다. 브라우저에서 선택했던 PC 원본은 서버가 직접 감시할 수 없으므로 UI에서 다시 업로드해야 합니다. 서버 볼륨의 `/data/files/{file_id}`를 직접 원자적으로 교체해도 감지됩니다.
- 실행 이력 재현을 위해 `/data/files/versions/`의 버전 사본을 보관합니다. 파일이 자주 교체되는 환경은 데이터 볼륨 용량을 관리하세요. 메타데이터 감지는 바이트 동일성 검사가 아니며, 수정 시각만 변경해도 새 입력으로 처리할 수 있습니다.

검증: `tests/test_source_files.py`의 임시 저장소 테스트와 실제 RIOTX import/파일 교체/중복 방지/토글 검증 결과는 배포 파일 `riotx-file-import-results.json`을 참고하세요.

### 전체 화면 테마

상단 **전체 설정 → Theme**에서 `System match`(기본값) 또는 `Dark`를 선택합니다. System match는 OS의 밝은 모드·다크 모드를 따르며 실행 중 시스템 모드 변경도 즉시 반영합니다. Dark는 시스템 모드와 관계없이 다크 테마를 유지합니다. 선택은 브라우저의 `riotx-theme` 로컬 저장소에 보관되어 모든 작업과 새로고침에 적용됩니다. 다른 브라우저·기기에는 별도로 선택합니다. 테마 변경은 Redis/RIOTX 실행이나 서버 설정을 변경하지 않습니다.

### 실행 이력 서랍

좌측 패널 하단의 **실행 이력** 바를 누르면 선택한 작업의 실행 이력이 위로 펼쳐집니다. 긴 목록은 서랍 내부에서 스크롤하고, 실행을 선택하면 해당 실행의 로그가 우측 서랍에 열립니다. 다시 누르거나 서랍 안에서 `Esc`를 누르면 접힙니다. 기본값은 접힌 상태이며, 열림 상태는 이 브라우저에 저장됩니다. 모바일에서는 화면 하단의 전체 너비 서랍으로 표시합니다.
