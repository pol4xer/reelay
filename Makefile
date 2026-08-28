SHELL := /bin/bash
.DEFAULT_GOAL := help

UV ?= uv
APP_MODULE := reelay

.PHONY: help install run format check queue-audit service-install service-start service-stop service-status service-uninstall

help:
	@echo "Reelay commands:"
	@echo "  make install          Install runtime and Ruff dependencies"
	@echo "  make run              Run Reelay in the foreground"
	@echo "  make format           Fix Ruff lint issues and format Python code"
	@echo "  make check            Check Ruff lint and formatting"
	@echo "  make queue-audit      Validate queue state and every pending MP4"
	@echo "  make service-install  Install and start the macOS LaunchAgent"
	@echo "  make service-start    Start or restart the macOS LaunchAgent"
	@echo "  make service-stop     Stop the macOS LaunchAgent"
	@echo "  make service-status   Show LaunchAgent state and log locations"
	@echo "  make service-uninstall Remove the macOS LaunchAgent"

install:
	$(UV) sync --group dev

run:
	$(UV) run --no-sync python -m $(APP_MODULE)

format:
	$(UV) run --no-sync ruff check --fix $(APP_MODULE)
	$(UV) run --no-sync ruff format $(APP_MODULE)

check:
	$(UV) lock --check
	$(UV) run --no-sync ruff check $(APP_MODULE)
	$(UV) run --no-sync ruff format --check $(APP_MODULE)
	PYTHONPYCACHEPREFIX=/tmp/reelay-check-pyc $(UV) run --no-sync python -m compileall -q $(APP_MODULE)
	bash -n scripts/service.sh
	plutil -lint deploy/macos/com.pol4xer.reelay.plist.template

queue-audit:
	$(UV) run --no-sync python -m reelay.maintenance queue-audit --probe

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
