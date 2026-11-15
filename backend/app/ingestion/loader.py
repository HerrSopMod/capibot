"""Importación de los Excel: lectura de solo lectura, normalización, validación y carga en DuckDB.

Los archivos fuente nunca se modifican: se leen a memoria y se calcula su hash SHA-256.
Cada fila cargada conserva su procedencia (_archivo, _hoja, _fila, _hash, _version, ...).
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import tempfile
import threading
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

import duckdb
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
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


@contextmanager
def leer_archivo(ruta: Path, esquema: Esquema) -> Iterator[tuple[Iterator[tuple[int, dict[str, Any]]], str, float, str]]:
    """Lee un Excel o CSV por filas y devuelve registros, hash, fecha y nombre de hoja."""
    if not ruta.exists():
        raise ErrorImportacion(f"No se encontró el archivo {ruta.name} en {ruta.parent}")
    hash_archivo = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            hash_archivo.update(bloque)
    huella = hash_archivo.hexdigest()
    if ruta.suffix.lower() == ".csv":
        try:
            archivo = ruta.open("r", encoding="utf-8-sig", newline="")
        except (OSError, UnicodeError) as exc:
            raise ErrorImportacion(f"{ruta.name} no se pudo abrir como CSV ({type(exc).__name__})") from exc
        try:
            muestra = archivo.read(8192)
            archivo.seek(0)
            try:
                delimitador = csv.Sniffer().sniff(muestra, delimiters=",;\t|").delimiter
            except csv.Error:
                delimitador = ","
            lector = csv.reader(archivo, delimiter=delimitador)
            try:
                encabezado = [str(c).strip().lower() for c in next(lector)]
            except StopIteration:
                raise ErrorImportacion(f"{ruta.name}: el CSV está vacío") from None
            _validar_encabezados(ruta, encabezado, esquema)

            def registros_csv() -> Iterator[tuple[int, dict[str, Any]]]:
                try:
                    for n, fila in enumerate(lector, start=2):
                        if all(_vacio(v) for v in fila):
                            continue
                        registro = {nom: (fila[pos] if pos < len(fila) else None)
                                    for pos, nom in enumerate(encabezado)}
                        registro.update({f"columna_{pos + 1}": valor
                                         for pos, valor in enumerate(fila[len(encabezado):], start=len(encabezado))})
                        yield n, registro
                except (csv.Error, UnicodeError) as exc:
                    raise ErrorImportacion(f"{ruta.name}: CSV inválido ({type(exc).__name__})") from exc

            yield registros_csv(), huella, ruta.stat().st_mtime, esquema.hoja
        finally:
            archivo.close()
        return
    if ruta.suffix.lower() != ".xlsx":
        raise ErrorImportacion(f"{ruta.name}: formato no admitido; usa .xlsx o .csv")
    try:
        libro = load_workbook(ruta, read_only=True, data_only=True)
    except Exception as exc:  # archivo corrupto o no es xlsx
        raise ErrorImportacion(f"{ruta.name} no es un .xlsx válido ({type(exc).__name__})") from exc
    try:
        nombre_hoja = esquema.hoja if esquema.hoja in libro.sheetnames else libro.sheetnames[0]
        hoja = libro[nombre_hoja]
        filas = hoja.iter_rows(values_only=True)
        try:
            encabezado = [str(c).strip().lower() if c is not None else "" for c in next(filas)]
        except StopIteration:
            raise ErrorImportacion(f"{ruta.name}: la hoja '{nombre_hoja}' está vacía") from None
        _validar_encabezados(ruta, encabezado, esquema)

        def registros() -> Iterator[tuple[int, dict[str, Any]]]:
            for n, fila in enumerate(filas, start=2):
                if not all(_vacio(v) for v in fila):
                    registro = {nom: (fila[pos] if pos < len(fila) else None)
                                for pos, nom in enumerate(encabezado)}
                    registro.update({f"columna_{pos + 1}": valor
                                     for pos, valor in enumerate(fila[len(encabezado):], start=len(encabezado))})
                    yield n, registro

        yield registros(), huella, ruta.stat().st_mtime, nombre_hoja
    finally:
        libro.close()


def _validar_encabezados(ruta: Path, encabezado: list[str], esquema: Esquema) -> None:
    if len(encabezado) != len(set(encabezado)):
        raise ErrorImportacion(f"{ruta.name}: hay nombres de columna repetidos")
    faltan = [c.nombre for c in esquema.columnas if c.nombre not in encabezado]
    if faltan:
        raise ErrorImportacion(f"{ruta.name}: faltan encabezados obligatorios: {', '.join(faltan)}")


class Catalogo:
    """Datos importados de las áreas, consultables con SQL parametrizado de solo lectura."""

    def __init__(self, datos_dir: Path, generado_dir: Path, subidos_dir: Path | None = None):
        self.datos_dir = Path(datos_dir)
        self.generado_dir = Path(generado_dir)
        self.subidos_dir = Path(subidos_dir) if subidos_dir else self.generado_dir.parent / "uploads"
        self.cargas: list[dict] = []
        self.archivos_area: dict[str, str] = {}
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

    def area_del_archivo(self, archivo: str) -> str | None:
        return self.archivos_area.get(archivo)

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
        """(Re)importa las áreas desde la carpeta de fuentes, sin modificarlas."""
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE _rechazos (area VARCHAR, archivo VARCHAR, hoja VARCHAR, fila INTEGER, causa VARCHAR, valores VARCHAR)")
        con.execute("CREATE TABLE _avisos (area VARCHAR, archivo VARCHAR, hoja VARCHAR, fila INTEGER, columna VARCHAR, valor VARCHAR, aviso VARCHAR)")
        archivos_area: dict[str, str] = {}
        try:
            cargas = [self._importar_area(con, ESQUEMAS[a], archivos_area) for a in AREAS]
        except Exception:
            con.close()
            raise
        with self._lock:
            self._con.close()
            self._con = con
            self.cargas = cargas
            self.archivos_area = archivos_area
        for c in cargas:
            self.registrar({"tipo": "carga", **{k: c[k] for k in ("archivo", "estado", "version", "filas_leidas", "validas", "rechazadas")}})
        return cargas

    def _importar_area(self, con: duckdb.DuckDBPyConnection, esq: Esquema,
                       archivos_area: dict[str, str]) -> dict:
        ahora = datetime.now().isoformat(timespec="seconds")
        base = {"area": esq.area, "archivo": esq.archivo, "hoja": esq.hoja, "importado_en": ahora}
        cols_sql = ", ".join(f"{c.nombre} {TIPO_SQL[c.tipo]}" for c in esq.columnas)
        meta_sql = "_archivo VARCHAR, _hoja VARCHAR, _fila INTEGER, _hash VARCHAR, _version INTEGER, _importado_en VARCHAR, _modificado_en VARCHAR"
        con.execute(f"CREATE TABLE {esq.area} ({cols_sql}, {meta_sql})")
        fuentes = sorted(
            [self.datos_dir / esq.archivo]
            + [p for p in self.subidos_dir.glob(f"{esq.area}_*") if p.suffix.lower() in {".xlsx", ".csv"}]
        )
        totales = {"filas_leidas": 0, "validas": 0, "rechazadas": 0, "avisos": 0}
        detalles = []
        vistas: dict[str, int] = {}
        for ruta in fuentes:
            if not ruta.exists():
                continue
            try:
                with leer_archivo(ruta, esq) as (crudas, huella, mtime, nombre_hoja):
                    version = self._versionar(ruta.name, huella)
                    modificado = datetime.fromtimestamp(mtime).isoformat(timespec="seconds")
                    conteos = self._insertar_filas(
                        con, esq, ruta.name, nombre_hoja, crudas, huella, version, ahora, modificado, vistas,
                    )
            except ErrorImportacion as exc:
                return {**base, "estado": "error", "error": str(exc), "version": None, "hash": None,
                        "modificado_en": None, **totales, "archivos": detalles}
            archivos_area[ruta.name] = esq.area
            detalles.append({"archivo": ruta.name, "hoja": nombre_hoja, "version": version,
                             "hash": huella, "modificado_en": modificado, "filas_leidas": conteos["filas_leidas"],
                             "validas": conteos["validas"], "rechazadas": conteos["rechazadas"],
                             "avisos": conteos["avisos"]})
            for campo in totales:
                totales[campo] += conteos[campo]
        if not detalles:
            return {**base, "estado": "error", "error": f"No se encontró el archivo {esq.archivo} en {self.datos_dir}",
                    "version": None, "hash": None, "modificado_en": None, **totales, "archivos": detalles}
        return {**base, "estado": "ok", "error": None, "version": detalles[0]["version"],
                "hash": detalles[0]["hash"], "modificado_en": detalles[0]["modificado_en"], **totales, "archivos": detalles}

    def _insertar_filas(self, con: duckdb.DuckDBPyConnection, esq: Esquema, nombre_archivo: str,
                        nombre_hoja: str, crudas: Iterator[tuple[int, dict[str, Any]]], huella: str,
                        version: int, ahora: str, modificado: str, vistas: dict[str, int]) -> dict[str, int]:
        rechazos: list[tuple] = []
        avisos: list[tuple] = []
        filas_leidas = filas_validas = filas_rechazadas = avisos_total = 0
        archivo_temporal: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", suffix=".csv", delete=False) as archivo:
                archivo_temporal = Path(archivo.name)
                escritor = csv.writer(archivo)
                for fila_excel, crudo in crudas:
                    filas_leidas += 1
                    valores, errores = {}, []
                    for col in esq.columnas:
                        valor_original = crudo.get(col.nombre)
                        v, err = convertir(col, valor_original)
                        if err:
                            errores.append(err)
                            if not _vacio(valor_original):
                                avisos.append((esq.area, nombre_archivo, nombre_hoja, fila_excel, col.nombre,
                                               json.dumps(valor_original, ensure_ascii=False, default=str),
                                               f"valor no válido: {err}"))
                                avisos_total += 1
                        valores[col.nombre] = v
                    for nombre, valor in crudo.items():
                        if _vacio(valor):
                            avisos.append((esq.area, nombre_archivo, nombre_hoja, fila_excel, nombre, None,
                                           f"campo vacío: {nombre}"))
                            avisos_total += 1
                    clave = valores.get(esq.clave)
                    if not errores and clave in vistas:
                        errores.append(f"duplicado: {esq.clave} {clave} ya aparece en la fila {vistas[clave]}")
                    if errores:
                        rechazos.append((esq.area, nombre_archivo, nombre_hoja, fila_excel, "; ".join(errores),
                                         json.dumps(crudo, ensure_ascii=False, default=str)))
                        filas_rechazadas += 1
                    else:
                        vistas[clave] = fila_excel
                        fila = [valores[c.nombre] for c in esq.columnas]
                        escritor.writerow(fila + [nombre_archivo, nombre_hoja, fila_excel, huella, version, ahora, modificado])
                        filas_validas += 1
                    if len(rechazos) >= 2000:
                        con.executemany("INSERT INTO _rechazos VALUES (?,?,?,?,?,?)", rechazos)
                        rechazos.clear()
                    if len(avisos) >= 2000:
                        con.executemany("INSERT INTO _avisos VALUES (?,?,?,?,?,?,?)", avisos)
                        avisos.clear()
            if rechazos:
                con.executemany("INSERT INTO _rechazos VALUES (?,?,?,?,?,?)", rechazos)
            if avisos:
                con.executemany("INSERT INTO _avisos VALUES (?,?,?,?,?,?,?)", avisos)
            if filas_validas:
                ruta_sql = str(archivo_temporal).replace("\\", "/").replace("'", "''")
                con.execute(f"COPY {esq.area} FROM '{ruta_sql}' (FORMAT CSV, HEADER FALSE, NULLSTR '')")
        finally:
            if archivo_temporal is not None:
                archivo_temporal.unlink(missing_ok=True)
        return {"filas_leidas": filas_leidas, "validas": filas_validas,
                "rechazadas": filas_rechazadas, "avisos": avisos_total}

    # ------------------------------------------------------------------ calidad
    def rechazos(self) -> list[dict]:
        return self.consultar("SELECT * FROM _rechazos ORDER BY archivo, fila")

    def avisos(self) -> list[dict]:
        return self.consultar("SELECT * FROM _avisos ORDER BY archivo, fila")

    def exportar_calidad(self, destino: Path) -> None:
        """Escribe rechazos y avisos en un XLSX por lotes, creando hojas al llegar al límite de filas."""
        limite_filas = 1_048_576
        libro = Workbook(write_only=True)

        def celda_segura(valor: Any) -> Any:
            if not isinstance(valor, str):
                return valor
            valor = ILLEGAL_CHARACTERS_RE.sub("", valor)[:32_767]
            if valor.lstrip(" \t\r\n")[:1] in {"=", "+", "-", "@"}:
                return "'" + valor
            return valor

        with self._lock:
            for nombre, consulta, encabezados in (
                ("Rechazos", "SELECT area, archivo, hoja, fila, causa, valores FROM _rechazos ORDER BY archivo, fila",
                 ("Área", "Archivo", "Hoja", "Fila", "Causa", "Valores originales")),
                ("Avisos", "SELECT area, archivo, hoja, fila, columna, valor, aviso FROM _avisos ORDER BY archivo, fila",
                 ("Área", "Archivo", "Hoja", "Fila", "Columna", "Valor original", "Aviso")),
            ):
                numero_hoja = 1
                hoja = libro.create_sheet(f"{nombre} {numero_hoja}")
                hoja.append(encabezados)
                filas_hoja = 1
                cursor = self._con.execute(consulta)
                while lote := cursor.fetchmany(1000):
                    for registro in lote:
                        if filas_hoja >= limite_filas:
                            numero_hoja += 1
                            hoja = libro.create_sheet(f"{nombre} {numero_hoja}")
                            hoja.append(encabezados)
                            filas_hoja = 1
                        hoja.append([celda_segura(valor) for valor in registro])
                        filas_hoja += 1
        libro.save(destino)
