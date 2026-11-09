SHELL := /bin/bash
PYTHON ?= python

.PHONY: fmt tests serve-docs

fmt:
	$(PYTHON) -m black .

tests:
	$(PYTHON) -m pytest

serve-docs:
	$(PYTHON) -m mkdocs serve
