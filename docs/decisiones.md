# Decisiones técnicas y lo que NO se implementó

## Decisiones

1. **Frontend en un solo `index.html` servido por FastAPI** en lugar de Next.js + Tailwind + shadcn/ui. La especificación permite priorizar backend y una UI mínima funcional; así se evita Node, un segundo servidor y una build, y la demo funciona sin internet. Coste: menos componentes reutilizables y estética hecha a mano (CSS propio con la paleta azul marino / turquesa / morado).
2. **Funciones cerradas en vez de text-to-SQL.** El LLM nunca ejecuta SQL; elige entre herramientas con argumentos validados.
3. **Diez áreas sintéticas.** Se mantienen las cuatro áreas originales y se agregan Fiscalización, Recaudación, Notificaciones, Impugnaciones, Convenios y Documentos para demostrar diez fuentes enlazadas por identificadores ficticios. No modelan los sistemas reales de multas.
4. **DuckDB en memoria, reconstruida al importar.** Los Excel originales y los archivos subidos se mantienen como fuentes; las cargas del navegador se guardan en `data/uploads/` y se agregan sin reemplazar. `data/generated/` guarda `manifiesto.json` (hash y versión por archivo) y `registro.jsonl`. Los archivos se procesan por filas para no acumular todas las filas válidas en listas Python.
5. **Reglas de «pendiente» y «atrasada» explícitas.** Pendiente = `saldo > 0`; si el estado dice Pagado pero hay saldo, se cuenta y se advierte la contradicción. Atrasada = no entregada con `fecha_estimada` anterior a la fecha de referencia (configurable, fija en la demo).
6. **Vacíos y valores inválidos se reportan**: cada celda vacía y cada valor no válido se muestra como aviso con columna y valor original. Los campos obligatorios vacíos, valores no válidos y duplicados rechazan la fila; un campo opcional vacío no invalida el registro. La interfaz permite descargar rechazos y avisos en un libro Excel.
7. **Permiso todo-o-nada por herramienta.** Si el perfil no cubre todas las áreas que la herramienta necesita, no se devuelve nada (devolver datos parciales haría creer, por ejemplo, que «no hay factura»).
8. **Ollama como único proveedor del Nivel B**: sin pago por tokens y sin dependencias nuevas (`urllib` de la biblioteca estándar).

## No se implementó

- **Next.js / shadcn / Tailwind:** ver decisión 1.
- **Adaptador Gemini u otra API en la nube:** no es necesario para el criterio de costo US$0 y evitaría enviar datos a terceros; el protocolo `ModeloChat` permite añadirlo.
- **Evaluación sistemática del Nivel B:** solo hubo una prueba puntual con `llama3.2` (3 preguntas: 2 resueltas por el agente, 1 caída a reglas). Falta medir exactitud, latencia y tokens con un conjunto de preguntas y, idealmente, un modelo mayor.
- **SSO empresarial y permisos por registro:** fuera de alcance de una POC con datos ficticios. La demo ahora tiene una cuenta compartida de acceso básico; no autentica identidades individuales.
- **RAG, vector DB, multiagentes, MCP, n8n, nube:** prohibidos por la especificación y sin beneficio medible aquí.
- **Playwright / pruebas de interfaz automatizadas:** la interfaz se revisó a mano por DOM; no hay pruebas automáticas de UI.
- **Multas reales ni reglas legales de intereses:** los datos nuevos son sintéticos; no representan una fuente oficial ni autorizan decisiones legales.
- **Pestaña de RR. HH.:** opcional en la especificación; omitida.

## Riesgos y siguientes pasos

- Validar el Nivel B con un modelo local real y medir exactitud, latencia y tokens antes de llamarlo «agente validado».
- Ejecutar `scripts/run_scale_test.py --area recaudacion --rows 1000000` en una máquina de pruebas con suficiente RAM y medir memoria máxima además del tiempo. La prueba de 200.000 filas no demuestra capacidad para un millón ni concurrencia.
- Con datos reales: SSO, permisos por registro, revisión de privacidad, y definición del negocio de «pendiente», «atrasada» y estados.
- Si se quiere la UI en Next.js, la API ya está separada y no necesita cambios.
