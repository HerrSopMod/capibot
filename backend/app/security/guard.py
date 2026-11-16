"""Control de acceso por perfil y filtros de preguntas sensibles.

El login de la demo autentica una sola cuenta compartida; los perfiles de área aún se eligen
desde la interfaz y no sustituyen autorización por usuario ni permisos por registro.
"""
from __future__ import annotations

import re

from ..ingestion.schemas import AREAS, NOMBRES_AREAS

PERFILES: dict[str, tuple[str, frozenset[str]]] = {
    "analista": ("Analista (todas las áreas)", frozenset(AREAS)),
    "finanzas": ("Finanzas", frozenset({"finanzas"})),
    "operaciones": ("Operaciones", frozenset({"operaciones"})),
    "logistica": ("Logística", frozenset({"logistica"})),
    "atencion": ("Atención al cliente", frozenset({"atencion"})),
}
PERFILES.update({area: (NOMBRES_AREAS[area], frozenset({area}))
                 for area in AREAS if area not in PERFILES})

_SENSIBLE = re.compile(r"\b(dni|contrase[ñn]a|password|clave|token|tarjeta|cvv|pin)\b", re.IGNORECASE)


class PerfilDesconocido(Exception):
    pass


class AccesoDenegado(Exception):
    def __init__(self, perfil: str, faltantes: list[str]):
        self.perfil, self.faltantes = perfil, faltantes
        super().__init__(f"El perfil «{PERFILES[perfil][0]}» no tiene acceso a: {', '.join(faltantes)}")


def areas_permitidas(perfil: str) -> frozenset[str]:
    if perfil not in PERFILES:
        raise PerfilDesconocido(perfil)
    return PERFILES[perfil][1]


def verificar(perfil: str, areas_requeridas: tuple[str, ...] | list[str]) -> None:
    faltantes = [a for a in areas_requeridas if a not in areas_permitidas(perfil)]
    if faltantes:
        raise AccesoDenegado(perfil, faltantes)


def pregunta_sensible(texto: str) -> str | None:
    """Motivo de bloqueo si la pregunta pide o aporta credenciales o documentos de identidad."""
    if _SENSIBLE.search(texto):
        return ("Esta demo no maneja DNI, contraseñas ni credenciales: no las escribas aquí. "
                "Consulta usando identificadores de ejemplo (por ejemplo CL-001 o PE-015).")
    return None
