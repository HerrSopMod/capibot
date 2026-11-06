"""Plantilla de esquema por área: qué hoja se espera, qué columnas y de qué tipo."""
from __future__ import annotations

from dataclasses import dataclass

ID_CLIENTE = r"CL-\d+"
ID_PEDIDO = r"PE-\d+"


@dataclass(frozen=True)
class Columna:
    nombre: str
    tipo: str  # id | texto | fecha | dinero | estado
    obligatoria: bool = True
    patron: str | None = None  # para ids
    vocabulario: frozenset[str] | None = None  # para estados
    avisa_vacio: bool = True  # False si estar vacío es normal (p. ej. fecha_real de una entrega pendiente)


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
        Columna("fecha_real", "fecha", obligatoria=False, avisa_vacio=False),
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
}

AREAS = tuple(ESQUEMAS)
