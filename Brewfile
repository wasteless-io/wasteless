# WasteLess — prérequis macOS
# Installation en une commande : brew bundle
#
# Docker Desktop inclut Docker Compose. Sur Apple Silicon comme sur Intel,
# ne jamais coder les chemins Homebrew en dur : utiliser $(brew --prefix).

brew "python@3.13"   # version épinglée dans .python-version
brew "uv"            # optionnel mais recommandé — installations rapides et atomiques
brew "awscli"        # optionnel — credentials via `aws configure`

tap "turbot/tap"
brew "turbot/tap/steampipe"  # détecteurs ELB/NAT/VPC/gp2/AMI/RDS (install.sh installe le plugin AWS)

cask "docker-desktop"
