"""Configuración por variables de entorno (con lectura opcional de un archivo .env en la raíz)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]


def _leer_dotenv() -> None:
    env = RAIZ / ".env"
    if not env.exists():
        return
    for linea in env.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip())


@dataclass(frozen=True)
class Config:
    modo_agente: str
    ollama_url: str
    ollama_modelo: str
    fecha_referencia: date
    datos_dir: Path
    generado_dir: Path
    moneda: str = "PEN"
    zona_horaria: str = "America/Lima"


def cargar_config() -> Config:
    _leer_dotenv()
    datos = Path(os.environ.get("DATOS_DIR", "data/synthetic"))
    generado = Path(os.environ.get("GENERADO_DIR", "data/generated"))
    return Config(
        modo_agente=os.environ.get("MODO_AGENTE", "reglas").strip().lower(),
        ollama_url=os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/"),
        ollama_modelo=os.environ.get("OLLAMA_MODELO", "llama3.1"),
        fecha_referencia=date.fromisoformat(os.environ.get("FECHA_REFERENCIA", "2026-10-31")),
        datos_dir=datos if datos.is_absolute() else RAIZ / datos,
        generado_dir=generado if generado.is_absolute() else RAIZ / generado,
    )
