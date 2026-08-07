# Run SkyScope in the EMF badge simulator on Windows. Needs the sim next door:
#   git clone https://github.com/emfcamp/badge-2024-software ..\badge-2024-software
# Windows symlinks need developer mode or admin, so this copies instead.
$ErrorActionPreference = 'Stop'

$app = Split-Path -Parent $PSScriptRoot
$sim = if ($env:SIM_DIR) { $env:SIM_DIR } else { Join-Path (Split-Path -Parent $app) 'badge-2024-software\sim' }

if (-not (Test-Path (Join-Path $sim 'run.py'))) {
    Write-Error "simulator not found at $sim (clone badge-2024-software next door, or set SIM_DIR)"
}

# The sim's override launcher trips a circular import; pre-import the scheduler.
$runPath = Join-Path $sim 'run.py'
$run = Get-Content $runPath -Raw
if ($run -notmatch 'skyscope-sim-fix') {
    $run = $run -replace '(?m)^def replace_launcher\(module_name: str, class_name: str\):\r?\n',
        "def replace_launcher(module_name: str, class_name: str):`n    import system.scheduler  # skyscope-sim-fix`n"
    [IO.File]::WriteAllText($runPath, $run)
}

$dest = Join-Path $sim 'apps\skyscope'
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item (Join-Path $app '*.py') $dest -Force
Copy-Item (Join-Path $app 'metadata.json') $dest -Force
Copy-Item (Join-Path $app 'tildagon.toml') $dest -Force

Push-Location $sim
try { python run.py skyscope.FlightRadarApp } finally { Pop-Location }
