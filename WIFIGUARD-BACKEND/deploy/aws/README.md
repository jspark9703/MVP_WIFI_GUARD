# deploy/aws — 1차 클라우드 기동 (EC2 2대 · docker compose 미사용)

결정(2026-09-07, 2차 플랜 D5 반영): 컨테이너 이미지가 없으므로 compose 대신 **EC2 2대**로 분리한다.
`wifiguard-db`(postgres:16 + timescaledb, `docker run`) · `wifiguard-api`(서비스 API `/api/v1`, uv workspace + uvicorn + systemd).
Kafka 인스턴스는 실시간 경로가 붙을 때 `user-data-kafka.sh` 로 추가한다 (`/health` 는 `KAFKA_BOOTSTRAP` 미설정 시 검사 생략).
리전 `ap-northeast-2`. 모든 명령은 **Git Bash** 에서 `export MSYS_NO_PATHCONV=1` 후 실행한다(`/wifiguard/...` 파라미터 이름이 Windows 경로로 바뀌는 것을 막는다). 실행 위치 `WIFIGUARD-BACKEND/`.

| 파일 | 용도 |
|---|---|
| `ec2-trust.json` | EC2 인스턴스 역할 신뢰 정책 |
| `ssm-param-policy.json` | 인스턴스 역할 인라인 정책 — `/wifiguard/*` 파라미터 읽기 + SSM 경유 KMS 복호화 |
| `ops-policy.json` | 운영자 IAM 사용자 `wifiguard-ops` 최소 권한 (ec2 서울 한정, ssm 파라미터/세션, PassRole) |
| `user-data-db.sh` / `user-data-api.sh` / `user-data-kafka.sh` | 인스턴스 부팅 스크립트 (kafka 는 후속) |
| `init-postgres.sql`, `init-timescale.sql` | 부팅 후 개발 PC에서 적용하는 초기 SQL (관계형 스키마는 Alembic 이 만든다) |
| `wifiguard-api.service` | systemd 유닛 (`/opt/wifiguard-api/.venv/bin/uvicorn wifiguard_api.app:app`) |

## 0. 재인증 + IAM (root 세션은 여기까지만) — 2026-09-08 완료
```bash
aws login && aws sts get-caller-identity
aws iam create-role --role-name wifiguard-ec2-role --assume-role-policy-document file://deploy/aws/ec2-trust.json
aws iam attach-role-policy --role-name wifiguard-ec2-role --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam put-role-policy --role-name wifiguard-ec2-role --policy-name wifiguard-ssm-params --policy-document file://deploy/aws/ssm-param-policy.json
aws iam create-instance-profile --instance-profile-name wifiguard-ec2-profile
aws iam add-role-to-instance-profile --instance-profile-name wifiguard-ec2-profile --role-name wifiguard-ec2-role
aws iam create-user --user-name wifiguard-ops
aws iam put-user-policy --user-name wifiguard-ops --policy-name wifiguard-ops-min --policy-document file://deploy/aws/ops-policy.json
aws iam create-access-key --user-name wifiguard-ops        # → aws configure --profile wifiguard (region ap-northeast-2)
export AWS_PROFILE=wifiguard && aws sts get-caller-identity
```

## 1. 시크릿 · 네트워크 · 키 — 2026-09-08 완료
```bash
for k in pg_password tsdb_password; do aws ssm put-parameter --name /wifiguard/dev/$k --type SecureString --value "$(openssl rand -hex 16)" --overwrite; done
aws ssm put-parameter --name /wifiguard/dev/jwt_secret --type SecureString --value "$(openssl rand -hex 32)" --overwrite
VPC=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
MYIP=$(curl -s https://checkip.amazonaws.com)
SG=$(aws ec2 create-security-group --group-name wifiguard-dev-sg --description "wifiguard dev" --vpc-id $VPC --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id $SG --ip-permissions "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=$MYIP/32}]"
aws ec2 authorize-security-group-ingress --group-id $SG --ip-permissions "IpProtocol=tcp,FromPort=0,ToPort=65535,UserIdGroupPairs=[{GroupId=$SG}]"
aws ec2 create-key-pair --key-name wifiguard-dev --key-type ed25519 --key-format pem --query KeyMaterial --output text | tr -d '\r' > ~/.ssh/wifiguard-dev.pem && chmod 600 ~/.ssh/wifiguard-dev.pem
ssh-keygen -y -f ~/.ssh/wifiguard-dev.pem >/dev/null && echo key-ok   # Windows aws CLI 는 CRLF 로 출력 → `tr -d '\r'` 없이는 "error in libcrypto"
AMI=$(aws ssm get-parameters --names /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 --query 'Parameters[0].Value' --output text)
```
5432/5433/8000 은 인터넷에 열지 않는다(자기참조 규칙으로 인스턴스 간만 허용). 8000은 SSH 터널로 접근. 개발 PC IP 가 바뀌면 22 규칙을 갱신한다.

