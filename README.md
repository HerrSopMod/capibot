# Capibot — consulta integrada de multas (POC académica)

> **ENTORNO DEMO — DATOS FICTICIOS.** Prueba de concepto universitaria. No es un sistema productivo ni debe usarse con datos reales.

La demo integra diez áreas desde Excel/CSV (Finanzas, Operaciones, Logística, Atención, Fiscalización, Recaudación, Notificaciones, Impugnaciones, Convenios y Documentos), dos sistemas de información simulados en SQLite y un CSV complementario. Todos los datos, incluidos los documentos de ocho dígitos, son ficticios. La app responde preguntas en español, permite buscar multas por documento ficticio, expediente o código, y conserva la procedencia de cada dato. Los conectores SQLite son de solo lectura y las fuentes originales no se modifican.

## Qué es (y qué no es)

| Nivel | Estado | Cómo se llama honestamente |
|---|---|---|
| **A — Consultas por reglas** (por defecto) | Implementado y probado | Consulta conversacional por reglas. **No es un agente de IA.** Sin tokens, sin clave, sin internet. |
| **B — Agente IA con tool calling** (opcional) | Implementado; probado con un modelo simulado y con `llama3.2` (3B) vía Ollama | Funciones cerradas + validación + trazas. Con el modelo real, 2 de 3 preguntas de prueba usaron el agente; en la otra el modelo escribió mal un argumento, la validación lo rechazó y se respondió por reglas. Es una prueba puntual, no una medición de exactitud. Latencia observada: 10–22 s por consulta en esta máquina. |

La interfaz muestra siempre el modo activo («Consultas por reglas» o «Agente IA»). Mientras no se valide el Nivel B con un modelo real, describe el entregable como *«POC de integración y consulta conversacional preparada para IA»*.

## Diagrama de despliegue

![Diagrama de despliegue de Capibot](docs/img/despliegue.png)

El navegador consulta por HTTPS a la aplicación FastAPI (en Render o en local), que lee los Excel/CSV, los carga en DuckDB y responde con herramientas cerradas. Los archivos nuevos se almacenan separados de las fuentes originales. GitHub redespliega en cada push a `main`; Ollama es opcional y solo local. Logotipos: Simple Icons (Python, FastAPI, DuckDB, HTML5, JavaScript, GitHub, Render, Ollama, pytest); el de Excel está dibujado a mano.

## Instalación y ejecución en Windows (PowerShell)

Requisitos: Python 3.11 o superior (probado con 3.14).

```powershell
cd capibot
.\scripts\run_demo.ps1
```

El script crea `.venv`, instala `requirements.txt`, genera los Excel si faltan y abre el servidor en <http://127.0.0.1:8000>.

Pruebas automatizadas (sin pruebas de carga masiva):

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests -q
```

El comando directo evita regenerar los archivos de `data/synthetic`. `.\scripts\run_tests.ps1` regenera primero los datos ficticios deterministas.

Si PowerShell bloquea los scripts: `powershell -ExecutionPolicy Bypass -File .\scripts\run_demo.ps1`.

Manual, sin scripts:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python scripts\generate_demo_data.py
.\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --port 8000
```

## Uso

1. Inicia sesión con las credenciales de demo `admin` / `12345678`. Puedes cambiarlas con `APP_USERNAME` y `APP_PASSWORD` en `.env`; nunca uses las credenciales de demo fuera de una demostración local.
2. Al iniciar sesión se habilitan Inicio, Consulta de multas, Chat, Carga y calidad, Inconsistencias y Fuentes de información. El backend protege sus endpoints con una cookie `HttpOnly`, `SameSite=Strict` que vence a las 8 horas; «Cerrar sesión» elimina la cookie del navegador.
3. En «Consulta de multas» busca por documento ficticio de ocho dígitos (por ejemplo, `90000001`), expediente (por ejemplo, `EX-900004`) o multa (por ejemplo, `MU-900004`). Para el documento ficticio `90000001` se muestran cuatro multas: dos de SI A, una de SI B y una que solo existe en el CSV complementario. `MU-900005` tiene datos deliberadamente contradictorios entre SI A y Excel; la app muestra los valores de cada fuente y una advertencia, sin elegir cuál es correcto.
4. En «Chat» pregunta por multas con un documento, expediente o código (por ejemplo, «¿Qué multas tiene el documento 90000001?»). También puedes preguntar por pendientes, importes registrados, área, procedencia, inconsistencias, actualización y expedientes relacionados. Continúan disponibles las consultas de pedidos y las diez áreas. El chat reutiliza la consulta integrada y avisa si faltan datos o hay fuentes desactualizadas; un resultado vacío no prueba que no existan multas fuera de las fuentes disponibles.
5. En «Carga y calidad», selecciona un área y sube un archivo `.xlsx` o `.csv` de hasta 20 MB. La carga **agrega filas** y no reemplaza los datos existentes. Se aceptan equivalencias de encabezados configuradas en `backend/app/ingestion/aliases.json`; se pueden previsualizar asociaciones para CSV. Se distinguen aceptadas sin avisos, aceptadas con avisos y rechazadas; las columnas adicionales se conservan y advierten. Las reglas por tipo de fuente están en `backend/app/ingestion/quality_rules.json`.
6. Descarga el Excel de «Filas rechazadas y avisos» desde esa misma sección; incluye hojas separadas con filas, causas, severidad, columnas y valores originales.
7. En «Fuentes de información», compara por separado la fecha del dato, la modificación del archivo y la última sincronización exitosa. El estado de vigencia usa la última sincronización exitosa; los errores o fechas ausentes no se muestran como vigentes. Pulsa **Sincronizar ahora** para comprobar cambios inmediatamente. La revisión periódica corre dentro del proceso local de FastAPI y evita reimportar fuentes sin cambios.
8. En «Inconsistencias», filtra por estado, área y tipo; cambia entre Pendiente, En revisión y Resuelta, guarda una observación y consulta el historial. Marcar una incidencia como resuelta no borra los valores ni la traza; si reaparece, vuelve a Pendiente y requiere revisión.
9. El selector **Perfil** sigue siendo una simulación para la POC, no autorización por persona. Las pantallas integradas que cruzan áreas requieren «Analista».

