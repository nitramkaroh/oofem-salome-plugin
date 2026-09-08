[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, HelpMessage = "Path to the SALOME installation directory (e.g. C:\SALOME-9.15.0)")]
    [string]$SalomeDir,
    [switch]$LegacyGlobalRegistration
)

$ErrorActionPreference = "Stop"

$SalomeDir = (Resolve-Path -Path $SalomeDir).Path
if (-not (Test-Path -LiteralPath $SalomeDir -PathType Container)) {
    throw "SALOME directory not found: $SalomeDir"
}

$RepoRoot = $PSScriptRoot
$SourcePackage = Join-Path $RepoRoot "src\OOFEMSalomePlugin"
$SourceModule = Join-Path $RepoRoot "module"

if (-not (Test-Path -LiteralPath $SourcePackage -PathType Container)) {
    throw "OOFEM source package not found: $SourcePackage"
}

$ModuleRoot = Join-Path $SalomeDir "INSTALL\OOFEM"
$PythonRoot = Join-Path $ModuleRoot "bin\salome"
$ResourceRoot = Join-Path $ModuleRoot "share\salome\resources\oofem"
$ExtraEnvRoot = Join-Path $SalomeDir "extra.env.d"

Write-Host "Installing OOFEM module files into $ModuleRoot..."

# 1. Ensure target directories exist
New-Item -ItemType Directory -Force -Path $PythonRoot | Out-Null
New-Item -ItemType Directory -Force -Path $ResourceRoot | Out-Null
New-Item -ItemType Directory -Force -Path $ExtraEnvRoot | Out-Null

# 2. Copy Python package and module adapter files
$TargetPackage = Join-Path $PythonRoot "OOFEMSalomePlugin"
if (Test-Path -LiteralPath $TargetPackage) {
    Remove-Item -LiteralPath $TargetPackage -Recurse -Force
}
Copy-Item -LiteralPath $SourcePackage -Destination $TargetPackage -Recurse -Force
Copy-Item -LiteralPath (Join-Path $SourceModule "OOFEMGUI.py") -Destination $PythonRoot -Force
Copy-Item -LiteralPath (Join-Path $SourceModule "oofem_preferences.py") -Destination $PythonRoot -Force
Copy-Item -LiteralPath (Join-Path $SourceModule "register_oofem_user_config.py") -Destination $PythonRoot -Force

# 3. Copy GUI resources and XML
Copy-Item -LiteralPath (Join-Path $SourceModule "SalomeApp.xml") -Destination $ResourceRoot -Force
Copy-Item -LiteralPath (Join-Path $SourceModule "oofem.png") -Destination $ResourceRoot -Force
Copy-Item -LiteralPath (Join-Path $SourceModule "oofem-logo.png") -Destination $ResourceRoot -Force

# 4. Copy launcher extra.env.d hook
Copy-Item -LiteralPath (Join-Path $SourceModule "oofem_env.py") -Destination (Join-Path $ExtraEnvRoot "oofem.py") -Force

# 5. Locate Python executable to run registrar.
# The registrar needs nothing but the standard library and the oofem_preferences
# module sitting beside it, so any Python 3 will do -- but it must be a real one.
# A bare "python" on Windows commonly resolves to the Microsoft Store alias stub,
# which prints "Python was not found" and exits without doing anything.
$PythonCandidates = @(
    (Join-Path $SalomeDir "W64\Python\python3.exe"),
    (Join-Path $SalomeDir "W64\Python\python.exe")
)

$PythonExe = $null
foreach ($Candidate in $PythonCandidates) {
    if (Test-Path -LiteralPath $Candidate) {
        $PythonExe = $Candidate
        break
    }
}

# Those two paths are only where some builds keep it. Rather than guess at more
# layouts, look for it: a SALOME installation always ships a Python somewhere.
if (-not $PythonExe) {
    $Found = Get-ChildItem -LiteralPath $SalomeDir -Recurse -Filter "python*.exe" `
        -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^python(3[\d.]*)?\.exe$' } |
        Select-Object -First 1
    if ($Found) {
        $PythonExe = $Found.FullName
        Write-Host "Found SALOME's Python: $PythonExe"
    }
}

# Last resort: PATH, but only if it is not the Store stub. The stub reports a
# zero-byte-ish executable under WindowsApps; a genuine interpreter answers.
if (-not $PythonExe) {
    $OnPath = Get-Command python -ErrorAction SilentlyContinue
    if ($OnPath -and $OnPath.Source -notlike "*\WindowsApps\*") {
        $PythonExe = $OnPath.Source
    }
}

$Registrar = Join-Path $PythonRoot "register_oofem_user_config.py"

if (-not $PythonExe) {
    Write-Host ""
    Write-Host "Module files are installed, but the per-user GUI registration was NOT done." -ForegroundColor Yellow
    Write-Host "No Python interpreter was found. That step writes OOFEM's module name," -ForegroundColor Yellow
    Write-Host "icon and library into SalomeApprc; without it OOFEM may be missing from" -ForegroundColor Yellow
    Write-Host "the module dropdown. Finish it by hand from SALOME's own shell:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "    $SalomeDir\run_salome_shell.bat"
    Write-Host "    python `"$Registrar`" --salome `"$SalomeDir`""
    Write-Host ""
    throw "No Python interpreter available for the registration step."
}

Write-Host "Registering OOFEM in per-user SALOME GUI configuration..."
& $PythonExe "$Registrar" --salome "$SalomeDir"
if ($LASTEXITCODE -ne 0) {
    # Never fall through to "Installation Complete" after this fails: the copies
    # alone leave a module SALOME may not list, and a green success message sends
    # the user looking for the fault in the wrong place.
    throw ("Registration failed: {0} exited with {1}. " -f $PythonExe, $LASTEXITCODE) +
          "The module files are in place; rerun the registrar by hand from " +
          "$SalomeDir\run_salome_shell.bat"
}

# 6. Global resource registration in SalomeApp.xml
$GlobalXmlCandidates = @(
    (Join-Path $SalomeDir "W64\SALOME\share\salome\resources\salome\SalomeApp.xml"),
    (Join-Path $SalomeDir "INSTALL\SALOME\share\salome\resources\salome\SalomeApp.xml")
)

$IntegrationXmlPath = Join-Path $SourceModule "SalomeApp.integration.xml"
foreach ($GlobalXml in $GlobalXmlCandidates) {
    if (Test-Path -LiteralPath $GlobalXml) {
        [string]$Content = Get-Content -LiteralPath $GlobalXml -Raw
        if (-not $Content.Contains('name="OOFEM"')) {
            $Integration = Get-Content -LiteralPath $IntegrationXmlPath -Raw
            $Updated = $Content.Replace("</document>", "$Integration`n</document>")
            Set-Content -LiteralPath $GlobalXml -Value $Updated -Encoding UTF8
            Write-Host "Registered OOFEM in global SALOME resource: $GlobalXml"
        } else {
            Write-Host "OOFEM is already registered in: $GlobalXml"
        }
        break
    }
}

Write-Host ""
Write-Host "=== OOFEM Native Module Installation Complete ===" -ForegroundColor Green
Write-Host "1. Restart SALOME."
Write-Host "2. Select 'OOFEM' from the module dropdown in the top toolbar."