## 2. 인스턴스 (db → 사설 IP 기록 → api)
```bash
run() { aws ec2 run-instances --image-id $AMI --instance-type $2 --key-name wifiguard-dev --security-group-ids $SG \
  --iam-instance-profile Name=wifiguard-ec2-profile --user-data file://deploy/aws/user-data-$1.sh --metadata-options HttpTokens=required \
  --block-device-mappings "DeviceName=/dev/xvda,Ebs={VolumeSize=$3,VolumeType=gp3}" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=wifiguard-$1},{Key=Project,Value=wifiguard}]" \
  --query 'Instances[0].InstanceId' --output text; }
priv() { aws ec2 describe-instances --instance-ids $1 --query 'Reservations[0].Instances[0].PrivateIpAddress' --output text; }
pub()  { aws ec2 describe-instances --instance-ids $1 --query 'Reservations[0].Instances[0].PublicIpAddress'  --output text; }

DB=$(run db t3.small 20)
aws ec2 wait instance-running --instance-ids $DB
aws ssm put-parameter --name /wifiguard/dev/db_host --type String --value $(priv $DB) --overwrite
API=$(run api t3.micro 10)
aws ec2 wait instance-status-ok --instance-ids $DB $API
```

## 3. 스키마 적용 + API 코드 배포 (uv workspace) — 2026-09-08 완료
`api.env` 의 `CORS_ORIGIN_REGEX` 값은 따옴표로 감싸져 있어(`user-data-api.sh`) bash `.` 와 systemd `EnvironmentFile` 양쪽에서 읽힌다. `tar` 의 출력 경로는 Git Bash 에서 `C:` 가 원격 호스트로 해석되므로 상대 경로나 `$(cygpath -u "$TEMP")` 를 쓴다.
```bash
SSH="ssh -i ~/.ssh/wifiguard-dev.pem -o StrictHostKeyChecking=accept-new"
$SSH ec2-user@$(pub $DB) 'cat /var/log/wifiguard-userdata.done'                      # wg-pg / wg-ts Up
scp -i ~/.ssh/wifiguard-dev.pem deploy/aws/init-*.sql ec2-user@$(pub $DB):~/
$SSH ec2-user@$(pub $DB) 'docker exec -i wg-pg psql -U wifiguard -d wifiguard < init-postgres.sql && docker exec -i wg-ts psql -U wifiguard -d wifiguard_ts < init-timescale.sql'

# 앱 트리만 압축 (venv·참조자산·compose 제외) → api 인스턴스
tar czf api.tgz --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' --exclude='tests' \
  pyproject.toml uv.lock packages/db packages/contracts services/api tools/seed.py tools/export_openapi.py Makefile
scp -i ~/.ssh/wifiguard-dev.pem api.tgz deploy/aws/wifiguard-api.service ec2-user@$(pub $API):~/
$SSH ec2-user@$(pub $API) 'set -e; cat /var/log/wifiguard-userdata.done; tar xzf api.tgz -C /opt/wifiguard-api; cd /opt/wifiguard-api
  uv sync --no-dev --python 3.12
  set -a; . /etc/wifiguard/api.env; set +a
  uv run --no-dev alembic -c packages/db/alembic.ini upgrade head
  uv run --no-dev python tools/seed.py
  sudo install -m 644 wifiguard-api.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now wifiguard-api
  sleep 3; systemctl is-active wifiguard-api; curl -s localhost:8000/health'
```

## 4. 개발 PC에서 확인 (터널) — 2026-09-08 완료
```bash
ssh -i ~/.ssh/wifiguard-dev.pem -N -L 8000:127.0.0.1:8000 ec2-user@$(pub $API) &
curl -s http://127.0.0.1:8000/health | python -m json.tool      # "status": "ok", schema "0001"
bash tools/smoke.sh http://127.0.0.1:8000                        # SMOKE OK (python 또는 python3 자동 선택 — 인스턴스 안에서도 그대로 실행 가능)
# 프론트: WIFIGUARD-FRONTEND 에서 bun run dev → http://localhost:8080 (API 기본값 127.0.0.1:8000 = 터널)
```
터널을 닫을 때: Git Bash 의 `kill` 이 Windows ssh 를 못 죽이면
`powershell -c "Get-NetTCPConnection -LocalPort 8000 -State Listen | % { Stop-Process -Id \$_.OwningProcess -Force }"`.
로컬 API(`make api`)와 터널은 같은 8000 포트를 쓰므로 동시에 띄우지 않는다.

재부팅 복원: `aws ec2 reboot-instances --instance-ids $DB $API` 후 db 컨테이너(`--restart unless-stopped`)와 `wifiguard-api`(systemd enable) 가 자동 복귀하는지 `docker ps` / `systemctl is-active` 로 확인한다.

## 5. 비용 · 정리
t3.micro(api) + t3.small(db) ≈ $9.5 + $19 = **약 $29/월** + 퍼블릭 IPv4 2개 ≈ $7 + EBS 30GB ≈ $3 → **약 $39/월 상시**, `stop-instances` 시 EBS만(≈ $3).
```bash
aws ec2 stop-instances --instance-ids $DB $API                                        # 보존
aws ec2 terminate-instances --instance-ids $DB $API && aws ec2 wait instance-terminated --instance-ids $DB $API
aws ec2 delete-security-group --group-id $SG; aws ec2 delete-key-pair --key-name wifiguard-dev
aws ssm delete-parameters --names /wifiguard/dev/pg_password /wifiguard/dev/tsdb_password /wifiguard/dev/jwt_secret /wifiguard/dev/db_host
```
