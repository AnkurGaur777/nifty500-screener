.PHONY: up down pipeline lint test

up:
	docker compose up -d

down:
	docker compose down

pipeline:
	python -m src.screener.reference && \
	python -m src.screener.load --all && \
	python -m src.screener.metrics && \
	python -m src.screener.flags && \
	python -m src.screener.scoring && \
	python -m src.screener.backtest && \
	python -m src.screener.artifacts

lint:
	ruff check src/ tests/

test:
	pytest -v
