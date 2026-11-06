# Decisiones técnicas y lo que NO se implementó

## Decisiones

1. **Frontend en un solo `index.html` servido por FastAPI** en lugar de Next.js + Tailwind + shadcn/ui. La especificación permite priorizar backend y una UI mínima funcional; así se evita Node, un segundo servidor y una build, y la demo funciona sin internet. Coste: menos componentes reutilizables y estética hecha a mano (CSS propio con la paleta azul marino / turquesa / morado).
2. **Funciones cerradas en vez de text-to-SQL.** El LLM nunca ejecuta SQL; elige entre ocho herramientas con argumentos validados.
3. **DuckDB en memoria, reconstruida al importar.** Los Excel siguen siendo la fuente; `data/generated/` solo guarda `manifiesto.json` (hash y versión por archivo) y `registro.jsonl`.
4. **Reglas de «pendiente» y «atrasada» explícitas.** Pendiente = `saldo > 0`; si el estado dice Pagado pero hay saldo, se cuenta y se advierte la contradicción. Atrasada = no entregada con `fecha_estimada` anterior a la fecha de referencia (configurable, fija en la demo).
5. **Una fila vacía en una fecha no la rechaza**: se importa con aviso, porque rechazarla ocultaría el pedido. Montos inválidos, ids con formato incorrecto, estados desconocidos y duplicados sí se rechazan, y las respuestas avisan cuando una fila rechazada menciona el id consultado.
6. **Permiso todo-o-nada por herramienta.** Si el perfil no cubre todas las áreas que la herramienta necesita, no se devuelve nada (devolver datos parciales haría creer, por ejemplo, que «no hay factura»).
7. **Ollama como único proveedor del Nivel B**: sin pago por tokens y sin dependencias nuevas (`urllib` de la biblioteca estándar).

## No se implementó

- **Next.js / shadcn / Tailwind:** ver decisión 1.
- **Adaptador Gemini u otra API en la nube:** no es necesario para el criterio de costo US$0 y evitaría enviar datos a terceros; el protocolo `ModeloChat` permite añadirlo.
- **Evaluación sistemática del Nivel B:** solo hubo una prueba puntual con `llama3.2` (3 preguntas: 2 resueltas por el agente, 1 caída a reglas). Falta medir exactitud, latencia y tokens con un conjunto de preguntas y, idealmente, un modelo mayor.
- **Subida de Excel desde el navegador:** se lee una carpeta local y se reimporta con un botón (la especificación admite «o lectura desde carpeta local»). Evita endpoints que escriban archivos.
- **Autenticación real, permisos por registro, Supabase/PostgreSQL:** fuera de alcance de una POC con datos ficticios.
- **RAG, vector DB, multiagentes, MCP, n8n, nube:** prohibidos por la especificación y sin beneficio medible aquí.
- **Playwright / pruebas de interfaz automatizadas:** la interfaz se revisó a mano por DOM; no hay pruebas automáticas de UI.
- **Cálculo de intereses y verificación de notificaciones:** no hay regla aprobada ni fuente; el sistema lo dice.
- **Pestaña de RR. HH.:** opcional en la especificación; omitida.

## Riesgos y siguientes pasos

- Validar el Nivel B con un modelo local real y medir exactitud, latencia y tokens antes de llamarlo «agente validado».
- Con datos reales: login, permisos por registro, revisión de privacidad, y definición del negocio de «pendiente», «atrasada» y estados.
- Si se quiere la UI en Next.js, la API ya está separada y no necesita cambios.
