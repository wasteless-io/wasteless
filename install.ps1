#
# WasteLess - Script d'installation pour Windows natif (equivalent de install.sh)
#
# Usage: install.cmd [options]     (ou: powershell -ExecutionPolicy Bypass -File install.ps1)
#
# Options (les formes bash --xxx de install.sh sont aussi acceptees):
#   -Quiet              Masque la sortie detaillee de chaque commande
#   -Doctor             Diagnostic uniquement, aucune modification systeme
#   -InstallSystemDeps  Installe les prerequis manquants (Python, Docker Desktop) via winget
#   -NoSchedule         N'installe pas la collecte automatique (Planificateur de taches)
#   -Yes                Mode non-interactif (valide les modifications systeme)
#
# Ce fichier doit rester en ASCII : Windows PowerShell 5.1 lit les scripts
# sans BOM dans la page de code ANSI, un accent y serait corrompu.
#

# Les commandes natives (docker, python) signalent leurs echecs par code de
# sortie, verifie explicitement. "Stop" ferait echouer chaque ligne ecrite
# sur stderr sous PowerShell 5.1, meme quand la commande reussit.
$ErrorActionPreference = "Continue"

# Pas de bloc param() : $args recoit tel quel -Quiet comme --quiet (un script
# "avance" transformerait -v en -Verbose et refuserait les formes --xxx).
$Quiet = $false; $Doctor = $false; $InstallSystemDeps = $false
$NoSchedule = $false; $Yes = $false; $Help = $false
foreach ($arg in $args) {
    switch ($arg) {
        { $_ -in "-q", "-quiet", "--quiet" } { $Quiet = $true }
        { $_ -in "-v", "-verbose", "--verbose" } { $Quiet = $false }
        { $_ -in "-doctor", "--doctor" } { $Doctor = $true }
        { $_ -in "-installsystemdeps", "--install-system-deps" } { $InstallSystemDeps = $true }
        { $_ -in "-noschedule", "--no-schedule" } { $NoSchedule = $true }
        { $_ -in "-y", "-yes", "--yes" } { $Yes = $true }
        { $_ -in "-h", "-help", "--help", "/?" } { $Help = $true }
        default {
            Write-Host "Option inconnue: $arg"
            Write-Host "Usage: install.cmd [-Quiet] [-Doctor] [-InstallSystemDeps] [-NoSchedule] [-Yes]"
            exit 1
        }
    }
}

if ($Help) {
    Write-Host "Usage: install.cmd [OPTIONS]"
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  -Quiet              Masque la sortie detaillee des commandes"
    Write-Host "  -Doctor             Diagnostic uniquement, aucune modification systeme"
    Write-Host "  -InstallSystemDeps  Installe les prerequis manquants (Python, Docker Desktop) via winget"
    Write-Host "  -NoSchedule         N'installe pas la collecte automatique (Planificateur de taches)"
    Write-Host "  -Yes                Mode non-interactif (valide les modifs systeme)"
    exit 0
}

$VerboseMode = -not $Quiet
$Root = $PSScriptRoot
Set-Location $Root

# UTF-8 pour tous les processus Python lances (et leurs enfants) : Windows
# lirait sinon les fichiers UTF-8 du depot en cp1252.
$env:PYTHONUTF8 = "1"

$VenvPython = Join-Path $Root "venv\Scripts\python.exe"
$UiVenvPython = Join-Path $Root "ui\venv\Scripts\python.exe"

# =============================================================================
# FONCTIONS UTILITAIRES
# =============================================================================
function Write-Header($msg) {
    Write-Host ""
    Write-Host "=======================================================================" -ForegroundColor Blue
    Write-Host "  $msg" -ForegroundColor Cyan
    Write-Host "=======================================================================" -ForegroundColor Blue
    Write-Host ""
}
function Write-Step($msg)    { Write-Host "[OK] " -ForegroundColor Green -NoNewline; Write-Host $msg }
function Write-Warn($msg)    { Write-Host "[WARN] " -ForegroundColor Yellow -NoNewline; Write-Host $msg }
function Write-Err($msg)     { Write-Host "[ERROR] " -ForegroundColor Red -NoNewline; Write-Host $msg }
function Write-Info($msg)    { Write-Host "[INFO] " -ForegroundColor Blue -NoNewline; Write-Host $msg }
function Write-Detail($msg)  { if ($VerboseMode) { Write-Host "  >> $msg" -ForegroundColor Cyan } }

function Test-Command($name) {
    return $null -ne (Get-Command $name -ErrorAction SilentlyContinue)
}

# Execute une commande native ; en mode -Quiet, sa sortie est masquee.
# Retourne $true si le code de sortie vaut 0.
function Invoke-Quietly([scriptblock]$Block) {
    if ($VerboseMode) { & $Block | Out-Host } else { & $Block *> $null }
    return ($LASTEXITCODE -eq 0)
}

function Confirm-SystemChange([string]$Message) {
    if ($InstallSystemDeps -or $Yes) { return $true }
    Write-Host ""
    Write-Warn $Message
    $answer = Read-Host "Autoriser cette modification systeme ? (o/N)"
    return ($answer -match '^[OoYy]$')
}

function Read-YesNo([string]$Prompt) {
    $answer = Read-Host "$Prompt (o/N)"
    return ($answer -match '^[OoYy]$')
}

