# Guion de demostración (5 minutos)

Mensaje clave: *no hacemos más eficientes los Excel por separado; resolvemos la consulta entre áreas, sin sustituir lo que ya funciona.*

Preparación: `.\scripts\run_demo.ps1` y abrir <http://127.0.0.1:8000>. Perfil «Analista (todas las áreas)».

| Min | Qué hacer | Qué debe verse |
|---|---|---|
| 0:00 | Abrir `data/synthetic/` en el Explorador y mostrar los cuatro `.xlsx` | Formatos distintos: Logística guarda fechas como texto `dd/mm/aaaa`, Atención como `aaaa-mm-dd`, Finanzas como fechas Excel. |
| 0:45 | Pestaña **Carga y calidad** | 4 archivos importados; Finanzas con 2 filas rechazadas (duplicado e importe `N/D`). Pulsar una fila para ver el valor original. |
| 1:15 | Chip «¿Cuál es el estado del pedido PE-015?» | Operaciones Completado, Logística Entregado, Finanzas saldo S/ 1,250.50 Pendiente, 1 ticket abierto. Botones de fuente por archivo/hoja/fila. |
| 2:00 | Chip «¿Está entregado y pagado?» | Responde sobre PE-015 y lo dice («Asumo que te refieres a PE-015»): sí entregado, no pagado. Une 4 áreas. |
| 2:45 | Preguntar «¿Qué clientes tienen facturas pendientes y reclamos abiertos?» | Cruce Finanzas × Atención: 10 clientes. |
| 3:15 | Preguntar «¿Hay pedidos completados en Operaciones que aún figuran pendientes en Logística?» | PE-040, con la advertencia de contradicción (no decide cuál es correcta). |
| 3:45 | Preguntar «¿Por qué se atrasó el pedido PE-030?» y luego «¿Se notificó al cliente?» | Muestra el atraso pero dice que **no consta la causa**; y que no puede verificar notificaciones. |
| 4:20 | Cambiar Perfil a «Logística» y preguntar «¿Qué facturas tiene pendientes el cliente CL-001?» | Acceso denegado, sin datos. |
| 4:40 | Mostrar el modo activo en la cabecera y cerrar con costos | «Consultas por reglas», 0 tokens. Costo real de la demo: US$0. |

Extra (si hay tiempo): editar con Excel un saldo en una **copia** de `data/synthetic/finanzas.xlsx`, o cambiar `DATOS_DIR` a esa copia, reimportar y repetir PE-015; la versión sube a 2 y el original no cambia.

Si Ollama está instalado y se arranca con `MODO_AGENTE=ollama`, la cabecera pasa a «Agente IA» y puede mostrarse una pregunta que requiere dos herramientas, con sus llamadas en «Cálculo y trazas». Con `llama3.2` funcionó en 2 de 3 preguntas de prueba y tarda 10–22 s; ensáyalo antes de exponerlo, y ten a mano el modo por reglas como respaldo (la app cae a él sola si el modelo falla).
