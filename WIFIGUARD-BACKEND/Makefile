# WIFI-GUARD 클라우드 스택 — 로컬 개발용 진입점.
#
# 서비스 계층(DB·API)은 uv workspace 로 관리한다:  uv sync  →  make migrate / seed / api / test
# compose 는 미들웨어 뼈대(1차 검토 §2 참조). 현재 클라우드 1차 토폴로지는 compose 를 쓰지 않는다 (deploy/aws/README.md).

COMPOSE     := docker compose -f compose/docker-compose.dev.yml --env-file compose/.env
COMPOSE_OBS := $(COMPOSE) -f compose/docker-compose.obs.yml
ALEMBIC     := uv run alembic -c packages/db/alembic.ini

.PHONY: up model-up model-gpu-up model-check model-e2e down logs ps obs monitor-host resource-estimate migrate migrate-down seed api openapi test replay clean

up:              ## 미들웨어 전 스택 기동
	$(COMPOSE) up -d

model-up:        ## 외부 주입된 temporal segmentation checkpoint를 포함한 전체 E2E 기동
	$(COMPOSE) --profile model up -d

model-gpu-up:    ## CUDA CWT + 모델 추론(실시간 4Hz 검증 경로)
	$(COMPOSE) --profile gpu up -d

model-check:     ## Kafka 없이 체크포인트 형식·피처·추론 1회를 검증
	uv run --project services/inference python tools/validate_model_checkpoint.py weights/temporal_segmentation_fixed_delay.pt

model-e2e:       ## 하드웨어 없이 MQTT→실제모델→DB→API 소프트웨어 E2E 검증
	uv run python tools/validate_model_software_e2e.py --mqtt-port $${MQTT_HOST_PORT:-1883}

obs:             ## 관측 스택까지 함께 기동
	$(COMPOSE_OBS) up -d

monitor-host:    ## 호스트 CPU·RAM·GPU·VRAM·디스크 실시간 모니터(http://127.0.0.1:9108)
	uv run python tools/resource_monitor.py --host 0.0.0.0 --port 9108 --interval 1 --output artifacts/resource-profiles/live-monitor.jsonl

resource-estimate: ## 검증 profile로 AWS 시작 용량 envelope 재계산
	uv run python tools/estimate_aws_resources.py --profiles artifacts/resource-profiles/inference-gpu.json artifacts/resource-profiles/training-smoke.json artifacts/resource-profiles/finetune-smoke.json --dataset-registry artifacts/validation/dataset-registry.json --headroom 1.5 --retained-checkpoints 10 --output artifacts/validation/aws-resource-envelope.json

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
