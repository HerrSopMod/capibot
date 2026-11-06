import importlib.util
import shutil
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "backend"))

from app.agents.servicio import Servicio  # noqa: E402
from app.config import Config  # noqa: E402
from app.ingestion.loader import Catalogo  # noqa: E402

REF = date(2026, 10, 31)
HOJAS = {"finanzas": "Facturas", "operaciones": "Pedidos", "logistica": "Entregas", "atencion": "Tickets"}
SYNTH = RAIZ / "data" / "synthetic"


def _generar(destino: Path) -> None:
    spec = importlib.util.spec_from_file_location("gen", RAIZ / "scripts" / "generate_demo_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.escribir(destino)


@pytest.fixture(scope="session", autouse=True)
def datos_sinteticos():
    """Garantiza que existan los Excel de la demo (generador determinista)."""
    if not all((SYNTH / f"{a}.xlsx").exists() for a in HOJAS):
        _generar(SYNTH)
    return SYNTH


def crear_config(datos: Path, generado: Path) -> Config:
    return Config("reglas", "http://localhost:11434", "x", REF, datos, generado)


@pytest.fixture
def servicio(tmp_path):
    cfg = crear_config(SYNTH, tmp_path / "gen")
    return Servicio(cfg, Catalogo(cfg.datos_dir, cfg.generado_dir))


@pytest.fixture
def copia(tmp_path):
    """Carpeta de fuentes copiada, para probar reimportación y archivos dañados sin tocar los originales."""
    destino = tmp_path / "fuentes"
    shutil.copytree(SYNTH, destino)
    return destino


# ------------- lectura independiente de los Excel (no usa el código bajo prueba) -------------
def _fecha(v):
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v)
    return datetime.strptime(s, "%d/%m/%Y" if "/" in s else "%Y-%m-%d").date()


def raw(area: str, datos: Path = SYNTH) -> list[dict]:
    """Filas crudas del Excel, con su número de fila. Fechas y dinero convertidos de forma independiente."""
    ws = load_workbook(datos / f"{area}.xlsx", data_only=True)[HOJAS[area]]
    filas = list(ws.iter_rows(values_only=True))
    cab = filas[0]
    out = []
    for n, f in enumerate(filas[1:], start=2):
        d = dict(zip(cab, f))
        d["_fila"] = n
        for k, v in d.items():
            if k.startswith("fecha"):
                d[k] = _fecha(v)
            elif k in ("importe", "saldo"):
                try:
                    d[k] = Decimal(str(round(float(v), 2)))
                except (TypeError, ValueError):
                    d[k] = None  # "N/D": fila inválida
        out.append(d)
    return out


def validas_finanzas(datos: Path = SYNTH) -> list[dict]:
    vistas, out = set(), []
    for f in raw("finanzas", datos):
        if f["importe"] is None or f["saldo"] is None or f["id_factura"] in vistas:
            continue
        vistas.add(f["id_factura"])
        out.append(f)
    return out
