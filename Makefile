# WasteLess — raccourcis de développement
# `make` ou `make help` liste les cibles disponibles.

.PHONY: help setup db ui test test-ui lint doctor

help:
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Installation complète (prérequis, venvs, DB, UI) — idempotent
	./install.sh

db: ## Démarre PostgreSQL (Docker)
	docker compose up -d postgres

ui: ## Démarre l'interface web (port 8888)
	./wasteless.sh

test: ## Tests backend (pytest, venv racine)
	./venv/bin/pytest

test-ui: ## Tests UI
	cd ui && venv/bin/python3 run_tests.py

lint: ## black + ruff (à installer: pip install black ruff)
	./venv/bin/black --check src/ ui/ || true
	./venv/bin/ruff check src/ ui/

doctor: ## Diagnostique les problèmes d'environnement macOS courants
	@echo "— Python —"
	@python3 --version
	@[ -f .python-version ] && echo "  version attendue: $$(cat .python-version)" || true
	@echo "— Venvs —"
	@for v in venv ui/venv; do \
		if [ -e "$$v/bin/python3" ]; then \
			echo "  $$v: OK ($$($$v/bin/python3 --version 2>&1))"; \
		else \
			echo "  $$v: MANQUANT ou symlink cassé — relancez ./install.sh"; \
		fi; \
	done
	@echo "— Artefacts de conflit iCloud —"
	@found=0; for a in "venv 2" "ui/venv 2" "venv.nosync/bin 2" "ui/venv.nosync/bin 2"; do \
		[ -e "$$a" ] && { echo "  $$a: à supprimer (copie de conflit iCloud)"; found=1; }; \
	done; [ $$found -eq 0 ] && echo "  aucun" || true
	@echo "— Docker —"
	@docker info >/dev/null 2>&1 && echo "  démon: OK" || echo "  démon: ARRÊTÉ — lancez Docker Desktop"
	@docker ps --format '{{.Names}}' 2>/dev/null | grep -q wasteless-postgres && echo "  postgres: en cours" || echo "  postgres: arrêté (make db)"
	@echo "— Configuration —"
	@[ -f .env ] && echo "  .env: présent" || echo "  .env: MANQUANT — relancez ./install.sh"
	@[ -f ui/.env ] && echo "  ui/.env: présent" || echo "  ui/.env: MANQUANT — relancez ./install.sh"
