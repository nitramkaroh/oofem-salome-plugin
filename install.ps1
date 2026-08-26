[CmdletBinding()]
param(
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
if (-not (Test-Path -LiteralPath $SourceDir -PathType Container)) {
    throw "Plugin package not found: $SourceDir"
}

$PackageDir = Join-Path $TargetDir "OOFEMSalomePlugin"
$RegistrationFile = Join-Path $TargetDir "salome_plugins.py"
$Marker = "# >>> OOFEM SALOME plugin >>>"

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
# <<< OOFEM SALOME plugin <<<
"@
    Add-Content -LiteralPath $RegistrationFile -Value $Block -Encoding UTF8
}

Write-Host "OOFEM SALOME plugin installed in: $TargetDir"
Write-Host "Restart SALOME, then open Tools > Plugins > OOFEM."
