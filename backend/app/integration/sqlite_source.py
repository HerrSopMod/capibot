"""Conector de solo lectura y conjunto de dos sistemas ficticios en SQLite."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any


_CONSULTA_MULTAS = """
    SELECT m.id_multa, m.id_cliente, c.documento, c.nombre AS ciudadano,
           m.id_expediente, a.nombre AS area, m.fecha_infraccion, m.estado,
           m.importe, m.actualizado_en
    FROM {tabla_multas} m
    JOIN ciudadanos c ON c.id_cliente = m.id_cliente
    LEFT JOIN areas a ON a.id_area = m.id_area
    LEFT JOIN {tabla_expedientes} e ON e.id_expediente = m.id_expediente
    WHERE {filtro}
    ORDER BY m.id_multa
"""


class SQLiteReadOnlyConnector:
    """Ejecuta consultas parametrizadas contra una base SQLite abierta en modo de solo lectura."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def consultar(self, sql: str, parametros: tuple | list = ()) -> list[dict[str, Any]]:
        uri = self.path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as conexion:
            conexion.row_factory = sqlite3.Row
            conexion.execute("PRAGMA query_only = ON")
            return [dict(fila) for fila in conexion.execute(sql, parametros).fetchall()]

    def multas(self, campo: str, valor: str) -> list[dict[str, Any]]:
        filtros = {
            "documento": "c.documento",
            "id_expediente": "m.id_expediente",
            "id_multa": "m.id_multa",
        }
        columna = filtros.get(campo)
        if columna is None:
            raise ValueError(f"Campo de búsqueda no admitido: {campo}")
        filas = []
        for sistema in ("si_a", "si_b"):
            sql = _CONSULTA_MULTAS.format(
                tabla_multas=f"multas_{sistema}",
                tabla_expedientes=f"expedientes_{sistema}",
                filtro=f"{columna} = ?",
            )
            for fila in self.consultar(sql, (valor,)):
                fila["sistema"] = "SI A" if sistema == "si_a" else "SI B"
                filas.append(fila)
        return filas

    def todas_las_multas(self) -> list[dict[str, Any]]:
        filas = []
        for sistema in ("si_a", "si_b"):
            sql = _CONSULTA_MULTAS.format(
                tabla_multas=f"multas_{sistema}",
                tabla_expedientes=f"expedientes_{sistema}",
                filtro="1 = 1",
            )
            for fila in self.consultar(sql):
                fila["sistema"] = "SI A" if sistema == "si_a" else "SI B"
                filas.append(fila)
        return filas

    def ciudadano_por_documento(self, documento: str) -> dict[str, Any] | None:
        filas = self.consultar(
            "SELECT id_cliente, documento, nombre AS ciudadano FROM ciudadanos WHERE documento = ?",
            (documento,),
        )
        return filas[0] if filas else None

    def ciudadano_por_cliente(self, id_cliente: str) -> dict[str, Any] | None:
        filas = self.consultar(
            "SELECT id_cliente, documento, nombre AS ciudadano FROM ciudadanos WHERE id_cliente = ?",
            (id_cliente,),
        )
        return filas[0] if filas else None


