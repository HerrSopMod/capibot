"""API FastAPI + servidor del chat web. Arranque: uvicorn app.main:app (ver scripts/run_demo.ps1)."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agents.router import SUGERENCIAS
from .agents.servicio import Servicio
from .config import RAIZ, Config, cargar_config
from .ingestion.loader import Catalogo
from .security import guard
from .tools.queries import ArgumentoInvalido, ejecutar, incidencias

FRONTEND = RAIZ / "frontend" / "index.html"


class ChatIn(BaseModel):
    pregunta: str = Field(max_length=500)
    perfil: str = "analista"
    contexto: dict | None = None


def crear_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or cargar_config()
    catalogo = Catalogo(cfg.datos_dir, cfg.generado_dir)
    servicio = Servicio(cfg, catalogo)
    app = FastAPI(title="Capibot (POC académica)", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.servicio = servicio

    app.mount("/icons", StaticFiles(directory=RAIZ / "frontend" / "icons"), name="icons")

    @app.get("/", include_in_schema=False)
    def inicio():
        return FileResponse(FRONTEND, headers={"Cache-Control": "no-cache"})

    @app.get("/api/estado")
    def estado():
        cargas = catalogo.cargas
        return {
            "entorno": "ENTORNO DEMO — DATOS FICTICIOS",
            "modo": servicio.modo(),
            "moneda": cfg.moneda, "zona_horaria": cfg.zona_horaria, "fecha_referencia": cfg.fecha_referencia.isoformat(),
            "cargas": cargas,
            "metricas": {
                "registros_validos": {c["area"]: c["validas"] for c in cargas},
                "filas_rechazadas": sum(c["rechazadas"] for c in cargas),
                "avisos_de_calidad": sum(c["avisos"] for c in cargas),
                "inconsistencias_entre_areas": len(incidencias(servicio.ctx)) if all(c["estado"] == "ok" for c in cargas) else None,
                "consultas_en_esta_sesion": servicio.n_consultas,
            },
        }

    @app.get("/api/perfiles")
    def perfiles():
        return [{"id": k, "nombre": v[0], "areas": sorted(v[1])} for k, v in guard.PERFILES.items()]

    @app.get("/api/sugerencias")
    def sugerencias():
        return SUGERENCIAS

    @app.post("/api/chat")
    def chat(entrada: ChatIn):
        try:
            return servicio.responder(entrada.pregunta, entrada.perfil, entrada.contexto)
        except guard.PerfilDesconocido:
            raise HTTPException(400, "Perfil desconocido")

    @app.post("/api/reimportar")
    def reimportar():
        """Vuelve a leer los Excel de la carpeta de fuentes (que nunca se modifican)."""
        return {"cargas": catalogo.importar()}

    @app.get("/api/calidad")
    def calidad():
        return {"cargas": catalogo.cargas, "rechazos": catalogo.rechazos(), "avisos": catalogo.avisos()}

    @app.get("/api/fuente")
    def fuente(archivo: str, hoja: str, fila: int = Query(ge=2), perfil: str = "analista"):
        try:
            return ejecutar(servicio.ctx, perfil, "ver_fuente", {"archivo": archivo, "hoja": hoja, "fila": fila}).a_dict()
        except guard.PerfilDesconocido:
            raise HTTPException(400, "Perfil desconocido")
        except ArgumentoInvalido as exc:
            raise HTTPException(400, str(exc))

    return app


app = crear_app()
