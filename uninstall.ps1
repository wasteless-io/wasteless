#
# WasteLess - Script de desinstallation pour Windows natif (equivalent de uninstall.sh)
#
# Usage:
#   uninstall.cmd          # Desinstallation standard (conserve les donnees DB)
#   uninstall.cmd --full   # Desinstallation complete (supprime aussi les donnees DB)
#
# Fichier ASCII uniquement (Windows PowerShell 5.1 lit les scripts sans BOM en ANSI).
#

$ErrorActionPreference = "Continue"

$FullUninstall = $false
foreach ($arg in $args) {
    if ($arg -in "-full", "--full") { $FullUninstall = $true }
}

$Root = $PSScriptRoot
Set-Location $Root

function Write-Header($msg) {
    Write-Host ""
    Write-Host "=======================================================================" -ForegroundColor Blue
    Write-Host "  $msg" -ForegroundColor Cyan
    Write-Host "=======================================================================" -ForegroundColor Blue
    Write-Host ""
}
function Write-Step($msg) { Write-Host "[OK] " -ForegroundColor Green -NoNewline; Write-Host $msg }
function Write-Warn($msg) { Write-Host "[WARN] " -ForegroundColor Yellow -NoNewline; Write-Host $msg }
function Write-Info($msg) { Write-Host "[INFO] " -ForegroundColor Blue -NoNewline; Write-Host $msg }
function Write-Skip($msg) { Write-Host "[SKIP] " -ForegroundColor Cyan -NoNewline; Write-Host $msg }

function Get-EnvVar([string]$Key) {
    $file = Join-Path $Root ".env"
    if (-not (Test-Path $file)) { return "" }
    $line = Get-Content $file | Where-Object { $_ -match "^$([regex]::Escape($Key))=" } | Select-Object -Last 1
    if ($null -eq $line) { return "" }
    return $line.Substring($Key.Length + 1)
}

# Connexion AWS lue AVANT la suppression des .env (etape 3) : l'etape 6 en a
# besoin pour retrouver la stack d'onboarding.
$EnvAwsRoleArn = Get-EnvVar "AWS_ROLE_ARN"
$EnvAwsRegion = if ($env:AWS_REGION) { $env:AWS_REGION } else { Get-EnvVar "AWS_REGION" }
if (-not $EnvAwsRegion) { $EnvAwsRegion = "eu-west-1" }

Clear-Host
Write-Host @"
 __        __        _       _
 \ \      / /_ _ ___| |_ ___| | ___  ___ ___
  \ \ /\ / / _`` / __| __/ _ \ |/ _ \/ __/ __|
   \ V  V / (_| \__ \ ||  __/ |  __/\__ \__ \
    \_/\_/ \__,_|___/\__\___|_|\___||___/___/

    Desinstallation
"@ -ForegroundColor Red
Write-Host ""
if ($FullUninstall) {
    Write-Host "Mode: DESINSTALLATION COMPLETE (donnees supprimees)" -ForegroundColor Red
} else {
    Write-Host "Mode: Desinstallation standard (donnees conservees)" -ForegroundColor Yellow
    Write-Host "  Utilisez --full pour supprimer egalement les donnees."
}
Write-Host ""
Read-Host "Appuyez sur Entree pour continuer ou Ctrl+C pour annuler" | Out-Null

# =============================================================================
# 1/6 - ARRET DE WASTELESS
# =============================================================================
Write-Header "1/6 - Arret de WasteLess"

$wastelessPs1 = Join-Path $Root "wasteless.ps1"
if (Test-Path $wastelessPs1) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $wastelessPs1 stop
}
$logFile = Join-Path $env:USERPROFILE ".wasteless.log"
foreach ($f in ".wasteless.pid", ".wasteless-collector.pid") {
    Remove-Item (Join-Path $env:USERPROFILE $f) -ErrorAction SilentlyContinue
}
if (Test-Path $logFile) {
    Remove-Item $logFile -ErrorAction SilentlyContinue
    Write-Step "Fichier de log supprime ($logFile)"
} else {
    Write-Skip "Aucun fichier de log a supprimer"
}