def crear_sistemas_demo(path: Path) -> None:
    """Crea una sola vez datos íntegramente ficticios; no reemplaza una base existente."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        SQLiteReadOnlyConnector(path).consultar("SELECT 1 FROM areas LIMIT 1")
        return

    temporal = None
    conexion: sqlite3.Connection | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="capibot-si-", suffix=".sqlite3", dir=path.parent, delete=False) as archivo:
            temporal = Path(archivo.name)
        conexion = sqlite3.connect(temporal)
        with conexion:
            conexion.executescript("""
                PRAGMA foreign_keys = ON;
                CREATE TABLE areas (
                    id_area TEXT PRIMARY KEY,
                    nombre TEXT NOT NULL UNIQUE
                );
                CREATE TABLE ciudadanos (
                    id_cliente TEXT PRIMARY KEY,
                    documento TEXT NOT NULL UNIQUE,
                    nombre TEXT NOT NULL
                );
                CREATE TABLE expedientes_si_a (
                    id_expediente TEXT PRIMARY KEY,
                    id_cliente TEXT NOT NULL REFERENCES ciudadanos(id_cliente),
                    fecha_apertura TEXT NOT NULL
                );
                CREATE TABLE expedientes_si_b (
                    id_expediente TEXT PRIMARY KEY,
                    id_cliente TEXT NOT NULL REFERENCES ciudadanos(id_cliente),
                    fecha_apertura TEXT NOT NULL
                );
                CREATE TABLE multas_si_a (
                    id_multa TEXT PRIMARY KEY,
                    id_cliente TEXT NOT NULL REFERENCES ciudadanos(id_cliente),
                    id_expediente TEXT NOT NULL,
                    id_area TEXT NOT NULL REFERENCES areas(id_area),
                    fecha_infraccion TEXT NOT NULL,
                    estado TEXT NOT NULL,
                    importe NUMERIC NOT NULL,
                    actualizado_en TEXT NOT NULL
                );
                CREATE TABLE multas_si_b (
                    id_multa TEXT PRIMARY KEY,
                    id_cliente TEXT NOT NULL REFERENCES ciudadanos(id_cliente),
                    id_expediente TEXT NOT NULL,
                    id_area TEXT NOT NULL REFERENCES areas(id_area),
                    fecha_infraccion TEXT NOT NULL,
                    estado TEXT NOT NULL,
                    importe NUMERIC NOT NULL,
                    actualizado_en TEXT NOT NULL
                );
                CREATE TABLE pagos_si_a (
                    id_pago TEXT PRIMARY KEY,
                    id_multa TEXT NOT NULL REFERENCES multas_si_a(id_multa),
                    fecha_pago TEXT NOT NULL,
                    importe NUMERIC NOT NULL
                );
                CREATE TABLE pagos_si_b (
                    id_pago TEXT PRIMARY KEY,
                    id_multa TEXT NOT NULL REFERENCES multas_si_b(id_multa),
                    fecha_pago TEXT NOT NULL,
                    importe NUMERIC NOT NULL
                );
            """)
            conexion.executemany("INSERT INTO areas VALUES (?, ?)", [
                ("fiscalizacion", "Fiscalización"),
                ("recaudacion", "Recaudación"),
                ("transito", "Tránsito"),
            ])
            conexion.executemany("INSERT INTO ciudadanos VALUES (?, ?, ?)", [
                ("CL-900001", "90000001", "Ciudadano ficticio 001"),
                ("CL-900002", "90000002", "Ciudadana ficticia 002"),
                ("CL-900003", "90000003", "Ciudadano ficticio 003"),
                *[
                    (f"CL-{i:03d}", f"700000{i:02d}", f"Persona ficticia {i:03d}")
                    for i in range(1, 21)
                ],
            ])
            conexion.executemany("INSERT INTO expedientes_si_a VALUES (?, ?, ?)", [
                ("EX-900001", "CL-900001", "2026-03-10"),
                ("EX-900002", "CL-900001", "2026-03-12"),
                ("EX-900005", "CL-900002", "2026-05-05"),
            ])
            conexion.executemany("INSERT INTO expedientes_si_b VALUES (?, ?, ?)", [
                ("EX-900003", "CL-900001", "2026-03-20"),
                ("EX-900006", "CL-900003", "2026-06-01"),
            ])
            conexion.executemany("INSERT INTO multas_si_a VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
                ("MU-900001", "CL-900001", "EX-900001", "fiscalizacion", "2026-03-10", "pendiente", 100.00, "2026-03-11T09:00:00"),
                ("MU-900002", "CL-900001", "EX-900002", "recaudacion", "2026-03-12", "pendiente", 200.00, "2026-03-13T10:00:00"),
                ("MU-900005", "CL-900002", "EX-900005", "fiscalizacion", "2026-05-05", "pendiente", 75.00, "2026-05-06T09:00:00"),
            ])
            conexion.executemany("INSERT INTO multas_si_b VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
                ("MU-900003", "CL-900001", "EX-900003", "transito", "2026-03-20", "pagada", 50.00, "2026-03-21T11:00:00"),
                ("MU-900006", "CL-900003", "EX-900006", "recaudacion", "2026-06-01", "pendiente", 125.00, "2026-06-02T08:30:00"),
            ])
            conexion.execute("INSERT INTO pagos_si_b VALUES (?, ?, ?, ?)", (
                "PG-900003", "MU-900003", "2026-04-02", 50.00,
            ))
        conexion.close()
        os.replace(temporal, path)
    except Exception:
        if conexion is not None:
            conexion.close()
        if temporal is not None:
            temporal.unlink(missing_ok=True)
        raise

