"""Herramientas cerradas de consulta (solo lectura, SQL parametrizado, sin SQL libre).

Las usan tanto el router por reglas (Nivel A) como el agente con LLM (Nivel B).
Los importes y conteos se calculan aquí, de forma determinista; nunca los redacta un modelo.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Callable

from ..ingestion.loader import Catalogo
from ..ingestion.schemas import AREAS, ESQUEMAS
from ..security import guard

ID_CLIENTE = r"CL-\d{1,6}"
ID_PEDIDO = r"PE-\d{1,6}"


class ArgumentoInvalido(ValueError):
    pass


class HerramientaDesconocida(KeyError):
    pass


# ------------------------------------------------------------------ utilidades
def soles(x: Decimal | None) -> str:
    return "—" if x is None else f"S/ {x:,.2f}"


def fecha(d: date | None) -> str:
    return "—" if d is None else d.strftime("%d/%m/%Y")


def et(estado: str | None) -> str:
    return "—" if not estado else estado[0].upper() + estado[1:]


def fuente(area: str, r: dict, id_: str | None = None) -> dict:
    return {"area": area, "archivo": r["_archivo"], "hoja": r["_hoja"], "fila": r["_fila"], "id": id_,
            "version": r["_version"], "modificado_en": r["_modificado_en"], "hash": r["_hash"][:12]}


def tabla(titulo: str, columnas: list[str], filas: list[list[Any]], derecha: tuple[int, ...] = ()) -> dict:
    return {"titulo": titulo, "columnas": columnas, "filas": filas, "derecha": list(derecha)}


@dataclass
class Resultado:
    herramienta: str
    argumentos: dict
    texto: str
    tablas: list[dict] = field(default_factory=list)
    fuentes: list[dict] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)
    calculo: str | None = None
    datos: dict = field(default_factory=dict)
    linea_tiempo: list[dict] = field(default_factory=list)
    denegado: bool = False
    error: bool = False

    def agregar_fuentes(self, area: str, filas: list[dict], clave: str | None = None) -> None:
        vistas = {(f["archivo"], f["hoja"], f["fila"]) for f in self.fuentes}
        for r in filas:
            f = fuente(area, r, r.get(clave) if clave else None)
            if (f["archivo"], f["hoja"], f["fila"]) not in vistas:
                vistas.add((f["archivo"], f["hoja"], f["fila"]))
                self.fuentes.append(f)

    def a_dict(self) -> dict:
        return {"herramienta": self.herramienta, "argumentos": self.argumentos, "texto": self.texto, "tablas": self.tablas,
                "fuentes": self.fuentes, "advertencias": self.advertencias, "calculo": self.calculo, "datos": self.datos,
                "linea_tiempo": self.linea_tiempo, "denegado": self.denegado, "error": self.error}


@dataclass
class Contexto:
    cat: Catalogo
    ref: date  # fecha de referencia para calcular atrasos

    def q(self, sql: str, params: tuple | list = ()) -> list[dict]:
        return self.cat.consultar(sql, params)


def _rechazos_relacionados(ctx: Contexto, area: str, ids: list[str]) -> list[str]:
    avisos = []
    for i in ids:
        for r in ctx.q("SELECT archivo, hoja, fila, causa FROM _rechazos WHERE area=? AND valores LIKE ?", [area, f'%"{i}"%']):
            avisos.append(f"La fila {r['fila']} de {r['archivo']} ({r['hoja']}) menciona {i} pero fue rechazada al importar "
                          f"({r['causa']}); puede haber datos no considerados.")
    return avisos


# ---------------------------------------------------------------- incidencias
def incidencias(ctx: Contexto) -> list[dict]:
    """Contradicciones, datos faltantes y referencias rotas entre áreas, con su procedencia."""
    t = {a: ctx.q(f"SELECT * FROM {a} ORDER BY _fila") for a in AREAS}
    ops = {r["id_pedido"]: r for r in t["operaciones"]}
    ent = defaultdict(list)
    for r in t["logistica"]:
        ent[r["id_pedido"]].append(r)
    out: list[dict] = []

    def add(tipo, pedido, detalle, *fuentes):
        out.append({"tipo": tipo, "id_pedido": pedido, "detalle": detalle, "fuentes": [fuente(a, r) for a, r in fuentes]})

    for pid, o in ops.items():
        if o["estado_operativo"] == "completado":
            es = ent.get(pid, [])
            if not es:
                add("Sin registro en Logística", pid, "Operaciones indica Completado pero Logística no tiene entrega registrada.", ("operaciones", o))
            elif not any(e["estado_entrega"] == "entregado" for e in es):
                e = es[0]
                add("Contradicción entre áreas", pid, f"Operaciones indica Completado, pero Logística indica {et(e['estado_entrega'])} ({e['id_entrega']}).",
                    ("operaciones", o), ("logistica", e))
    for e in t["logistica"]:
        o = ops.get(e["id_pedido"])
        if o is None:
            add("Referencia a pedido inexistente", e["id_pedido"], f"La entrega {e['id_entrega']} apunta a un pedido que no existe en Operaciones.", ("logistica", e))
            continue
        if e["estado_entrega"] == "entregado" and e["fecha_real"] is None:
            add("Dato faltante", e["id_pedido"], f"La entrega {e['id_entrega']} figura Entregado pero no tiene fecha_real.", ("logistica", e))
        if e["estado_entrega"] == "entregado" and o["estado_operativo"] != "completado":
            add("Contradicción entre áreas", e["id_pedido"],
                f"Logística indica Entregado ({e['id_entrega']}), pero Operaciones indica {et(o['estado_operativo'])}.", ("operaciones", o), ("logistica", e))
    for f in t["finanzas"]:
        pid, o = f["id_pedido"], ops.get(f["id_pedido"])
        if f["estado_pago"] == "pagado" and f["saldo"] > 0:
            add("Contradicción interna", pid, f"{f['id_factura']} figura Pagado pero conserva saldo {soles(f['saldo'])}.", ("finanzas", f))
        elif f["estado_pago"] == "pendiente" and f["saldo"] == 0:
            add("Contradicción interna", pid, f"{f['id_factura']} figura Pendiente pero tiene saldo S/ 0.00.", ("finanzas", f))
        elif f["saldo"] > f["importe"]:
            add("Contradicción interna", pid, f"{f['id_factura']} tiene un saldo mayor que el importe.", ("finanzas", f))
        if f["fecha_vencimiento"] is None:
            add("Dato faltante", pid, f"{f['id_factura']} no tiene fecha_vencimiento.", ("finanzas", f))
        if o is None:
            add("Referencia a pedido inexistente", pid, f"La factura {f['id_factura']} apunta a un pedido que no existe en Operaciones.", ("finanzas", f))
        elif o["id_cliente"] != f["id_cliente"]:
            add("Cliente distinto entre áreas", pid, f"{f['id_factura']} es de {f['id_cliente']} pero el pedido es de {o['id_cliente']}.", ("operaciones", o), ("finanzas", f))
    for k in t["atencion"]:
        o = ops.get(k["id_pedido"])
        if o is None:
            add("Referencia a pedido inexistente", k["id_pedido"], f"El ticket {k['id_ticket']} apunta a un pedido que no existe en Operaciones.", ("atencion", k))
        elif o["id_cliente"] != k["id_cliente"]:
            add("Cliente distinto entre áreas", k["id_pedido"], f"{k['id_ticket']} es de {k['id_cliente']} pero el pedido es de {o['id_cliente']}.", ("operaciones", o), ("atencion", k))
    return out


# ----------------------------------------------------------------- herramientas
def consultar_facturas(ctx: Contexto, id_cliente: str | None = None, id_pedido: str | None = None, solo_pendientes: bool = True) -> Resultado:
    args = {"id_cliente": id_cliente, "id_pedido": id_pedido, "solo_pendientes": solo_pendientes}
    if not id_cliente and not id_pedido:
        raise ArgumentoInvalido("Indica un id_cliente (CL-001) o un id_pedido (PE-015).")
    donde, params = [], []
    if id_cliente:
        donde.append("id_cliente = ?"); params.append(id_cliente)
    if id_pedido:
        donde.append("id_pedido = ?"); params.append(id_pedido)
    if solo_pendientes:
        donde.append("saldo > 0")
    w = " AND ".join(donde)
    filas = ctx.q(f"SELECT * FROM finanzas WHERE {w} ORDER BY fecha_vencimiento NULLS LAST, id_factura", params)
    total = ctx.q(f"SELECT COALESCE(SUM(saldo), 0) AS total FROM finanzas WHERE {w}", params)[0]["total"]
    quien = id_cliente or id_pedido
    r = Resultado("consultar_facturas", args, "")
    r.agregar_fuentes("finanzas", filas, "id_factura")
    r.advertencias += _rechazos_relacionados(ctx, "finanzas", [i for i in (id_cliente, id_pedido) if i])
    if not filas:
        r.texto = (f"No encontré constancia de facturas {'con saldo pendiente ' if solo_pendientes else ''}de {quien} en finanzas.xlsx. "
                   "Eso no prueba que no exista deuda en otras fuentes.")
        return r
    r.tablas.append(tabla(f"Facturas de {quien}", ["Factura", "Pedido", "Emisión", "Vencimiento", "Importe", "Saldo", "Estado"],
                          [[f["id_factura"], f["id_pedido"], fecha(f["fecha_emision"]), fecha(f["fecha_vencimiento"]),
                            soles(f["importe"]), soles(f["saldo"]), et(f["estado_pago"])] for f in filas], derecha=(4, 5)))
    for f in filas:
        if f["estado_pago"] == "pagado" and f["saldo"] > 0:
            r.advertencias.append(f"{f['id_factura']} figura Pagado pero tiene saldo {soles(f['saldo'])}: las fuentes se contradicen; el saldo se cuenta tal como está registrado.")
        if f["fecha_vencimiento"] is None:
            r.advertencias.append(f"{f['id_factura']} no tiene fecha_vencimiento.")
    r.datos = {"total_saldo": str(total), "n_facturas": len(filas), "facturas": [f["id_factura"] for f in filas]}
    r.calculo = "Suma de la columna «saldo» de las facturas con saldo > 0 en finanzas.xlsx (hoja Facturas)." if solo_pendientes else "Suma de «saldo» en finanzas.xlsx."
    r.texto = (f"{quien} tiene {len(filas)} factura(s){' con saldo pendiente' if solo_pendientes else ''} por un total registrado de {soles(total)}. "
               "Es el importe que consta en Finanzas, no una validación legal ni contable.")
    return r


def consultar_entregas(ctx: Contexto, solo_atrasadas: bool = True, id_pedido: str | None = None) -> Resultado:
    args = {"solo_atrasadas": solo_atrasadas, "id_pedido": id_pedido}
    donde, params = [], []
    if id_pedido:
        donde.append("id_pedido = ?"); params.append(id_pedido)
    if solo_atrasadas:
        donde.append("estado_entrega <> 'entregado' AND fecha_estimada < ?"); params.append(ctx.ref)
    w = ("WHERE " + " AND ".join(donde)) if donde else ""
    filas = ctx.q(f"SELECT *, date_diff('day', fecha_estimada, ?::DATE) AS dias FROM logistica {w} ORDER BY fecha_estimada, id_entrega", [ctx.ref, *params])
    r = Resultado("consultar_entregas", args, "")
    r.agregar_fuentes("logistica", filas, "id_entrega")
    if not filas:
        r.texto = "No encontré entregas que cumplan ese criterio en logistica.xlsx."
        return r
    r.tablas.append(tabla("Entregas atrasadas" if solo_atrasadas else "Entregas",
                          ["Entrega", "Pedido", "Estimada", "Real", "Estado", "Días de atraso" if solo_atrasadas else "Días vs. fecha ref."],
                          [[f["id_entrega"], f["id_pedido"], fecha(f["fecha_estimada"]), fecha(f["fecha_real"]), et(f["estado_entrega"]), f["dias"]] for f in filas], derecha=(5,)))
    r.datos = {"n_entregas": len(filas), "pedidos": [f["id_pedido"] for f in filas]}
    if solo_atrasadas:
        r.calculo = f"Atraso = entrega no entregada con fecha_estimada anterior a la fecha de referencia ({fecha(ctx.ref)}); días = diferencia en días."
        r.texto = f"Hay {len(filas)} entrega(s) atrasada(s) al {fecha(ctx.ref)}. No consta en las fuentes la causa del atraso."
    else:
        r.texto = f"Encontré {len(filas)} entrega(s) en logistica.xlsx."
    sin_fecha = ctx.q("SELECT COUNT(*) AS n FROM logistica WHERE fecha_estimada IS NULL AND estado_entrega <> 'entregado'")[0]["n"]
    if sin_fecha:
        r.advertencias.append(f"{sin_fecha} entrega(s) pendientes no tienen fecha_estimada y no se pueden evaluar.")
    return r


def consultar_pedido(ctx: Contexto, id_pedido: str) -> Resultado:
    args = {"id_pedido": id_pedido}
    o = ctx.q("SELECT * FROM operaciones WHERE id_pedido = ?", [id_pedido])
    es = ctx.q("SELECT * FROM logistica WHERE id_pedido = ? ORDER BY _fila", [id_pedido])
    fs = ctx.q("SELECT * FROM finanzas WHERE id_pedido = ? ORDER BY _fila", [id_pedido])
    ks = ctx.q("SELECT * FROM atencion WHERE id_pedido = ? ORDER BY _fila", [id_pedido])
    r = Resultado("consultar_pedido", args, "")
    r.advertencias += _rechazos_relacionados(ctx, "finanzas", [id_pedido])
    if not (o or es or fs or ks):
        r.texto = f"No encontré constancia de {id_pedido} en ninguna de las cuatro áreas importadas."
        r.datos = {"encontrado": False}
        return r
    for area, filas, clave in (("operaciones", o, "id_pedido"), ("logistica", es, "id_entrega"), ("finanzas", fs, "id_factura"), ("atencion", ks, "id_ticket")):
        r.agregar_fuentes(area, filas, clave)
    lineas = []
    if o:
        x = o[0]
        lineas.append(f"Operaciones: {et(x['estado_operativo'])} (cliente {x['id_cliente']}, responsable {x['responsable'] or '—'}, solicitado el {fecha(x['fecha_solicitud'])}).")
    else:
        lineas.append("Operaciones: no consta el pedido.")
    entregado = "no_consta"
    atraso = None
    if es:
        entregado = "si" if any(e["estado_entrega"] == "entregado" for e in es) else "no"
        for e in es:
            if e["estado_entrega"] == "entregado":
                txt = f"Entregado el {fecha(e['fecha_real'])}" if e["fecha_real"] else "Entregado, sin fecha real registrada"
                if e["fecha_real"] and e["fecha_estimada"] and e["fecha_real"] > e["fecha_estimada"]:
                    atraso = (e["fecha_real"] - e["fecha_estimada"]).days
            else:
                txt = f"{et(e['estado_entrega'])}, estimada {fecha(e['fecha_estimada'])}"
                if e["fecha_estimada"] and e["fecha_estimada"] < ctx.ref:
                    atraso = (ctx.ref - e["fecha_estimada"]).days
            lineas.append(f"Logística ({e['id_entrega']}): {txt}.")
    else:
        lineas.append("Logística: no consta entrega.")
    pagado = "no_consta"
    saldo = Decimal("0.00")
    if fs:
        saldo = ctx.q("SELECT COALESCE(SUM(saldo),0) AS s FROM finanzas WHERE id_pedido = ?", [id_pedido])[0]["s"]
        pagado = "si" if saldo == 0 else "no"
        if any(f["estado_pago"] == "pagado" and f["saldo"] > 0 for f in fs):
            pagado = "contradictorio"
        for f in fs:
            lineas.append(f"Finanzas ({f['id_factura']}): importe {soles(f['importe'])}, saldo {soles(f['saldo'])}, {et(f['estado_pago'])}.")
    else:
        lineas.append("Finanzas: no consta factura.")
    abiertos = [k for k in ks if k["estado_ticket"] != "cerrado"]
    lineas.append(f"Atención: {len(ks)} ticket(s), {len(abiertos)} abierto(s)." if ks else "Atención: no constan tickets.")
    if o and o[0]["estado_operativo"] == "completado" and es and entregado == "no":
        entregado = "contradictorio"
    inc = [i for i in incidencias(ctx) if i["id_pedido"] == id_pedido]
    for i in inc:
        r.advertencias.append(f"{i['tipo']}: {i['detalle']}")
        for f in i["fuentes"]:
            if (f["archivo"], f["hoja"], f["fila"]) not in {(s["archivo"], s["hoja"], s["fila"]) for s in r.fuentes}:
                r.fuentes.append(f)
    if atraso:
        cuando = "La entrega se registró con" if entregado == "si" else "La entrega acumula"
        r.advertencias.append(f"{cuando} {atraso} día(s) de retraso respecto de la fecha estimada. En las fuentes no consta la causa.")
    r.datos = {"encontrado": True, "estado_operativo": o[0]["estado_operativo"] if o else None, "entregado": entregado, "pagado": pagado,
               "saldo": str(saldo), "dias_atraso": atraso, "tickets_abiertos": len(abiertos), "incidencias": len(inc)}
    palabras = {"si": "sí", "no": "no", "no_consta": "no consta", "contradictorio": "las fuentes se contradicen"}
    r.texto = f"{id_pedido}\n" + "\n".join(lineas) + f"\n\n¿Entregado? {palabras[entregado]}. ¿Pagado? {palabras[pagado]}" + (f" (saldo {soles(saldo)})" if pagado in ("no", "contradictorio") else "") + "."
    r.calculo = "Pagado = suma de saldo de las facturas del pedido igual a S/ 0.00; entregado = existe una entrega con estado Entregado en Logística."
    ev = []
    if o: ev.append((o[0]["fecha_solicitud"], "operaciones", "Pedido solicitado"))
    for e in es:
        ev.append((e["fecha_estimada"], "logistica", "Entrega estimada"))
        ev.append((e["fecha_real"], "logistica", "Entrega real"))
    for f in fs:
        ev.append((f["fecha_emision"], "finanzas", f"Factura {f['id_factura']} emitida"))
        ev.append((f["fecha_vencimiento"], "finanzas", f"Factura {f['id_factura']} vence"))
    for k in ks:
        ev.append((k["fecha_reclamo"], "atencion", f"Ticket {k['id_ticket']}: {k['motivo']} ({et(k['estado_ticket'])})"))
    r.linea_tiempo = [{"fecha": fecha(d), "iso": d.isoformat(), "area": a, "evento": txt} for d, a, txt in sorted((e for e in ev if e[0]), key=lambda e: e[0])]
    return r


def consultar_cliente(ctx: Contexto, id_cliente: str) -> Resultado:
    args = {"id_cliente": id_cliente}
    ped = ctx.q("SELECT * FROM operaciones WHERE id_cliente = ? ORDER BY id_pedido", [id_cliente])
    r = Resultado("consultar_cliente", args, "")
    if not ped:
        r.texto = f"No encontré pedidos de {id_cliente} en operaciones.xlsx."
        return r
    ids = [p["id_pedido"] for p in ped]
    marcas = ",".join("?" * len(ids))
    ent = {e["id_pedido"]: e for e in ctx.q(f"SELECT * FROM logistica WHERE id_pedido IN ({marcas})", ids)}
    sal = {x["id_pedido"]: x["s"] for x in ctx.q(f"SELECT id_pedido, SUM(saldo) AS s FROM finanzas WHERE id_pedido IN ({marcas}) GROUP BY id_pedido", ids)}
    tk = defaultdict(int)
    for k in ctx.q(f"SELECT * FROM atencion WHERE id_pedido IN ({marcas}) AND estado_ticket <> 'cerrado'", ids):
        tk[k["id_pedido"]] += 1
    total = ctx.q("SELECT COALESCE(SUM(saldo),0) AS s FROM finanzas WHERE id_cliente = ?", [id_cliente])[0]["s"]
    r.agregar_fuentes("operaciones", ped, "id_pedido")
    r.agregar_fuentes("logistica", list(ent.values()), "id_entrega")
    r.agregar_fuentes("finanzas", ctx.q(f"SELECT * FROM finanzas WHERE id_pedido IN ({marcas}) OR id_cliente = ?", [*ids, id_cliente]), "id_factura")
    r.agregar_fuentes("atencion", ctx.q("SELECT * FROM atencion WHERE id_cliente = ?", [id_cliente]), "id_ticket")
    r.tablas.append(tabla(f"Pedidos de {id_cliente}", ["Pedido", "Operaciones", "Logística", "Saldo", "Tickets abiertos"],
                          [[p["id_pedido"], et(p["estado_operativo"]), et(ent[p["id_pedido"]]["estado_entrega"]) if p["id_pedido"] in ent else "—",
                            soles(sal.get(p["id_pedido"])) if p["id_pedido"] in sal else "—", tk.get(p["id_pedido"], 0)] for p in ped], derecha=(3, 4)))
    r.datos = {"n_pedidos": len(ped), "saldo_total": str(total)}
    r.calculo = "Saldo total = suma de «saldo» en finanzas.xlsx para el cliente."
    r.texto = f"{id_cliente} tiene {len(ped)} pedido(s) en Operaciones y un saldo total registrado de {soles(total)} en Finanzas."
    return r


def clientes_deuda_y_reclamo(ctx: Contexto) -> Resultado:
    filas = ctx.q("""
        SELECT f.id_cliente, SUM(f.saldo) AS saldo, COUNT(*) AS n_facturas,
               (SELECT COUNT(*) FROM atencion a WHERE a.id_cliente = f.id_cliente AND a.estado_ticket <> 'cerrado') AS n_tickets
        FROM finanzas f WHERE f.saldo > 0 GROUP BY f.id_cliente
        HAVING (SELECT COUNT(*) FROM atencion a WHERE a.id_cliente = f.id_cliente AND a.estado_ticket <> 'cerrado') > 0
        ORDER BY f.id_cliente""")
    r = Resultado("clientes_deuda_y_reclamo", {}, "")
    if not filas:
        r.texto = "No encontré clientes que tengan a la vez facturas con saldo y reclamos abiertos."
        return r
    ids = [f["id_cliente"] for f in filas]
    marcas = ",".join("?" * len(ids))
    r.agregar_fuentes("finanzas", ctx.q(f"SELECT * FROM finanzas WHERE saldo > 0 AND id_cliente IN ({marcas})", ids), "id_factura")
    r.agregar_fuentes("atencion", ctx.q(f"SELECT * FROM atencion WHERE estado_ticket <> 'cerrado' AND id_cliente IN ({marcas})", ids), "id_ticket")
    r.tablas.append(tabla("Clientes con deuda y reclamo abierto", ["Cliente", "Facturas con saldo", "Saldo total", "Tickets abiertos"],
                          [[f["id_cliente"], f["n_facturas"], soles(f["saldo"]), f["n_tickets"]] for f in filas], derecha=(1, 2, 3)))
    r.datos = {"clientes": ids}
    r.calculo = "Cruce por id_cliente: Finanzas (saldo > 0) con Atención (ticket no cerrado)."
    r.texto = f"{len(filas)} cliente(s) tienen facturas con saldo en Finanzas y al menos un reclamo abierto en Atención: {', '.join(ids)}."
    return r


def operaciones_vs_logistica(ctx: Contexto) -> Resultado:
    filas = ctx.q("""
        SELECT o.id_pedido, o._archivo AS oa, l.id_entrega, l.estado_entrega FROM operaciones o
        LEFT JOIN logistica l ON l.id_pedido = o.id_pedido
        WHERE o.estado_operativo = 'completado' AND (l.estado_entrega IS NULL OR l.estado_entrega <> 'entregado')
        ORDER BY o.id_pedido""")
    r = Resultado("operaciones_vs_logistica", {}, "")
    if not filas:
        r.texto = "No hay pedidos Completados en Operaciones que figuren sin entregar en Logística."
        return r
    ids = [f["id_pedido"] for f in filas]
    marcas = ",".join("?" * len(ids))
    r.agregar_fuentes("operaciones", ctx.q(f"SELECT * FROM operaciones WHERE id_pedido IN ({marcas})", ids), "id_pedido")
    r.agregar_fuentes("logistica", ctx.q(f"SELECT * FROM logistica WHERE id_pedido IN ({marcas})", ids), "id_entrega")
    r.tablas.append(tabla("Completados en Operaciones, no entregados en Logística", ["Pedido", "Operaciones", "Logística"],
                          [[f["id_pedido"], "Completado", et(f["estado_entrega"]) if f["estado_entrega"] else "Sin registro de entrega"] for f in filas]))
    r.datos = {"pedidos": ids}
    r.calculo = "Cruce por id_pedido: Operaciones (Completado) contra Logística (estado distinto de Entregado o sin entrega)."
    r.advertencias.append("Cada fila es una contradicción entre áreas: las fuentes no permiten decir cuál es la correcta.")
    r.texto = f"Sí: {len(filas)} pedido(s) figuran Completados en Operaciones y no figuran Entregados en Logística: {', '.join(ids)}."
    return r


def listar_inconsistencias(ctx: Contexto) -> Resultado:
    inc = incidencias(ctx)
    r = Resultado("listar_inconsistencias", {}, "")
    for i in inc:
        for f in i["fuentes"]:
            if (f["archivo"], f["hoja"], f["fila"]) not in {(s["archivo"], s["hoja"], s["fila"]) for s in r.fuentes}:
                r.fuentes.append(f)
    r.tablas.append(tabla("Inconsistencias detectadas", ["Tipo", "Pedido", "Detalle"], [[i["tipo"], i["id_pedido"], i["detalle"]] for i in inc]))
    por_tipo: dict[str, int] = defaultdict(int)
    for i in inc:
        por_tipo[i["tipo"]] += 1
    r.datos = {"total": len(inc), "por_tipo": dict(por_tipo), "pedidos": sorted({i["id_pedido"] for i in inc})}
    rech = ctx.q("SELECT COUNT(*) AS n FROM _rechazos")[0]["n"]
    if rech:
        r.advertencias.append(f"Además, {rech} fila(s) fueron rechazadas al importar (ver «Calidad de Excel»).")
    r.calculo = "Reglas fijas de comparación entre áreas (ver docs/casos-prueba.md)."
    r.texto = f"Detecté {len(inc)} inconsistencia(s): " + ", ".join(f"{n} de tipo «{t}»" for t, n in por_tipo.items()) + "." if inc else "No detecté inconsistencias."
    return r


def ver_fuente(ctx: Contexto, archivo: str, hoja: str, fila: int) -> Resultado:
    args = {"archivo": archivo, "hoja": hoja, "fila": fila}
    esq = next((e for e in ESQUEMAS.values() if e.archivo == archivo), None)
    if esq is None or hoja != esq.hoja:
        raise ArgumentoInvalido("Archivo u hoja no reconocidos.")
    r = Resultado("ver_fuente", args, "")
    filas = ctx.q(f"SELECT * FROM {esq.area} WHERE _fila = ?", [fila])
    if filas:
        x = filas[0]
        r.tablas.append(tabla(f"{archivo} · {hoja} · fila {fila}", ["Campo", "Valor"],
                              [[c.nombre, _mostrar(x[c.nombre])] for c in esq.columnas]))
        r.fuentes.append(fuente(esq.area, x))
        r.texto = f"Fila {fila} de {archivo} (hoja {hoja}), importada como registro válido (versión {x['_version']})."
        r.datos = {"valida": True, "campos": {c.nombre: _mostrar(x[c.nombre]) for c in esq.columnas}}
        return r
    rech = ctx.q("SELECT * FROM _rechazos WHERE archivo = ? AND fila = ?", [archivo, fila])
    if rech:
        crudo = json.loads(rech[0]["valores"])
        r.tablas.append(tabla(f"{archivo} · {hoja} · fila {fila} (RECHAZADA)", ["Campo", "Valor original"], [[k, str(v)] for k, v in crudo.items()]))
        r.texto = f"La fila {fila} de {archivo} fue rechazada al importar: {rech[0]['causa']}."
        r.datos = {"valida": False, "causa": rech[0]["causa"]}
        return r
    r.texto = f"No encontré la fila {fila} en {archivo}."
    return r


def _mostrar(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, Decimal):
        return f"{v:.2f}"
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


# --------------------------------------------------------------------- registro
@dataclass(frozen=True)
class Herramienta:
    nombre: str
    descripcion: str
    areas: tuple[str, ...]
    parametros: dict[str, dict]
    requeridos: tuple[str, ...]
    func: Callable[..., Resultado]


_P_CLIENTE = {"type": "string", "pattern": f"^{ID_CLIENTE}$", "description": "Identificador de cliente, p. ej. CL-001"}
_P_PEDIDO = {"type": "string", "pattern": f"^{ID_PEDIDO}$", "description": "Identificador de pedido, p. ej. PE-015"}

HERRAMIENTAS: dict[str, Herramienta] = {h.nombre: h for h in (
    Herramienta("consultar_facturas", "Facturas de un cliente o pedido (Finanzas) y su saldo pendiente.", ("finanzas",),
                {"id_cliente": _P_CLIENTE, "id_pedido": _P_PEDIDO, "solo_pendientes": {"type": "boolean"}}, (), consultar_facturas),
    Herramienta("consultar_entregas", "Entregas de Logística; por defecto solo las atrasadas.", ("logistica",),
                {"solo_atrasadas": {"type": "boolean"}, "id_pedido": _P_PEDIDO}, (), consultar_entregas),
    Herramienta("consultar_pedido", "Estado integral de un pedido cruzando Operaciones, Logística, Finanzas y Atención.", AREAS,
                {"id_pedido": _P_PEDIDO}, ("id_pedido",), consultar_pedido),
    Herramienta("consultar_cliente", "Resumen de un cliente: pedidos, estado de entrega, saldos y reclamos abiertos.", AREAS,
                {"id_cliente": _P_CLIENTE}, ("id_cliente",), consultar_cliente),
    Herramienta("clientes_deuda_y_reclamo", "Clientes con facturas con saldo y reclamos abiertos a la vez.", ("finanzas", "atencion"), {}, (), clientes_deuda_y_reclamo),
    Herramienta("operaciones_vs_logistica", "Pedidos Completados en Operaciones que no figuran Entregados en Logística.", ("operaciones", "logistica"), {}, (), operaciones_vs_logistica),
    Herramienta("listar_inconsistencias", "Contradicciones, datos faltantes y referencias rotas entre áreas.", AREAS, {}, (), listar_inconsistencias),
    Herramienta("ver_fuente", "Fila original de un Excel (archivo, hoja y número de fila).", (),
                {"archivo": {"type": "string", "enum": [e.archivo for e in ESQUEMAS.values()]},
                 "hoja": {"type": "string"}, "fila": {"type": "integer", "minimum": 2}}, ("archivo", "hoja", "fila"), ver_fuente),
)}


def validar_argumentos(h: Herramienta, args: dict) -> dict:
    """Valida los argumentos contra el esquema cerrado de la herramienta; rechaza cualquier extra."""
    if not isinstance(args, dict):
        raise ArgumentoInvalido("Los argumentos deben ser un objeto.")
    extra = set(args) - set(h.parametros)
    if extra:
        raise ArgumentoInvalido(f"Argumentos no permitidos: {', '.join(sorted(extra))}")
    faltan = [p for p in h.requeridos if args.get(p) in (None, "")]
    if faltan:
        raise ArgumentoInvalido(f"Faltan argumentos: {', '.join(faltan)}")
    limpio = {}
    for nombre, valor in args.items():
        if valor is None:
            continue
        spec = h.parametros[nombre]
        if spec["type"] == "string":
            if not isinstance(valor, str) or ("pattern" in spec and not re.fullmatch(spec["pattern"], valor.strip().upper())) \
                    or ("enum" in spec and valor not in spec["enum"]) or len(valor) > 80:
                raise ArgumentoInvalido(f"Valor inválido para {nombre}.")
            limpio[nombre] = valor.strip().upper() if "pattern" in spec else valor
        elif spec["type"] == "boolean":
            if not isinstance(valor, bool):
                raise ArgumentoInvalido(f"{nombre} debe ser verdadero o falso.")
            limpio[nombre] = valor
        elif spec["type"] == "integer":
            if isinstance(valor, bool) or not isinstance(valor, int) or valor < spec.get("minimum", 0):
                raise ArgumentoInvalido(f"{nombre} debe ser un entero válido.")
            limpio[nombre] = valor
    return limpio


def ejecutar(ctx: Contexto, perfil: str, nombre: str, args: dict | None = None) -> Resultado:
    """Único punto de entrada a los datos: valida herramienta, argumentos, permisos y disponibilidad."""
    h = HERRAMIENTAS.get(nombre)
    if h is None:
        raise HerramientaDesconocida(nombre)
    limpio = validar_argumentos(h, args or {})
    if nombre == "ver_fuente":
        areas = tuple(e.area for e in ESQUEMAS.values() if e.archivo == limpio["archivo"])
    else:
        areas = h.areas
    try:
        guard.verificar(perfil, areas)
    except guard.AccesoDenegado as exc:
        return Resultado(nombre, limpio, f"{exc}. No se devolvieron datos.", denegado=True)
    for a in areas:
        motivo = ctx.cat.area_disponible(a)
        if motivo:
            return Resultado(nombre, limpio, f"No puedo responder: el área {a} no está disponible ({motivo}).", error=True)
    return h.func(ctx, **limpio)


def esquema_herramientas() -> list[dict]:
    """Definiciones en el formato de tool calling (JSON Schema) para el agente con LLM."""
    return [{"type": "function", "function": {"name": h.nombre, "description": h.descripcion,
                                              "parameters": {"type": "object", "properties": h.parametros, "required": list(h.requeridos)}}}
            for h in HERRAMIENTAS.values()]
