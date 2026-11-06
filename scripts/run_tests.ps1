# Regenera los datos sintéticos (deterministas) y corre todas las pruebas
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt
}
& .\.venv\Scripts\python.exe scripts\generate_demo_data.py
& .\.venv\Scripts\python.exe -m pytest backend/tests -v
exit $LASTEXITCODE