# =============================================================================
# 2/6 - DOCKER
# =============================================================================
Write-Header "2/6 - Arret des services Docker"

if ((Get-Command docker -ErrorAction SilentlyContinue) -and (Test-Path (Join-Path $Root "docker-compose.yml"))) {
    $running = @(& docker ps --format '{{.Names}}' 2>$null) | Where-Object { $_ -like "wasteless*" }
    if ($running) {
        & docker compose down *> $null
        Write-Step "Conteneurs Docker arretes"
    } else {
        Write-Skip "Aucun conteneur WasteLess en cours d'execution"
    }
    if ($FullUninstall) {
        Write-Host ""
        Write-Warn "Suppression des volumes Docker (donnees de la base de donnees)..."
        & docker compose down -v *> $null
        & docker volume rm wasteless_postgres_data *> $null
        Write-Step "Volumes Docker supprimes (donnees effacees)"
    } else {
        Write-Skip "Volumes Docker conserves (utilisez --full pour les supprimer)"
    }
} else {
    Write-Skip "Docker non disponible ou docker-compose.yml absent"
}

# =============================================================================
# 3/6 - FICHIERS LOCAUX
# =============================================================================
Write-Header "3/6 - Suppression des fichiers locaux"

foreach ($v in "venv", "ui\venv") {
    $path = Join-Path $Root $v
    if (Test-Path $path) {
        Remove-Item $path -Recurse -Force
        Write-Step "Environnement virtuel supprime ($v\)"
    } else {
        Write-Skip "Environnement virtuel absent ($v\)"
    }
}
foreach ($e in ".env", "ui\.env") {
    $path = Join-Path $Root $e
    if (Test-Path $path) {
        Remove-Item $path -Force
        Write-Step "Fichier de configuration supprime: $path"
    } else {
        Write-Skip "Absent: $path"
    }
}
Get-ChildItem $Root -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Write-Step "Caches Python supprimes"

# =============================================================================
# 4/6 - TACHE PLANIFIEE
# =============================================================================
Write-Header "4/6 - Suppression des taches automatiques"

if (Get-ScheduledTask -TaskName "WasteLessCollect" -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName "WasteLessCollect" -Confirm:$false
    Write-Step "Tache planifiee WasteLessCollect supprimee"
} else {
    Write-Skip "Aucune tache planifiee WasteLess"
}

# =============================================================================
# 5/6 - COMMANDE 'wasteless'
# =============================================================================
Write-Header "5/6 - Suppression de la commande 'wasteless'"

# Ne supprimer le shim que s'il pointe vers cette installation.
$shim = Join-Path $env:USERPROFILE ".local\bin\wasteless.cmd"
if (Test-Path $shim) {
    if ((Get-Content $shim -Raw) -like "*$Root\wasteless.ps1*") {
        Remove-Item $shim -Force
        Write-Step "Commande $shim supprimee"
    } else {
        Write-Skip "$shim conserve (ne correspond pas a cette installation)"
    }
} else {
    Write-Skip "Aucune commande 'wasteless' trouvee"
}
Remove-Item (Join-Path $env:USERPROFILE ".config\wasteless") -Recurse -Force -ErrorAction SilentlyContinue
# %USERPROFILE%\.local\bin reste volontairement dans le PATH utilisateur :
# benin, et souvent partage avec d'autres outils (pipx, uv).

# =============================================================================
# 6/6 - ROLES IAM DANS AWS
# =============================================================================
Write-Header "6/6 - Roles IAM wasteless dans AWS"

$StackName = if ($env:WASTELESS_ONBOARDING_STACK) { $env:WASTELESS_ONBOARDING_STACK } else { "wasteless-onboarding" }
$AwsRolesDeleted = $false

