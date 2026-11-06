"""Nivel A (sin IA): interpretación de preguntas por reglas y palabras clave.

NO es un agente autónomo: reconoce un conjunto cerrado de intenciones y elige herramientas predefinidas.
Si no entiende la pregunta lo dice, en lugar de adivinar.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..ingestion.loader import sin_acentos

_ID = re.compile(r"\b(pe|cl)\s*-?\s*(\d{1,4})\b")
SUGERENCIAS = [
    "¿Cuál es el estado del pedido PE-015?",
    "¿Está entregado y pagado?",
    "¿Qué facturas tiene pendientes el cliente CL-001?",
    "¿Qué entregas están atrasadas?",
    "¿Qué clientes tienen facturas pendientes y reclamos abiertos?",
    "¿Hay pedidos completados en Operaciones que aún no figuran entregados en Logística?",
    "¿Qué inconsistencias hay entre las áreas?",
    "¿Por qué se atrasó el pedido PE-030?",
    "¿Se notificó al cliente?",
    "¿Cuánto interés corresponde pagar por PE-015?",
]


@dataclass
class Plan:
    intencion: str
    llamadas: list[tuple[str, dict]] = field(default_factory=list)
    mensaje: str | None = None  # respuesta fija cuando no hay herramienta que llamar
    notas: list[str] = field(default_factory=list)
    pedido: str | None = None  # para mantener el contexto de la conversación


def extraer_ids(pregunta: str) -> tuple[list[str], list[str]]:
    pedidos, clientes = [], []
    for pref, num in _ID.findall(sin_acentos(pregunta.lower())):
        ident = f"{pref.upper()}-{int(num):03d}"
        destino = pedidos if pref == "pe" else clientes
        if ident not in destino:
            destino.append(ident)
    return pedidos, clientes


def interpretar(pregunta: str, contexto: dict | None = None) -> Plan:
    n = sin_acentos(pregunta.lower())
    pedidos, clientes = extraer_ids(pregunta)
    notas: list[str] = []
    contexto = contexto or {}

    def tiene(*palabras: str) -> bool:
        return any(p in n for p in palabras)

    if tiene("notific"):
        return Plan("no_verificable", mensaje=("No puedo verificar si se notificó al cliente: ninguna de las cuatro fuentes "
                                               "(Finanzas, Operaciones, Logística, Atención) registra notificaciones. "
                                               "No consta, y tampoco puedo afirmar que no ocurrió."), pedido=pedidos[0] if pedidos else None)
    if tiene("interes", "mora", "multa", "recargo"):
        if pedidos or clientes:
            llamada = ("consultar_facturas", {"id_pedido": pedidos[0]} if pedidos else {"id_cliente": clientes[0]})
            return Plan("interes_no_calculable", [llamada], notas=[
                "No calculo intereses: no hay una regla de interés aprobada en las fuentes. Lo que sigue es el importe tal como está registrado en Finanzas, no un cálculo de interés."],
                pedido=pedidos[0] if pedidos else None)
        return Plan("interes_no_calculable", mensaje="No calculo intereses: no hay una regla de interés aprobada en las fuentes. Indica un pedido o cliente para ver el importe registrado.")

    if tiene("por que", "porque", "causa", "razon", "motivo") and tiene("atras", "retras", "demor"):
        pid = pedidos[0] if pedidos else contexto.get("ultimo_pedido")
        if not pid:
            return Plan("causa_atraso", mensaje="Indica el pedido (por ejemplo PE-030) para revisar su demora.")
        if not pedidos:
            notas.append(f"Asumo que te refieres a {pid} (consulta anterior).")
        notas.append("Las fuentes muestran fechas y estados, pero no registran la causa de la demora: no consta por qué ocurrió.")
        return Plan("causa_atraso", [("consultar_pedido", {"id_pedido": pid})], notas=notas, pedido=pid)

    if tiene("pendiente") and tiene("reclamo", "ticket") and not pedidos:
        return Plan("cruce_deuda_reclamo", [("clientes_deuda_y_reclamo", {})])
    if tiene("completad") and tiene("logistica", "entregad") and not pedidos:
        return Plan("cruce_ops_logistica", [("operaciones_vs_logistica", {})])
    if tiene("inconsisten", "contradic", "calidad", "discrepan", "incongruen"):
        return Plan("inconsistencias", [("listar_inconsistencias", {})])
    if tiene("atrasad", "retras", "demorad") and not pedidos and not clientes and (tiene("entregas", "pedidos") or not contexto.get("ultimo_pedido")):
        return Plan("entregas_atrasadas", [("consultar_entregas", {"solo_atrasadas": True})])

    if pedidos:
        if tiene("factura") and not tiene("entreg", "pagad", "estado", "pago"):
            return Plan("facturas_pedido", [("consultar_facturas", {"id_pedido": p, "solo_pendientes": False}) for p in pedidos[:3]], pedido=pedidos[0])
        return Plan("estado_pedido", [("consultar_pedido", {"id_pedido": p}) for p in pedidos[:3]], pedido=pedidos[0])
    if clientes:
        if tiene("factura", "deuda", "pendiente", "debe", "saldo", "adeuda"):
            return Plan("facturas_cliente", [("consultar_facturas", {"id_cliente": clientes[0], "solo_pendientes": True})])
        return Plan("resumen_cliente", [("consultar_cliente", {"id_cliente": clientes[0]})])

    ultimo = contexto.get("ultimo_pedido")
    if ultimo and tiene("entreg", "pagad", "pago", "estado", "atras", "retras", "factura", "reclamo"):
        return Plan("estado_pedido", [("consultar_pedido", {"id_pedido": ultimo})], notas=[f"Asumo que te refieres a {ultimo} (consulta anterior)."], pedido=ultimo)

    return Plan("no_entendida", mensaje=("No pude relacionar tu pregunta con las consultas que esta demo sabe resolver, así que prefiero no adivinar. "
                                         "Prueba con un identificador (PE-015, CL-001) o con alguna de las sugerencias."))
