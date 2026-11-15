"""Mide la ingesta y una búsqueda por clave con un Excel sintético de gran volumen.

Ejemplos:
  python scripts/run_scale_test.py --area fiscalizacion --rows 200000
  python scripts/run_scale_test.py --area recaudacion --rows 1000000
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "backend"))

from app.ingestion.loader import Catalogo  # noqa: E402
from app.ingestion.schemas import ESQUEMAS  # noqa: E402

PREFIJOS = {
    "finanzas": "FA", "operaciones": "PE", "logistica": "EN", "atencion": "TK",
    "fiscalizacion": "AC", "recaudacion": "PG", "notificaciones": "NT",
    "impugnaciones": "IM", "convenios": "CV", "documentos": "DC",
}


def generar_area(area: str, destino: Path, cantidad: int) -> None:
    esq = ESQUEMAS[area]
    libro = Workbook(write_only=True)
    hoja = libro.create_sheet(esq.hoja)
    hoja.append([col.nombre for col in esq.columnas])
    for i in range(1, cantidad + 1):
        id_pedido = f"PE-{(i - 1) % 60 + 1:03d}"
        id_cliente = f"CL-{((i - 1) * 7) % 20 + 1:03d}"
        fila = []
        for col in esq.columnas:
            if col.nombre == esq.clave:
                valor = f"{PREFIJOS[area]}-{i:08d}"
            elif col.nombre == "id_pedido":
                valor = id_pedido
            elif col.nombre == "id_cliente":
                valor = id_cliente
            elif col.nombre == "id_multa":
                valor = f"MU-{i:08d}"
            elif col.tipo == "fecha":
                valor = date(2026, 1, 1) + timedelta(days=i % 365)
            elif col.tipo == "dinero":
                valor = 25.50
            elif col.tipo == "estado":
                valor = sorted(col.vocabulario)[0].title()
            elif col.nombre == "cuotas":
                valor = "3"
            elif col.nombre == "referencia":
                valor = f"docs/{i:08d}.pdf"
            else:
                valor = "sintetico"
            fila.append(valor)
        hoja.append(fila)
    libro.save(destino / esq.archivo)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area", choices=ESQUEMAS, default="fiscalizacion")
    parser.add_argument("--rows", type=int, default=200_000)
    args = parser.parse_args()
    if not 1 <= args.rows <= 1_048_575:
        parser.error("--rows debe estar entre 1 y 1,048,575 (Excel reserva una fila para encabezados)")

    spec = importlib.util.spec_from_file_location("generate_demo_data", RAIZ / "scripts" / "generate_demo_data.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("No se pudo cargar el generador de datos sintéticos.")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)

    with tempfile.TemporaryDirectory(prefix="capibot-scale-") as temporal:
        raiz = Path(temporal)
        datos = raiz / "datos"
        datos.mkdir()
        modulo.escribir(datos)
        generar_area(args.area, datos, args.rows)
        inicio = time.perf_counter()
        catalogo = Catalogo(datos, raiz / "generado")
        segundos_ingesta = round(time.perf_counter() - inicio, 2)
        carga = next(c for c in catalogo.cargas if c["area"] == args.area)
        resultado = catalogo.consultar(f"SELECT COUNT(*) AS n FROM {args.area}")[0]["n"]
        consulta_inicio = time.perf_counter()
        muestra = catalogo.consultar(
            f"SELECT * FROM {args.area} WHERE {ESQUEMAS[args.area].clave} = ? LIMIT 1",
            [f"{PREFIJOS[args.area]}-{args.rows:08d}"],
        )
        segundos_consulta = round(time.perf_counter() - consulta_inicio, 4)
        reporte = {
            "area": args.area,
            "filas_solicitadas": args.rows,
            "filas_leidas": carga["filas_leidas"],
            "filas_validas": carga["validas"],
            "filas_rechazadas": carga["rechazadas"],
            "filas_en_duckdb": resultado,
            "otras_areas_importadas": sum(c["estado"] == "ok" for c in catalogo.cargas) - 1,
            "segundos_ingesta_todas_las_areas": segundos_ingesta,
            "segundos_consulta_por_clave": segundos_consulta,
            "busqueda_encontro_fila": bool(muestra),
            "estado": carga["estado"],
        }
        print(json.dumps(reporte, ensure_ascii=False, indent=2))
        if carga["estado"] != "ok" or resultado != args.rows or not muestra:
            raise SystemExit("La prueba de escala no pasó: revisa las métricas anteriores.")


if __name__ == "__main__":
    main()
