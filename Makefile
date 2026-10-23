.PHONY: format lint check test docs
format:
	black app.py src tests
	ruff check --fix app.py src tests
lint:
	black --check app.py src tests
	ruff check app.py src tests
test:
	pytest -q
check: lint test
docs:
	sphinx-build -b html docs docs/_build/html