function Read-Secret([string]$Prompt) {
    $secure = Read-Host $Prompt -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

# Fichiers lus par Python (dotenv) et Docker Compose : UTF-8 SANS BOM et fins
# de ligne LF. Set-Content -Encoding utf8 de PowerShell 5.1 ajoute un BOM.
function Write-TextFile([string]$Path, [string]$Content) {
    $full = if ([IO.Path]::IsPathRooted($Path)) { $Path } else { Join-Path $Root $Path }
    [IO.File]::WriteAllText($full, $Content.Replace("`r`n", "`n"), (New-Object Text.UTF8Encoding $false))
}

function Add-TextLine([string]$Path, [string]$Line) {
    $full = if ([IO.Path]::IsPathRooted($Path)) { $Path } else { Join-Path $Root $Path }
    [IO.File]::AppendAllText($full, "$Line`n", (New-Object Text.UTF8Encoding $false))
}

# Equivalent de chmod 600 : heritage coupe, seul l'utilisateur courant garde
# l'acces (les .env contiennent le mot de passe DB et parfois des cles AWS).
function Protect-File([string]$Path) {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $Path /inheritance:r /grant:r "*${sid}:F" *> $null
}

function Get-EnvVar([string]$Key, [string]$File = ".env") {
    if (-not (Test-Path $File)) { return "" }
    $line = Get-Content $File | Where-Object { $_ -match "^$([regex]::Escape($Key))=" } | Select-Object -Last 1
    if ($null -eq $line) { return "" }
    return $line.Substring($Key.Length + 1)
}

# Pose KEY=VALUE dans un fichier .env : remplace la ligne si la cle existe,
# l'ajoute sinon.
function Set-EnvKv([string]$File, [string]$Key, [string]$Value) {
    $lines = @()
    if (Test-Path $File) { $lines = @(Get-Content $File) }
    $found = $false
    $out = foreach ($l in $lines) {
        if ($l -match "^$([regex]::Escape($Key))=") { $found = $true; "$Key=$Value" } else { $l }
    }
    if (-not $found) { $out = @($out) + "$Key=$Value" }
    Write-TextFile $File ((@($out) -join "`n") + "`n")
}

# Le PATH de la session ne voit pas les logiciels installes par winget
# pendant qu'elle tourne : on le relit depuis le registre.
function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Install-WithWinget([string]$Id, [string]$Label, [string[]]$Extra = @()) {
    if (-not (Test-Command winget)) {
        Write-Err "winget introuvable : installez $Label manuellement"
        return $false
    }
    Write-Info "Installation de $Label via winget ($Id)..."
    & winget install -e --id $Id --accept-package-agreements --accept-source-agreements @Extra
    $ok = ($LASTEXITCODE -eq 0)
    Update-SessionPath
    return $ok
}

# =============================================================================
# PYTHON : py launcher, puis python / python3. L'alias "python.exe" du
# Microsoft Store (WindowsApps) n'est qu'un raccourci vers le Store : il sort
# en erreur sans rien imprimer, et est ecarte par le test d'execution.
# =============================================================================
$script:PythonExe = $null
$script:PythonVersion = $null

function Find-Python {
    $probe = "import sys; print('%d.%d|%s' % (sys.version_info[0], sys.version_info[1], sys.executable))"
    $candidates = @(@("py", "-3"), @("python"), @("python3"))
    $best = $null
    foreach ($c in $candidates) {
        if (-not (Test-Command $c[0])) { continue }
        $cmdArgs = @($c | Select-Object -Skip 1) + @("-c", $probe)
        $out = & $c[0] @cmdArgs 2>$null | Select-Object -First 1
        if ($LASTEXITCODE -ne 0 -or -not $out) { continue }
        $parts = "$out".Trim().Split('|', 2)
        $ver = [version]$parts[0]
        if ($ver -ge [version]"3.11") {
            $script:PythonExe = $parts[1]
            $script:PythonVersion = $parts[0]
            return $true
        }
        if ($null -eq $best -or $ver -gt [version]$best) { $best = $parts[0] }
    }
    if ($best) { $script:PythonVersion = $best }
    return $false
}

# =============================================================================
# DOCKER : Docker Desktop (backend WSL2 ou Hyper-V, gere par Docker lui-meme)
# =============================================================================
$script:ComposeV1 = $false

function Invoke-Compose {
    if ($script:ComposeV1) { & docker-compose @args } else { & docker compose @args }
}

function Test-DockerUp {
    & docker info *> $null
    return ($LASTEXITCODE -eq 0)
}

function Start-DockerDesktop {
    $exe = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $exe)) { return $false }
    Write-Info "Demarrage de Docker Desktop (jusqu'a 3 min au premier lancement)..."
    Start-Process $exe | Out-Null
    for ($i = 0; $i -lt 90; $i++) {
        if (Test-DockerUp) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Initialize-Docker {
    if (-not (Test-Command docker)) {
        if (-not (Confirm-SystemChange "Docker est absent. Le script peut installer Docker Desktop via winget (droits administrateur demandes).")) {
            Write-Err "Docker requis. Installez Docker Desktop (https://www.docker.com/products/docker-desktop/) puis relancez."
            return $false
        }
        if (-not (Install-WithWinget "Docker.DockerDesktop" "Docker Desktop")) {
            Write-Err "Installation de Docker Desktop echouee"
            return $false
        }
        Write-Warn "Docker Desktop installe. Un redemarrage de Windows peut etre necessaire (WSL2)."
        # Le CLI est livre avec Docker Desktop mais pas toujours visible
        # dans le PATH de la session courante.
        $dockerBin = Join-Path $env:ProgramFiles "Docker\Docker\resources\bin"
        if ((Test-Path $dockerBin) -and ($env:Path -notlike "*$dockerBin*")) { $env:Path = "$dockerBin;$env:Path" }
        if (-not (Test-Command docker)) {
            Write-Err "Docker installe mais introuvable dans le PATH. Ouvrez un nouveau terminal puis relancez install.cmd"
            return $false
        }
    }
    if (-not (Test-DockerUp)) {
        if (-not (Start-DockerDesktop)) {
            Write-Err "Docker installe mais non demarre."
            Write-Info "Lancez Docker Desktop, attendez 'Engine running', puis relancez install.cmd"
            return $false
        }
    }
    Write-Step "Docker detecte et fonctionnel"
    & docker compose version *> $null
    if ($LASTEXITCODE -ne 0) {
        if (Test-Command docker-compose) { $script:ComposeV1 = $true }
        else {
            Write-Err "Docker Compose introuvable (inclus dans Docker Desktop : mettez-le a jour)"
            return $false
        }
    }
    Write-Step "Docker Compose detecte"
    return $true
}

# =============================================================================
# POSTGRESQL
# =============================================================================
function Test-PortConflict {
    $port = if ($env:DB_PORT) { [int]$env:DB_PORT } else { 5432 }
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
        $owner = (Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue).ProcessName
        # Docker Desktop publie lui-meme le port : pas un conflit.
        if ($owner -notmatch '^(com\.docker\.backend|wslrelay|vpnkit|docker)') {
            Write-Warn "Le port $port semble deja utilise sur l'hote (processus: $owner)"
            Write-Info "Si le demarrage echoue, changez DB_PORT dans .env (ex: DB_PORT=5433)"
        }
    }
}

