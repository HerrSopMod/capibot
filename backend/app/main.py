"""API FastAPI + servidor del chat web. Arranque: uvicorn app.main:app (ver scripts/run_demo.ps1)."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from starlette.background import BackgroundTask
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agents.router import SUGERENCIAS
from .agents.servicio import Servicio
from .config import RAIZ, Config, cargar_config
from .ingestion.loader import Catalogo
from .ingestion.schemas import ESQUEMAS
from .security import guard
from .tools.queries import ArgumentoInvalido, contar_incidencias, ejecutar

FRONTEND = RAIZ / "frontend" / "index.html"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class ChatIn(BaseModel):
    pregunta: str = Field(max_length=500)
    perfil: str = "analista"
    contexto: dict | None = None


class LoginIn(BaseModel):
    usuario: str = Field(min_length=1, max_length=100)
    contrasena: str = Field(min_length=1, max_length=256)


def crear_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or cargar_config()
    catalogo = Catalogo(cfg.datos_dir, cfg.generado_dir, cfg.subidos_dir)
    servicio = Servicio(cfg, catalogo)
    app = FastAPI(title="Capibot (POC académica)", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.servicio = servicio
    app.state.usuario = os.environ.get("APP_USERNAME", "admin")
    app.state.contrasena = os.environ.get("APP_PASSWORD", "12345678")
    app.state.session_secret = secrets.token_bytes(32)
    cookie_secure = os.environ.get("COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"}
    cookie_name = "capibot_session"
    session_max_age = 8 * 60 * 60

    def firmar_sesion(marca_tiempo: str) -> str:
        return hmac.new(app.state.session_secret, marca_tiempo.encode("ascii"), hashlib.sha256).hexdigest()

    def sesion_valida(token: str | None) -> bool:
        if not token or "." not in token:
            return False
        marca_tiempo, firma = token.split(".", 1)
        if not marca_tiempo.isdecimal() or len(marca_tiempo) > 12:
            return False
        emitida = int(marca_tiempo)
        ahora = int(time.time())
        if emitida > ahora or ahora - emitida > session_max_age:
            return False
        return hmac.compare_digest(firma, firmar_sesion(marca_tiempo))

    def requiere_sesion(request: Request) -> None:
        if not sesion_valida(request.cookies.get(cookie_name)):
            raise HTTPException(status_code=401, detail="Inicia sesión para continuar.")

    app.mount("/icons", StaticFiles(directory=RAIZ / "frontend" / "icons"), name="icons")

    @app.get("/", include_in_schema=False)
    def inicio():
        return FileResponse(FRONTEND, headers={"Cache-Control": "no-cache"})

    @app.get("/api/sesion")
    def sesion(request: Request):
        return {"autenticado": sesion_valida(request.cookies.get(cookie_name))}

    @app.post("/api/login")
    def login(entrada: LoginIn, response: Response):
        usuario_valido = hmac.compare_digest(entrada.usuario.encode("utf-8"), app.state.usuario.encode("utf-8"))
        contrasena_valida = hmac.compare_digest(entrada.contrasena.encode("utf-8"), app.state.contrasena.encode("utf-8"))
        if not (usuario_valido and contrasena_valida):
            raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos.")
        marca_tiempo = str(int(time.time()))
        response.set_cookie(
            cookie_name, f"{marca_tiempo}.{firmar_sesion(marca_tiempo)}",
            max_age=session_max_age, httponly=True, secure=cookie_secure,
            samesite="strict", path="/",
        )
        return {"autenticado": True, "usuario": app.state.usuario}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(cookie_name, path="/", httponly=True, secure=cookie_secure, samesite="strict")
        return {"autenticado": False}

    @app.get("/api/estado")
    def estado(_: None = Depends(requiere_sesion)):
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
                "inconsistencias_entre_areas": contar_incidencias(servicio.ctx) if all(c["estado"] == "ok" for c in cargas) else None,
                "consultas_en_esta_sesion": servicio.n_consultas,
            },
        }

    @app.get("/api/perfiles")
    def perfiles(_: None = Depends(requiere_sesion)):
        return [{"id": k, "nombre": v[0], "areas": sorted(v[1])} for k, v in guard.PERFILES.items()]

    @app.get("/api/sugerencias")
    def sugerencias(_: None = Depends(requiere_sesion)):
        return SUGERENCIAS

    @app.post("/api/chat")
    def chat(entrada: ChatIn, _: None = Depends(requiere_sesion)):
        try:
            return servicio.responder(entrada.pregunta, entrada.perfil, entrada.contexto)
        except guard.PerfilDesconocido:
            raise HTTPException(400, "Perfil desconocido")

    @app.post("/api/reimportar")
    def reimportar(_: None = Depends(requiere_sesion)):
        """Vuelve a leer los Excel de la carpeta de fuentes (que nunca se modifican)."""
        return {"cargas": catalogo.importar()}

    @app.post("/api/subir")
    async def subir(area: str = Form(...), archivo: UploadFile = File(...),
                    _: None = Depends(requiere_sesion)):
        if area not in ESQUEMAS:
            raise HTTPException(status_code=400, detail="Selecciona un área válida.")
        nombre_original = Path((archivo.filename or "").replace("\\", "/")).name
        extension = Path(nombre_original).suffix.lower()
        if extension not in {".xlsx", ".csv"}:
            raise HTTPException(status_code=400, detail="Formato no admitido. Sube un archivo .xlsx o .csv.")
        nombre_seguro = re.sub(r"[^A-Za-z0-9._-]+", "_", nombre_original).strip("._")
        if not nombre_seguro:
            nombre_seguro = f"datos{extension}"
        catalogo.subidos_dir.mkdir(parents=True, exist_ok=True)
        nombre_guardado = f"{area}_{uuid.uuid4().hex}_{nombre_seguro}"
        destino = catalogo.subidos_dir / nombre_guardado
        temporal = destino.with_name(destino.name + ".part")
        total = 0
        try:
            with temporal.open("wb") as salida:
                while bloque := await archivo.read(1024 * 1024):
                    total += len(bloque)
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="El archivo supera el límite de 20 MB.")
                    salida.write(bloque)
            if total == 0:
                raise HTTPException(status_code=400, detail="El archivo está vacío.")
            temporal.replace(destino)
            try:
                cargas = catalogo.importar()
            except Exception:
                destino.unlink(missing_ok=True)
                raise
            carga = next(c for c in cargas if c["area"] == area)
            if carga["estado"] != "ok":
                destino.unlink(missing_ok=True)
                catalogo.importar()
                raise HTTPException(status_code=400, detail=carga["error"])
            return {"archivo": nombre_guardado, "carga": carga}
        finally:
            temporal.unlink(missing_ok=True)
            await archivo.close()

    @app.get("/api/calidad")
    def calidad(_: None = Depends(requiere_sesion)):
        return {"cargas": catalogo.cargas, "rechazos": catalogo.rechazos(), "avisos": catalogo.avisos()}

    @app.get("/api/calidad/descargar")
    def descargar_calidad(_: None = Depends(requiere_sesion)):
        temporal = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        destino = Path(temporal.name)
        temporal.close()
        try:
            catalogo.exportar_calidad(destino)
        except Exception:
            destino.unlink(missing_ok=True)
            raise
        return FileResponse(
            destino,
            filename="rechazos_y_avisos.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            background=BackgroundTask(lambda: destino.unlink(missing_ok=True)),
        )

    @app.get("/api/fuente")
    def fuente(archivo: str, hoja: str, fila: int = Query(ge=2), perfil: str = "analista",
               _: None = Depends(requiere_sesion)):
        try:
            return ejecutar(servicio.ctx, perfil, "ver_fuente", {"archivo": archivo, "hoja": hoja, "fila": fila}).a_dict()
        except guard.PerfilDesconocido:
            raise HTTPException(400, "Perfil desconocido")
        except ArgumentoInvalido as exc:
            raise HTTPException(400, str(exc))

    return app


app = crear_app()