## Vigencia, sincronización y seguimiento

- Al arrancar, FastAPI registra el inventario de archivos locales, CSV complementario y bases SQLite simuladas. El sincronizador comprueba hash SHA-256, tamaño y fecha de modificación; los archivos sin cambios no se vuelven a importar.
- La aplicación comprueba las fuentes al iniciar y luego el proceso periódico se ejecuta hasta apagarla. Por defecto comprueba cada 60 segundos. Para mantenerlo activo, deja en ejecución la ventana del servidor iniciada con `.\scripts\run_demo.ps1`; no hay un servicio externo ni una conexión a sistemas empresariales.
- `SINCRONIZACION_INTERVALO_SEGUNDOS=0` desactiva la tarea periódica; el botón de sincronización manual sigue disponible. El historial y los estados se guardan en `data/generated/sincronizaciones.sqlite3`.
- `VIGENCIA_DIAS_POR_FUENTE` recibe un objeto JSON. Las claves pueden ser áreas como `fiscalizacion` o IDs como `sqlite:si_a`; `default` define el umbral general. Ejemplo: `{"default":7,"fiscalizacion":1,"sqlite:si_a":30}`. Fiscalización vence tras un día por defecto y el resto tras siete.
- Una actualización fallida conserva la última tabla válida y registra el error. La vigencia no se renueva por una modificación del archivo ni por conservar datos anteriores: solo una sincronización exitosa la renueva. Fecha del dato, mtime y sincronización exitosa se mantienen como conceptos separados.
- Las reglas de validación son editables por área en `backend/app/ingestion/quality_rules.json`. `default` aplica a todas y la configuración específica de un área la reemplaza. Las inconsistencias, estados y observaciones se guardan en `data/generated/seguimiento.sqlite3`; los perfiles y datos siguen siendo demostrativos, no una auditoría institucional con identidad verificada.
- Los dos archivos SQLite locales de integración/seguimiento se generan bajo `data/generated`; los archivos originales en `data/synthetic` no se modifican.

La autenticación es básica para demostración local, no un control de producción: la contraseña por defecto es conocida, no hay gestión de usuarios ni bloqueo por intentos y la sesión firmada es válida solo para el proceso de la app. Antes de exponerla, cambia las credenciales, despliega detrás de HTTPS y establece `COOKIE_SECURE=1`; para uso real integra SSO y permisos por usuario.

Guion de exposición de 5 minutos: [DEMO.md](DEMO.md).

## Publicarlo en línea (opcional, p. ej. para el docente)

