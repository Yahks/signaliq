.PHONY: install dev test lint fmt typecheck clean

install:
	pip install -r requirements-dev.txt

dev:
	uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest -q

test-cov:
	pytest --cov=packages --cov=apps --cov-report=term-missing

lint:
	ruff check .

fmt:
	ruff format .
	black .

typecheck:
	mypy packages apps

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage