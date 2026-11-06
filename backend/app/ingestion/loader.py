"""Importación de los Excel: lectura de solo lectura, normalización, validación y carga en DuckDB.

Los archivos fuente nunca se modifican: se leen a memoria y se calcula su hash SHA-256.
Cada fila cargada conserva su procedencia (_archivo, _hoja, _fila, _hash, _version, ...).
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import duckdb
from openpyxl import load_workbook

from .schemas import AREAS, ESQUEMAS, Columna, Esquema

TIPO_SQL = {"id": "VARCHAR", "texto": "VARCHAR", "estado": "VARCHAR", "fecha": "DATE", "dinero": "DECIMAL(14,2)"}
META = ["_archivo", "_hoja", "_fila", "_hash", "_version", "_importado_en", "_modificado_en"]
FORMATOS_FECHA = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y")


class ErrorImportacion(Exception):
    """El archivo completo no se puede importar (falta el archivo, la hoja o encabezados)."""


def sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def _vacio(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def convertir(col: Columna, valor: Any) -> tuple[Any, str | None]:
    """Devuelve (valor_normalizado, error). Un campo vacío no obligatorio devuelve (None, None)."""
    if _vacio(valor):
        return (None, f"campo obligatorio vacío: {col.nombre}") if col.obligatoria else (None, None)
    if col.tipo == "id":
        v = str(valor).strip().upper()
        if not re.fullmatch(col.patron, v):
            return None, f"identificador inválido en {col.nombre}: {valor!r}"
        return v, None
    if col.tipo == "texto":
        return str(valor).strip(), None
    if col.tipo == "estado":
        v = sin_acentos(str(valor).strip().lower())
        if v not in col.vocabulario:
            return None, f"estado no reconocido en {col.nombre}: {valor!r}"
        return v, None
    if col.tipo == "fecha":
        if isinstance(valor, datetime):
            return valor.date(), None
        if isinstance(valor, date):
            return valor, None
        for fmt in FORMATOS_FECHA:
            try:
                return datetime.strptime(str(valor).strip(), fmt).date(), None
            except ValueError:
                continue
        return None, f"fecha inválida en {col.nombre}: {valor!r}"
    if col.tipo == "dinero":
        try:
            if isinstance(valor, (int, float)):
                d = Decimal(str(valor))
            else:
                d = Decimal(str(valor).replace("S/", "").replace(",", "").strip())
            d = d.quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            return None, f"importe no numérico en {col.nombre}: {valor!r}"
        if d < 0:
            return None, f"importe negativo en {col.nombre}: {valor!r}"
        return d, None
    raise ValueError(f"tipo desconocido {col.tipo}")


def leer_excel(ruta: Path, esquema: Esquema) -> tuple[list[tuple[int, dict[str, Any]]], str, float]:
    """Lee las filas crudas de la hoja esperada. Devuelve ([(fila_excel, {columna: valor})], hash, mtime)."""
    if not ruta.exists():
        raise ErrorImportacion(f"No se encontró el archivo {ruta.name} en {ruta.parent}")
    contenido = ruta.read_bytes()
    huella = hashlib.sha256(contenido).hexdigest()
    try:
        libro = load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    except Exception as exc:  # archivo corrupto o no es xlsx
        raise ErrorImportacion(f"{ruta.name} no es un .xlsx válido ({type(exc).__name__})") from exc
    if esquema.hoja not in libro.sheetnames:
        raise ErrorImportacion(f"{ruta.name}: no existe la hoja '{esquema.hoja}' (hojas: {', '.join(libro.sheetnames)})")
    filas = libro[esquema.hoja].iter_rows(values_only=True)
    try:
        encabezado = [str(c).strip().lower() if c is not None else "" for c in next(filas)]
    except StopIteration:
        raise ErrorImportacion(f"{ruta.name}: la hoja '{esquema.hoja}' está vacía") from None
    faltan = [c.nombre for c in esquema.columnas if c.nombre not in encabezado]
    if faltan:
        raise ErrorImportacion(f"{ruta.name}: faltan encabezados obligatorios: {', '.join(faltan)}")
    posiciones = {nombre: encabezado.index(nombre) for nombre in (c.nombre for c in esquema.columnas)}
    resultado = []
    for n, fila in enumerate(filas, start=2):
        if all(_vacio(v) for v in fila):
            continue
        resultado.append((n, {nom: (fila[pos] if pos < len(fila) else None) for nom, pos in posiciones.items()}))
    return resultado, huella, ruta.stat().st_mtime


class Catalogo:
    """Datos importados de las cuatro áreas, consultables con SQL parametrizado de solo lectura."""

    def __init__(self, datos_dir: Path, generado_dir: Path):
        self.datos_dir = Path(datos_dir)
        self.generado_dir = Path(generado_dir)
        self.cargas: list[dict] = []
        self._con = duckdb.connect(":memory:")
        self._lock = threading.Lock()
        self.importar()

    # ---------------------------------------------------------------- consulta
    def consultar(self, sql: str, params: tuple | list = ()) -> list[dict]:
        with self._lock:
            cur = self._con.execute(sql, list(params))
            nombres = [d[0] for d in cur.description]
            return [dict(zip(nombres, fila)) for fila in cur.fetchall()]

    def area_disponible(self, area: str) -> str | None:
        """None si el área está cargada; si no, el motivo."""
        for c in self.cargas:
            if c["area"] == area:
                return None if c["estado"] == "ok" else c["error"]
        return "área no importada"

    # --------------------------------------------------------------- registro
    def _manifiesto(self) -> Path:
        return self.generado_dir / "manifiesto.json"

    def registrar(self, evento: dict) -> None:
        try:
            self.generado_dir.mkdir(parents=True, exist_ok=True)
            evento = {"ts": datetime.now().isoformat(timespec="seconds"), **evento}
            with (self.generado_dir / "registro.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(evento, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass  # el registro nunca debe impedir responder

    def _versionar(self, archivo: str, huella: str) -> int:
        try:
            manifiesto = json.loads(self._manifiesto().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifiesto = {}
        previo = manifiesto.get(archivo)
        if previo and previo["hash"] == huella:
            return previo["version"]
        version = (previo["version"] + 1) if previo else 1
        manifiesto[archivo] = {"hash": huella, "version": version}
        try:
            self.generado_dir.mkdir(parents=True, exist_ok=True)
            self._manifiesto().write_text(json.dumps(manifiesto, indent=2), encoding="utf-8")
        except OSError:
            pass
        return version

    # --------------------------------------------------------------- importar
    def importar(self) -> list[dict]:
        """(Re)importa las cuatro áreas desde la carpeta de fuentes, sin modificarlas."""
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE _rechazos (area VARCHAR, archivo VARCHAR, hoja VARCHAR, fila INTEGER, causa VARCHAR, valores VARCHAR)")
        con.execute("CREATE TABLE _avisos (area VARCHAR, archivo VARCHAR, hoja VARCHAR, fila INTEGER, aviso VARCHAR)")
        cargas = [self._importar_area(con, ESQUEMAS[a]) for a in AREAS]
        with self._lock:
            self._con.close()
            self._con = con
            self.cargas = cargas
        for c in cargas:
            self.registrar({"tipo": "carga", **{k: c[k] for k in ("archivo", "estado", "version", "filas_leidas", "validas", "rechazadas")}})
        return cargas

    def _importar_area(self, con: duckdb.DuckDBPyConnection, esq: Esquema) -> dict:
        ahora = datetime.now().isoformat(timespec="seconds")
        base = {"area": esq.area, "archivo": esq.archivo, "hoja": esq.hoja, "importado_en": ahora}
        cols_sql = ", ".join(f"{c.nombre} {TIPO_SQL[c.tipo]}" for c in esq.columnas)
        meta_sql = "_archivo VARCHAR, _hoja VARCHAR, _fila INTEGER, _hash VARCHAR, _version INTEGER, _importado_en VARCHAR, _modificado_en VARCHAR"
        con.execute(f"CREATE TABLE {esq.area} ({cols_sql}, {meta_sql})")
        try:
            crudas, huella, mtime = leer_excel(self.datos_dir / esq.archivo, esq)
        except ErrorImportacion as exc:
            return {**base, "estado": "error", "error": str(exc), "version": None, "hash": None, "modificado_en": None,
                    "filas_leidas": 0, "validas": 0, "rechazadas": 0, "avisos": 0}
        version = self._versionar(esq.archivo, huella)
        modificado = datetime.fromtimestamp(mtime).isoformat(timespec="seconds")
        vistas: dict[str, int] = {}
        validas, rechazos, avisos = [], [], []
        for fila_excel, crudo in crudas:
            valores, errores, vacios = {}, [], []
            for col in esq.columnas:
                v, err = convertir(col, crudo[col.nombre])
                if err:
                    errores.append(err)
                elif v is None and col.avisa_vacio:
                    vacios.append(col.nombre)
                valores[col.nombre] = v
            clave = valores.get(esq.clave)
            if not errores and clave in vistas:
                errores.append(f"duplicado: {esq.clave} {clave} ya aparece en la fila {vistas[clave]}")
            if errores:
                rechazos.append((esq.area, esq.archivo, esq.hoja, fila_excel, "; ".join(errores),
                                 json.dumps({k: v for k, v in crudo.items()}, ensure_ascii=False, default=str)))
                continue
            vistas[clave] = fila_excel
            for nombre in vacios:
                avisos.append((esq.area, esq.archivo, esq.hoja, fila_excel, f"campo vacío: {nombre}"))
            validas.append([valores[c.nombre] for c in esq.columnas]
                           + [esq.archivo, esq.hoja, fila_excel, huella, version, ahora, modificado])
        marcas = ", ".join("?" * (len(esq.columnas) + 7))
        if validas:
            con.executemany(f"INSERT INTO {esq.area} VALUES ({marcas})", validas)
        if rechazos:
            con.executemany("INSERT INTO _rechazos VALUES (?,?,?,?,?,?)", rechazos)
        if avisos:
            con.executemany("INSERT INTO _avisos VALUES (?,?,?,?,?)", avisos)
        return {**base, "estado": "ok", "error": None, "version": version, "hash": huella, "modificado_en": modificado,
                "filas_leidas": len(crudas), "validas": len(validas), "rechazadas": len(rechazos), "avisos": len(avisos)}

    # ------------------------------------------------------------------ calidad
    def rechazos(self) -> list[dict]:
        return self.consultar("SELECT * FROM _rechazos ORDER BY archivo, fila")

    def avisos(self) -> list[dict]:
        return self.consultar("SELECT * FROM _avisos ORDER BY archivo, fila")
