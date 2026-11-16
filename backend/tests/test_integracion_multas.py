import sqlite3

from fastapi.testclient import TestClient

from app.integration.sqlite_source import SQLiteReadOnlyConnector, crear_sistemas_demo
from app.main import crear_app
from conftest import SYNTH, crear_config


def _cliente(tmp_path):
    cliente = TestClient(crear_app(crear_config(SYNTH, tmp_path / "generado")))
    respuesta = cliente.post("/api/login", json={"usuario": "admin", "contrasena": "12345678"})
    assert respuesta.status_code == 200
    return cliente


def test_sistemas_sqlite_son_parametrizados_y_solo_lectura(tmp_path):
    archivo = tmp_path / "sistemas.sqlite3"
    crear_sistemas_demo(archivo)
    conector = SQLiteReadOnlyConnector(archivo)

    filas = conector.multas("documento", "90000001")
    assert {fila["id_multa"] for fila in filas} == {"MU-900001", "MU-900002", "MU-900003"}
    assert {fila["sistema"] for fila in filas} == {"SI A", "SI B"}
    try:
        conector.consultar("DELETE FROM ciudadanos")
    except sqlite3.OperationalError:
        pass
    else:
        raise AssertionError("El conector permitió una escritura.")


def test_consulta_por_documento_consolida_dos_si_y_excel(tmp_path):
    cliente = _cliente(tmp_path)
    respuesta = cliente.get("/api/multas/buscar", params={"tipo": "documento", "valor": "90000001"})
    assert respuesta.status_code == 200, respuesta.text
    resultado = respuesta.json()
    assert resultado["total"] == 4
    multas = {m["id_multa"]: m for m in resultado["multas"]}
    assert set(multas) == {"MU-900001", "MU-900002", "MU-900003", "MU-900004"}
    assert {f["nombre"] for f in multas["MU-900001"]["fuentes"]} == {"SI A"}
    assert {f["nombre"] for f in multas["MU-900003"]["fuentes"]} == {"SI B"}
    assert multas["MU-900004"]["inconsistencias"][0]["tipo"] == "Solo en Excel"
    assert multas["MU-900001"]["estado"] == "pendiente"
    assert multas["MU-900003"]["importe"] == "50.00"


def test_consulta_por_expediente_y_multa_contradictoria(tmp_path):
    cliente = _cliente(tmp_path)
    expediente = cliente.get("/api/multas/buscar", params={"tipo": "id_expediente", "valor": "EX-900004"})
    assert expediente.status_code == 200
    assert [m["id_multa"] for m in expediente.json()["multas"]] == ["MU-900004"]

    multa = cliente.get("/api/multas/buscar", params={"tipo": "id_multa", "valor": "MU-900005"})
    assert multa.status_code == 200
    resultado = multa.json()["multas"][0]
    assert resultado["estado"] == "Contradictorio"
    assert resultado["importe"] is None
    assert {i["tipo"] for i in resultado["inconsistencias"]} >= {"Estado contradictorio", "Importe contradictorio"}
    assert {f["nombre"] for f in resultado["fuentes"]} == {"SI A", "multas_demo.csv"}


def test_vista_inconsistencias_incluye_fuentes_y_resumen_integrado(tmp_path):
    cliente = _cliente(tmp_path)
    resultado = cliente.get("/api/inconsistencias").json()
    conflicto = next(i for i in resultado["inconsistencias"] if i.get("id_multa") == "MU-900005")
    assert conflicto["tipo"] == "Estado contradictorio"
    assert len(conflicto["fuentes"]) == 2
    assert resultado["por_tipo"]["Solo en Excel"] >= 1
    assert resultado["por_tipo"]["Solo en bases de datos"] >= 1

    resumen = cliente.get("/api/resumen").json()
    assert resumen["registros_integrados"] >= 65
    assert resumen["areas"] >= 3
    assert resumen["multas_pendientes"] >= 1
    assert resumen["inconsistencias"] == resultado["total"]


def test_consulta_integrada_valida_identificadores_y_perfil(tmp_path):
    cliente = _cliente(tmp_path)
    invalida = cliente.get("/api/multas/buscar", params={"tipo": "id_multa", "valor": "MU-900001 OR 1=1"})
    assert invalida.status_code == 400
    denegada = cliente.get(
        "/api/multas/buscar",
        params={"tipo": "documento", "valor": "90000001", "perfil": "finanzas"},
    )
    assert denegada.status_code == 403
    sin_sesion = TestClient(crear_app(crear_config(SYNTH, tmp_path / "sin-sesion")))
    assert sin_sesion.get("/api/inconsistencias").status_code == 401


def test_homologacion_acepta_alias_campos_opcionales_y_conserva_extras(tmp_path):
    cliente = _cliente(tmp_path)
    estado = cliente.get("/api/estado").json()
    anteriores = next(c["validas"] for c in estado["cargas"] if c["area"] == "logistica")
    contenido = (
        "Entrega,Pedido,Estado,Nota operativa\n"
        "EN-999001,PE-015,Pendiente,revision manual\n"
    )
    respuesta = cliente.post(
        "/api/subir",
        data={"area": "logistica"},
        files={"archivo": ("columnas.csv", contenido.encode("utf-8"), "text/csv")},
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["carga"]["validas"] == anteriores + 1
    nombre = respuesta.json()["archivo"]
    fuente = cliente.get("/api/fuente", params={"archivo": nombre, "hoja": "Entregas", "fila": 2})
    assert fuente.status_code == 200
    assert fuente.json()["datos"]["campos"]["Nota operativa"] == "revision manual"

    analisis = cliente.post("/api/columnas/analizar", json={
        "area": "fiscalizacion",
        "columnas": ["Acta", "Pedido", "Cliente", "Multa", "Fecha Infraccion", "Situacion", "Nota"],
    })
    assert analisis.status_code == 200
    cuerpo = analisis.json()
    assert cuerpo["estado"] == "ok"
    assert {a["campo"] for a in cuerpo["asociaciones"]} >= {
        "id_acta", "id_pedido", "id_cliente", "id_multa", "fecha_acta", "estado_acta",
    }
    assert cuerpo["extras"] == ["Nota"]
