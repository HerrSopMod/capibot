"""Orquesta una consulta: seguridad -> (agente IA | reglas) -> herramientas -> respuesta con evidencia."""
from __future__ import annotations

import re
import time
from decimal import Decimal

from ..config import Config
from ..ingestion.loader import Catalogo
from ..security import guard
from ..tools.queries import ArgumentoInvalido, Contexto, Resultado, ejecutar, tabla
from .llm import ModeloChat, OllamaChat, responder_con_llm
from .router import interpretar
from ..ingestion.loader import sin_acentos

MODO_REGLAS = "Consultas por reglas"
MODO_IA = "Agente IA"


class Servicio:
    def __init__(
        self,
        cfg: Config,
        catalogo: Catalogo,
        modelo: ModeloChat | None = None,
        consulta_multas=None,
    ):
        self.cfg, self.cat = cfg, catalogo
        self.consulta_multas = consulta_multas
        self.modelo = modelo
        if modelo is None and cfg.modo_agente == "ollama":
            self.modelo = OllamaChat(cfg.ollama_url, cfg.ollama_modelo)
        self.n_consultas = 0

    @property
    def ctx(self) -> Contexto:
        return Contexto(self.cat, self.cfg.fecha_referencia)

    def modo(self) -> dict:
        """Modo realmente activo (no el deseado): la etiqueta de la interfaz sale de aquí."""
        if self.modelo is None:
            return {"nombre": MODO_REGLAS, "ia": False, "detalle": "Sin IA: reglas y consultas predefinidas. Costo de tokens: US$0."}
        disponible = getattr(self.modelo, "disponible", lambda: True)()
        if disponible:
            return {"nombre": MODO_IA, "ia": True, "detalle": f"{self.modelo.etiqueta} con funciones cerradas. Costo de tokens: US$0 (modelo local)."}
        return {"nombre": MODO_REGLAS, "ia": False, "detalle": f"{self.modelo.etiqueta} no responde: se usan las reglas."}

    # ------------------------------------------------------------------
    def responder(self, pregunta: str, perfil: str = "analista", contexto: dict | None = None) -> dict:
        t0 = time.perf_counter()
        pregunta = (pregunta or "").strip()[:500]
        guard.areas_permitidas(perfil)  # lanza PerfilDesconocido si no existe
        contexto = contexto or {}
        avisos_extra: list[str] = []
        if not pregunta:
            return self._cerrar(t0, perfil, "vacia", {"texto": "Escribe una pregunta.", "resultados": []}, MODO_REGLAS, contexto)
        bloqueo = guard.pregunta_sensible(pregunta)
        if bloqueo:
            return self._cerrar(t0, perfil, "bloqueada", {"texto": bloqueo, "resultados": []}, MODO_REGLAS, contexto)

        consulta_multa = self._identificar_consulta_multa(pregunta, contexto)
        if consulta_multa and self.consulta_multas is not None:
            out = self._responder_consulta_multa(pregunta, consulta_multa)
            return self._cerrar(t0, perfil, "consulta_multas", out, MODO_REGLAS, contexto)

        if self.modelo is not None and self.modo()["ia"]:
            try:
                out = responder_con_llm(self.ctx, perfil, pregunta, self.modelo)
                return self._cerrar(t0, perfil, "agente_ia", out, MODO_IA, contexto)
            except Exception as exc:  # modelo caído, sin herramientas, respuesta inválida...
                avisos_extra.append(f"No se pudo usar el agente IA ({type(exc).__name__}: {exc}); se respondió por reglas.")

        plan = interpretar(pregunta, contexto)
        resultados: list[Resultado] = []
        llamadas = []
        for nombre, args in plan.llamadas:
            try:
                resultados.append(ejecutar(self.ctx, perfil, nombre, args))
                llamadas.append({"herramienta": nombre, "argumentos": args, "estado": "denegada" if resultados[-1].denegado else "ok"})
            except ArgumentoInvalido as exc:
                llamadas.append({"herramienta": nombre, "argumentos": args, "estado": "rechazada"})
                avisos_extra.append(str(exc))
        out = {"texto": plan.mensaje or "", "resultados": resultados, "llamadas": llamadas, "advertencias": avisos_extra,
               "notas": plan.notas, "pedido": plan.pedido}
        return self._cerrar(t0, perfil, plan.intencion, out, MODO_REGLAS, contexto)

    def _identificar_consulta_multa(
        self, pregunta: str, contexto: dict,
    ) -> tuple[str, str] | None:
        if self.consulta_multas is None:
            return None
        normalizada = sin_acentos(pregunta.lower())
        multa = re.search(r"\bmu\s*-?\s*(\d{1,8})\b", normalizada)
        expediente = re.search(r"\bex\s*-?\s*(\d{1,8})\b", normalizada)
        documento = re.search(r"(?<!\d)(\d{8})(?!\d)", pregunta)
        if multa:
            return "id_multa", f"MU-{int(multa.group(1)):06d}"
        if expediente:
            return "id_expediente", f"EX-{int(expediente.group(1)):06d}"
        if documento:
            return "documento", documento.group(1)

        tiene_contexto_multa = any(
            contexto.get(clave)
            for clave in ("ultimo_documento", "ultimo_id_multa", "ultimo_expediente")
        )
        consulta_sobre_multa = any(
            palabra in normalizada
            for palabra in (
                "multa", "ciudadano", "debe", "deuda", "pendiente", "expediente",
                "fuente", "inconsisten", "actualiz", "area", "importe",
            )
        )
        if not (tiene_contexto_multa and consulta_sobre_multa):
            return None
        for clave, tipo in (
            ("ultimo_documento", "documento"),
            ("ultimo_id_multa", "id_multa"),
            ("ultimo_expediente", "id_expediente"),
        ):
            valor = contexto.get(clave)
            if valor:
                try:
                    return tipo, self.consulta_multas.validar_busqueda(tipo, str(valor))
                except ValueError:
                    continue
        return None

    def _responder_consulta_multa(
        self, pregunta: str, consulta: tuple[str, str],
    ) -> dict:
        tipo, valor = consulta
        busqueda = self.consulta_multas.buscar(tipo, valor)
        multas = busqueda["multas"]
        pregunta_normalizada = sin_acentos(pregunta.lower())
        solo_pendientes = "pendiente" in pregunta_normalizada
        if solo_pendientes:
            multas = [m for m in multas if sin_acentos(str(m.get("estado") or "").lower()) == "pendiente"]

        advertencias = [
            f"{inc['tipo']}: {inc['detalle']}"
            for multa in multas
            for inc in multa.get("inconsistencias", [])
        ]
        advertencias.extend(
            f"{aviso['tipo']}: {aviso['detalle']}" if isinstance(aviso, dict) else str(aviso)
            for aviso in busqueda.get("advertencias", [])
        )
        texto_inicio = f"Consulta de multas por {tipo.replace('_', ' ')} {valor}."
        if not busqueda["multas"]:
            texto = (
                f"No encontré coincidencias de {valor} en las fuentes disponibles. "
                "Eso no permite afirmar que no existan multas si hay fuentes sin datos o con errores."
            )
        elif solo_pendientes and not multas:
            texto = (
                f"No encontré multas con estado pendiente para {valor} en las fuentes disponibles. "
                "Esto no acredita que no exista deuda fuera de esas fuentes."
            )
        elif any(t in pregunta_normalizada for t in ("cuanto debe", "cuanto adeuda", "deuda")):
            total = busqueda["importe_pendiente_total_registrado"]
            n = busqueda["multas_pendientes_encontradas"]
            desconocidos = busqueda["multas_pendientes_sin_importe"]
            texto = (
                f"Las fuentes registran {n} multa(s) pendiente(s) y un total conocido de S/ {Decimal(total):,.2f}. "
                "Es un importe registrado, no una determinación legal de deuda."
            )
            if desconocidos:
                texto += f" El importe de {desconocidos} multa(s) pendiente(s) no está disponible o es contradictorio."
        else:
            texto = f"Encontré {len(multas)} multa(s) que coinciden en las fuentes disponibles."

        if busqueda.get("advertencias"):
            for aviso in busqueda["advertencias"]:
                texto_inicio += f"\n{aviso['tipo']}: {aviso['detalle']}"
        resultado = Resultado(
            "consultar_multas_integradas",
            {"tipo": tipo, "valor": valor},
            f"{texto_inicio}\n{texto}",
            fuentes=[
                fuente
                for multa in multas
                for fuente in multa.get("fuentes", [])
            ],
            advertencias=list(dict.fromkeys(advertencias)),
            datos={
                "tipo_busqueda": tipo,
                "valor_busqueda": valor,
                "total": len(multas),
                "multas": multas,
                "importe_pendiente_total_registrado": busqueda["importe_pendiente_total_registrado"],
            },
        )
        if multas:
            resultado.tablas.append(tabla(
                "Multas y datos de sus fuentes",
                ["Multa", "Expediente", "Área", "Fecha del dato", "Estado", "Importe", "Procedencia"],
                [[
                    multa["id_multa"], multa.get("id_expediente") or "No consta",
                    multa.get("area") or "No consta", multa.get("fecha_infraccion") or "No consta",
                    multa.get("estado") or "No consta", multa.get("importe") or "No consta",
                    ", ".join(f.get("nombre", "Fuente") for f in multa.get("fuentes", [])),
                ] for multa in multas],
                derecha=(5,),
            ))
        contexto = {"tipo": tipo, "valor": valor}
        resultado.datos["contexto_multa"] = contexto
        return {
            "resultados": [resultado],
            "contexto_multa": contexto,
            "advertencias": resultado.advertencias,
        }

    # ------------------------------------------------------------------
    def _cerrar(self, t0: float, perfil: str, intencion: str, out: dict, modo: str, contexto: dict) -> dict:
        resultados: list[Resultado] = out.get("resultados", [])
        partes = ["\n".join(out["notas"])] if out.get("notas") else []
        if out.get("texto"):
            partes.append(out["texto"])
        verificado = "\n\n".join(r.texto for r in resultados if r.texto)
        if modo == MODO_IA and verificado:
            partes.append("— Datos verificados por las herramientas —\n" + verificado)
        elif verificado:
            partes.append(verificado)
        fuentes, vistas = [], set()
        advertencias = list(out.get("advertencias", []))
        for r in resultados:
            for f in r.fuentes:
                k = (f.get("archivo"), f.get("hoja"), f.get("fila"), f.get("nombre"))
                if k not in vistas:
                    vistas.add(k)
                    fuentes.append(f)
            advertencias += [a for a in r.advertencias if a not in advertencias]
        origen = []
        if any(f.get("archivo") for f in fuentes):
            origen.append("Datos de Excel")
        if any(f.get("tipo") == "Base de datos" for f in fuentes):
            origen.append("Bases de datos locales")
        if any(r.calculo for r in resultados):
            origen.append("Cálculo determinista")
        origen.append("Redacción del modelo" if modo == MODO_IA and out.get("texto") else "Reglas fijas (sin IA)")
        pedido = out.get("pedido") or next((r.argumentos.get("id_pedido") for r in resultados if r.argumentos.get("id_pedido")), None)
        ms = round((time.perf_counter() - t0) * 1000)
        self.n_consultas += 1
        llamadas = out.get("llamadas") or [{"herramienta": r.herramienta, "argumentos": r.argumentos, "estado": "ok"} for r in resultados]
        # Registro mínimo: intención, herramientas y conteos; nunca el texto de la pregunta.
        self.cat.registrar({"tipo": "consulta", "perfil": perfil, "modo": modo, "intencion": intencion, "herramientas": [c["herramienta"] for c in llamadas],
                            "fuentes": len(fuentes), "ms": ms})
        return {
            "texto": "\n\n".join(p for p in partes if p) or "Sin resultado.",
            "tablas": [t for r in resultados for t in r.tablas],
            "fuentes": fuentes,
            "advertencias": advertencias,
            "calculos": [r.calculo for r in resultados if r.calculo],
            "datos": [{"herramienta": r.herramienta, "datos": r.datos} for r in resultados],
            "linea_tiempo": next((r.linea_tiempo for r in resultados if r.linea_tiempo), []),
            "origen": origen,
            "herramientas": llamadas,
            "intencion": intencion,
            "modo": modo,
            "denegado": any(r.denegado for r in resultados),
            "contexto": {
                "ultimo_pedido": pedido or contexto.get("ultimo_pedido"),
                "ultimo_documento": out.get("contexto_multa", {}).get("valor")
                if out.get("contexto_multa", {}).get("tipo") == "documento"
                else contexto.get("ultimo_documento"),
                "ultimo_id_multa": out.get("contexto_multa", {}).get("valor")
                if out.get("contexto_multa", {}).get("tipo") == "id_multa"
                else contexto.get("ultimo_id_multa"),
                "ultimo_expediente": out.get("contexto_multa", {}).get("valor")
                if out.get("contexto_multa", {}).get("tipo") == "id_expediente"
                else contexto.get("ultimo_expediente"),
            },
            "tiempo_ms": ms,
        }
