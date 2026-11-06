"""Orquesta una consulta: seguridad -> (agente IA | reglas) -> herramientas -> respuesta con evidencia."""
from __future__ import annotations

import time

from ..config import Config
from ..ingestion.loader import Catalogo
from ..security import guard
from ..tools.queries import ArgumentoInvalido, Contexto, Resultado, ejecutar
from .llm import ModeloChat, OllamaChat, responder_con_llm
from .router import interpretar

MODO_REGLAS = "Consultas por reglas"
MODO_IA = "Agente IA"


class Servicio:
    def __init__(self, cfg: Config, catalogo: Catalogo, modelo: ModeloChat | None = None):
        self.cfg, self.cat = cfg, catalogo
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
                k = (f["archivo"], f["hoja"], f["fila"])
                if k not in vistas:
                    vistas.add(k)
                    fuentes.append(f)
            advertencias += [a for a in r.advertencias if a not in advertencias]
        origen = []
        if fuentes:
            origen.append("Datos de Excel")
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
            "linea_tiempo": next((r.linea_tiempo for r in resultados if r.linea_tiempo), []),
            "origen": origen,
            "herramientas": llamadas,
            "intencion": intencion,
            "modo": modo,
            "denegado": any(r.denegado for r in resultados),
            "contexto": {"ultimo_pedido": pedido or contexto.get("ultimo_pedido")},
            "tiempo_ms": ms,
        }
