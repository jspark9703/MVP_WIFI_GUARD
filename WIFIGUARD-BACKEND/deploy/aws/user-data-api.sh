#!/bin/bash
# wifiguard-api (Amazon Linux 2023) — 서비스 API(uv workspace) 실행 준비. 컨테이너 이미지가 없으므로 네이티브로 띄운다.
# 앱 트리(pyproject.toml, uv.lock, packages/, services/api, tools/)는 부팅 후 개발 PC에서 scp 로 올리고
# deploy/aws/README.md §3 순서로 `uv sync --no-dev` → alembic → seed → systemd 유닛 설치를 한다.
# DB 사설 IP와 비밀번호·JWT 시크릿은 SSM Parameter Store /wifiguard/dev/* 에서 읽어 /etc/wifiguard/api.env 로 만든다.
set -euxo pipefail
export AWS_DEFAULT_REGION=ap-northeast-2

dnf update -y
dnf install -y python3.12 git tar

# uv (시스템 전역) — 워크스페이스 동기화용
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
/usr/local/bin/uv --version

install -d -o ec2-user -g ec2-user /opt/wifiguard-api

DB_HOST=$(aws ssm get-parameter --name /wifiguard/dev/db_host      --query Parameter.Value --output text)
PG_PASSWORD=$(aws ssm get-parameter --name /wifiguard/dev/pg_password   --with-decryption --query Parameter.Value --output text)
TSDB_PASSWORD=$(aws ssm get-parameter --name /wifiguard/dev/tsdb_password --with-decryption --query Parameter.Value --output text)
JWT_SECRET=$(aws ssm get-parameter --name /wifiguard/dev/jwt_secret    --with-decryption --query Parameter.Value --output text)

install -d -m 750 -o root -g ec2-user /etc/wifiguard
cat > /etc/wifiguard/api.env <<ENV
DATABASE_URL=postgresql+psycopg://wifiguard:${PG_PASSWORD}@${DB_HOST}:5432/wifiguard
TSDB_DSN=postgresql://wifiguard:${TSDB_PASSWORD}@${DB_HOST}:5433/wifiguard_ts
JWT_SECRET=${JWT_SECRET}
JWT_ACCESS_TTL_MIN=15
JWT_REFRESH_TTL_DAYS=14
ALLOW_FALL_SIMULATE=1
CORS_ORIGIN_REGEX="^https?://(localhost|127\\.0\\.0\\.1)(:\\d+)?$"
ENV
# systemd EnvironmentFile 과 bash `export` 양쪽에서 읽히도록 정규식 값은 따옴표로 감싼다.
chown root:ec2-user /etc/wifiguard/api.env && chmod 640 /etc/wifiguard/api.env

echo "api host ready: python=$(python3.12 --version) uv=$(/usr/local/bin/uv --version) db_host=${DB_HOST}" > /var/log/wifiguard-userdata.done
