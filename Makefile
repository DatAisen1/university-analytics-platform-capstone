COMPOSE := docker compose -f docker/docker-compose.yml --env-file .env
ENV_FILE := .env
PYTHON ?= python

.PHONY: check-env up down down-v ps clean-start logs verify-minio bootstrap acceptance-test

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

# README's Quick Start has always documented this target ("make bootstrap
# # runs migrations, seeds config, generates Faker data"), but no such
# target existed in this Makefile -- a fresh clone had no way to run it
# except by reverse-engineering the two underlying entry points below.
# Both already exist and are already tested (pipelines/common/postgres.py's
# bootstrap_warehouse() / __main__ block, data_generator's generate_all);
# this target adds no new pipeline logic, it just wires the two together
# the way the README already promised.
bootstrap: check-env
	$(PYTHON) -m pipelines.common.postgres
	$(PYTHON) -m data_generator.generators.generate_all

# Item 26 (Final End-to-End Acceptance Test): fresh-clone reproducibility,
# verified by an actual script rather than a manual checklist. See
# scripts/run_acceptance_test.sh for what each stage checks.
acceptance-test: check-env
	bash scripts/run_acceptance_test.sh