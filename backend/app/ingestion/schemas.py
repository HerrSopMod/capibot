"""Plantilla de esquema por área: qué hoja se espera, qué columnas y de qué tipo."""
from __future__ import annotations

from dataclasses import dataclass

ID_CLIENTE = r"CL-\d+"
ID_PEDIDO = r"PE-\d+"
ID_MULTA = r"MU-\d+"


@dataclass(frozen=True)
class Columna:
    nombre: str
    tipo: str  # id | texto | fecha | dinero | estado
    obligatoria: bool = True
    patron: str | None = None  # para ids
    vocabulario: frozenset[str] | None = None  # para estados


@dataclass(frozen=True)
class Esquema:
    area: str
    archivo: str
    hoja: str
    clave: str
    columnas: tuple[Columna, ...]


def _estado(nombre: str, *valores: str) -> Columna:
    return Columna(nombre, "estado", True, vocabulario=frozenset(valores))


ESQUEMAS: dict[str, Esquema] = {
    "finanzas": Esquema("finanzas", "finanzas.xlsx", "Facturas", "id_factura", (
        Columna("id_cliente", "id", patron=ID_CLIENTE),
        Columna("id_factura", "id", patron=r"FA-\d+"),
        Columna("id_pedido", "id", patron=ID_PEDIDO),
        Columna("fecha_emision", "fecha", obligatoria=False),
        Columna("fecha_vencimiento", "fecha", obligatoria=False),
        Columna("importe", "dinero"),
        Columna("saldo", "dinero"),
        _estado("estado_pago", "pagado", "pendiente", "parcial"),
    )),
    "operaciones": Esquema("operaciones", "operaciones.xlsx", "Pedidos", "id_pedido", (
        Columna("id_pedido", "id", patron=ID_PEDIDO),
        Columna("id_cliente", "id", patron=ID_CLIENTE),
        Columna("fecha_solicitud", "fecha", obligatoria=False),
        Columna("responsable", "texto", obligatoria=False),
        _estado("estado_operativo", "completado", "en proceso", "cancelado"),
    )),
    "logistica": Esquema("logistica", "logistica.xlsx", "Entregas", "id_entrega", (
        Columna("id_entrega", "id", patron=r"EN-\d+"),
        Columna("id_pedido", "id", patron=ID_PEDIDO),
        Columna("fecha_estimada", "fecha", obligatoria=False),
        Columna("fecha_real", "fecha", obligatoria=False),
        _estado("estado_entrega", "entregado", "en ruta", "pendiente"),
    )),
    "atencion": Esquema("atencion", "atencion.xlsx", "Tickets", "id_ticket", (
        Columna("id_ticket", "id", patron=r"TK-\d+"),
        Columna("id_cliente", "id", patron=ID_CLIENTE),
        Columna("id_pedido", "id", patron=ID_PEDIDO),
        Columna("fecha_reclamo", "fecha", obligatoria=False),
        Columna("motivo", "texto", obligatoria=False),
        _estado("estado_ticket", "abierto", "en revision", "cerrado"),
    )),
    "fiscalizacion": Esquema("fiscalizacion", "fiscalizacion.xlsx", "Actas", "id_acta", (
        Columna("id_acta", "id", patron=r"AC-\d+"),
        Columna("id_pedido", "id", patron=ID_PEDIDO),
        Columna("id_cliente", "id", patron=ID_CLIENTE),
        Columna("id_multa", "id", patron=ID_MULTA),
        Columna("fecha_acta", "fecha"),
        _estado("estado_acta", "emitida", "observada", "anulada"),
    )),
    "recaudacion": Esquema("recaudacion", "recaudacion.xlsx", "Pagos", "id_pago", (
        Columna("id_pago", "id", patron=r"PG-\d+"),
        Columna("id_pedido", "id", patron=ID_PEDIDO),
        Columna("id_cliente", "id", patron=ID_CLIENTE),
        Columna("id_multa", "id", patron=ID_MULTA),
        Columna("fecha_pago", "fecha", obligatoria=False),
        Columna("importe", "dinero"),
        _estado("estado_pago", "aplicado", "pendiente", "reversado"),
    )),
    "notificaciones": Esquema("notificaciones", "notificaciones.xlsx", "Envios", "id_notificacion", (
        Columna("id_notificacion", "id", patron=r"NT-\d+"),
        Columna("id_pedido", "id", patron=ID_PEDIDO),
        Columna("id_cliente", "id", patron=ID_CLIENTE),
        Columna("id_multa", "id", patron=ID_MULTA),
        Columna("fecha_envio", "fecha", obligatoria=False),
        Columna("canal", "texto"),
        _estado("estado_envio", "enviado", "pendiente", "devuelto"),
    )),
    "impugnaciones": Esquema("impugnaciones", "impugnaciones.xlsx", "Recursos", "id_recurso", (
        Columna("id_recurso", "id", patron=r"IM-\d+"),
        Columna("id_pedido", "id", patron=ID_PEDIDO),
        Columna("id_cliente", "id", patron=ID_CLIENTE),
        Columna("id_multa", "id", patron=ID_MULTA),
        Columna("fecha_recurso", "fecha"),
        _estado("estado_recurso", "recibido", "en revision", "resuelto"),
    )),
    "convenios": Esquema("convenios", "convenios.xlsx", "Convenios", "id_convenio", (
        Columna("id_convenio", "id", patron=r"CV-\d+"),
        Columna("id_pedido", "id", patron=ID_PEDIDO),
        Columna("id_cliente", "id", patron=ID_CLIENTE),
        Columna("id_multa", "id", patron=ID_MULTA),
        Columna("fecha_inicio", "fecha"),
        Columna("cuotas", "texto"),
        _estado("estado_convenio", "activo", "vencido", "cancelado"),
    )),
    "documentos": Esquema("documentos", "documentos.xlsx", "Documentos", "id_documento", (
        Columna("id_documento", "id", patron=r"DC-\d+"),
        Columna("id_pedido", "id", patron=ID_PEDIDO),
        Columna("id_cliente", "id", patron=ID_CLIENTE),
        Columna("id_multa", "id", patron=ID_MULTA),
        Columna("tipo_documento", "texto"),
        Columna("referencia", "texto", obligatoria=False),
        _estado("estado_documento", "registrado", "pendiente", "observado"),
    )),
}

AREAS = tuple(ESQUEMAS)
NOMBRES_AREAS = {
    "finanzas": "Finanzas",
    "operaciones": "Operaciones",
    "logistica": "Logística",
    "atencion": "Atención",
    "fiscalizacion": "Fiscalización",
    "recaudacion": "Recaudación",
    "notificaciones": "Notificaciones",
    "impugnaciones": "Impugnaciones",
    "convenios": "Convenios",
    "documentos": "Documentos",
}
