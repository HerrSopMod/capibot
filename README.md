# Capibot — consulta integrada de Excel (POC académica)

> **ENTORNO DEMO — DATOS FICTICIOS.** Prueba de concepto universitaria. No es un sistema productivo ni debe usarse con datos reales.

Cuatro áreas (Finanzas, Operaciones, Logística y Atención) mantienen sus propios Excel. Esta POC añade una **capa de consulta** que los une por clave de negocio (`id_pedido`, `id_cliente`), responde preguntas en español y **cita archivo, hoja y fila** de cada dato. Los Excel originales no se modifican.

## Qué es (y qué no es)

| Nivel | Estado | Cómo se llama honestamente |
|---|---|---|
| **A — Consultas por reglas** (por defecto) | Implementado y probado | Consulta conversacional por reglas. **No es un agente de IA.** Sin tokens, sin clave, sin internet. |
| **B — Agente IA con tool calling** (opcional) | Implementado; probado con un modelo simulado y con `llama3.2` (3B) vía Ollama | Funciones cerradas + validación + trazas. Con el modelo real, 2 de 3 preguntas de prueba usaron el agente; en la otra el modelo escribió mal un argumento, la validación lo rechazó y se respondió por reglas. Es una prueba puntual, no una medición de exactitud. Latencia observada: 10–22 s por consulta en esta máquina. |

La interfaz muestra siempre el modo activo («Consultas por reglas» o «Agente IA»). Mientras no se valide el Nivel B con un modelo real, describe el entregable como *«POC de integración y consulta conversacional preparada para IA»*.

## Instalación y ejecución en Windows (PowerShell)

Requisitos: Python 3.11 o superior (probado con 3.14).

```powershell
cd capibot
.\scripts\run_demo.ps1
```

El script crea `.venv`, instala `requirements.txt`, genera los Excel si faltan y abre el servidor en <http://127.0.0.1:8000>.

Pruebas (regenera los datos y ejecuta todo):

```powershell
.\scripts\run_tests.ps1
```

Si PowerShell bloquea los scripts: `powershell -ExecutionPolicy Bypass -File .\scripts\run_demo.ps1`.

Manual, sin scripts:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python scripts\generate_demo_data.py
.\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --port 8000
```

## Uso

1. Abre <http://127.0.0.1:8000> y pulsa una sugerencia o escribe una pregunta («¿Cuál es el estado del pedido PE-015?»).
2. Cada respuesta trae advertencias, tablas, línea de tiempo, el origen del dato (Excel / cálculo / reglas) y botones de fuente; al pulsarlos ves la fila original.
3. «Carga y calidad» muestra versión, filas válidas/rechazadas y causas. Si editas un Excel de `data/synthetic/`, pulsa **Reimportar**.
4. El selector **Perfil** simula permisos por área: con «Logística», una pregunta sobre facturas no devuelve datos.

Guion de exposición de 5 minutos: [DEMO.md](DEMO.md).

## Publicarlo en línea (opcional, p. ej. para el docente)

El repo trae `render.yaml` para [Render](https://render.com) (plan gratuito):

1. Crea una cuenta en Render y elige **New → Blueprint**.
2. Conecta el repositorio `capibot` de GitHub; Render lee `render.yaml` y despliega.
3. Al terminar te da una URL pública. En el plan gratuito el servicio se duerme tras inactividad y la primera carga puede tardar de 30 a 60 s.

En línea solo funciona el modo «Consultas por reglas» (Ollama es local). No hay autenticación: cualquiera con el enlace puede usarlo y elegir cualquier perfil; es aceptable porque los datos son ficticios y el sistema solo lee. **Esta configuración no se ha probado en Render** (no se creó ninguna cuenta); en local sí se probó el mismo comando de arranque.

## Variables de entorno (todas opcionales — ver `.env.example`)

| Variable | Defecto | Efecto |
|---|---|---|
| `MODO_AGENTE` | `reglas` | `ollama` activa el Nivel B si Ollama responde; si no, cae a reglas y lo indica. |
| `OLLAMA_URL` / `OLLAMA_MODELO` | `http://localhost:11434` / `llama3.1` | Solo para el Nivel B. El modelo debe soportar herramientas. |
| `FECHA_REFERENCIA` | `2026-10-31` | «Hoy» para calcular atrasos; fija para que la demo sea repetible. |
| `DATOS_DIR` / `GENERADO_DIR` | `data/synthetic` / `data/generated` | Carpeta de Excel (solo lectura) y de registros/versiones. |