El repo trae `render.yaml` para [Render](https://render.com) (plan gratuito):

1. Crea una cuenta en Render y elige **New → Blueprint**.
2. Conecta el repositorio `capibot` de GitHub; Render lee `render.yaml` y despliega.
3. Al terminar te da una URL pública. En el plan gratuito el servicio se duerme tras inactividad y la primera carga puede tardar de 30 a 60 s.

En línea solo funciona el modo «Consultas por reglas» (Ollama es local). El login también protege los endpoints de datos, pero usa credenciales de demo conocidas si no configuras variables. En Render define `APP_USERNAME`, `APP_PASSWORD` y `COOKIE_SECURE=1` en las variables privadas del servicio antes de exponer la URL. **Esta configuración no se ha probado en Render** (no se creó ninguna cuenta); en local sí se probó el arranque.

## Variables de entorno (todas opcionales — ver `.env.example`)

| Variable | Defecto | Efecto |
|---|---|---|
| `MODO_AGENTE` | `reglas` | `ollama` activa el Nivel B si Ollama responde; si no, cae a reglas y lo indica. |
| `OLLAMA_URL` / `OLLAMA_MODELO` | `http://localhost:11434` / `llama3.1` | Solo para el Nivel B. El modelo debe soportar herramientas. |
| `FECHA_REFERENCIA` | `2026-10-31` | «Hoy» para calcular atrasos; fija para que la demo sea repetible. |
| `DATOS_DIR` / `GENERADO_DIR` | `data/synthetic` / `data/generated` | Carpeta de fuentes originales (solo lectura) y de registros/versiones. |
| `SUBIDOS_DIR` | `data/uploads` | Carpeta persistente para archivos cargados desde la app. |
| `SINCRONIZACION_INTERVALO_SEGUNDOS` | `60` | Periodicidad de la comprobación local; `0` la desactiva sin quitar el botón manual. |
| `VIGENCIA_DIAS_POR_FUENTE` | `{"default":7,"fiscalizacion":1}` | JSON con umbrales de vigencia por área/ID de fuente y valor general. |
| `APP_USERNAME` / `APP_PASSWORD` | `admin` / `12345678` | Credenciales demo; cámbialas antes de desplegar. |
| `COOKIE_SECURE` | `0` | Usa `1` detrás de HTTPS para exigir cookies seguras. |

## Estructura

```
backend/app/   ingestion/ (lectura, homologación, DuckDB)  integration/ (conectores SQLite de solo lectura)
               tools/ (herramientas cerradas)  agents/ (router de reglas, LLM opcional)  main.py (API)
backend/tests/ integración Excel, SQLite, API y sincronización  frontend/index.html  app web sin build
data/synthetic/ 10 Excel + CSV ficticios scripts/ datos, arranque, pruebas y carga docs/ arquitectura y decisiones
```

Arquitectura y diagrama: [docs/arquitectura.md](docs/arquitectura.md). Qué no se hizo y por qué: [docs/decisiones.md](docs/decisiones.md). Casos de prueba y datos deliberadamente inconsistentes: [docs/casos-prueba.md](docs/casos-prueba.md).

## Resultados medidos

- `.\.venv\Scripts\python.exe -m pytest backend\tests -q`: **61 pruebas aprobadas** en 81.97 s. Incluye sincronización y detección de cambios, manejo de fallos, vigencia, calidad, seguimiento, chat y pruebas existentes.
- No se ejecutaron pruebas de estrés, carga ni generación de datos masivos en esta iteración.
- Archivos y escenarios de comprobación de las mejoras: [MEJORAS_IMPLEMENTADAS.md](MEJORAS_IMPLEMENTADAS.md).
- Tokens consumidos en el modo por defecto: 0.

## Costos

**Reales de esta demo:** US$0 (software libre, ejecución local, sin API ni tokens).

**Hipotéticos de un uso productivo (no medidos aquí):** dependen de volumen, hosting, soporte y licencias ya disponibles. Con un LLM de pago, tokens por consulta × tarifa vigente; con Ollama, hardware y mantenimiento. Power Apps/Power Automate pueden resultar más baratos si la organización ya tiene licencias y conectores incluidos. Verifica precios vigentes en las páginas oficiales antes de afirmar cifras (Microsoft, Gemini, OpenAI, Ollama; enlaces en el documento de especificación).

## Límites de seguridad

- Las fuentes originales se recorren por filas y nunca se escriben. Las cargas se almacenan aparte, se procesan por filas y se calcula su SHA-256 en bloques.
- Sin SQL libre: herramientas cerradas con argumentos validados por patrón; consultas parametrizadas; un identificador mal formado se rechaza.
- El login local protege la API con cookie firmada, pero comparte una sola cuenta demo; los perfiles siguen siendo una simulación elegida por el usuario, no permisos reales por persona.
- Las instrucciones dentro de celdas son datos (hay una celda con «IGNORA TUS INSTRUCCIONES…» para demostrarlo).
- Se bloquean preguntas que mencionan DNI, contraseñas o credenciales.
- El registro (`data/generated/registro.jsonl`) guarda intención, herramientas y conteos, no el texto de la pregunta.

## Límites conocidos

- Las consultas de multas por reglas requieren un documento, expediente o código detectable, o el contexto de una consulta anterior. El router sigue reconociendo un conjunto acotado de preguntas sobre pedidos y áreas.
- Frontend en HTML/JS simple servido por FastAPI, no Next.js/shadcn (ver decisiones).
- La carga desde el navegador admite `.xlsx` y `.csv` de hasta 20 MB y agrega datos sin reemplazar fuentes. La reimportación lee los archivos originales y los cargados. Opcionales vacíos y columnas extra se advierten; las reglas de cada área definen el tratamiento de valores desconocidos y campos críticos.
- Cada carga debe incluir los campos obligatorios del área; las columnas adicionales se conservan y advierten. Se aceptan alias configurados, pero no se procesan adjuntos automáticamente. No se certifica el rendimiento de cargas extensas.
- Nivel B probado solo con 3 preguntas sobre `llama3.2` (3B); un modelo pequeño falla llamadas con frecuencia. No hay medición de exactitud ni de tokens. Solo Ollama como proveedor (sin adaptador Gemini/OpenAI). Para activarlo: `MODO_AGENTE=ollama` y `OLLAMA_MODELO=llama3.2` en `.env`.
- «Atrasada» se define como entrega no entregada con `fecha_estimada` anterior a la fecha de referencia; no hay regla de negocio real detrás.
- No se calculan intereses ni se verifica ninguna notificación: las fuentes no tienen esos datos.
