"""Consolida multas ficticias de SQLite y archivos Excel/CSV conservando la evidencia."""
from __future__ import annotations

import csv
import hashlib
import re
import threading
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

from ..ingestion.loader import (
    ErrorImportacion,
    _registro_homologado,
    convertir,
    homologar_encabezados,
    normalizar_encabezado,
    sin_acentos,
)
from ..ingestion.schemas import Columna, NOMBRES_AREAS

_COLUMNAS_MULTAS = (
    Columna("documento", "texto", obligatoria=False),
    Columna("id_cliente", "id", obligatoria=False, patron=r"CL-\d{1,6}"),
    Columna("id_expediente", "id", obligatoria=False, patron=r"EX-\d{1,8}"),
    Columna("id_multa", "id", patron=r"MU-\d{1,8}"),
    Columna("ciudadano", "texto", obligatoria=False),
    Columna("area", "texto", obligatoria=False),
    Columna("fecha_infraccion", "fecha", obligatoria=False),
    Columna("estado", "texto", obligatoria=False),
    Columna("importe", "dinero", obligatoria=False),
    Columna("actualizado_en", "texto", obligatoria=False),
)
_IDENTIFICADORES = {
    "documento": re.compile(r"\d{8}"),
    "id_expediente": re.compile(r"EX-\d{1,8}", re.IGNORECASE),
    "id_multa": re.compile(r"MU-\d{1,8}", re.IGNORECASE),
}
_AREAS_CANONICAS = {normalizar_encabezado(nombre): nombre for nombre in NOMBRES_AREAS.values()}


class FuenteSistemas(Protocol):
    def multas(self, campo: str, valor: str) -> list[dict[str, Any]]: ...
    def todas_las_multas(self) -> list[dict[str, Any]]: ...
    def ciudadano_por_documento(self, documento: str) -> dict[str, Any] | None: ...
    def ciudadano_por_cliente(self, id_cliente: str) -> dict[str, Any] | None: ...


def _nulo(valor: Any) -> bool:
    return valor is None or (isinstance(valor, str) and not valor.strip())


def _valor_fecha(valor: Any) -> str | None:
    if valor is None:
        return None
    return valor.isoformat() if isinstance(valor, date) else str(valor)


def _source(kind: str, name: str, **extra: Any) -> dict[str, Any]:
    return {"tipo": kind, "nombre": name, **extra}


