"""Detección local de cambios, sincronización programada y vigencia de fuentes."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Config
from ..ingestion.schemas import ESQUEMAS
from .multas import ConsultaMultas
from .sqlite_source import SQLiteReadOnlyConnector


class Sincronizador:
    def __init__(
        self,
        cfg: Config,
        catalogo,
        consulta_multas: ConsultaMultas,
        sqlite_path: Path,
        sqlite: SQLiteReadOnlyConnector,
    ):
        self.cfg = cfg
        self.catalogo = catalogo
        self.consulta_multas = consulta_multas
        self.sqlite_path = Path(sqlite_path)
        self.sqlite = sqlite
        self.db_path = cfg.generado_dir / "sincronizaciones.sqlite3"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.al_actualizar = None
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._crear_esquema()
        self._inicializar_estado()

    def _conexion(self) -> sqlite3.Connection:
        conexion = sqlite3.connect(self.db_path, timeout=10)
        conexion.row_factory = sqlite3.Row
        return conexion

    def _crear_esquema(self) -> None:
        with self._conexion() as conexion:
            conexion.executescript("""
                CREATE TABLE IF NOT EXISTS fuentes_sync (
                    fuente_id TEXT PRIMARY KEY,
                    nombre TEXT NOT NULL,
                    tipo TEXT NOT NULL,
                    area TEXT,
                    ruta TEXT,
                    hash TEXT,
                    mtime_ns INTEGER,
                    tamano INTEGER,
                    fecha_modificacion TEXT,
                    fecha_dato TEXT,
                    ultima_sincronizacion TEXT,
                    estado TEXT NOT NULL,
                    error TEXT,
                    registros INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS historial_sync (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fuente_id TEXT NOT NULL,
                    nombre TEXT NOT NULL,
                    iniciada_en TEXT NOT NULL,
                    terminada_en TEXT NOT NULL,
                    estado TEXT NOT NULL,
                    registros_procesados INTEGER NOT NULL,
                    mensaje TEXT
                );
            """)

    @staticmethod
    def _ahora() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    @staticmethod
    def _como_datetime(valor: str | None) -> datetime | None:
        if not valor:
            return None
        try:
            fecha = datetime.fromisoformat(valor)
        except ValueError:
            return None
        return fecha.astimezone() if fecha.tzinfo else fecha.astimezone()

    def _inventario(self) -> dict[str, dict[str, Any]]:
        fuentes: dict[str, dict[str, Any]] = {}
        for area, esquema in ESQUEMAS.items():
            ruta = self.catalogo.datos_dir / esquema.archivo
            fuente_id = f"archivo:{area}:base"
            fuentes[fuente_id] = {
                "fuente_id": fuente_id, "nombre": ruta.name, "tipo": "Excel/CSV",
                "area": area, "ruta": ruta, "obligatoria": True,
            }
            for subida in self.catalogo.subidos_dir.glob(f"{area}_*"):
                if subida.suffix.lower() not in {".xlsx", ".csv"}:
                    continue
                fuente_id = f"archivo:{area}:{subida.name}"
                fuentes[fuente_id] = {
                    "fuente_id": fuente_id, "nombre": subida.name, "tipo": "Archivo cargado",
                    "area": area, "ruta": subida, "obligatoria": False,
                }

        ruta_extra = self.consulta_multas.archivo_extra
        fuentes["integracion:multas_csv"] = {
            "fuente_id": "integracion:multas_csv", "nombre": ruta_extra.name,
            "tipo": "Excel complementario", "area": "fiscalizacion",
            "ruta": ruta_extra, "obligatoria": False,
        }
        for sistema in ("SI A", "SI B"):
            fuente_id = "sqlite:" + sistema.lower().replace(" ", "_")
            fuentes[fuente_id] = {
                "fuente_id": fuente_id, "nombre": sistema,
                "tipo": "SQLite (solo lectura)", "area": None,
                "ruta": self.sqlite_path, "sistema": sistema,
            }
        return fuentes

    def _huella(self, ruta: Path) -> tuple[str, int, int, str] | None:
        try:
            stat = ruta.stat()
        except FileNotFoundError:
            return None
        digest = hashlib.sha256()
        with ruta.open("rb") as archivo:
            for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
                digest.update(bloque)
        return (
            digest.hexdigest(), stat.st_mtime_ns, stat.st_size,
            datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        )

    def _fecha_dato_area(self, area: str) -> str | None:
        esquema = ESQUEMAS[area]
        columna = next((c.nombre for c in esquema.columnas if c.tipo == "fecha"), None)
        if not columna or self.catalogo.area_disponible(area) is not None:
            return None
        valor = self.catalogo.consultar(f"SELECT MAX({columna}) AS fecha FROM {area}")[0]["fecha"]
        return valor.isoformat() if hasattr(valor, "isoformat") else str(valor) if valor else None

    def _fecha_dato(self, fuente: dict[str, Any]) -> str | None:
        if fuente["tipo"] in ("Excel/CSV", "Archivo cargado"):
            return self._fecha_dato_area(fuente["area"])
        if fuente["tipo"] == "Excel complementario":
            registros, _ = self.consulta_multas._leer_excel_extra()
            fechas = [r.get("actualizado_en") or r.get("fecha_infraccion") for r in registros]
            return max((str(f) for f in fechas if f), default=None)
        todas = self.sqlite.todas_las_multas()
        sistema = fuente["sistema"]
        fechas = [
            fila.get("actualizado_en") or fila.get("fecha_infraccion")
            for fila in todas if fila["sistema"] == sistema
        ]
        return max((str(f) for f in fechas if f), default=None)

    def _guardar_fuente(
        self,
        fuente: dict[str, Any],
        huella: tuple[str, int, int, str] | None,
        fecha_dato: str | None,
        ultima_sincronizacion: str | None,
        estado: str,
        error: str | None,
        registros: int,
        hash_anterior: str | None = None,
        mtime_anterior: int | None = None,
        tamano_anterior: int | None = None,
        fecha_mod_anterior: str | None = None,
    ) -> None:
        hash_actual, mtime, tamano, fecha_mod = huella or (None, None, None, None)
        with self._conexion() as conexion:
            conexion.execute("""
                INSERT INTO fuentes_sync (
                    fuente_id, nombre, tipo, area, ruta, hash, mtime_ns, tamano,
                    fecha_modificacion, fecha_dato, ultima_sincronizacion, estado, error, registros
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fuente_id) DO UPDATE SET
                    nombre=excluded.nombre, tipo=excluded.tipo, area=excluded.area,
                    ruta=excluded.ruta,
                    hash=COALESCE(excluded.hash, ?),
                    mtime_ns=COALESCE(excluded.mtime_ns, ?),
                    tamano=COALESCE(excluded.tamano, ?),
                    fecha_modificacion=COALESCE(excluded.fecha_modificacion, ?),
                    fecha_dato=COALESCE(excluded.fecha_dato, fuentes_sync.fecha_dato),
                    ultima_sincronizacion=COALESCE(excluded.ultima_sincronizacion, fuentes_sync.ultima_sincronizacion),
                    estado=excluded.estado, error=excluded.error, registros=excluded.registros
            """, (
                fuente["fuente_id"], fuente["nombre"], fuente["tipo"], fuente.get("area"),
                str(fuente["ruta"]) if fuente.get("ruta") else None,
                hash_actual if hash_actual is not None else hash_anterior,
                mtime if mtime is not None else mtime_anterior,
                tamano if tamano is not None else tamano_anterior,
                fecha_mod if fecha_mod is not None else fecha_mod_anterior,
                fecha_dato, ultima_sincronizacion, estado, error, registros,
                hash_anterior, mtime_anterior, tamano_anterior, fecha_mod_anterior,
            ))

    def _inicializar_estado(self) -> None:
        with self._conexion() as conexion:
            cantidad = conexion.execute("SELECT COUNT(*) FROM fuentes_sync").fetchone()[0]
        if cantidad:
            return
        cargas = {c["area"]: c for c in self.catalogo.cargas}
        fuentes = self._inventario()
        for fuente in fuentes.values():
            huella = self._huella(fuente["ruta"])
            ultima = None
            registros = 0
            estado, error, fecha_dato = "no_disponible", "Fecha no disponible: aún no hay una sincronización exitosa.", None
            if fuente["tipo"] in ("Excel/CSV", "Archivo cargado"):
                carga = cargas.get(fuente["area"], {})
                detalle = next((f for f in carga.get("archivos", []) if f["archivo"] == fuente["nombre"]), None)
                if carga.get("estado") == "ok" and detalle:
                    ultima = carga["importado_en"]
                    registros = detalle["validas"]
                    estado, error = "ok", None
                    fecha_dato = self._fecha_dato(fuente)
                    if huella:
                        huella = (detalle["hash"], huella[1], huella[2], detalle["modificado_en"])
                elif carga.get("estado") == "error":
                    estado, error = "error", carga.get("error")
            elif fuente["tipo"] == "Excel complementario" and huella:
                registros_csv, errores = self.consulta_multas._leer_excel_extra()
                error_estructura = any(e.get("fila") is None for e in errores)
                if not error_estructura:
                    ultima, estado, error = self._ahora(), "ok", None
                    registros, fecha_dato = len(registros_csv), self._fecha_dato(fuente)
                else:
                    estado, error = "error", "; ".join(e["detalle"] for e in errores)
            elif fuente["tipo"].startswith("SQLite"):
                filas = self.sqlite.todas_las_multas()
                sistema = fuente["sistema"]
                registros = sum(f["sistema"] == sistema for f in filas)
                ultima, estado, error = self._ahora(), "ok", None
                fecha_dato = self._fecha_dato(fuente)
            self._guardar_fuente(fuente, huella, fecha_dato, ultima, estado, error, registros)
            if estado in {"ok", "error"}:
                self._historial(
                    fuente, ultima or self._ahora(), estado, registros,
                    error or "Carga inicial local registrada.",
                )

    def _historial(
        self, fuente: dict[str, Any], inicio: str, estado: str, registros: int, mensaje: str | None,
    ) -> None:
        with self._conexion() as conexion:
            conexion.execute("""
                INSERT INTO historial_sync
                    (fuente_id, nombre, iniciada_en, terminada_en, estado, registros_procesados, mensaje)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (fuente["fuente_id"], fuente["nombre"], inicio, self._ahora(), estado, registros, mensaje))

    def _marca_fallo(
        self, fuente: dict[str, Any], huella: tuple[str, int, int, str] | None,
        mensaje: str, inicio: str,
    ) -> None:
        with self._conexion() as conexion:
            previo = conexion.execute(
                "SELECT * FROM fuentes_sync WHERE fuente_id = ?", [fuente["fuente_id"]]
            ).fetchone()
        self._guardar_fuente(
            fuente, huella, None, None, "error", mensaje,
            int(previo["registros"]) if previo else 0,
            previo["hash"] if previo else None,
            previo["mtime_ns"] if previo else None,
            previo["tamano"] if previo else None,
            previo["fecha_modificacion"] if previo else None,
        )
        self._historial(fuente, inicio, "error", 0, mensaje)

    def sincronizar(self) -> dict[str, Any]:
        with self._lock:
            inicio = self._ahora()
            inventario = self._inventario()
            with self._conexion() as conexion:
                anteriores = {
                    fila["fuente_id"]: dict(fila)
                    for fila in conexion.execute("SELECT * FROM fuentes_sync").fetchall()
                }
            cambios: dict[str, tuple[dict[str, Any], tuple[str, int, int, str] | None]] = {}
            procesados = 0
            errores: list[str] = []
            for fuente_id, fuente in inventario.items():
                previo = anteriores.get(fuente_id)
                try:
                    huella = self._huella(fuente["ruta"])
                except OSError as exc:
                    mensaje = f"No se pudo leer {fuente['nombre']}: {exc}"
                    self._marca_fallo(fuente, None, mensaje, inicio)
                    errores.append(mensaje)
                    continue
                if huella is None:
                    if previo and previo["hash"]:
                        cambios[fuente_id] = (fuente, None)
                    elif previo is None:
                        self._guardar_fuente(fuente, None, None, None, "no_disponible",
                                             "Fecha no disponible: archivo no encontrado.", 0)
                    continue
                if previo and (previo["mtime_ns"], previo["tamano"]) == (huella[1], huella[2]):
                    continue
                if previo and previo["hash"] == huella[0]:
                    self._guardar_fuente(fuente, huella, None, None, previo["estado"],
                                         previo["error"], int(previo["registros"]))
                    continue
                cambios[fuente_id] = (fuente, huella)

            for fuente_id, previo in anteriores.items():
                if fuente_id in inventario or not fuente_id.startswith("archivo:") or ":base" in fuente_id:
                    continue
                fuente = {
                    "fuente_id": fuente_id, "nombre": Path(previo["ruta"]).name,
                    "tipo": previo["tipo"], "area": previo["area"], "ruta": Path(previo["ruta"]),
                    "obligatoria": False,
                }
                cambios[fuente_id] = (fuente, None)

            areas = {
                fuente["area"] for fuente, _ in cambios.values()
                if fuente["tipo"] in ("Excel/CSV", "Archivo cargado")
            }
            cargas: dict[str, dict[str, Any]] = {}
            if areas:
                try:
                    cargas = {c["area"]: c for c in self.catalogo.importar(areas)}
                except Exception as exc:
                    mensaje = f"{type(exc).__name__}: {exc}"
                    for fuente_id, (fuente, huella) in cambios.items():
                        if fuente.get("area") in areas:
                            self._marca_fallo(fuente, huella, mensaje, inicio)
                    raise
                for area in areas:
                    carga = cargas[area]
                    fuentes_area = [
                        (fuente_id, fuente, huella)
                        for fuente_id, (fuente, huella) in cambios.items()
                        if fuente.get("area") == area
                    ]
                    if carga["estado"] != "ok":
                        mensaje = carga.get("error") or "No se pudo sincronizar el área."
                        for _, fuente, huella in fuentes_area:
                            self._marca_fallo(fuente, huella, mensaje, inicio)
                            errores.append(f"{fuente['nombre']}: {mensaje}")
                        continue
                    momento = carga["importado_en"]
                    detalles = {f["archivo"]: f for f in carga.get("archivos", [])}
                    for fuente_id, fuente in inventario.items():
                        if fuente.get("area") != area or fuente["tipo"] not in ("Excel/CSV", "Archivo cargado"):
                            continue
                        detalle = detalles.get(fuente["nombre"])
                        huella_actual = self._huella(fuente["ruta"])
                        if not detalle:
                            continue
                        fecha_dato = self._fecha_dato_archivo(area, fuente["nombre"])
                        self._guardar_fuente(
                            fuente, huella_actual, fecha_dato, momento, "ok", None,
                            detalle["validas"],
                        )
                        procesados += detalle["filas_leidas"]

                    for fuente_id, fuente, huella in fuentes_area:
                        if huella is None and fuente_id not in inventario:
                            self._guardar_fuente(fuente, None, self._fecha_dato(fuente), momento,
                                                 "ok", None, 0)
                            procesados += 0

            for fuente_id, (fuente, huella) in cambios.items():
                if fuente["tipo"] == "Excel complementario":
                    if huella is None:
                        self._marca_fallo(fuente, None, "El archivo complementario ya no está disponible.", inicio)
                        errores.append(f"{fuente['nombre']}: archivo no disponible")
                        continue
                    registros, problemas = self.consulta_multas._leer_excel_extra()
                    error_estructura = next((e["detalle"] for e in problemas if e.get("fila") is None), None)
                    if error_estructura:
                        self._marca_fallo(fuente, huella, error_estructura, inicio)
                        errores.append(f"{fuente['nombre']}: {error_estructura}")
                        continue
                    fecha_dato = self._fecha_dato(fuente)
                    self._guardar_fuente(fuente, huella, fecha_dato, self._ahora(), "ok", None, len(registros))
                    procesados += len(registros)
                elif fuente["tipo"].startswith("SQLite"):
                    try:
                        filas = self.sqlite.todas_las_multas()
                    except sqlite3.Error as exc:
                        mensaje = f"No se pudo leer la base SQLite: {exc}"
                        self._marca_fallo(fuente, huella, mensaje, inicio)
                        errores.append(f"{fuente['nombre']}: {mensaje}")
                        continue
                    registros = sum(f["sistema"] == fuente["sistema"] for f in filas)
                    self._guardar_fuente(fuente, huella, self._fecha_dato(fuente), self._ahora(),
                                         "ok", None, registros)
                    procesados += registros

            if not cambios:
                estado = "sin_cambios"
                self._historial(
                    {"fuente_id": "sync:local", "nombre": "Fuentes locales"},
                    inicio, estado, 0, "Hash, fecha y tamaño sin cambios; no se reimportó.",
                )
            else:
                estado = "error" if errores else "ok"
                for fuente_id, (fuente, _) in cambios.items():
                    with self._conexion() as conexion:
                        estado_fuente = conexion.execute(
                            "SELECT estado, registros FROM fuentes_sync WHERE fuente_id=?",
                            [fuente_id],
                        ).fetchone()
                    if estado_fuente and estado_fuente["estado"] == "ok":
                        self._historial(
                            fuente, inicio, "ok", int(estado_fuente["registros"]),
                            "Sincronización completada.",
                        )
            resultado = {
                "estado": estado,
                "fuentes_cambiadas": len(cambios),
                "registros_procesados": procesados,
                "errores": errores,
                "iniciada_en": inicio,
                "terminada_en": self._ahora(),
            }
            if self.al_actualizar:
                self.al_actualizar()
            return resultado

    def _fecha_dato_archivo(self, area: str, nombre: str) -> str | None:
        esquema = ESQUEMAS[area]
        columna = next((c.nombre for c in esquema.columnas if c.tipo == "fecha"), None)
        if not columna or self.catalogo.area_disponible(area) is not None:
            return None
        valor = self.catalogo.consultar(
            f"SELECT MAX({columna}) AS fecha FROM {area} WHERE _archivo = ?", [nombre]
        )[0]["fecha"]
        return valor.isoformat() if hasattr(valor, "isoformat") else str(valor) if valor else None

    def _vigencia(self, fila: dict[str, Any]) -> tuple[str, str | None, int]:
        limites = self.cfg.vigencia_dias_por_fuente or {}
        clave = fila.get("area") or fila["fuente_id"]
        dias = limites.get(clave, limites.get("default", 7))
        if not fila.get("ultima_sincronizacion"):
            return "fecha_no_disponible", "Fecha no disponible: no hay sincronización exitosa registrada.", dias
        if fila["estado"] == "error":
            return "error", "Falló la última sincronización; se conserva la última versión válida.", dias
        ultima = self._como_datetime(fila["ultima_sincronizacion"])
        if ultima is None:
            return "fecha_no_disponible", "Fecha no disponible: la fecha registrada no es válida.", dias
        edad = datetime.now().astimezone() - ultima
        if edad.total_seconds() > dias * 86400:
            return "desactualizada", f"Última sincronización hace más de {dias} día(s).", dias
        return "vigente", None, dias

    def fuentes(self) -> list[dict[str, Any]]:
        inventario = self._inventario()
        with self._conexion() as conexion:
            filas = [dict(f) for f in conexion.execute(
                "SELECT * FROM fuentes_sync ORDER BY area, nombre"
            ).fetchall()]
        por_id = {f["fuente_id"]: f for f in filas}
        resultado = []
        for fuente_id, fuente in inventario.items():
            guardada = por_id.get(fuente_id, {})
            path = fuente["ruta"]
            try:
                modificado = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
            except FileNotFoundError:
                modificado = guardada.get("fecha_modificacion")
            fila = {**guardada, **{k: v for k, v in fuente.items() if k != "ruta"}}
            fila.pop("ruta", None)
            fila["fecha_modificacion_archivo"] = modificado
            fila["fecha_dato"] = guardada.get("fecha_dato")
            fila["ultima_sincronizacion"] = guardada.get("ultima_sincronizacion")
            fila["registros"] = guardada.get("registros", 0)
            fila["estado_sincronizacion"] = guardada.get("estado", "no_disponible")
            fila["error"] = guardada.get("error")
            if not path.exists():
                fila["estado_sincronizacion"] = "error" if fila["ultima_sincronizacion"] else "no_disponible"
                fila["error"] = f"{fuente['nombre']} no está disponible."
            vigencia, advertencia, dias = self._vigencia({
                **fila,
                "fuente_id": fuente_id,
                "estado": fila["estado_sincronizacion"],
            })
            fila["vigencia"] = vigencia
            fila["umbral_dias"] = dias
            fila["advertencia"] = advertencia
            resultado.append(fila)
        known = set(inventario)
        for fuente_id, fila in por_id.items():
            if fuente_id not in known and fuente_id.startswith("archivo:"):
                no_disponible = bool(fila.get("ruta") and not Path(fila["ruta"]).exists())
                fila["estado_sincronizacion"] = fila["estado"]
                if no_disponible:
                    fila["estado_sincronizacion"] = "error" if fila.get("ultima_sincronizacion") else "no_disponible"
                    fila["error"] = f"{fila['nombre']} no está disponible."
                fila["fecha_modificacion_archivo"] = fila["fecha_modificacion"]
                fila["vigencia"], fila["advertencia"], fila["umbral_dias"] = self._vigencia({
                    **fila,
                    "estado": fila["estado_sincronizacion"],
                })
                fila.pop("ruta", None)
                resultado.append(fila)
        return resultado

    def enriquecer_consulta(self, respuesta: dict[str, Any]) -> dict[str, Any]:
        estados = {(f["nombre"], f.get("area")): f for f in self.fuentes()}
        advertencias = list(respuesta.get("advertencias", []))
        for multa in respuesta.get("multas", []):
            detalle = []
            for fuente in multa.get("fuentes", []):
                estado = estados.get((fuente.get("nombre"), fuente.get("area")))
                if estado is None:
                    estado = next((f for (nombre, _), f in estados.items() if nombre == fuente.get("nombre")), None)
                if estado is None:
                    fuente.update({
                        "fecha_dato": None, "fecha_modificacion_archivo": None,
                        "ultima_sincronizacion": None, "vigencia": "fecha_no_disponible",
                    })
                    continue
                for campo in (
                    "fecha_dato", "fecha_modificacion_archivo", "ultima_sincronizacion",
                    "vigencia", "advertencia", "umbral_dias",
                ):
                    fuente[campo] = estado.get(campo)
                detalle.append({
                    "fuente": fuente.get("nombre"),
                    "fecha_dato": fuente.get("fecha_dato"),
                    "fecha_modificacion_archivo": fuente.get("fecha_modificacion_archivo"),
                    "ultima_sincronizacion": fuente.get("ultima_sincronizacion"),
                    "vigencia": fuente.get("vigencia"),
                })
                if fuente.get("advertencia"):
                    advertencia = f"{fuente['nombre']}: {fuente['advertencia']}"
                    if advertencia not in advertencias:
                        advertencias.append(advertencia)
            multa["fechas_fuentes"] = detalle
        respuesta["advertencias"] = advertencias
        return respuesta

    def historial(self, limite: int = 50) -> list[dict[str, Any]]:
        with self._conexion() as conexion:
            return [dict(fila) for fila in conexion.execute(
                "SELECT * FROM historial_sync ORDER BY id DESC LIMIT ?", [limite]
            ).fetchall()]

    def iniciar(self) -> None:
        intervalo = self.cfg.intervalo_sincronizacion_segundos
        if intervalo <= 0 or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()

        def ejecutar() -> None:
            while not self._stop.wait(intervalo):
                try:
                    self.sincronizar()
                except Exception as exc:
                    logging.getLogger(__name__).exception("Falló la sincronización programada.")
                    self._historial(
                        {"fuente_id": "sync:local", "nombre": "Fuentes locales"},
                        self._ahora(), "error", 0, f"{type(exc).__name__}: {exc}",
                    )

        self._thread = threading.Thread(target=ejecutar, name="capibot-sync", daemon=True)
        self._thread.start()

    def detener(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