function Write-ManualTeardown {
    Write-Host "    Console : CloudFormation -> stack '$StackName' -> Delete"
    Write-Host "    Ou      : terraform destroy dans votre module onboarding\terraform"
}

if (-not $EnvAwsRoleArn) {
    Write-Skip "Aucun role IAM wasteless configure (AWS_ROLE_ARN absent du .env) - rien a supprimer cote AWS"
} else {
    Write-Host "  WasteLess utilisait ce role IAM dans votre compte AWS :"
    Write-Host "    - $EnvAwsRoleArn"
    Write-Host "    (+ le role remediation s'il avait ete cree)"
    Write-Host ""
    $answer = Read-Host "  Supprimer ces roles maintenant (stack '$StackName', region $EnvAwsRegion) ? [o/N]"
    if ($answer -match '^[oOyY]') {
        if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
            Write-Warn "AWS CLI absent - supprimez les roles manuellement :"
            Write-ManualTeardown
        } else {
            & aws cloudformation describe-stacks --stack-name $StackName --region $EnvAwsRegion *> $null
            if ($LASTEXITCODE -ne 0) {
                Write-Warn "Stack '$StackName' introuvable dans la region $EnvAwsRegion"
                Write-Host "    - Onboarding fait via Terraform : terraform destroy dans onboarding\terraform"
                Write-Host "    - Autre nom ou autre region     : definissez WASTELESS_ONBOARDING_STACK / AWS_REGION puis relancez"
            } else {
                & aws cloudformation delete-stack --stack-name $StackName --region $EnvAwsRegion
                if ($LASTEXITCODE -eq 0) {
                    Write-Info "Suppression lancee, attente de la confirmation (moins d'une minute en general)..."
                    & aws cloudformation wait stack-delete-complete --stack-name $StackName --region $EnvAwsRegion *> $null
                    if ($LASTEXITCODE -eq 0) {
                        Write-Step "Stack '$StackName' supprimee - les roles IAM wasteless n'existent plus"
                        $AwsRolesDeleted = $true
                    } else {
                        Write-Warn "Suppression non confirmee - verifiez l'etat de la stack dans la console CloudFormation"
                    }
                } else {
                    Write-Warn "Echec de la suppression (droits IAM insuffisants ?) - faites-le avec un compte admin :"
                    Write-ManualTeardown
                }
            }
        }
    } else {
        Write-Skip "Roles IAM conserves. Pour les supprimer plus tard :"
        Write-ManualTeardown
    }
}

# =============================================================================
# RESUME
# =============================================================================
Write-Header "Desinstallation terminee"

Write-Host "WasteLess a ete desinstalle." -ForegroundColor Green
Write-Host ""
Write-Host "Ce qui a ete supprime:"
Write-Host "  - Processus WasteLess (si en cours)"
Write-Host "  - Conteneurs Docker"
Write-Host "  - Environnements virtuels Python (venv\, ui\venv\)"
Write-Host "  - Fichiers de configuration (.env, ui\.env)"
Write-Host "  - Caches Python (__pycache__)"
Write-Host "  - Tache planifiee WasteLessCollect"
Write-Host "  - Commande 'wasteless'"
if ($AwsRolesDeleted) { Write-Host "  - Roles IAM wasteless dans AWS (stack '$StackName')" }
Write-Host ""
if ($FullUninstall) {
    Write-Host "Donnees supprimees:" -ForegroundColor Red
    Write-Host "  - Volumes Docker (base de donnees PostgreSQL)"
} else {
    Write-Host "Conserve:" -ForegroundColor Yellow
    Write-Host "  - Volumes Docker (base de donnees PostgreSQL)"
    Write-Host "  - Code source du projet"
    if (-not $AwsRolesDeleted -and $EnvAwsRoleArn) {
        Write-Host "  - Roles IAM wasteless dans AWS (voir etape 6 pour les supprimer)"
    }
    Write-Host ""
    Write-Host "  Pour supprimer aussi les donnees: uninstall.cmd --full"
}
Write-Host ""
