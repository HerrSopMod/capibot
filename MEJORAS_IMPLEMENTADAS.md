# Mejoras finales implementadas en Capibot

> **ENTORNO DEMO — DATOS FICTICIOS.** Esta POC no es una auditoría institucional ni debe usarse con datos personales o como sistema productivo.

## Funcionalidades incorporadas

- **Vigencia de fuentes:** Fuentes y resultados de multas distinguen fecha del dato, fecha de modificación del archivo y última sincronización exitosa. Se indica «Fecha no disponible» si falta una fecha; los umbrales de vigencia son configurables por área o fuente. Las fallas se advierten sin renovar la fecha de éxito.
- **Sincronización local:** Comprueba archivos registrados usando SHA-256, tamaño y mtime, evita reimportar si no hay cambios y registra resultados/cantidad de filas. Hay sincronización manual desde Fuentes y una tarea periódica dentro del proceso FastAPI. Ante una importación inválida o fallida se conserva la última tabla válida.
- **Calidad de datos:** La interfaz separa aceptadas sin avisos, aceptadas con avisos y rechazadas; muestra razones/campos y conserva la descarga Excel. Se pueden configurar reglas por área. Campos opcionales vacíos y columnas desconocidas se conservan con aviso; diferencias SI/Excel se mantienen como inconsistencias, no se borran.
- **Seguimiento de inconsistencias:** Filtros por estado, área y tipo; estados Pendiente, En revisión y Resuelta; observaciones, fecha e historial. Resolver no elimina evidencia. Si vuelve a aparecer tras una sincronización, se marca Pendiente y requiere revisión.
- **Chat de multas:** El chat usa la misma consulta integrada por documento, expediente o código; responde sobre pendientes, importes registrados, área, procedencia, inconsistencias, fechas y expedientes relacionados. Sin Ollama funciona por reglas; con Ollama sigue siendo opcional. Si faltan fuentes o datos, no afirma que no existan multas ni inventa importes.
- Se mantienen las consultas previas de pedidos/áreas, Inicio, carga de archivos, homologación, resumen y conectores ficticios SQLite/CSV. No se conectan sistemas empresariales reales ni se modifican archivos originales.

## Configuración y ejecución

Requiere Python 3.11+. En Windows PowerShell, desde la raíz:

```powershell
.\scripts\run_demo.ps1
```

La app queda en <http://127.0.0.1:8000>; acceso de demostración `admin` / `12345678`. No uses esas credenciales en producción.

En `.env` o variables de entorno:

- `SINCRONIZACION_INTERVALO_SEGUNDOS=60`; usa `0` para desactivar solo la tarea periódica (el botón manual sigue activo). El servidor local debe permanecer ejecutándose para que se programe la comprobación.
- `VIGENCIA_DIAS_POR_FUENTE={"default":7,"fiscalizacion":1}`. Las claves pueden ser áreas como `fiscalizacion` o IDs como `sqlite:si_a`; `default` aplica cuando no hay clave específica.
- `GENERADO_DIR` guarda `sincronizaciones.sqlite3`, `seguimiento.sqlite3` y las bases SQLite demo. `DATOS_DIR` contiene fuentes originales que solo se leen.
- Las reglas de validación por defecto y por área están en `backend/app/ingestion/quality_rules.json`.

La configuración completa se describe en [README.md](README.md) y [.env.example](.env.example).

## Archivos añadidos o actualizados

- Integración/sincronización: `backend/app/integration/__init__.py`, `sqlite_source.py`, `multas.py`, `sync.py`, `tracking.py`.
- Ingesta/configuración: `backend/app/ingestion/loader.py`, `aliases.json`, `quality_rules.json`, `backend/app/config.py`.
- API/chat/UI: `backend/app/main.py`, `backend/app/agents/router.py`, `servicio.py`, `backend/app/tools/queries.py`, `frontend/index.html`.
- Pruebas: `backend/tests/test_integracion_multas.py`, `test_final_features.py`, y ajustes a `test_agente_y_api.py`.
- Datos ficticios y entrega: `data/synthetic/multas_demo.csv`, `scripts/generate_demo_data.py`, `.env.example`, `README.md`, este documento.

## Pruebas realizadas

Comando completo ejecutado:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests -q
```

Resultado: **61 pruebas aprobadas** en 81.97 s. Incluye los casos de detección de cambios, sincronización sin duplicados, conservación de datos ante errores, fechas/vigencia, opcionales vacíos, persistencia/reaparición de inconsistencias, consulta por chat y pruebas previas. No se ejecutaron pruebas de estrés ni generación masiva.

## Verificación desde la interfaz

1. En «Consulta de multas», busca `90000001`, `EX-900004` o `MU-900005`; revisa las fechas y advertencias en los resultados.
2. En «Fuentes de información», compara las tres fechas, su estado de vigencia, pulsa «Sincronizar ahora» y consulta el historial.
3. En «Carga y calidad», importa un CSV/XLSX pequeño. La tabla distingue aceptadas, aceptadas con avisos y rechazadas; el Excel descargable conserva motivos y campos.
4. En «Inconsistencias», usa los filtros, cambia estado y observación, expande historial. `MU-900005` es un conflicto ficticio intencional.
5. En «Chat», pregunta «¿Qué multas tiene el documento 90000001?» y sigue con «¿Cuáles están pendientes?» o «¿Cuándo se actualizó la información?».

## Límites pendientes

- Los sistemas, ciudadanos, multas y expedientes son ficticios. El perfil es demostrativo y no identifica ni autoriza a un trabajador real.
- La sincronización solo lee archivos locales registrados y las bases SQLite demo. No hay conexiones empresariales, webhooks ni actualizaciones remotas.
- La aplicación comprueba fuentes al iniciar y luego la tarea periódica depende de que FastAPI permanezca encendido; no hay servicio externo ni conexión empresarial.
- El umbral indica la edad desde la sincronización exitosa, no certifica que el dato de origen sea correcto. Una sincronización correcta solo acredita que la importación local fue válida.
- El chat por reglas entiende preguntas acotadas y necesita un identificador o contexto. Ollama puede fallar o estar desactualizado; no se usa como fuente de verdad.
- Los perfiles y el registro de seguimiento no constituyen una auditoría institucional verificada.
