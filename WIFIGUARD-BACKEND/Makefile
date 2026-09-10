# WIFI-GUARD 클라우드 스택 — 로컬 개발용 진입점.
#
# 서비스 계층(DB·API)은 uv workspace 로 관리한다:  uv sync  →  make migrate / seed / api / test
# compose 는 미들웨어 뼈대(1차 검토 §2 참조). 현재 클라우드 1차 토폴로지는 compose 를 쓰지 않는다 (deploy/aws/README.md).

COMPOSE     := docker compose -f compose/docker-compose.dev.yml --env-file compose/.env
COMPOSE_OBS := $(COMPOSE) -f compose/docker-compose.obs.yml
ALEMBIC     := uv run alembic -c packages/db/alembic.ini

.PHONY: up down logs ps obs migrate migrate-down seed api openapi test replay clean

up:              ## 미들웨어 전 스택 기동
	$(COMPOSE) up -d

obs:             ## 관측 스택까지 함께 기동
	$(COMPOSE_OBS) up -d

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

migrate:         ## Alembic upgrade head (DATABASE_URL, 기본 로컬 wg-pg)
	$(ALEMBIC) upgrade head

migrate-down:    ## Alembic downgrade base (스키마 전부 제거)
	$(ALEMBIC) downgrade base

seed:            ## 개발용 시드 데이터 (목업 시드 재현). --reset 은 make seed SEED_ARGS=--reset
	uv run python tools/seed.py $(SEED_ARGS)

api:             ## 서비스 API 개발 서버 (http://127.0.0.1:8000/docs)
	uv run uvicorn wifiguard_api.app:app --host 127.0.0.1 --port 8000 --reload

openapi:         ## 스키마 스냅샷 2종 → packages/contracts/{openapi,realtime.schema}.json (프론트 코드젠 입력)
	uv run python tools/export_openapi.py

test:            ## pytest (DATABASE_URL_TEST, 기본 로컬 wifiguard_test DB)
	uv run pytest

replay:          ## 엣지 페이로드 재생 (Pi 없이 개발) — tools/replay_edge.py 미구현
	@echo "미구현: tools/replay_edge.py 가 없다"; exit 1

clean:           ## 볼륨까지 삭제 (데이터 전부 날아간다)
	$(COMPOSE_OBS) down -v
