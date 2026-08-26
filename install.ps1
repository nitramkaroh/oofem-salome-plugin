[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$LegacyToolsPlugin,
    [string]$TargetDir
)

$ErrorActionPreference = "Stop"

if (-not $TargetDir) {
    if ($env:XDG_CONFIG_HOME) {
        $ConfigHome = $env:XDG_CONFIG_HOME
    } else {
        $ConfigHome = Join-Path $HOME ".config"
    }
    $TargetDir = Join-Path $ConfigHome "salome\Plugins"
}

$SourceDir = Join-Path $PSScriptRoot "src\OOFEMSalomePlugin"
$PackageDir = Join-Path $TargetDir "OOFEMSalomePlugin"
$RegistrationFile = Join-Path $TargetDir "salome_plugins.py"
$Marker = "# >>> OOFEM SALOME plugin >>>"
$EndMarker = "# <<< OOFEM SALOME plugin <<<"

if (-not $Uninstall -and -not $LegacyToolsPlugin) {
    Write-Error "Refusing to install the legacy Tools > Plugins entry implicitly.`nUse .\install-salome-module.ps1 -SalomeDir <path> to install OOFEM as a native SALOME module, or pass -LegacyToolsPlugin to install legacy entry anyway, or -Uninstall to remove it."
    exit 2
}

if ($Uninstall) {
    $Removed = $false
    if (Test-Path -LiteralPath $PackageDir) {
        Remove-Item -LiteralPath $PackageDir -Recurse -Force
        Write-Host "Removed package copy: $PackageDir"
        $Removed = $true
    }
    if (Test-Path -LiteralPath $RegistrationFile) {
        [string]$Content = Get-Content -LiteralPath $RegistrationFile -Raw
        if ($Content.Contains($Marker)) {
            $Pattern = "(?s)\r?\n?" + [regex]::Escape($Marker) + ".*?" + [regex]::Escape($EndMarker) + "\r?\n?"
            $Cleaned = [regex]::Replace($Content, $Pattern, "")
            if ([string]::IsNullOrWhiteSpace($Cleaned)) {
                Remove-Item -LiteralPath $RegistrationFile -Force
                Write-Host "Removed now-empty registration: $RegistrationFile"
            } else {
                Set-Content -LiteralPath $RegistrationFile -Value $Cleaned -Encoding UTF8
                Write-Host "Removed OOFEM block from: $RegistrationFile"
            }
            $Removed = $true
        }
    }
    if ($Removed) {
        Write-Host "Restart SALOME; Tools > Plugins > OOFEM is gone."
    } else {
        Write-Host "Nothing to remove in: $TargetDir"
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $SourceDir -PathType Container)) {
    throw "Plugin package not found: $SourceDir"
}

New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
if (Test-Path -LiteralPath $PackageDir) {
    Remove-Item -LiteralPath $PackageDir -Recurse -Force
}
Copy-Item -LiteralPath $SourceDir -Destination $PackageDir -Recurse

if (-not (Test-Path -LiteralPath $RegistrationFile)) {
    New-Item -ItemType File -Path $RegistrationFile | Out-Null
}

[string]$Registration = Get-Content -LiteralPath $RegistrationFile -Raw
if (-not $Registration.Contains($Marker)) {
    $Block = @"

$Marker
from OOFEMSalomePlugin.plugin_entry import register_plugin as _register_oofem_plugin
_register_oofem_plugin()
$EndMarker
"@
    Add-Content -LiteralPath $RegistrationFile -Value $Block -Encoding UTF8
}

Write-Host "Legacy OOFEM SALOME plugin installed in: $TargetDir"
Write-Host "Restart SALOME, then open Tools > Plugins > OOFEM."
Write-Host "NOTE: This copy is separate from the native module; prefer .\install-salome-module.ps1 unless the native module fails."
