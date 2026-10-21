# 배포 구성 검증 기록

검증일: 2026-10-21 (Asia/Seoul)

| 항목 | 결과 |
| --- | --- |
| Terraform | 1.13.5, AWS provider 6.69.1; 공식 체크섬/서명 확인 |
| Terraform fmt/validate | ECR·ECS 스택 통과 |
| Terraform mock plan | ECR 1개 + ECS 8개 통과, AWS 리소스 생성 없음 |
| Compose 설정·Bash 문법 | 운영 Compose config와 스크립트 문법 통과 |
| Python 단위 테스트 | 기존 기능 32개 + 배포 스크립트 7개 통과 |
| UI 테스트 | 18개 통과 |
| 내부 문서 링크 | README와 한국어 가이드 경로 확인 |

Mock plan은 단일 writer/stop-first, 암호화된 로컬 저장소, 내부 ALB 기본값, HTTPS, Secret ARN 주입, 명시적인 Redis SG 연결을 확인합니다. 공개 전체 CIDR·mutable image·아키텍처 불일치·메모리 부족·비 NVMe 노드의 거부와 ARM64 구성을 검사합니다.

배포 스크립트 단위 테스트는 도구를 stub으로 대체합니다. 기존 토큰 파일 보존과 권한 0600, 볼륨을 삭제하지 않는 stop, 짧은 토큰 거부, saved plan 필수, 검증 실패 시 오래된 계획 제거, 이미지 push 실패 시 기존 digest 보존을 확인합니다.

**실제 AWS apply, ECR push, ECS 서비스 기동, 실제 계정의 IAM/네트워크/인증서 검증은 수행하지 않았습니다.** 위 결과는 해당 계정에서의 배포 성공이나 무중단을 보장하지 않습니다. 실제 환경은 DEPLOY_ECS_KO.md의 사전 조건부터 검토하세요. 기존 로컬 Workbench·Redis·데이터 생성기는 이 작업에서 재시작하지 않았습니다.
