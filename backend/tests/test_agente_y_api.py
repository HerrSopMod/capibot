"""Nivel B con un modelo simulado (no se prueba contra un LLM real) y pruebas de la API HTTP."""
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
    return TestClient(crear_app(crear_config(SYNTH, tmp_path / "gen")))


def test_api_arranca_sin_clave_en_modo_reglas(tmp_path):
    c = _cliente(tmp_path)
    e = c.get("/api/estado").json()
    assert e["modo"]["nombre"] == "Consultas por reglas" and e["modo"]["ia"] is False
    assert e["entorno"] == "ENTORNO DEMO — DATOS FICTICIOS"
    assert all(x["estado"] == "ok" for x in e["cargas"])
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
    assert len(q["rechazos"]) == 2 and len(q["cargas"]) == 4
    assert len(c.post("/api/reimportar").json()["cargas"]) == 4