class ConsultaMultas:
    def __init__(self, fuente_sistemas: FuenteSistemas, catalogo):
        self.catalogo = catalogo
        self.sqlite = fuente_sistemas
        self.archivo_extra = Path(catalogo.datos_dir) / "multas_demo.csv"
        self.sincronizador = None
        self._extra_lock = threading.RLock()
        self._extra_hash: str | None = None
        self._extra_registros: list[dict[str, Any]] = []
        self._extra_errores: list[dict[str, Any]] = []

    def validar_busqueda(self, tipo: str, valor: str) -> str:
        patron = _IDENTIFICADORES.get(tipo)
        valor = valor.strip()
        if patron is None or not patron.fullmatch(valor):
            raise ValueError("Usa un documento de 8 dígitos, expediente EX-######## o multa MU-########.")
        return valor.upper() if tipo != "documento" else valor

    def buscar(self, tipo: str, valor: str) -> dict[str, Any]:
        valor = self.validar_busqueda(tipo, valor)
        registros = self._registros_sistemas(tipo, valor)
        registros.extend(self._registros_excel(tipo, valor))
        consolidados = self._consolidar(registros)
        _, errores_extra = self._leer_excel_extra()
        pendientes = [
            multa for multa in consolidados
            if sin_acentos(str(multa.get("estado") or "").lower()) == "pendiente"
        ]
        importes_conocidos = [
            Decimal(str(multa["importe"]))
            for multa in pendientes
            if multa.get("importe") not in (None, "")
        ]
        respuesta = {
            "tipo_busqueda": tipo,
            "valor_busqueda": valor,
            "total": len(consolidados),
            "multas": consolidados,
            "advertencias": (
                [i for multa in consolidados for i in multa["inconsistencias"]]
                + [{"tipo": error["tipo"], "detalle": error["detalle"]} for error in errores_extra]
            ),
            "multas_pendientes_encontradas": len(pendientes),
            "importe_pendiente_total_registrado": str(sum(importes_conocidos, Decimal("0.00"))),
            "multas_pendientes_sin_importe": len(pendientes) - len(importes_conocidos),
        }
        return self.sincronizador.enriquecer_consulta(respuesta) if self.sincronizador else respuesta

    def _registros_sistemas(self, tipo: str, valor: str) -> list[dict[str, Any]]:
        filas = self.sqlite.multas(tipo, valor)
        resultado = []
        for fila in filas:
            importe = fila.get("importe")
            try:
                importe = f"{Decimal(str(importe)).quantize(Decimal('0.01')):.2f}" if importe is not None else None
            except InvalidOperation:
                importe = str(importe)
            resultado.append({
                **fila,
                "fecha_infraccion": fila["fecha_infraccion"],
                "actualizado_en": fila["actualizado_en"],
                "importe": importe,
                "_clase": "sistema",
                "_fuentes": [_source("Base de datos", fila["sistema"],
                                     tabla=f"multas_{fila['sistema'].lower().replace(' ', '_')}",
                                     area=fila.get("area"),
                                     fecha_dato=fila.get("actualizado_en") or fila.get("fecha_infraccion"),
                                     actualizado_en=fila["actualizado_en"])],
            })
        return resultado

    def _registros_excel(self, tipo: str, valor: str) -> list[dict[str, Any]]:
        filas: list[dict[str, Any]] = []
        ciudadano = None
        if tipo == "documento":
            ciudadano = self.sqlite.ciudadano_por_documento(valor)
        if ciudadano:
            filas.extend(self._fiscalizacion_por_cliente(ciudadano["id_cliente"]))
        elif tipo == "id_multa":
            filas.extend(self._fiscalizacion_por_multa(valor))

        extras, _ = self._leer_excel_extra()
        for registro in extras:
            coincide = (
                registro.get("documento") == valor if tipo == "documento"
                else registro.get("id_expediente") == valor if tipo == "id_expediente"
                else registro.get("id_multa") == valor
            )
            if coincide:
                filas.append(registro)
        return filas

    def _fiscalizacion_por_cliente(self, id_cliente: str) -> list[dict[str, Any]]:
        filas = self.catalogo.consultar("SELECT * FROM fiscalizacion WHERE id_cliente = ? ORDER BY id_multa", [id_cliente])
        return [self._fila_fiscalizacion(fila) for fila in filas]

    def _fiscalizacion_por_multa(self, id_multa: str) -> list[dict[str, Any]]:
        filas = self.catalogo.consultar("SELECT * FROM fiscalizacion WHERE id_multa = ? ORDER BY _fila", [id_multa])
        return [self._fila_fiscalizacion(fila) for fila in filas]

    def _fila_fiscalizacion(self, fila: dict[str, Any]) -> dict[str, Any]:
        ciudadano = self.sqlite.ciudadano_por_cliente(fila["id_cliente"])
        fuente = _source(
            "Excel",
            fila["_archivo"],
            archivo=fila["_archivo"],
            hoja=fila["_hoja"],
            fila=fila["_fila"],
            version=fila["_version"],
            area="Fiscalización",
            fecha_dato=_valor_fecha(fila["fecha_acta"]),
            fecha_modificacion_archivo=fila["_modificado_en"],
            ultima_sincronizacion=fila["_importado_en"],
        )
        return {
            "id_multa": fila["id_multa"],
            "id_cliente": fila["id_cliente"],
            "documento": ciudadano["documento"] if ciudadano else None,
            "ciudadano": ciudadano["ciudadano"] if ciudadano else None,
            "id_expediente": None,
            "area": "Fiscalización",
            "fecha_infraccion": _valor_fecha(fila["fecha_acta"]),
            "estado": fila["estado_acta"],
            "importe": None,
            "actualizado_en": None,
            "_clase": "excel",
            "_fuentes": [fuente],
        }

    def _leer_excel_extra(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        with self._extra_lock:
            if not self.archivo_extra.exists():
                error = [{"tipo": "Fuente no disponible",
                          "detalle": f"{self.archivo_extra.name} no está disponible."}]
                return list(self._extra_registros), error
            huella = hashlib.sha256()
            try:
                with self.archivo_extra.open("rb") as archivo:
                    for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
                        huella.update(bloque)
            except OSError as exc:
                error = [{"tipo": "Fuente no disponible",
                          "detalle": f"No se pudo leer {self.archivo_extra.name}: {exc}"}]
                return list(self._extra_registros), error
            digest = huella.hexdigest()
            if digest == self._extra_hash:
                return list(self._extra_registros), list(self._extra_errores)
            try:
                registros, errores = self._leer_excel_extra_actual(digest)
            except (OSError, UnicodeError, csv.Error) as exc:
                error = [{"tipo": "Fuente no disponible",
                          "detalle": f"No se pudo procesar {self.archivo_extra.name}: {exc}"}]
                return list(self._extra_registros), error
            fallo_estructura = any(e.get("fila") is None for e in errores)
            if fallo_estructura:
                return list(self._extra_registros), errores
            self._extra_hash = digest
            self._extra_registros = registros
            self._extra_errores = errores
            return list(registros), list(errores)

    def _leer_excel_extra_actual(
        self, huella: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        errores: list[dict[str, Any]] = []
        registros: list[dict[str, Any]] = []
        with self.archivo_extra.open("r", encoding="utf-8-sig", newline="") as archivo:
            muestra = archivo.read(8192)
            archivo.seek(0)
            try:
                delimitador = csv.Sniffer().sniff(muestra, delimiters=",;\t|").delimiter
            except csv.Error:
                delimitador = ","
            lector = csv.reader(archivo, delimiter=delimitador)
            try:
                encabezado = [str(c).strip() for c in next(lector)]
            except StopIteration:
                return [], [{"tipo": "Archivo vacío", "detalle": "El CSV de multas no contiene encabezados."}]
            try:
                asignados, _ = homologar_encabezados("multas", encabezado, _COLUMNAS_MULTAS)
            except ErrorImportacion as exc:
                return [], [{"tipo": "Encabezados inválidos", "detalle": str(exc)}]
            campos = {campo for campo in asignados.values()}
            if not ({"documento", "id_cliente"} & campos):
                return [], [{"tipo": "Campo obligatorio incompleto",
                             "detalle": f"{self.archivo_extra.name} debe identificar al ciudadano por documento o id_cliente."}]
            for numero, fila in enumerate(lector, start=2):
                if all(_nulo(valor) for valor in fila):
                    continue
                crudo = _registro_homologado(encabezado, asignados, fila)
                convertido: dict[str, Any] = {}
                causas = []
                for columna in _COLUMNAS_MULTAS:
                    original = crudo.get(columna.nombre)
                    valor, error = convertir(columna, original)
                    convertido[columna.nombre] = valor
                    if error:
                        causas.append(error)
                documento = str(convertido.get("documento") or "").strip()
                if documento and not _IDENTIFICADORES["documento"].fullmatch(documento):
                    causas.append(f"documento inválido: {documento!r}")
                id_cliente = convertido.get("id_cliente")
                if documento and id_cliente:
                    ciudadano_por_dni = self.sqlite.ciudadano_por_documento(documento)
                    if ciudadano_por_dni and ciudadano_por_dni["id_cliente"] != id_cliente:
                        causas.append("documento e id_cliente identifican ciudadanos distintos")
                ciudadano = (self.sqlite.ciudadano_por_documento(documento) if documento
                             else self.sqlite.ciudadano_por_cliente(id_cliente) if id_cliente else None)
                if ciudadano is None:
                    causas.append("persona no encontrada en las bases de datos locales")
                if causas:
                    errores.append({
                        "tipo": "Fecha inválida" if any("fecha inválida" in causa for causa in causas)
                        else "Campo obligatorio incompleto" if any("vacío" in causa or "ciudadano" in causa for causa in causas)
                        else "Registro de Excel rechazado",
                        "detalle": f"{self.archivo_extra.name}, fila {numero}: {'; '.join(causas)}",
                        "archivo": self.archivo_extra.name,
                        "hoja": "multas",
                        "fila": numero,
                    })
                    continue
                extras = {k: v for k, v in crudo.items() if k not in {c.nombre for c in _COLUMNAS_MULTAS}}
                registros.append({
                    **convertido,
                    "documento": documento or ciudadano["documento"],
                    "id_cliente": ciudadano["id_cliente"],
                    "ciudadano": convertido.get("ciudadano") or ciudadano["ciudadano"],
                    "area": _AREAS_CANONICAS.get(
                        normalizar_encabezado(str(convertido.get("area") or "")),
                        convertido.get("area"),
                    ),
                    "fecha_infraccion": _valor_fecha(convertido.get("fecha_infraccion")),
                    "estado": sin_acentos(str(convertido.get("estado") or "").strip().lower()) or None,
                    "importe": str(convertido["importe"]) if convertido.get("importe") is not None else None,
                    "actualizado_en": convertido.get("actualizado_en"),
                    "extra_campos": extras,
                    "_clase": "excel",
                    "_fuentes": [_source(
                        "Excel",
                        self.archivo_extra.name,
                        archivo=self.archivo_extra.name,
                        hoja="multas",
                        fila=numero,
                        hash=huella[:12],
                        area=convertido.get("area"),
                        fecha_dato=_valor_fecha(convertido.get("actualizado_en") or convertido.get("fecha_infraccion")),
                        fecha_modificacion_archivo=datetime.fromtimestamp(
                            self.archivo_extra.stat().st_mtime
                        ).astimezone().isoformat(timespec="seconds"),
                        actualizado_en=convertido.get("actualizado_en"),
                    )],
                })
        return registros, errores

    @staticmethod
    def _consolidar(registros: list[dict[str, Any]]) -> list[dict[str, Any]]:
        agrupados: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for fila in registros:
            agrupados[fila["id_multa"]].append(fila)
        resultado = []
        for id_multa, filas in sorted(agrupados.items()):
            advertencias: list[dict[str, Any]] = []
            fuentes = [
                {
                    **fuente,
                    "fecha_infraccion": fila.get("fecha_infraccion"),
                    "fecha_dato": fuente.get("fecha_dato") or _valor_fecha(fila.get("fecha_infraccion")),
                    "estado": fila.get("estado"),
                    "importe": fila.get("importe"),
                }
                for fila in filas
                for fuente in fila["_fuentes"]
            ]
            sistemas = [fila for fila in filas if fila["_clase"] == "sistema"]
            excels = [fila for fila in filas if fila["_clase"] == "excel"]
            if not sistemas:
                advertencias.append({"tipo": "Solo en Excel", "detalle": "La multa aparece en Excel, pero no en las bases de datos locales."})
            if not excels:
                advertencias.append({"tipo": "Solo en bases de datos", "detalle": "La multa aparece en las bases de datos locales, pero no en Excel."})
            for campo, etiqueta in (("estado", "Estado contradictorio"), ("importe", "Importe contradictorio")):
                valores = sorted({str(fila[campo]) for fila in filas if fila.get(campo) not in (None, "")})
                if len(valores) > 1:
                    advertencias.append({
                        "tipo": etiqueta,
                        "detalle": f"Las fuentes registran valores diferentes: {', '.join(valores)}.",
                    })
            origenes = [fila.get("_fuentes", [{}])[0].get("nombre") for fila in filas]
            if len(origenes) != len(set(origenes)):
                advertencias.append({"tipo": "Registro duplicado", "detalle": "La multa aparece repetida dentro de una misma fuente."})
            ciudadano = next((fila for fila in filas if fila.get("ciudadano")), filas[0])
            resultado.append({
                "id_multa": id_multa,
                "documento": ciudadano.get("documento"),
                "ciudadano": ciudadano.get("ciudadano"),
                "area": _valor_unico(filas, "area"),
                "id_expediente": _valor_unico(filas, "id_expediente"),
                "fecha_infraccion": _valor_unico(filas, "fecha_infraccion"),
                "estado": _valor_unico(filas, "estado") or ("Contradictorio" if any(i["tipo"] == "Estado contradictorio" for i in advertencias) else None),
                "importe": _valor_unico(filas, "importe"),
                "fecha_actualizacion": _valor_unico(filas, "actualizado_en"),
                "fuentes": fuentes,
                "inconsistencias": advertencias,
            })
        return resultado

    def _todos_los_registros(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        registros = self.sqlite.todas_las_multas()
        registros = [
            {
                **fila,
                "importe": f"{Decimal(str(fila['importe'])).quantize(Decimal('0.01')):.2f}"
                if fila.get("importe") is not None else None,
                "_clase": "sistema",
                "_fuentes": [_source(
                    "Base de datos", fila["sistema"], area=fila.get("area"),
                    fecha_dato=fila.get("actualizado_en") or fila.get("fecha_infraccion"),
                    actualizado_en=fila["actualizado_en"],
                )],
            }
            for fila in registros
        ]
        excel = self.catalogo.consultar("SELECT * FROM fiscalizacion ORDER BY _fila")
        registros.extend(self._fila_fiscalizacion(fila) for fila in excel)
        extras, errores = self._leer_excel_extra()
        registros.extend(extras)
        return registros, errores

    def inconsistencias(self) -> dict[str, Any]:
        registros, errores = self._todos_los_registros()
        consolidados = self._consolidar(registros)
        incidencias = []
        por_ciudadano: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for multa in consolidados:
            for inconsistencia in multa["inconsistencias"]:
                incidencias.append({
                    **inconsistencia,
                    "id_multa": multa["id_multa"],
                    "documento": multa["documento"],
                    "area": multa.get("area"),
                    "fuentes": multa["fuentes"],
                })
            if multa["documento"]:
                por_ciudadano[multa["documento"]].append(multa)
        for documento, multas in por_ciudadano.items():
            if len(multas) > 1:
                incidencias.append({
                    "tipo": "Varias multas para un ciudadano",
                    "detalle": f"El documento {documento} tiene {len(multas)} multas distintas.",
                    "documento": documento,
                    "id_multa": None,
                    "area": _valor_unico(multas, "area"),
                    "fuentes": [fuente for multa in multas for fuente in multa["fuentes"]],
                })
        for error in errores:
            incidencias.append({**error, "id_multa": None, "fuentes": [
                _source("Excel", error.get("archivo", self.archivo_extra.name),
                        archivo=error.get("archivo"), hoja=error.get("hoja"), fila=error.get("fila"))
            ]})
        for rechazo in self.catalogo.rechazos():
            if rechazo["area"] == "fiscalizacion":
                causa = rechazo["causa"]
                tipo = "Registro duplicado" if "duplicado:" in causa else "Registro Excel rechazado"
                incidencias.append({
                    "tipo": tipo,
                    "detalle": f"{rechazo['archivo']}, fila {rechazo['fila']}: {causa}",
                    "id_multa": None,
                    "area": rechazo["area"],
                    "fuentes": [_source("Excel", rechazo["archivo"], archivo=rechazo["archivo"],
                                        hoja=rechazo["hoja"], fila=rechazo["fila"],
                                        area=rechazo["area"])],
                })
        return {
            "total": len(incidencias),
            "por_tipo": dict(sorted(_contar_tipos(incidencias).items())),
            "inconsistencias": incidencias,
        }

    def resumen(self) -> dict[str, Any]:
        registros, _ = self._todos_los_registros()
        multas = self._consolidar(registros)
        areas = {multa["area"] for multa in multas if multa["area"]}
        actualizaciones = [
            fila.get("actualizado_en")
            for fila in registros
            if fila.get("actualizado_en")
        ]
        return {
            "registros_integrados": len(multas),
            "areas": len(areas),
            "multas_pendientes": sum(multa["estado"] == "pendiente" for multa in multas),
            "inconsistencias": self.inconsistencias()["total"],
            "ultima_actualizacion": max(actualizaciones) if actualizaciones else None,
        }

    def fuentes(self) -> list[dict[str, Any]]:
        filas = []
        for carga in self.catalogo.cargas:
            for archivo in carga.get("archivos", []):
                filas.append({
                    "tipo": "Excel/CSV",
                    "nombre": archivo["archivo"],
                    "area": carga["area"],
                    "estado": carga["estado"],
                    "registros": archivo["validas"],
                    "actualizado_en": archivo["modificado_en"],
                })
        filas_si = self.sqlite.todas_las_multas()
        for sistema in ("SI A", "SI B"):
            filas.append({
                "tipo": "SQLite (solo lectura)",
                "nombre": sistema,
                "area": None,
                "estado": "ok",
                "registros": sum(fila["sistema"] == sistema for fila in filas_si),
                "actualizado_en": None,
            })
        extra, errores = self._leer_excel_extra()
        filas.append({
            "tipo": "Excel complementario",
            "nombre": self.archivo_extra.name,
            "area": "fiscalizacion",
            "estado": "ok" if not errores else "con avisos",
            "registros": len(extra),
            "actualizado_en": date.fromtimestamp(self.archivo_extra.stat().st_mtime).isoformat()
            if self.archivo_extra.exists() else None,
        })
        return filas


def _valor_unico(filas: list[dict[str, Any]], campo: str) -> Any:
    valores = []
    for fila in filas:
        valor = fila.get(campo)
        if valor not in (None, "") and valor not in valores:
            valores.append(valor)
    return valores[0] if len(valores) == 1 else None


def _contar_tipos(incidencias: list[dict[str, Any]]) -> dict[str, int]:
    conteos: dict[str, int] = defaultdict(int)
    for incidencia in incidencias:
        conteos[incidencia["tipo"]] += 1
    return dict(conteos)
