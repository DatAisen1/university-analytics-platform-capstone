COMPOSE := docker compose -f docker/docker-compose.yml --env-file .env
ENV_FILE := .env
PYTHON ?= python3

.PHONY: check-env up down down-v ps clean-start logs verify-minio

check-env:
	@test -f $(ENV_FILE) || { \
		echo "Missing $(ENV_FILE). Run: cp .env.example .env, then edit it with real local values."; \
		exit 1; \
	}

up: check-env
	$(COMPOSE) up -d

down: check-env
	$(COMPOSE) down

down-v: check-env
	$(COMPOSE) down -v

ps: check-env
	$(COMPOSE) ps

logs: check-env
	$(COMPOSE) logs -f

clean-start: check-env
	$(COMPOSE) down -v
	$(COMPOSE) up -d
	bash scripts/verify_docker_stack.sh

verify-minio: check-env
	$(PYTHON) scripts/verify_minio_data.py
