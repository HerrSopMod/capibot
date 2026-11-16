import shutil
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.config import Config
from app.integration.multas import ConsultaMultas
from app.integration.sqlite_source import SQLiteReadOnlyConnector, crear_sistemas_demo
from app.integration.sync import Sincronizador
from app.integration.tracking import SeguimientoInconsistencias
from app.main import crear_app
from conftest import SYNTH, crear_config
from app.ingestion.loader import Catalogo


def _sincronizador(tmp_path, *, vigencia=None):
    datos = tmp_path / "fuentes"
    shutil.copytree(SYNTH, datos)
    generado = tmp_path / "generado"
    subidos = tmp_path / "subidos"
    cfg = Config(
        "reglas", "http://localhost:11434", "x", date(2026, 10, 31),
        datos, generado, subidos_dir=subidos, intervalo_sincronizacion_segundos=0,
        vigencia_dias_por_fuente=vigencia or {"default": 7, "fiscalizacion": 0},
    )
    catalogo = Catalogo(datos, generado, subidos)
    sqlite_path = generado / "sistemas_simulados.sqlite3"
    crear_sistemas_demo(sqlite_path)
    sqlite = SQLiteReadOnlyConnector(sqlite_path)
    consulta = ConsultaMultas(sqlite, catalogo)
    sync = Sincronizador(cfg, catalogo, consulta, sqlite_path, sqlite)
    consulta.sincronizador = sync
    return cfg, catalogo, consulta, sync


def test_sincronizacion_detecta_cambios_y_omite_fuentes_iguales(tmp_path):
    _, catalogo, _, sync = _sincronizador(tmp_path)
    assert any(
        fila["estado"] == "ok" and fila["fuente_id"] == "archivo:fiscalizacion:base"
        for fila in sync.historial()
    )
    iniciales = catalogo.consultar("SELECT COUNT(*) AS n FROM fiscalizacion")[0]["n"]
    archivo = catalogo.datos_dir / "fiscalizacion.xlsx"
    libro = load_workbook(archivo)
    hoja = libro["Actas"]
    hoja.append(["AC-999999", "PE-099", "CL-001", "MU-999999", "15/10/2026", "Emitida"])
    libro.save(archivo)

    resultado = sync.sincronizar()

    assert resultado["estado"] == "ok"
    assert resultado["fuentes_cambiadas"] == 1
    assert catalogo.consultar("SELECT COUNT(*) AS n FROM fiscalizacion")[0]["n"] == iniciales + 1
    assert sync.sincronizar()["estado"] == "sin_cambios"
    assert catalogo.consultar("SELECT COUNT(*) AS n FROM fiscalizacion")[0]["n"] == iniciales + 1


def test_falla_de_fuente_conserva_datos_anteriores_y_marca_error(tmp_path):
    _, catalogo, _, sync = _sincronizador(tmp_path)
    iniciales = catalogo.consultar("SELECT COUNT(*) AS n FROM fiscalizacion")[0]["n"]
    archivo = catalogo.datos_dir / "fiscalizacion.xlsx"
    archivo.write_text("archivo inválido", encoding="utf-8")

    resultado = sync.sincronizar()

    assert resultado["estado"] == "error"
    assert catalogo.consultar("SELECT COUNT(*) AS n FROM fiscalizacion")[0]["n"] == iniciales
    fuente = next(f for f in sync.fuentes() if f["nombre"] == "fiscalizacion.xlsx")
    assert fuente["estado_sincronizacion"] == "error"
    assert fuente["ultima_sincronizacion"]
    assert "se conserva" in fuente["advertencia"]


def test_fuentes_exponen_fechas_distintas_y_vigencia_configurable(tmp_path):
    _, _, _, sync = _sincronizador(tmp_path, vigencia={"default": 7, "fiscalizacion": 0})

    fuente = next(f for f in sync.fuentes() if f["nombre"] == "fiscalizacion.xlsx")

    assert fuente["fecha_dato"]
    assert fuente["fecha_modificacion_archivo"]
    assert fuente["ultima_sincronizacion"]
    assert fuente["vigencia"] == "desactualizada"
    assert fuente["umbral_dias"] == 0


