#
# Why Docker Desktop cannot run on this machine: dot-sourced by install.ps1,
# only when Docker does not answer (a working Docker needs no diagnosis).
#
# Get-VirtualizationState reads the machine and needs no admin rights.
# Get-VirtualizationIssue only decides, so tests can feed it simulated states;
# a value it could not read stays $null and never counts as a problem.
#
# Fichier ASCII uniquement (Windows PowerShell 5.1 lit les scripts sans BOM en ANSI).
#

function Get-VirtualizationState {
    $state = @{
        FirmwareEnabled   = $null
        HypervisorPresent = $null
        Vmp               = $null
        Wsl               = $null
        HyperV            = $null
        VmCompute         = Test-Path (Join-Path $env:windir "System32\vmcompute.exe")
        RebootPending     = Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending"
    }
    try {
        $state.FirmwareEnabled = (Get-CimInstance Win32_Processor -ErrorAction Stop | Select-Object -First 1).VirtualizationFirmwareEnabled
        $state.HypervisorPresent = (Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).HypervisorPresent
        $features = Get-CimInstance Win32_OptionalFeature -ErrorAction Stop -Filter "Name='VirtualMachinePlatform' OR Name='Microsoft-Windows-Subsystem-Linux' OR Name='Microsoft-Hyper-V-All'"
        foreach ($feature in $features) {
            switch ($feature.Name) {
                "VirtualMachinePlatform" { $state.Vmp = $feature.InstallState }
                "Microsoft-Windows-Subsystem-Linux" { $state.Wsl = $feature.InstallState }
                "Microsoft-Hyper-V-All" { $state.HyperV = $feature.InstallState }
            }
        }
    } catch {
        # Diagnosis is best effort: what could not be read stays $null.
    }
    return $state
}

# The first blocking problem, as @{ Code; Message; Fix }, or $null. Only the
# first: each fix usually needs a restart, which changes what comes next.
function Get-VirtualizationIssue($State) {
    # With the hypervisor running, the firmware flag can read false.
    if ($State.HypervisorPresent -eq $false -and $State.FirmwareEnabled -eq $false) {
        return @{
            Code    = "firmware"
            Message = "La virtualisation est desactivee dans le BIOS/UEFI de cet ordinateur."
            Fix     = "Activez Intel VT-x ou AMD-V (SVM) dans le BIOS/UEFI, puis relancez install.cmd"
        }
    }

    # 1 = enabled. Docker Desktop runs on WSL2 (the two components) or Hyper-V.
    $wslReady = ($State.Vmp -eq 1 -and $State.Wsl -eq 1)
    $unknown = ($null -eq $State.Vmp -and $null -eq $State.Wsl -and $null -eq $State.HyperV)
    if (-not $unknown -and -not $wslReady -and $State.HyperV -ne 1) {
        return @{
            Code    = "features"
            Message = "Les composants Windows de virtualisation (WSL2) ne sont pas actives."
            Fix     = "Dans un terminal administrateur : wsl --install --no-distribution ; redemarrez Windows, puis relancez install.cmd"
        }
    }

    # An enabled feature can still wait for its files: Windows reports it as
    # enabled as soon as the installation is queued.
    if ($State.VmCompute -eq $false) {
        return @{
            Code    = "pending"
            Message = "Les composants de virtualisation sont actives mais pas encore installes par Windows."
            Fix     = "Menu Demarrer > Marche/Arret > 'Mettre a jour et redemarrer' si l'option existe (sinon 'Redemarrer'), laissez Windows terminer, puis relancez install.cmd"
        }
    }

    if ($State.HypervisorPresent -eq $false) {
        if ($State.RebootPending) {
            return @{
                Code    = "reboot"
                Message = "L'hyperviseur Windows n'est pas demarre et un redemarrage est en attente."
                Fix     = "Redemarrez Windows, puis relancez install.cmd"
            }
        }
        return @{
            Code    = "hypervisor"
            Message = "L'hyperviseur Windows ne demarre pas alors que ses composants sont installes."
            Fix     = "Dans un terminal administrateur : bcdedit /set hypervisorlaunchtype auto ; redemarrez Windows, puis relancez install.cmd"
        }
    }
    return $null
}
