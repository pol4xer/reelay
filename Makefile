SHELL := /bin/bash
.DEFAULT_GOAL := help

UV ?= uv
APP_MODULE := reelay
PYTHON_SOURCES := reelay tests

.PHONY: help install run config-ui tiktok-oauth format test check queue-audit server-bundle service-install service-start service-stop service-status service-uninstall

help:
	@echo "Reelay commands:"
	@echo "  make install          Install runtime and Ruff dependencies"
	@echo "  make run              Run Reelay in the foreground"
	@echo "  make config-ui        Open the local settings panel"
	@echo "  make tiktok-oauth     Authorize a TikTok account for Inbox uploads"
	@echo "  make format           Fix Ruff lint issues and format Python code"
	@echo "  make test             Run the Python test suite"
	@echo "  make check            Check Ruff lint and formatting"
	@echo "  make queue-audit      Validate queue state and every pending MP4"
	@echo "  make server-bundle    Build one credential+queue Docker deploy ZIP"
	@echo "  make service-install  Install and start the macOS LaunchAgent"
	@echo "  make service-start    Start or restart the macOS LaunchAgent"
	@echo "  make service-stop     Stop the macOS LaunchAgent"
	@echo "  make service-status   Show LaunchAgent state and log locations"
	@echo "  make service-uninstall Remove the macOS LaunchAgent"

install:
	$(UV) sync --group dev

run:
	$(UV) run --no-sync python -m $(APP_MODULE)

config-ui:
	$(UV) run --no-sync python -m reelay.config_ui

tiktok-oauth:
	$(UV) run --no-sync python -m reelay.tiktok_oauth

format:
	$(UV) run --no-sync ruff check --fix $(PYTHON_SOURCES)
	$(UV) run --no-sync ruff format $(PYTHON_SOURCES)

test:
	PYTHONPYCACHEPREFIX=/tmp/reelay-test-pyc $(UV) run --no-sync python -m unittest discover -s tests -v

check:
	$(UV) lock --check
	$(UV) run --no-sync ruff check $(PYTHON_SOURCES)
	$(UV) run --no-sync ruff format --check $(PYTHON_SOURCES)
	PYTHONPYCACHEPREFIX=/tmp/reelay-check-pyc $(UV) run --no-sync python -m compileall -q $(APP_MODULE)
	PYTHONPYCACHEPREFIX=/tmp/reelay-test-pyc $(UV) run --no-sync python -m unittest discover -s tests
	@if command -v node >/dev/null 2>&1; then node --check reelay/config_ui_static/app.js; fi
	@if command -v docker >/dev/null 2>&1; then env REELAY_ENV_FILE=/dev/null docker compose config --quiet; fi
	bash -n scripts/service.sh scripts/server-preflight.sh scripts/build-server-bundle.sh deploy/docker/install.sh "Reelay Settings.command"
	plutil -lint deploy/macos/com.pol4xer.reelay.plist.template

queue-audit:
	$(UV) run --no-sync python -m reelay.maintenance queue-audit --probe

server-bundle:
	bash scripts/build-server-bundle.sh "$(CURDIR)/Reelay-All-In-One.zip"

service-install:
	bash scripts/service.sh install

service-start:
	bash scripts/service.sh start

service-stop:
	bash scripts/service.sh stop

service-status:
	bash scripts/service.sh status

service-uninstall:
	bash scripts/service.sh uninstall
