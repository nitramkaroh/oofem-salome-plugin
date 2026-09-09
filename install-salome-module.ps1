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

# A leftover Tools > Plugins copy silently defeats everything below:
# salome_pluginsmanager puts its directory at the FRONT of sys.path, so the old
# copy is imported instead of the one being installed here.
$ConfigHome = if ($env:XDG_CONFIG_HOME) { $env:XDG_CONFIG_HOME } else { Join-Path $HOME ".config" }
$SalomeConfigDir = Join-Path $ConfigHome "salome"
$LegacyPluginDir = Join-Path $SalomeConfigDir "Plugins\OOFEMSalomePlugin"
if (Test-Path -LiteralPath $LegacyPluginDir) {
    Write-Host "WARNING: a legacy Tools > Plugins copy is present:" -ForegroundColor Yellow
    Write-Host "  $LegacyPluginDir" -ForegroundColor Yellow
    Write-Host "It is imported before the native module and will shadow it. Remove it with:" -ForegroundColor Yellow
    Write-Host "  .\install.ps1 -Uninstall" -ForegroundColor Yellow
}

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

# Trust the file, not the exit code. SUIT_ResourceMgr reads
# SalomeApp.xml.<version> on Windows and SalomeApprc.<version> elsewhere, and
# a machine carrying both can have OOFEM written into the one the GUI never
# opens -- the module is then simply absent from the selector, with nothing
# reported anywhere. Name the file, so the next person does not have to guess.
$WindowsConfigs = @(Get-ChildItem -LiteralPath $SalomeConfigDir -Filter "SalomeApp.xml.*" `
    -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -notlike "*.before-oofem" })
$PosixConfigs = @(Get-ChildItem -LiteralPath $SalomeConfigDir -Filter "SalomeApprc.*" `
    -File -ErrorAction SilentlyContinue)
$RegisteredIn = @()
foreach ($Candidate in ($WindowsConfigs + $PosixConfigs)) {
    if ((Get-Content -LiteralPath $Candidate.FullName -Raw) -match 'name="OOFEM"') {
        $RegisteredIn += $Candidate
    }
}
if ($RegisteredIn.Count -eq 0) {
    throw "OOFEM is not registered in any per-user resource file under $SalomeConfigDir. " +
          "Rerun: `"$PythonExe`" `"$Registrar`" --salome `"$SalomeDir`""
}
if (-not ($RegisteredIn | Where-Object { $_.Name -like "SalomeApp.xml.*" })) {
    Write-Host ""
    Write-Host "WARNING: OOFEM is registered only in $($RegisteredIn[0].Name)." -ForegroundColor Yellow
    Write-Host "Windows SALOME reads SalomeApp.xml.<version>, so the module will be" -ForegroundColor Yellow
    Write-Host "missing from the selector. Rerun the registrar with a build of this" -ForegroundColor Yellow
    Write-Host "installer that picks the name by platform." -ForegroundColor Yellow
} else {
    Write-Host "Registration verified in $(($RegisteredIn | ForEach-Object { $_.Name }) -join ', ')"
}

# 6. Global resource registration in SalomeApp.xml.
# Only on request. This edits a file shared by every SALOME module, and the
# per-user registration plus the extra.env.d hook are enough on builds that
# read them; -LegacyGlobalRegistration exists for the ones that do not. The
# switch was declared but never tested, so this ran unconditionally -- against
# what the README promises and what the .sh installer does.
if ($LegacyGlobalRegistration) {
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
} else {
    Write-Host "Global SALOME resource left unchanged (pass -LegacyGlobalRegistration to edit it)"
}

Write-Host ""
Write-Host "=== OOFEM Native Module Installation Complete ===" -ForegroundColor Green
Write-Host "1. Restart SALOME."
Write-Host "2. Select 'OOFEM' from the module dropdown in the top toolbar."
