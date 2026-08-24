.PHONY: up down logs migrate test check shell create-superadmin prod-up prod-config preflight backup smoke

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

migrate:
	docker compose run --rm migrate

test:
	pytest

check:
	./scripts/check.sh

shell:
	docker compose exec api /bin/sh

create-superadmin:
	docker compose exec api python -m app.admin.cli create-superadmin

prod-config:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml config

prod-up:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

preflight:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml exec api python -m app.ops.preflight

backup:
	./scripts/backup.sh

smoke:
	./scripts/smoke.sh $(PUBLIC_BASE_URL)
