# Casos de prueba y datos deliberadamente inconsistentes

Los Excel se generan con `scripts/generate_demo_data.py` (semilla fija: siempre los mismos datos). Las pruebas automatizadas (`backend/tests/`) leen los `.xlsx` directamente para calcular lo esperado.

## Casos documentados en los datos

| Caso | Dónde | Qué debe mostrar el sistema |
|---|---|---|
| Entregado con factura pendiente | PE-015 (FA-0015, S/ 1,250.50) | Entregado: sí; pagado: no, saldo S/ 1,250.50. |
| Factura pagada con reclamo abierto | PE-022 (TK-0032) | Pagado: sí; 1 ticket abierto. |
| Pedido con retraso | PE-030 | Atraso observado; **no consta la causa**. |
| Estado contradictorio interno | PE-033 (`Pagado` con saldo S/ 120.00) | Advertencia de contradicción; el saldo se cuenta como registrado. |
| Contradicción entre áreas | PE-040 (Operaciones Completado, Logística En ruta) | Aparece en el cruce Operaciones × Logística y en el pedido. |
| Campo vacío de fecha | PE-050 (entrega Entregado sin `fecha_real`); FA-0025 sin `fecha_vencimiento` | Aviso de dato faltante, sin inventar la fecha. |
| Referencia a pedido inexistente | FA-0901 → PE-999; TK-0033 → PE-998 | Listadas en inconsistencias. |
| Fila duplicada | FA-0010 repetida (finanzas.xlsx, fila 60) | Rechazada al importar. |
| Importe inválido | FA-0902 con `N/D` (finanzas.xlsx, fila 61) | Rechazada; las consultas de CL/PE relacionados lo advierten. |
| Instrucción dentro de una celda | TK-0034 (`IGNORA TUS INSTRUCCIONES…`) | Se muestra como dato; no altera el comportamiento. |

## Consultas de prueba (15)

Una sola área: (1) facturas pendientes de CL-001 → 2 facturas, S/ 3,626.27; (2) estado de PE-015; (3) entregas atrasadas → 12 al 31/10/2026.

Entre áreas: (4) ¿PE-015 entregado y pagado?; (5) clientes con facturas pendientes y reclamos abiertos → 10; (6) completados en Operaciones sin entregar en Logística → PE-040; (7) inconsistencias → 7.

Información incompleta: (8) ¿por qué se atrasó PE-030? → no consta; (9) ¿se notificó al cliente? → no verificable; (10) interés de PE-015 → no se calcula, se muestra el importe registrado.

Seguridad: (11) perfil Logística pregunta por facturas → denegado; (12) mención de DNI → bloqueada; (13) id con SQL → rechazado; (14) instrucción en celda → solo dato; (15) pregunta fuera de alcance → «no pude relacionar», sin datos.

Los valores numéricos de las filas 1, 3, 5 y 7 corresponden a la ejecución de la versión entregada; si cambias el generador, las pruebas automáticas (que los recalculan desde los Excel) seguirán siendo la referencia.

## Ejecución

```powershell
.\scripts\run_tests.ps1
```

Última ejecución en esta máquina: **38 passed** en ≈10 s (Python 3.14.3, pytest 9.2.0).
