#!/bin/bash
# wifiguard-db (Amazon Linux 2023) — postgres:16(5432) + timescale/timescaledb:2.17.2-pg16(5433) 을 docker run 으로 기동.
# 비밀번호는 SSM Parameter Store(SecureString) /wifiguard/dev/{pg_password,tsdb_password} 에서 읽는다 (인스턴스 역할 필요).
# 스키마(init-postgres.sql / init-timescale.sql)는 부팅 시 적용하지 않고, 개발 PC에서 scp + docker exec psql 로 적용한다.
set -euxo pipefail
export AWS_DEFAULT_REGION=ap-northeast-2

dnf update -y
dnf install -y docker
systemctl enable --now docker
usermod -aG docker ec2-user

PG_PASSWORD=$(aws ssm get-parameter --name /wifiguard/dev/pg_password   --with-decryption --query Parameter.Value --output text)
TSDB_PASSWORD=$(aws ssm get-parameter --name /wifiguard/dev/tsdb_password --with-decryption --query Parameter.Value --output text)

mkdir -p /opt/wifiguard/db
docker run -d --name wg-pg --restart unless-stopped -p 5432:5432 \
  -e POSTGRES_DB=wifiguard -e POSTGRES_USER=wifiguard -e POSTGRES_PASSWORD="$PG_PASSWORD" \
  -v wg-pg-data:/var/lib/postgresql/data postgres:16
docker run -d --name wg-ts --restart unless-stopped -p 5433:5432 \
  -e POSTGRES_DB=wifiguard_ts -e POSTGRES_USER=wifiguard -e POSTGRES_PASSWORD="$TSDB_PASSWORD" \
  -v wg-ts-data:/var/lib/postgresql/data timescale/timescaledb:2.17.2-pg16

for c in wg-pg wg-ts; do
  for i in $(seq 1 30); do docker exec "$c" pg_isready -U wifiguard >/dev/null 2>&1 && break; sleep 2; done
done
docker ps --format '{{.Names}} {{.Status}}' > /var/log/wifiguard-userdata.done
