.PHONY: up down logs test lint build smoke

up:
	docker compose up --build --wait

down:
	docker compose down

logs:
	docker compose logs -f

test:
	python -m pytest --cov

lint:
	python -m ruff check .
	python -m ruff format --check .

build:
	python -m build

smoke:
	python scripts/smoke.py --check-expiry