function Write-PostgresDebug {
    Write-Err "PostgreSQL indisponible apres attente"
    Write-Host ""
    Write-Info "Etat Docker Compose:"; Invoke-Compose ps 2>&1 | Out-Host
    Write-Host ""
    Write-Info "Logs PostgreSQL (120 dernieres lignes):"
    & docker logs --tail=120 wasteless-postgres 2>&1 | Out-Host
    Write-Host ""
    Write-Info "Causes probables: port $($env:DB_PORT) occupe - DB_PASSWORD different du volume existant - volume postgres corrompu"
}

function Wait-Postgres([int]$Timeout = 120) {
    Write-Info "Attente de la disponibilite de PostgreSQL..."
    $user = if ($env:DB_USER) { $env:DB_USER } else { "wasteless" }
    $db = if ($env:DB_NAME) { $env:DB_NAME } else { "wasteless" }
    for ($elapsed = 0; $elapsed -lt $Timeout; $elapsed += 2) {
        & docker exec wasteless-postgres pg_isready -U $user -d $db *> $null
        if ($LASTEXITCODE -eq 0) { Write-Step "PostgreSQL est pret"; return $true }
        $running = & docker inspect -f '{{.State.Running}}' wasteless-postgres 2>$null
        if ($LASTEXITCODE -eq 0 -and "$running".Trim() -ne "true") { Write-PostgresDebug; return $false }
        Start-Sleep -Seconds 2
    }
    Write-PostgresDebug
    return $false
}

function Start-PostgresContainer {
    $out = Invoke-Compose up -d postgres 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) { return $true }
    Write-Err "Echec du demarrage de PostgreSQL :"
    Write-Host $out
    return $false
}

# =============================================================================
# ENVIRONNEMENTS PYTHON
# =============================================================================
$script:UseUv = $false

function New-Venv([string]$Path) {
    if ($script:UseUv) { & uv venv $Path --python $script:PythonExe | Out-Host }
    else { & $script:PythonExe -m venv $Path | Out-Host }
    return ($LASTEXITCODE -eq 0)
}

function Install-Deps([string]$VenvPy, [string[]]$PipArgs) {
    $q = if ($VerboseMode) { @() } else { @("-q") }
    if ($script:UseUv) {
        return (Invoke-Quietly { & uv pip install --python $VenvPy @PipArgs @q })
    }
    # python -m pip (et non pip.exe) : sous Windows pip.exe ne peut pas se
    # mettre a jour lui-meme, son executable est verrouille.
    return (Invoke-Quietly { & $VenvPy -m pip install @PipArgs @q })
}

function Initialize-Venv([string]$Path, [string]$Label, [bool]$AskRecreate) {
    $py = Join-Path $Root "$Path\Scripts\python.exe"
    if (Test-Path $Path) {
        & $py -c "import sys" *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "Environnement virtuel $Label corrompu detecte - recreation automatique"
            Remove-Item $Path -Recurse -Force
        } elseif ($AskRecreate) {
            Write-Warn "Environnement virtuel $Label existant detecte"
            if (Read-YesNo "Voulez-vous le recreer?") { Remove-Item $Path -Recurse -Force }
            else { Write-Step "Environnement virtuel $Label conserve"; return $true }
        } else {
            Write-Step "Environnement virtuel $Label deja present"
            return $true
        }
    }
    if (-not (New-Venv $Path)) { Write-Err "Creation de l'environnement virtuel $Label echouee"; return $false }
    Write-Step "Environnement virtuel $Label cree"
    if (-not $script:UseUv) { Install-Deps $py @("--upgrade", "pip") | Out-Null }
    return $true
}

