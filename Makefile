PY ?= .venv/bin/python

.PHONY: setup demo test pull serve mcp

setup:
	uv venv .venv --python 3.13
	uv pip install --python $(PY) -e ".[dev]"

demo:
	$(PY) -m thread_outcomes demo

test:
	$(PY) -m pytest -q

pull:
	$(PY) -m thread_outcomes pull --webhook-id $(WEBHOOK_ID)

serve:
	$(PY) -m thread_outcomes serve --port 8787

mcp:
	$(PY) -m thread_outcomes mcp
