# Instala (si hace falta), genera los datos y levanta la demo en http://127.0.0.1:8000
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creando entorno virtual .venv ..."
    python -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt
}
if (-not (Test-Path "data\synthetic\finanzas.xlsx")) {
    & .\.venv\Scripts\python.exe scripts\generate_demo_data.py
}
Write-Host "Abre http://127.0.0.1:8000  (Ctrl+C para detener)"
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
