"""Nivel B con un modelo simulado (no se prueba contra un LLM real) y pruebas de la API HTTP."""
from io import BytesIO

from openpyxl import Workbook
from fastapi.testclient import TestClient

from app.agents.llm import responder_con_llm
from app.agents.servicio import Servicio
from app.ingestion.loader import Catalogo
from app.main import crear_app
from conftest import SYNTH, crear_config


class ModeloFalso:
    """Reproduce una conversación guionada: una lista de mensajes del asistente."""
    etiqueta = "Modelo simulado"

    def __init__(self, *mensajes):
        self.mensajes = list(mensajes)
        self.vistos = []

    def chat(self, mensajes, herramientas):
        self.vistos.append(mensajes)
        return self.mensajes.pop(0)


def llamada(nombre, **args):
    return {"function": {"name": nombre, "arguments": args}}


def _servicio(tmp_path, modelo):
    cfg = crear_config(SYNTH, tmp_path / "gen")
    return Servicio(cfg, Catalogo(cfg.datos_dir, cfg.generado_dir), modelo)


def test_agente_usa_dos_herramientas_y_registra_las_llamadas(tmp_path):
    modelo = ModeloFalso(
        {"content": "", "tool_calls": [llamada("consultar_pedido", id_pedido="PE-015"), llamada("consultar_facturas", id_cliente="CL-006")]},
        {"content": "PE-015 fue entregado y su factura FA-0015 sigue con saldo S/ 1,250.50.", "tool_calls": []})
    s = _servicio(tmp_path, modelo)
    r = s.responder("¿PE-015 ya fue entregado y pagado?")
    assert r["modo"] == "Agente IA" and r["intencion"] == "agente_ia"
    assert [h["herramienta"] for h in r["herramientas"]] == ["consultar_pedido", "consultar_facturas"]
    assert all(h["estado"] == "ok" for h in r["herramientas"])
    assert "Datos verificados" in r["texto"] and "Redacción del modelo" in r["origen"]
    assert not any("cifras" in a for a in r["advertencias"])  # S/ 1,250.50 sí está en los resultados
    # el modelo recibió el resultado de la herramienta, no el Excel completo
    resultado_tool = next(m for m in modelo.vistos[1] if m["role"] == "tool")
    assert "FA-0015" in resultado_tool["content"] and "FA-0030" not in resultado_tool["content"]


def test_cifras_inventadas_por_el_modelo_se_senalan(tmp_path):
    modelo = ModeloFalso({"content": "", "tool_calls": [llamada("consultar_pedido", id_pedido="PE-015")]},
                         {"content": "El saldo es S/ 9,999.00.", "tool_calls": []})
    r = _servicio(tmp_path, modelo).responder("¿cuánto debe PE-015?")
    assert any("S/9999.00" in a or "9999.00" in a for a in r["advertencias"])


def test_llamadas_no_permitidas_se_rechazan_y_se_cae_a_reglas(tmp_path):
    modelo = ModeloFalso({"content": "", "tool_calls": [llamada("ejecutar_sql", consulta="DROP TABLE finanzas"),
                                                        llamada("consultar_facturas", id_cliente="CL-001' OR 1=1")]},
                         {"content": "listo", "tool_calls": []})
    s = _servicio(tmp_path, modelo)
    r = s.responder("¿Qué facturas tiene pendientes el cliente CL-001?")
    assert r["modo"] == "Consultas por reglas" and any("agente IA" in a for a in r["advertencias"])
    assert r["fuentes"]  # la respuesta por reglas sí tiene datos


def test_modelo_sin_herramientas_no_se_acepta(tmp_path):
    import pytest
    cfg = crear_config(SYNTH, tmp_path / "gen")
    s = Servicio(cfg, Catalogo(cfg.datos_dir, cfg.generado_dir))
    with pytest.raises(RuntimeError):
        responder_con_llm(s.ctx, "analista", "hola", ModeloFalso({"content": "Hola, el saldo es S/ 5.00", "tool_calls": []}))


def test_agente_respeta_permisos_del_perfil(tmp_path):
    modelo = ModeloFalso({"content": "", "tool_calls": [llamada("consultar_facturas", id_cliente="CL-001")]},
                         {"content": "No tengo acceso.", "tool_calls": []})
    r = _servicio(tmp_path, modelo).responder("facturas de CL-001", perfil="logistica")
    assert r["denegado"] and r["fuentes"] == []


# --------------------------------------------------------------------- API
def _cliente(tmp_path):
    cliente = TestClient(crear_app(crear_config(SYNTH, tmp_path / "gen")))
    respuesta = cliente.post("/api/login", json={"usuario": "admin", "contrasena": "12345678"})
    assert respuesta.status_code == 200
    return cliente