# =============================================================================
# BANNIERE
# =============================================================================
Clear-Host
Write-Host @"
 __        __        _       _
 \ \      / /_ _ ___| |_ ___| | ___  ___ ___
  \ \ /\ / / _`` / __| __/ _ \ |/ _ \/ __/ __|
   \ V  V / (_| \__ \ ||  __/ |  __/\__ \__ \
    \_/\_/ \__,_|___/\__\___|_|\___||___/___/

    Cloud Cost Optimization Platform
"@ -ForegroundColor Cyan
Write-Host ""
Write-Host "Version 1.0 - Installation automatique (Windows)"
if ($VerboseMode) { Write-Host "Mode verbose active - sortie detaillee des commandes" -ForegroundColor Cyan }
Write-Host ""

# =============================================================================
# 1/7 - VERIFICATION DES PREREQUIS
# =============================================================================
Write-Header "1/7 - Verification des prerequis"

$MissingDeps = $false
$os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
Write-Info "Systeme: $($os.Caption) $($os.Version) ($env:PROCESSOR_ARCHITECTURE)"

# Python 3.11+
if (Find-Python) {
    Write-Step "Python $script:PythonVersion detecte ($script:PythonExe)"
} else {
    if ($script:PythonVersion) { Write-Err "Python 3.11+ requis (trouve: $script:PythonVersion)" }
    else { Write-Err "Python non trouve (l'alias 'python' du Microsoft Store ne compte pas)" }
    if (-not $Doctor -and (Confirm-SystemChange "Le script peut installer Python 3.12 pour l'utilisateur courant via winget.")) {
        if ((Install-WithWinget "Python.Python.3.12" "Python 3.12" @("--scope", "user")) -and (Find-Python)) {
            Write-Step "Python $script:PythonVersion installe ($script:PythonExe)"
        } else {
            Write-Err "Installation de Python echouee - https://www.python.org/downloads/windows/"
            $MissingDeps = $true
        }
    } else {
        Write-Info "Installation manuelle: winget install -e --id Python.Python.3.12"
        $MissingDeps = $true
    }
}

# Docker + Docker Compose
if ($Doctor) {
    if ((Test-Command docker) -and (Test-DockerUp)) {
        Write-Step "Docker detecte et fonctionnel"
        & docker compose version *> $null
        if ($LASTEXITCODE -eq 0) { Write-Step "Docker Compose detecte" }
        else { Write-Warn "Docker Compose introuvable"; $MissingDeps = $true }
    } elseif (Test-Command docker) {
        Write-Warn "Docker installe mais non demarre (lancez Docker Desktop)"
        $MissingDeps = $true
    } else {
        Write-Warn "Docker absent (relancez avec -InstallSystemDeps pour installer Docker Desktop)"
        $MissingDeps = $true
    }
} elseif (-not (Initialize-Docker)) {
    $MissingDeps = $true
}

# uv (optionnel - installations plus rapides)
if (Test-Command uv) {
    Write-Step "uv detecte ($(& uv --version)) - installations atomiques activees"
    $script:UseUv = $true
} else {
    Write-Warn "uv non trouve (optionnel) - utilisation de pip"
    Write-Info "Installez uv: winget install -e --id astral-sh.uv"
}

# AWS CLI (optionnel)
if (Test-Command aws) { Write-Step "AWS CLI detecte" }
else {
    Write-Warn "AWS CLI non trouve (optionnel)"
    Write-Info "Installez AWS CLI: winget install -e --id Amazon.AWSCLI"
}

# Steampipe : pas de version Windows native. Les detecteurs 7-14 (ELB, NAT,
# VPC, gp2->gp3, AMI, RDS) sont ignores et la collecte marquee partielle.
if (Test-Command steampipe) { Write-Step "Steampipe detecte" }
else {
    Write-Warn "Steampipe indisponible sous Windows natif : detecteurs ELB/NAT/VPC/gp2/AMI/RDS ignores (collecte partielle)"
    Write-Info "Pour la couverture complete, installez WasteLess sous WSL2 (voir README)"
}

# Git (optionnel)
if (Test-Command git) { Write-Step "Git detecte" } else { Write-Warn "Git non trouve (optionnel)" }

if ($MissingDeps) {
    Write-Host ""
    Write-Err "Certains prerequis sont manquants. Installez-les et reexecutez install.cmd"
    Write-Info "Installation automatique : install.cmd -InstallSystemDeps"
    exit 1
}

Write-Host ""
Write-Step "Tous les prerequis sont satisfaits"

if ($Doctor) {
    Write-Host ""
    Write-Step "Diagnostic termine (-Doctor) - aucune modification effectuee"
    exit 0
}

# =============================================================================
# 2/7 - ENVIRONNEMENT PYTHON
# =============================================================================
Write-Header "2/7 - Configuration de l'environnement Python"

if (-not (Initialize-Venv "venv" "backend" $true)) { exit 1 }

Write-Detail "installation des dependances (requirements.lock)"
if (-not (Install-Deps $VenvPython @("-r", "requirements.lock"))) {
    Write-Err "Installation des dependances echouee"
    exit 1
}

# Outils de lint (requirements-dev.lock) : utiles aux contributeurs, pas au
# fonctionnement. Un echec ici n'interrompt pas l'installation.
Write-Detail "installation des outils de lint (requirements-dev.lock)"
if (-not (Install-Deps $VenvPython @("-r", "requirements-dev.lock"))) {
    Write-Warn "Outils de lint non installes (ruff/black/mypy) - sans effet sur WasteLess"
}

Write-Detail "installation du backend en editable dans venv (pyproject.toml)"
if (-not (Install-Deps $VenvPython @("-e", "."))) {
    Write-Err "Installation du backend echouee"
    exit 1
}
Write-Step "Dependances Python installees"

# =============================================================================
# 3/7 - CONFIGURATION (.env)
# =============================================================================
Write-Header "3/7 - Configuration de l'application"

if (-not (Test-Path "config\remediation.yaml")) {
    Copy-Item "config\remediation.yaml.template" "config\remediation.yaml"
    Write-Step "config\remediation.yaml cree depuis le template (remediation desactivee)"
}

$SkipEnvConfig = $false
if (Test-Path ".env") {
    Write-Warn "Fichier .env existant detecte"
    if (-not (Read-YesNo "Voulez-vous le reconfigurer?")) {
        Write-Step "Configuration existante conservee"
        $SkipEnvConfig = $true
    }
}

$AwsConfigured = $false
$AWS_ROLE_ARN = ""; $AWS_WRITE_ROLE_ARN = ""; $AWS_EXTERNAL_ID = ""
$AWS_ACCESS_KEY_ID = ""; $AWS_SECRET_ACCESS_KEY = ""
$AWS_ACCOUNT_ID = ""; $AWS_REGION = ""

if (-not $SkipEnvConfig) {
    Write-Host ""
    Write-Info "Configuration de la base de donnees"
    Write-Host ""

    # Meme charset restreint que install.sh : le mot de passe transite par
    # .env et par la substitution ${DB_PASSWORD} de docker-compose.
    while ($true) {
        $DB_PASSWORD = Read-Secret "Creez un mot de passe pour la base de donnees"
        if ($DB_PASSWORD.Length -lt 8) { Write-Err "Le mot de passe doit contenir au moins 8 caracteres"; continue }
        if ($DB_PASSWORD -notmatch '^[A-Za-z0-9_@%+=:,.~-]+$') {
            Write-Err "Caracteres autorises: lettres, chiffres et _ @ % + = : , . ~ -"
            continue
        }
        $confirm = Read-Secret "Confirmez le mot de passe"
        if ($DB_PASSWORD -cne $confirm) { Write-Err "Les deux saisies ne correspondent pas, recommencez"; continue }
        break
    }

    Write-Host ""
    Write-Info "Configuration AWS"
    Write-Host ""

    $AWS_REGION = Read-Host "Region AWS [eu-west-1]"
    if (-not $AWS_REGION) { $AWS_REGION = "eu-west-1" }

    $AWS_ACCOUNT_ID = Read-Host "AWS Account ID (12 chiffres)"
    while ($AWS_ACCOUNT_ID -notmatch '^[0-9]{12}$') {
        Write-Err "L'Account ID doit contenir exactement 12 chiffres"
        $AWS_ACCOUNT_ID = Read-Host "AWS Account ID (12 chiffres)"
    }

    Write-Host ""
    Write-Host "  Connexion du compte AWS"
    Write-Host "  Wasteless lit votre compte via un role IAM read-only, a creer en une etape"
    Write-Host "  avec le template fourni (dossier onboarding\, CloudFormation ou Terraform)."
    Write-Host ""
    Write-Host "    1) Les roles sont crees, j'ai leurs ARN sous la main -> je les colle maintenant"
    Write-Host "    2) Pas encore -> terminer l'installation d'abord. Le navigateur s'ouvrira"
    Write-Host "       sur le guide de connexion (/setup). C'est le choix par defaut."
    Write-Host "    3) Coller des cles d'acces IAM directes (deconseille : wasteless obtient"
    Write-Host "       alors tous les droits de ces cles, au lieu d'etre limite au role read-only)"

    while ($true) {
        $choice = Read-Host "  Votre choix [1/2/3, defaut 2]"
        if ($choice -eq "1") {
            $AWS_ROLE_ARN = Read-Host "ARN du role read-only (arn:aws:iam::${AWS_ACCOUNT_ID}:role/wasteless-readonly)"
            while ($AWS_ROLE_ARN -notmatch '^arn:aws:iam::[0-9]{12}:role/.+$') {
                Write-Err "Format attendu: arn:aws:iam::<12 chiffres>:role/<nom>"
                $AWS_ROLE_ARN = Read-Host "ARN du role read-only"
            }
            $AWS_WRITE_ROLE_ARN = Read-Host "ARN du role remediation (optionnel, Entree pour passer)"
            $AWS_EXTERNAL_ID = Read-Host "ExternalId (optionnel)"
            Write-Host ""
            Write-Info "Credentials source pour assumer le role (laissez vide si %USERPROFILE%\.aws est deja configure)"
            $AWS_ACCESS_KEY_ID = Read-Host "AWS Access Key ID"
            if ($AWS_ACCESS_KEY_ID) { $AWS_SECRET_ACCESS_KEY = Read-Secret "AWS Secret Access Key" }
            $AwsConfigured = $true
        } elseif ($choice -eq "3") {
            $AWS_ACCESS_KEY_ID = Read-Host "AWS Access Key ID"
            while (-not $AWS_ACCESS_KEY_ID) { $AWS_ACCESS_KEY_ID = Read-Host "AWS Access Key ID (requis pour ce choix)" }
            $AWS_SECRET_ACCESS_KEY = Read-Secret "AWS Secret Access Key"
            $AwsConfigured = $true
        } else {
            Write-Step "Connexion AWS reportee - elle se fera dans le navigateur en fin d'installation (/setup)"
            break
        }

        # Validation immediate (STS + AssumeRole si role fourni), variables
        # WL_CHECK_* dediees comme dans install.sh.
        Write-Info "Validation de la connexion AWS..."
        $env:WL_CHECK_KEY = $AWS_ACCESS_KEY_ID
        $env:WL_CHECK_SECRET = $AWS_SECRET_ACCESS_KEY
        $env:WL_CHECK_REGION = $AWS_REGION
        $env:WL_CHECK_ROLE_ARN = $AWS_ROLE_ARN
        $env:WL_CHECK_EXTERNAL_ID = $AWS_EXTERNAL_ID
        $check = @'
import os
import boto3

session = boto3.Session(
    aws_access_key_id=os.environ.get("WL_CHECK_KEY") or None,
    aws_secret_access_key=os.environ.get("WL_CHECK_SECRET") or None,
    region_name=os.environ.get("WL_CHECK_REGION") or "eu-west-1",
)
sts = session.client("sts")
ident = sts.get_caller_identity()
print(f"identite source : {ident['Arn']}")
role = os.environ.get("WL_CHECK_ROLE_ARN")
if role:
    kwargs = {"RoleArn": role, "RoleSessionName": "wasteless-install-check"}
    if os.environ.get("WL_CHECK_EXTERNAL_ID"):
        kwargs["ExternalId"] = os.environ["WL_CHECK_EXTERNAL_ID"]
    sts.assume_role(**kwargs)
    print(f"assume-role : OK ({role})")
'@
        $output = $check | & $VenvPython - 2>&1 | ForEach-Object { "$_" }
        $ok = ($LASTEXITCODE -eq 0)
        Remove-Item Env:WL_CHECK_KEY, Env:WL_CHECK_SECRET, Env:WL_CHECK_REGION, Env:WL_CHECK_ROLE_ARN, Env:WL_CHECK_EXTERNAL_ID -ErrorAction SilentlyContinue
        if ($ok) {
            Write-Step "Connexion AWS verifiee"
            $output | ForEach-Object { Write-Host "    $_" }
            break
        }
        Write-Err "Validation AWS echouee :"
        $output | Select-Object -Last 2 | ForEach-Object { Write-Host "    $_" }
        if (-not (Read-YesNo "Ressaisir les valeurs ?")) {
            Write-Warn "Valeurs enregistrees telles quelles - corrigez-les via la page /setup de l'interface (ou dans .env et ui\.env)"
            break
        }
        $AWS_ROLE_ARN = ""; $AWS_WRITE_ROLE_ARN = ""; $AWS_EXTERNAL_ID = ""
        $AWS_ACCESS_KEY_ID = ""; $AWS_SECRET_ACCESS_KEY = ""
        $AwsConfigured = $false
    }

    # Insights IA (optionnel, opt-in explicite - saute en mode quiet)
    $LLM_MODEL = ""; $LLM_KEY_VAR = ""; $LLM_API_KEY = ""
    if ($VerboseMode) {
        Write-Host ""
        Write-Info "Insights IA (optionnel)"
        Write-Host "  WasteLess peut expliquer chaque recommandation via un LLM."
        Write-Host "  Les metadonnees de vos ressources AWS seront envoyees au provider choisi."
        Write-Host "  1) DeepSeek   2) Claude (Anthropic)   3) OpenAI   4) Ollama (local, sans cle)"
        Write-Host "  5) Aucun - WasteLess marche sans IA (activable plus tard)"
        $llmChoice = Read-Host "Choisissez un provider [Entree ou 5 = aucun]"
        switch ($llmChoice) {
            "1" { $LLM_MODEL = "deepseek/deepseek-chat"; $LLM_KEY_VAR = "DEEPSEEK_API_KEY" }
            "2" { $LLM_MODEL = "anthropic/claude-haiku-4-5-20251001"; $LLM_KEY_VAR = "ANTHROPIC_API_KEY" }
            "3" { $LLM_MODEL = "openai/gpt-4o-mini"; $LLM_KEY_VAR = "OPENAI_API_KEY" }
            "4" {
                $ollama = Read-Host "Modele Ollama [llama3.1]"
                if (-not $ollama) { $ollama = "llama3.1" }
                $LLM_MODEL = "ollama/$ollama"
            }
            { $_ -in "5", "" } { Write-Step "Insights IA ignores - WasteLess fonctionne sans (activables plus tard: voir .env.template)" }
            default { Write-Warn "Choix invalide - insights IA ignores (activables plus tard: voir .env.template)" }
        }
        if ($LLM_KEY_VAR) { $LLM_API_KEY = Read-Secret "Cle API ($LLM_KEY_VAR)" }
    }

    $envLines = @(
        "# ===========================================",
        "# WasteLess Configuration",
        "# Generated: $(Get-Date)",
        "# ===========================================",
        "",
        "# Database",
        "DB_HOST=localhost",
        "DB_PORT=5432",
        "DB_NAME=wasteless",
        "DB_USER=wasteless",
        "DB_PASSWORD=$DB_PASSWORD",
        "",
        "# AWS",
        "AWS_REGION=$AWS_REGION",
        "AWS_ACCOUNT_ID=$AWS_ACCOUNT_ID"
    )
    if ($AWS_ROLE_ARN) {
        $envLines += "AWS_ROLE_ARN=$AWS_ROLE_ARN"
        if ($AWS_WRITE_ROLE_ARN) { $envLines += "AWS_WRITE_ROLE_ARN=$AWS_WRITE_ROLE_ARN" }
        if ($AWS_EXTERNAL_ID) { $envLines += "AWS_EXTERNAL_ID=$AWS_EXTERNAL_ID" }
    }
    if ($AWS_ACCESS_KEY_ID) {
        $envLines += "AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID"
        $envLines += "AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY"
    }
    $envLines += @("", "# Application", "LOG_LEVEL=INFO", "DRY_RUN=true")
    if ($LLM_MODEL) {
        $envLines += @("", "# AI insights (litellm - see .env.template for other providers)", "WASTELESS_LLM_MODEL=$LLM_MODEL")
        if ($LLM_API_KEY) { $envLines += "$LLM_KEY_VAR=$LLM_API_KEY" }
    }
    Write-TextFile ".env" (($envLines -join "`n") + "`n")
    Protect-File (Join-Path $Root ".env")
    Write-Step "Fichier .env cree et securise"

    # Installation + test de la config LLM (fail-soft)
    if ($LLM_MODEL) {
        Write-Info "Installation de litellm..."
        if (-not (Install-Deps $VenvPython @("litellm"))) {
            Write-Warn "Installation de litellm echouee - installez-la manuellement: venv\Scripts\python.exe -m pip install litellm"
        } else {
            Write-Info "Test de la configuration LLM ($LLM_MODEL)..."
            $llmTest = @'
import os, sys
from dotenv import load_dotenv
load_dotenv('.env')
try:
    import litellm
    litellm.completion(
        model=os.getenv('WASTELESS_LLM_MODEL'),
        messages=[{'role': 'user', 'content': 'ping'}],
        max_tokens=5, timeout=20)
except Exception as e:
    print(f"  {e}", file=sys.stderr)
    sys.exit(1)
'@
            $llmTest | & $VenvPython - 2>&1 | Out-Host
            if ($LASTEXITCODE -eq 0) { Write-Step "Insights IA configures et testes" }
            else {
                Write-Warn "Le test LLM a echoue - verifiez la cle dans .env"
                Write-Info "L'installation continue: les recommandations n'auront simplement pas d'insight IA"
            }
        }
    }
}

# =============================================================================
# 4/7 - BASE DE DONNEES
# =============================================================================
Write-Header "4/7 - Demarrage de la base de donnees"

if (-not (Test-Path ".env")) { Write-Err "Fichier .env introuvable"; exit 1 }
foreach ($k in "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "AWS_REGION") {
    Set-Item -Path "Env:$k" -Value (Get-EnvVar $k)
}
$DB_PASSWORD = $env:DB_PASSWORD
if (-not $AWS_REGION) { $AWS_REGION = $env:AWS_REGION }

Test-PortConflict

Write-Info "Demarrage de PostgreSQL via Docker Compose..."
$names = @(& docker ps --format '{{.Names}}')
$allNames = @(& docker ps -a --format '{{.Names}}')
if ($names -contains "wasteless-postgres") {
    Write-Warn "PostgreSQL deja en cours d'execution"
} elseif ($allNames -contains "wasteless-postgres") {
    Write-Warn "Conteneur wasteless-postgres existant detecte (arrete) - tentative de redemarrage..."
    if (Invoke-Quietly { & docker start wasteless-postgres }) {
        Write-Step "PostgreSQL redemarre"
    } else {
        Write-Warn "Redemarrage echoue (volumes invalides) - suppression et recreation du conteneur..."
        Invoke-Quietly { & docker rm wasteless-postgres } | Out-Null
        if (-not (Start-PostgresContainer)) { exit 1 }
        Write-Step "PostgreSQL recree et demarre"
    }
} else {
    if (-not (Start-PostgresContainer)) { exit 1 }
    Write-Step "PostgreSQL demarre"
}

if (-not (Wait-Postgres 120)) { exit 1 }

# =============================================================================
# 5/7 - SCHEMA
# =============================================================================
Write-Header "5/7 - Initialisation du schema de base de donnees"

# Ordre identique au glob de install.sh : ec2_metrics.sql puis migrations/
# par nom. Chaque fichier est copie dans le conteneur puis execute avec -f :
# un pipe PowerShell -> docker reencoderait le SQL (page de code ANSI).
$migrations = @(Get-Item "sql\ec2_metrics.sql") + @(Get-ChildItem "sql\migrations\*.sql" | Sort-Object Name)
Write-Info "Application de $($migrations.Count) migration(s)..."
$dbUser = if ($env:DB_USER) { $env:DB_USER } else { "wasteless" }
$dbName = if ($env:DB_NAME) { $env:DB_NAME } else { "wasteless" }
$num = 0
foreach ($m in $migrations) {
    $num++
    Write-Host -NoNewline "  [$num/$($migrations.Count)] $($m.Name)..." -ForegroundColor Blue
    $start = Get-Date
    $relative = Resolve-Path -Relative $m.FullName
    & docker cp $relative "wasteless-postgres:/tmp/wl_migration.sql" *> $null
    if ($VerboseMode) {
        Write-Host ""
        & docker exec wasteless-postgres psql -v ON_ERROR_STOP=1 -U $dbUser -d $dbName -f /tmp/wl_migration.sql 2>&1 | Out-Host
    } else {
        & docker exec wasteless-postgres psql -v ON_ERROR_STOP=1 -U $dbUser -d $dbName -f /tmp/wl_migration.sql *> $null
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host " ECHEC" -ForegroundColor Red
        Write-Err "Migration '$($m.Name)' echouee - installation interrompue"
        Write-Info "Relancez en mode verbose pour voir l'erreur SQL: install.cmd (sans -Quiet)"
        exit 1
    }
    Write-Host (" OK ({0}s)" -f [int]((Get-Date) - $start).TotalSeconds) -ForegroundColor Green
}
& docker exec wasteless-postgres rm -f /tmp/wl_migration.sql *> $null
Write-Step "Schema de base de donnees initialise"

# =============================================================================
# 6/7 - INTERFACE WEB
# =============================================================================
Write-Header "6/7 - Installation de l'interface web"

if (-not (Initialize-Venv "ui\venv" "UI" $false)) { exit 1 }

Write-Detail "installation des dependances UI (ui\requirements.lock)"
if (-not (Install-Deps $UiVenvPython @("-r", "ui\requirements.lock"))) {
    Write-Err "Installation des dependances UI echouee"
    exit 1
}
Write-Detail "installation du backend en editable dans ui\venv (pyproject.toml)"
if (-not (Install-Deps $UiVenvPython @("-e", "."))) {
    Write-Err "Installation du backend dans ui\venv echouee"
    exit 1
}
if (Get-EnvVar "WASTELESS_LLM_MODEL") {
    if (-not (Install-Deps $UiVenvPython @("litellm"))) {
        Write-Warn "litellm non installe dans ui\venv - le resume IA de la page Reports sera masque"
    }
}
Write-Step "Dependances UI installees"

$uiEnv = Join-Path $Root "ui\.env"
if (Test-Path $uiEnv) {
    # Toujours resynchronise : chemin du projet, mot de passe DB, compte AWS.
    Set-EnvKv $uiEnv "WASTELESS_BACKEND_PATH" $Root
    Set-EnvKv $uiEnv "DB_PASSWORD" $DB_PASSWORD
    if ($AWS_ACCOUNT_ID) { Set-EnvKv $uiEnv "AWS_ACCOUNT_ID" $AWS_ACCOUNT_ID }
    if (-not $SkipEnvConfig -and $AwsConfigured) {
        Set-EnvKv $uiEnv "AWS_REGION" $AWS_REGION
        if ($AWS_ROLE_ARN) { Set-EnvKv $uiEnv "AWS_ROLE_ARN" $AWS_ROLE_ARN }
        if ($AWS_WRITE_ROLE_ARN) { Set-EnvKv $uiEnv "AWS_WRITE_ROLE_ARN" $AWS_WRITE_ROLE_ARN }
        if ($AWS_EXTERNAL_ID) { Set-EnvKv $uiEnv "AWS_EXTERNAL_ID" $AWS_EXTERNAL_ID }
        if ($AWS_ACCESS_KEY_ID) { Set-EnvKv $uiEnv "AWS_ACCESS_KEY_ID" $AWS_ACCESS_KEY_ID }
        if ($AWS_SECRET_ACCESS_KEY) { Set-EnvKv $uiEnv "AWS_SECRET_ACCESS_KEY" $AWS_SECRET_ACCESS_KEY }
    }
    Write-Step "ui\.env synchronise avec la configuration courante"
} else {
    $region = if ($AWS_REGION) { $AWS_REGION } else { "eu-west-1" }
    $uiLines = @(
        "# WasteLess UI Configuration - Generated: $(Get-Date)",
        "DB_HOST=$(if ($env:DB_HOST) { $env:DB_HOST } else { 'localhost' })",
        "DB_PORT=$(if ($env:DB_PORT) { $env:DB_PORT } else { '5432' })",
        "DB_NAME=$dbName",
        "DB_USER=$dbUser",
        "DB_PASSWORD=$DB_PASSWORD",
        "WASTELESS_BACKEND_PATH=$Root",
        "STREAMLIT_SERVER_PORT=8888",
        "STREAMLIT_SERVER_ADDRESS=localhost",
        "# Adresse d'ecoute de l'UI. 127.0.0.1 par defaut : l'API n'a pas",
        "# d'authentification et ses endpoints POST executent de vraies actions AWS.",
        "# Ne passer a 0.0.0.0 que derriere un reverse proxy authentifie.",
        "WASTELESS_HOST=127.0.0.1",
        "LOG_LEVEL=INFO",
        "AWS_REGION=$region"
    )
    if ($AWS_ACCOUNT_ID) { $uiLines += "AWS_ACCOUNT_ID=$AWS_ACCOUNT_ID" }
    if ($AWS_ROLE_ARN) {
        $uiLines += "AWS_ROLE_ARN=$AWS_ROLE_ARN"
        if ($AWS_WRITE_ROLE_ARN) { $uiLines += "AWS_WRITE_ROLE_ARN=$AWS_WRITE_ROLE_ARN" }
        if ($AWS_EXTERNAL_ID) { $uiLines += "AWS_EXTERNAL_ID=$AWS_EXTERNAL_ID" }
    }
    if ($AWS_ACCESS_KEY_ID) {
        $uiLines += "AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID"
        $uiLines += "AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY"
    }
    Write-TextFile $uiEnv (($uiLines -join "`n") + "`n")
    Write-Step "Configuration UI creee (ui\.env)"
}
Protect-File $uiEnv

# Commande 'wasteless' : un shim .cmd dans %USERPROFILE%\.local\bin (meme
# emplacement que le symlink de install.sh), ajoute au PATH utilisateur.
$BinDir = Join-Path $env:USERPROFILE ".local\bin"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$shim = "@echo off`r`npowershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$Root\wasteless.ps1`" %*`r`nexit /b %ERRORLEVEL%`r`n"
[IO.File]::WriteAllText((Join-Path $BinDir "wasteless.cmd"), $shim, (New-Object Text.ASCIIEncoding))
$markerDir = Join-Path $env:USERPROFILE ".config\wasteless"
New-Item -ItemType Directory -Force -Path $markerDir | Out-Null
Write-TextFile (Join-Path $markerDir "root") "$Root`n"
Write-Step "Commande 'wasteless' installee -> $BinDir\wasteless.cmd"

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
if (($userPath.Split(';') | Where-Object { $_.TrimEnd('\') -ieq $BinDir }).Count -eq 0) {
    $newPath = if ($userPath) { "$userPath;$BinDir" } else { $BinDir }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Step "$BinDir ajoute au PATH utilisateur (nouveaux terminaux)"
}
if ($env:Path -notlike "*$BinDir*") { $env:Path = "$env:Path;$BinDir" }

# =============================================================================
# 7/7 - VERIFICATION FINALE
# =============================================================================
Write-Header "7/7 - Verification de l'installation"

Write-Info "Test de connexion a la base de donnees..."
$dbCheck = "from src.core.database import health_check`nimport sys`nsys.exit(0 if health_check() else 1)"
if (Invoke-Quietly { & $VenvPython -c $dbCheck }) { Write-Step "Connexion base de donnees OK" }
else { Write-Warn "Test de connexion echoue (normal si premiere installation)" }

Write-Info "Verification des modules..."
$modCheck = "from src.core.config import RemediationConfig`nconfig = RemediationConfig.from_yaml('config/remediation.yaml')`nprint('enabled' if config.enabled else 'disabled')"
$modOut = & $VenvPython -c $modCheck 2>&1 | ForEach-Object { "$_" }
if ($LASTEXITCODE -eq 0) {
    Write-Step "Modules Python OK"
    if (($modOut | Select-Object -Last 1) -eq "disabled") { Write-Step "Auto-remediation desactivee (securite)" }
    else {
        Write-Warn "Auto-remediation ACTIVE (heritee de votre configuration existante)"
        Write-Info "Desactivable dans Settings ou config\remediation.yaml (auto_remediation.enabled)"
    }
} else {
    Write-Err "Erreur lors du chargement des modules :"
    $modOut | Select-Object -Last 3 | ForEach-Object { Write-Host "    $_" }
}

Write-Info "Verification des credentials AWS..."
$awsCheck = "import os`nfrom dotenv import load_dotenv`nload_dotenv()`nimport boto3`nsts = boto3.client('sts', region_name=os.getenv('AWS_REGION') or 'eu-west-1')`nsts.get_caller_identity()"
& $VenvPython -c $awsCheck *> $null
$AwsCheckOk = ($LASTEXITCODE -eq 0)
if ($AwsCheckOk) { Write-Step "Credentials AWS valides" }
elseif ((Get-EnvVar "AWS_ACCESS_KEY_ID") -or (Get-EnvVar "AWS_ROLE_ARN")) {
    Write-Warn "Credentials AWS invalides ou inaccessibles"
    Write-Info "Corrigez-les via http://localhost:8888/setup (ou dans .env et ui\.env)"
} else {
    Write-Warn "AWS non configure - le dashboard restera vide"
    Write-Info "Connexion guidee: http://localhost:8888/setup ou docs\CTO_QUICKSTART.md (10 min)"
}

Write-Info "Execution des tests unitaires..."
$pytestOpt = if ($VerboseMode) { "-v" } else { "-q" }
if (Invoke-Quietly { & $VenvPython -m pytest tests\unit\test_validation.py $pytestOpt }) { Write-Step "Tests unitaires OK" }
else { Write-Warn "Certains tests ont echoue" }

# =============================================================================
# RESUME
# =============================================================================
Write-Header "Installation terminee"

Write-Host "WasteLess est installe et pret!" -ForegroundColor Green
Write-Host ""
if ($AwsCheckOk) { Write-Host "  AWS : connecte et verifie" -ForegroundColor Green }
else {
    Write-Host "  AWS : non connecte - le dashboard restera vide tant que ce n'est pas fait." -ForegroundColor Yellow
    Write-Host "        Le navigateur va s'ouvrir sur le guide de connexion (http://localhost:8888/setup)"
    Write-Host "        Guide detaille : docs\CTO_QUICKSTART.md"
}
Write-Host ""
Write-Host "Commandes (dans un nouveau terminal) :"
Write-Host "  wasteless              Demarrer l'interface -> http://localhost:8888"
Write-Host "  wasteless collect      Collecter les metriques et detecter le gaspillage"
Write-Host "  wasteless status       Etat de l'UI et de la collecte automatique"
Write-Host "  wasteless stop         Arreter l'interface"
Write-Host "  docker compose down    Arreter PostgreSQL"
Write-Host ""
Write-Host "Important:" -ForegroundColor Yellow
Write-Host "  L'auto-remediation est DESACTIVEE par defaut."
Write-Host "  Pour l'activer, modifiez config\remediation.yaml"
Write-Host ""

$wastelessPs1 = Join-Path $Root "wasteless.ps1"
if (-not $NoSchedule) {
    Write-Header "Collecte automatique"
    if (Confirm-SystemChange "Installer la collecte automatique toutes les 5 min (Planificateur de taches, survit au reboot) ?") {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $wastelessPs1 schedule
        if ($LASTEXITCODE -ne 0) { Write-Warn "Installation de la tache planifiee echouee - activez-la plus tard: wasteless schedule" }
    } else {
        Write-Info "Collecte automatique non installee. Activez-la quand vous voulez: wasteless schedule"
    }
}

Write-Header "Demarrage de l'interface"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $wastelessPs1 start
if ($LASTEXITCODE -ne 0) { Write-Warn "Demarrage automatique echoue. Lancez manuellement: wasteless" }
