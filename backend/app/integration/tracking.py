"""Seguimiento persistente de inconsistencias con datos locales de la demo."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


ESTADOS = {"pendiente", "en_revision", "resuelta"}


class SeguimientoInconsistencias:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conexion() as conexion:
            conexion.executescript("""
                CREATE TABLE IF NOT EXISTS inconsistencias (
                    id TEXT PRIMARY KEY,
                    tipo TEXT NOT NULL,
                    area TEXT,
                    id_multa TEXT,
                    documento TEXT,
                    detalle TEXT NOT NULL,
                    fuentes TEXT NOT NULL,
                    firma TEXT NOT NULL,
                    estado TEXT NOT NULL CHECK (estado IN ('pendiente', 'en_revision', 'resuelta')),
                    observacion TEXT NOT NULL DEFAULT '',
                    activa INTEGER NOT NULL DEFAULT 1,
                    requiere_revision INTEGER NOT NULL DEFAULT 0,
                    detectada_en TEXT NOT NULL,
                    actualizada_en TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS historial_inconsistencias (
                    secuencia INTEGER PRIMARY KEY AUTOINCREMENT,
                    inconsistencia_id TEXT NOT NULL,
                    accion TEXT NOT NULL,
                    estado TEXT NOT NULL,
                    observacion TEXT NOT NULL,
                    fecha TEXT NOT NULL,
                    detalle TEXT
                );
            """)

    def _conexion(self) -> sqlite3.Connection:
        conexion = sqlite3.connect(self.path, timeout=10)
        conexion.row_factory = sqlite3.Row
        return conexion

    @staticmethod
    def _ahora() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    @staticmethod
    def _fuentes_estables(fuentes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            fuentes,
            key=lambda f: (
                str(f.get("nombre", "")), str(f.get("archivo", "")),
                str(f.get("hoja", "")), str(f.get("fila", "")),
            ),
        )

    def _identidad(self, registro: dict[str, Any]) -> tuple[str, str]:
        claves_fuente = [
            {
                "nombre": fuente.get("nombre"),
                "archivo": fuente.get("archivo"),
                "hoja": fuente.get("hoja"),
                "fila": fuente.get("fila"),
            }
            for fuente in self._fuentes_estables(registro.get("fuentes", []))
        ]
        identidad = {
            "tipo": registro["tipo"],
            "id_multa": registro.get("id_multa"),
            "documento": registro.get("documento") if not registro.get("id_multa") else None,
            "fuentes": claves_fuente,
        }
        id_inconsistencia = hashlib.sha256(
            json.dumps(identidad, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:24]
        firma = hashlib.sha256(
            json.dumps(
                {"detalle": registro.get("detalle"), "fuentes": registro.get("fuentes", [])},
                sort_keys=True, ensure_ascii=False, default=str,
            ).encode("utf-8")
        ).hexdigest()
        return id_inconsistencia, firma

    def actualizar_detecciones(self, detecciones: list[dict[str, Any]]) -> None:
        ahora = self._ahora()
        with self._conexion() as conexion:
            activas_previas = {
                fila["id"] for fila in conexion.execute(
                    "SELECT id FROM inconsistencias WHERE activa=1"
                ).fetchall()
            }
            detectadas: set[str] = set()
            for registro in detecciones:
                inconsistencia_id, firma = self._identidad(registro)
                detectadas.add(inconsistencia_id)
                anterior = conexion.execute(
                    "SELECT * FROM inconsistencias WHERE id = ?", [inconsistencia_id]
                ).fetchone()
                fuentes = json.dumps(registro.get("fuentes", []), ensure_ascii=False, default=str)
                valores = (
                    registro["tipo"], registro.get("area"), registro.get("id_multa"),
                    registro.get("documento"), registro["detalle"], fuentes,
                )
                if anterior is None:
                    conexion.execute("""
                        INSERT INTO inconsistencias
                            (id, tipo, area, id_multa, documento, detalle, fuentes, firma,
                             estado, observacion, activa, requiere_revision, detectada_en, actualizada_en)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pendiente', '', 1, 0, ?, ?)
                    """, (inconsistencia_id, *valores, firma, ahora, ahora))
                    conexion.execute("""
                        INSERT INTO historial_inconsistencias
                            (inconsistencia_id, accion, estado, observacion, fecha, detalle)
                        VALUES (?, 'detectada', 'pendiente', '', ?, ?)
                    """, (inconsistencia_id, ahora, registro["detalle"]))
                    continue

                reaparecida = inconsistencia_id not in activas_previas
                cambio_datos = anterior["firma"] != firma
                requiere = bool(anterior["requiere_revision"])
                estado = anterior["estado"]
                accion = None
                detalle_evento = None
                if reaparecida:
                    estado, requiere, accion = "pendiente", True, "reaparecida"
                    detalle_evento = "La inconsistencia volvió a detectarse después de una sincronización."
                elif cambio_datos:
                    estado, requiere, accion = "pendiente", True, "valores_actualizados"
                    detalle_evento = "Cambió la evidencia de la inconsistencia; requiere una nueva revisión."
                conexion.execute("""
                    UPDATE inconsistencias
                    SET tipo=?, area=?, id_multa=?, documento=?, detalle=?, fuentes=?, firma=?,
                        estado=?, activa=1, requiere_revision=?, actualizada_en=?
                    WHERE id=?
                """, (*valores, firma, estado, int(requiere), ahora, inconsistencia_id))
                if accion:
                    conexion.execute("""
                        INSERT INTO historial_inconsistencias
                            (inconsistencia_id, accion, estado, observacion, fecha, detalle)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        inconsistencia_id, accion, estado, anterior["observacion"],
                        ahora, detalle_evento,
                    ))
            if detectadas:
                marcadores = ",".join("?" for _ in detectadas)
                conexion.execute(
                    f"UPDATE inconsistencias SET activa=0 WHERE id NOT IN ({marcadores})",
                    sorted(detectadas),
                )
            else:
                conexion.execute("UPDATE inconsistencias SET activa=0")

    def listar(
        self,
        estado: str | None = None,
        area: str | None = None,
        tipo: str | None = None,
    ) -> dict[str, Any]:
        filtros, parametros = [], []
        if estado:
            filtros.append("i.estado = ?")
            parametros.append(estado)
        if area:
            filtros.append("i.area = ?")
            parametros.append(area)
        if tipo:
            filtros.append("i.tipo = ?")
            parametros.append(tipo)
        where = f"WHERE {' AND '.join(filtros)}" if filtros else ""
        with self._conexion() as conexion:
            total = conexion.execute(
                "SELECT COUNT(*) FROM inconsistencias WHERE activa=1"
            ).fetchone()[0]
            registros = conexion.execute(
                f"SELECT i.*, (SELECT COUNT(*) FROM historial_inconsistencias h "
                f"WHERE h.inconsistencia_id=i.id) AS cambios FROM inconsistencias i {where} "
                "ORDER BY i.activa DESC, i.actualizada_en DESC, i.id",
                parametros,
            ).fetchall()
            inconsistencias = []
            for fila in registros:
                registro = dict(fila)
                registro["fuentes"] = json.loads(registro["fuentes"])
                registro["activa"] = bool(registro["activa"])
                registro["requiere_revision"] = bool(registro["requiere_revision"])
                registro["historial"] = [
                    dict(evento) for evento in conexion.execute(
                        "SELECT accion, estado, observacion, fecha, detalle "
                        "FROM historial_inconsistencias WHERE inconsistencia_id=? ORDER BY secuencia",
                        [registro["id"]],
                    ).fetchall()
                ]
                inconsistencias.append(registro)
        return {"total": total, "inconsistencias": inconsistencias}

    def cambiar(
        self, inconsistencia_id: str, estado: str, observacion: str = "",
    ) -> dict[str, Any] | None:
        if estado not in ESTADOS:
            raise ValueError(f"Estado inválido; usa uno de: {', '.join(sorted(ESTADOS))}.")
        observacion = observacion.strip()
        ahora = self._ahora()
        with self._conexion() as conexion:
            actual = conexion.execute(
                "SELECT estado, observacion FROM inconsistencias WHERE id = ?",
                [inconsistencia_id],
            ).fetchone()
            if actual is None:
                return None
            accion = "cambio_estado" if actual["estado"] != estado else "observacion"
            conexion.execute("""
                UPDATE inconsistencias SET estado=?, observacion=?, requiere_revision=0, actualizada_en=?
                WHERE id=?
            """, (estado, observacion, ahora, inconsistencia_id))
            conexion.execute("""
                INSERT INTO historial_inconsistencias
                    (inconsistencia_id, accion, estado, observacion, fecha, detalle)
                VALUES (?, ?, ?, ?, ?, NULL)
            """, (inconsistencia_id, accion, estado, observacion, ahora))
        return next(
            (i for i in self.listar()["inconsistencias"] if i["id"] == inconsistencia_id),
            None,
        )
