# WasteLess — raccourcis de développement
# `make` ou `make help` liste les cibles disponibles.

.PHONY: help setup db db-wait ui test test-ui lint doctor

help:
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Installation complète (prérequis, venvs, DB, UI) — idempotent
	./install.sh

db: ## Démarre PostgreSQL (Docker)
	docker compose up -d postgres

ui: ## Démarre l'interface web (port 8888)
	./wasteless.sh

# Démarre PostgreSQL si Docker est disponible, puis attend qu'il réponde.
# Best-effort (préfixe -) : sans Docker, les tests dépendants de la DB
# skippent proprement au lieu d'échouer — on ne bloque pas le reste.
db-wait:
	-@docker compose up -d postgres 2>/dev/null && \
	  for i in $$(seq 1 30); do \
	    docker compose exec -T postgres pg_isready -U wasteless >/dev/null 2>&1 && break; \
	    sleep 1; \
	  done

test: db-wait ## Tests backend (pytest, venv racine) — démarre PostgreSQL si possible
	./venv/bin/pytest

test-ui: db-wait ## Tests UI — démarre PostgreSQL si possible
	cd ui && venv/bin/python3 run_tests.py

lint: ## black + ruff + mypy + shellcheck — mêmes versions que la CI (requirements-dev.lock)
	./venv/bin/black --check src/ ui/ tests/
	./venv/bin/ruff check src/ ui/ tests/
	./venv/bin/mypy src/core/ src/remediators/ ui/utils/remediator.py ui/jobs.py ui/utils/aws_clients.py --ignore-missing-imports --follow-imports=silent
	./venv/bin/shellcheck -S warning install.sh wasteless.sh uninstall.sh scripts/*.sh ui/*.sh

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
	@found=0; for a in "venv "* "ui/venv "* "venv.nosync/bin "* "ui/venv.nosync/bin "*; do \
		[ -e "$$a" ] && { echo "  $$a: à supprimer (copie de conflit iCloud)"; found=1; }; \
	done; [ $$found -eq 0 ] && echo "  aucun" || true
	@echo "— Docker —"
	@docker info >/dev/null 2>&1 && echo "  démon: OK" || echo "  démon: ARRÊTÉ — lancez Docker Desktop"
	@docker ps --format '{{.Names}}' 2>/dev/null | grep -q wasteless-postgres && echo "  postgres: en cours" || echo "  postgres: arrêté (make db)"
	@echo "— Configuration —"
	@[ -f .env ] && echo "  .env: présent" || echo "  .env: MANQUANT — relancez ./install.sh"
	@[ -f ui/.env ] && echo "  ui/.env: présent" || echo "  ui/.env: MANQUANT — relancez ./install.sh"