def test_api_login_requerido_y_cierre_de_sesion(tmp_path):
    c = TestClient(crear_app(crear_config(SYNTH, tmp_path / "gen")))
    assert c.get("/").status_code == 200
    assert "Iniciar sesión" in c.get("/").text
    assert c.get("/api/sesion").json() == {"autenticado": False}
    assert c.get("/api/estado").status_code == 401
    assert c.get("/api/calidad").status_code == 401
    assert c.get("/api/calidad/descargar").status_code == 401
    assert c.get("/api/perfiles").status_code == 401
    assert c.get("/api/sugerencias").status_code == 401
    assert c.post("/api/chat", json={"pregunta": "hola"}).status_code == 401
    assert c.post("/api/reimportar").status_code == 401
    assert c.get("/api/fuente", params={"archivo": "finanzas.xlsx", "hoja": "Facturas", "fila": 2}).status_code == 401
    assert c.post("/api/login", json={"usuario": "admin", "contrasena": "incorrecta"}).status_code == 401
    assert c.get("/api/estado").status_code == 401
    login = c.post("/api/login", json={"usuario": "admin", "contrasena": "12345678"})
    assert login.status_code == 200
    assert "httponly" in login.headers["set-cookie"].lower()
    assert "samesite=strict" in login.headers["set-cookie"].lower()
    assert c.get("/api/sesion").json() == {"autenticado": True}
    assert c.get("/api/estado").status_code == 200
    assert c.post("/api/logout").status_code == 200
    assert c.get("/api/sesion").json() == {"autenticado": False}
    assert c.get("/api/estado").status_code == 401


def test_api_arranca_con_login_en_modo_reglas(tmp_path):
    c = _cliente(tmp_path)
    e = c.get("/api/estado").json()
    assert e["modo"]["nombre"] == "Consultas por reglas" and e["modo"]["ia"] is False
    assert e["entorno"] == "ENTORNO DE DEMOSTRACIÓN — REGISTROS DE EJEMPLO"
    assert len(e["cargas"]) == 10 and all(x["estado"] == "ok" for x in e["cargas"])
    assert len(c.get("/api/perfiles").json()) == 11
    assert "Consulta" in c.get("/").text


def test_api_chat_y_fuente_de_extremo_a_extremo(tmp_path):
    c = _cliente(tmp_path)
    r = c.post("/api/chat", json={"pregunta": "¿Cuál es el estado del pedido PE-015?", "perfil": "analista"}).json()
    assert "S/ 1,250.50" in r["texto"] and r["contexto"]["ultimo_pedido"] == "PE-015"
    assert r["datos"][0]["herramienta"] == "consultar_pedido" and r["datos"][0]["datos"]["entregado"] == "si"
    f = r["fuentes"][0]
    det = c.get("/api/fuente", params={"archivo": f["archivo"], "hoja": f["hoja"], "fila": f["fila"]}).json()
    assert det["datos"]["valida"] is True
    assert c.get("/api/fuente", params={"archivo": "otro.xlsx", "hoja": "x", "fila": 2}).status_code == 400
    assert c.post("/api/chat", json={"pregunta": "hola", "perfil": "root"}).status_code == 400
    assert c.post("/api/chat", json={"pregunta": "x" * 501}).status_code == 422


def test_api_calidad_y_reimportar(tmp_path):
    c = _cliente(tmp_path)
    q = c.get("/api/calidad").json()
    assert len(q["rechazos"]) == 2 and len(q["cargas"]) == 10
    assert len(c.post("/api/reimportar").json()["cargas"]) == 10


def test_api_subir_csv_agrega_datos_y_reporta_vacios_en_avisos_y_rechazos(tmp_path):
    c = _cliente(tmp_path)
    contenido = (
        "id_acta,id_pedido,id_cliente,id_multa,fecha_acta,estado_acta,observacion\n"
        "AC-900001,PE-015,CL-006,MU-900001,2026-10-01,Emitida,\n"
        "AC-900002,PE-016,CL-007,MU-900002,,Emitida,registro incompleto\n"
    )
    respuesta = c.post(
        "/api/subir",
        data={"area": "fiscalizacion"},
        files={"archivo": ("actas.csv", contenido.encode("utf-8"), "text/csv")},
    )
    assert respuesta.status_code == 200, respuesta.text
    nombre = respuesta.json()["archivo"]
    carga = respuesta.json()["carga"]
    assert carga["validas"] == 61 and carga["rechazadas"] == 1 and carga["avisos"] == 3
    calidad = c.get("/api/calidad").json()
    assert any(r["archivo"] == nombre and "fecha_acta" in r["causa"] for r in calidad["rechazos"])
    assert any(r["archivo"] == nombre and r["aviso"] == "campo vacío: observacion" for r in calidad["avisos"])
    assert any(r["archivo"] == nombre and r["aviso"] == "campo vacío: fecha_acta" for r in calidad["avisos"])
    assert any(r["archivo"] == nombre and r["columna"] == "fecha_acta" and r["valor"] is None
               for r in calidad["avisos"])
    valida = c.get("/api/fuente", params={"archivo": nombre, "hoja": "Actas", "fila": 2})
    rechazada = c.get("/api/fuente", params={"archivo": nombre, "hoja": "Actas", "fila": 3})
    assert valida.json()["datos"]["valida"] is True
    assert rechazada.json()["datos"]["valida"] is False
    assert c.post("/api/reimportar").json()["cargas"][4]["validas"] == 61