## Estructura

```
backend/app/   ingestion/ (lectura, validación, DuckDB)  tools/ (herramientas cerradas)
               agents/ (router de reglas, LLM opcional, orquestador)  security/ (perfiles)  main.py (API)
backend/tests/ 38 pruebas          frontend/index.html  chat web (sin build, sin internet)
data/synthetic/ 4 Excel ficticios  scripts/ datos, arranque y pruebas      docs/ arquitectura, decisiones, casos
```

Arquitectura y diagrama: [docs/arquitectura.md](docs/arquitectura.md). Qué no se hizo y por qué: [docs/decisiones.md](docs/decisiones.md). Casos de prueba y datos deliberadamente inconsistentes: [docs/casos-prueba.md](docs/casos-prueba.md).

## Resultados medidos

- `pytest`: **38 pruebas pasan** (≈10 s). Los valores esperados se calculan leyendo los `.xlsx` con `openpyxl`, no escritos a mano.
- Importación de los 4 archivos: 210 filas leídas → 208 válidas, 2 rechazadas (1 duplicado, 1 importe `N/D`), 1 aviso (fecha de vencimiento vacía).
- Una consulta típica respondió en pocos milisegundos (5 ms observados en una consulta cruzada). No se ha hecho una medición estadística de latencia.
- Interfaz: probada en el navegador integrado por DOM (consultas, ventana de fuente, calidad y resumen sin errores de consola ni desbordamiento horizontal). **No pude capturar pantallas** porque la ventana estaba oculta, así que el aspecto visual y la vista móvil no están verificados.
- Tokens consumidos en el modo por defecto: 0.

## Costos

**Reales de esta demo:** US$0 (software libre, ejecución local, sin API ni tokens).

**Hipotéticos de un uso productivo (no medidos aquí):** dependen de volumen, hosting, soporte y licencias ya disponibles. Con un LLM de pago, tokens por consulta × tarifa vigente; con Ollama, hardware y mantenimiento. Power Apps/Power Automate pueden resultar más baratos si la organización ya tiene licencias y conectores incluidos. Verifica precios vigentes en las páginas oficiales antes de afirmar cifras (Microsoft, Gemini, OpenAI, Ollama; enlaces en el documento de especificación).

## Límites de seguridad

- Solo lectura: los Excel se leen a memoria, se calcula su SHA-256 y nunca se escriben.
- Sin SQL libre: solo ocho herramientas con argumentos validados por patrón; consultas parametrizadas; un identificador mal formado se rechaza.
- Los perfiles simulan permisos por área, **pero no hay autenticación real**; cualquiera puede elegir un perfil. En producción se necesitarían login y permisos por registro.
- Las instrucciones dentro de celdas son datos (hay una celda con «IGNORA TUS INSTRUCCIONES…» para demostrarlo).
- Se bloquean preguntas que mencionan DNI, contraseñas o credenciales.
- El registro (`data/generated/registro.jsonl`) guarda intención, herramientas y conteos, no el texto de la pregunta.

## Límites conocidos

- El router por reglas solo entiende ~10 tipos de pregunta con identificadores `PE-###`/`CL-###`; lo demás recibe «no pude relacionar tu pregunta».
- Frontend en HTML/JS simple servido por FastAPI, no Next.js/shadcn (ver decisiones).
- No hay carga de archivos desde el navegador: se lee la carpeta `data/synthetic/` y se reimporta con un botón.
- Nivel B probado solo con 3 preguntas sobre `llama3.2` (3B); un modelo pequeño falla llamadas con frecuencia. No hay medición de exactitud ni de tokens. Solo Ollama como proveedor (sin adaptador Gemini/OpenAI). Para activarlo: `MODO_AGENTE=ollama` y `OLLAMA_MODELO=llama3.2` en `.env`.
- «Atrasada» se define como entrega no entregada con `fecha_estimada` anterior a la fecha de referencia; no hay regla de negocio real detrás.
- No se calculan intereses ni se verifica ninguna notificación: las fuentes no tienen esos datos.