def test_validacion_distingue_opcional_vacio_y_columna_desconocida(tmp_path):
    cliente = TestClient(crear_app(crear_config(SYNTH, tmp_path / "generado")))
    cliente.post("/api/login", json={"usuario": "admin", "contrasena": "12345678"})
    carga_inicial = next(
        carga
        for carga in cliente.get("/api/estado").json()["cargas"]
        if carga["area"] == "logistica"
    )
    contenido = (
        "Entrega,Pedido,Estado,Fecha prevista,Nota operativa\n"
        "EN-999001,PE-015,Pendiente,,\n"
    )

    respuesta = cliente.post(
        "/api/subir", data={"area": "logistica"},
        files={"archivo": ("incompleta.csv", contenido.encode("utf-8"), "text/csv")},
    )

    assert respuesta.status_code == 200
    carga = respuesta.json()["carga"]
    assert carga["validas"] == carga_inicial["validas"] + 1
    assert carga["rechazadas"] == 0
    assert carga["aceptadas_con_advertencias"] == carga_inicial["aceptadas_con_advertencias"] + 1
    avisos = cliente.get("/api/calidad").json()["avisos"]
    assert {aviso["categoria"] for aviso in avisos if aviso["archivo"] == respuesta.json()["archivo"]} == {
        "opcional_vacio", "columna_desconocida",
    }


def test_seguimiento_persiste_observaciones_y_reapertura(tmp_path):
    repo = SeguimientoInconsistencias(tmp_path / "seguimiento.sqlite3")
    conflicto = {
        "tipo": "Estado contradictorio",
        "area": "fiscalizacion",
        "id_multa": "MU-900005",
        "documento": "90000002",
        "detalle": "SI A y Excel discrepan.",
        "fuentes": [{"nombre": "SI A", "estado": "pendiente"}, {"nombre": "multas_demo.csv", "estado": "pagada"}],
    }
    repo.actualizar_detecciones([conflicto])
    detectada = repo.listar()["inconsistencias"][0]
    id_conflicto = detectada["id"]

    repo.cambiar(id_conflicto, "en_revision", "Verificar con el área.")
    repo.actualizar_detecciones([conflicto])
    revisada = repo.listar(estado="en_revision")["inconsistencias"][0]
    assert revisada["observacion"] == "Verificar con el área."
    assert len(revisada["historial"]) == 2

    repo.cambiar(id_conflicto, "resuelta", "Caso revisado.")
    repo.actualizar_detecciones([])
    repo.actualizar_detecciones([conflicto])
    reaparecida = repo.listar(estado="pendiente")["inconsistencias"][0]
    assert reaparecida["requiere_revision"] is True
    assert reaparecida["historial"][-1]["accion"] == "reaparecida"


def test_chat_consulta_multas_con_logica_integrada_y_no_afirma_ausencia(tmp_path):
    cliente = TestClient(crear_app(crear_config(SYNTH, tmp_path / "generado")))
    cliente.post("/api/login", json={"usuario": "admin", "contrasena": "12345678"})

    respuesta = cliente.post(
        "/api/chat",
        json={"pregunta": "¿Qué multas tiene el documento 90000001?", "perfil": "analista"},
    ).json()
    desconocido = cliente.post(
        "/api/chat",
        json={"pregunta": "¿Qué multas tiene el documento 12345678?", "perfil": "analista"},
    ).json()

    assert respuesta["datos"][0]["herramienta"] == "consultar_multas_integradas"
    assert respuesta["datos"][0]["datos"]["total"] == 4
    assert respuesta["texto"].count("Consulta de multas por documento") == 1
    assert any(f["tipo"] == "Base de datos" for f in respuesta["fuentes"])
    assert "no permite afirmar que no existan" in desconocido["texto"]


def test_api_guarda_estado_de_inconsistencia_y_muestra_historial(tmp_path):
    cliente = TestClient(crear_app(crear_config(SYNTH, tmp_path / "generado")))
    cliente.post("/api/login", json={"usuario": "admin", "contrasena": "12345678"})
    listado = cliente.get("/api/inconsistencias").json()
    conflicto = next(i for i in listado["inconsistencias"] if i.get("id_multa") == "MU-900005")

    actualizada = cliente.patch(
        f"/api/inconsistencias/{conflicto['id']}",
        json={"estado": "en_revision", "observacion": "Revisar valores de ambas fuentes."},
    )

    assert actualizada.status_code == 200
    assert actualizada.json()["estado"] == "en_revision"
    filtrada = cliente.get("/api/inconsistencias", params={"estado": "en_revision"}).json()
    assert any(i["id"] == conflicto["id"] and i["historial"] for i in filtrada["inconsistencias"])