def test_api_subir_excel_y_rechazar_esquema_incorrecto_sin_romper_area(tmp_path):
    c = _cliente(tmp_path)
    libro = Workbook()
    hoja = libro.active
    hoja.title = "Datos"
    hoja.append(["id_acta", "id_pedido", "id_cliente", "id_multa", "fecha_acta", "estado_acta"])
    hoja.append(["AC-900010", "PE-017", "CL-001", "MU-900010", "2026-10-02", "Emitida"])
    contenido = BytesIO()
    libro.save(contenido)

    respuesta = c.post("/api/subir", data={"area": "fiscalizacion"},
                       files={"archivo": ("actas.xlsx", contenido.getvalue(),
                                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert respuesta.status_code == 200, respuesta.text
    nombre = respuesta.json()["archivo"]
    assert respuesta.json()["carga"]["validas"] == 61
    fuente = c.get("/api/fuente", params={"archivo": nombre, "hoja": "Datos", "fila": 2})
    assert fuente.json()["datos"]["valida"] is True

    mala = "id_acta,id_pedido\nAC-900011,PE-018\n"
    fallo = c.post("/api/subir", data={"area": "fiscalizacion"},
                   files={"archivo": ("incompleto.csv", mala.encode(), "text/csv")})
    assert fallo.status_code == 400
    cargas = c.get("/api/estado").json()["cargas"]
    assert next(x for x in cargas if x["area"] == "fiscalizacion")["validas"] == 61


def test_api_valores_invalidos_aparecen_como_rechazo_y_aviso_y_se_descargan_en_excel(tmp_path):
    from openpyxl import load_workbook

    c = _cliente(tmp_path)
    contenido = (
        "id_acta,id_pedido,id_cliente,id_multa,fecha_acta,estado_acta\n"
        "BAD,PE-900001,CL-900001,MU-900001,2026-99-99,desconocido\n"
    )
    respuesta = c.post(
        "/api/subir",
        data={"area": "fiscalizacion"},
        files={"archivo": ("invalidas.csv", contenido.encode("utf-8"), "text/csv")},
    )
    assert respuesta.status_code == 200, respuesta.text
    nombre = respuesta.json()["archivo"]
    calidad = c.get("/api/calidad").json()
    fila_rechazada = next(r for r in calidad["rechazos"] if r["archivo"] == nombre)
    assert all(campo in fila_rechazada["causa"] for campo in ("id_acta", "fecha_acta", "estado_acta"))
    avisos = [r for r in calidad["avisos"] if r["archivo"] == nombre]
    assert {r["columna"] for r in avisos} == {"id_acta", "fecha_acta", "estado_acta"}
    assert all("valor no válido" in r["aviso"] for r in avisos)

    exportacion = c.get("/api/calidad/descargar")
    assert exportacion.status_code == 200
    libro = load_workbook(BytesIO(exportacion.content), read_only=True, data_only=True)
    assert libro.sheetnames == ["Rechazos 1", "Avisos 1"]
    assert next(libro["Rechazos 1"].iter_rows(values_only=True))[0:4] == ("Área", "Archivo", "Hoja", "Fila")
    avisos_exportados = list(libro["Avisos 1"].iter_rows(values_only=True))
    assert {fila[4] for fila in avisos_exportados[1:] if fila[1] == nombre} == {
        "id_acta", "fecha_acta", "estado_acta",
    }


def test_api_subir_limpia_archivo_si_falla_la_reimportacion(tmp_path, monkeypatch):
    import pytest

    c = _cliente(tmp_path)
    catalogo = c.app.state.servicio.cat

    def fallar_importacion(*_args, **_kwargs):
        raise RuntimeError("error inesperado de importación")

    monkeypatch.setattr(catalogo, "importar", fallar_importacion)
    with pytest.raises(RuntimeError, match="error inesperado"):
        c.post(
            "/api/subir",
            data={"area": "fiscalizacion"},
            files={"archivo": ("actas.csv", b"id_acta,id_pedido\n", "text/csv")},
        )
    assert list(catalogo.subidos_dir.glob("fiscalizacion_*.csv")) == []
    assert any(
        fila["estado"] == "error"
        for fila in c.app.state.sincronizador.historial()
    )
