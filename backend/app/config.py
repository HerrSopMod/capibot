"""Configuración por variables de entorno (con lectura opcional de un archivo .env en la raíz)."""
from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
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
    subidos_dir: Path | None = None
    intervalo_sincronizacion_segundos: int = 60
    vigencia_dias_por_fuente: dict[str, int] = field(default_factory=lambda: {"default": 7, "fiscalizacion": 1})


def cargar_config() -> Config:
    _leer_dotenv()
    datos = Path(os.environ.get("DATOS_DIR", "data/synthetic"))
    generado = Path(os.environ.get("GENERADO_DIR", "data/generated"))
    subidos = Path(os.environ.get("SUBIDOS_DIR", "data/uploads"))
    try:
        intervalo = int(os.environ.get("SINCRONIZACION_INTERVALO_SEGUNDOS", "60"))
    except ValueError as exc:
        raise ValueError("SINCRONIZACION_INTERVALO_SEGUNDOS debe ser un entero no negativo.") from exc
    if intervalo < 0:
        raise ValueError("SINCRONIZACION_INTERVALO_SEGUNDOS debe ser un entero no negativo.")
    try:
        vigencia = json.loads(os.environ.get("VIGENCIA_DIAS_POR_FUENTE", '{"default":7,"fiscalizacion":1}'))
    except ValueError as exc:
        raise ValueError("VIGENCIA_DIAS_POR_FUENTE debe ser un objeto JSON de días por fuente.") from exc
    if not isinstance(vigencia, dict) or any(
        not isinstance(clave, str) or not isinstance(dias, int) or isinstance(dias, bool) or dias < 0
        for clave, dias in vigencia.items()
    ):
        raise ValueError("VIGENCIA_DIAS_POR_FUENTE debe mapear nombres a enteros no negativos.")
    return Config(
        modo_agente=os.environ.get("MODO_AGENTE", "reglas").strip().lower(),
        ollama_url=os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/"),
        ollama_modelo=os.environ.get("OLLAMA_MODELO", "llama3.1"),
        fecha_referencia=date.fromisoformat(os.environ.get("FECHA_REFERENCIA", "2026-10-31")),
        datos_dir=datos if datos.is_absolute() else RAIZ / datos,
        generado_dir=generado if generado.is_absolute() else RAIZ / generado,
        subidos_dir=subidos if subidos.is_absolute() else RAIZ / subidos,
        intervalo_sincronizacion_segundos=intervalo,
        vigencia_dias_por_fuente=vigencia,
    )
