# Hikvision Asistencia API

API on-demand que descarga eventos de relojes Hikvision en paralelo y los guarda en Postgres.

## Stack
- FastAPI + uvicorn
- psycopg3 (pool)
- Postgres del stack `ever` (schema `asistencia`)

## Setup

```bash
cp .env.example .env
# editar .env: HIK_DEVICES, HIK_PASSWORD, DATABASE_URL
```

Crear schema/tablas (una vez):
```bash
psql "$DATABASE_URL" -f sql/001_init.sql
```

Levantar:
```bash
docker compose up -d --build
curl http://localhost:8088/health
```

## Endpoints

| Método | Path | Descripción |
|---|---|---|
| GET  | `/health` | ping + lista de devices |
| POST | `/sync` | dispara descarga para un rango |
| POST | `/sync/today` | descarga el día de hoy (AR) |
| GET  | `/resumen?desde=&hasta=&employee_no=` | resumen check-in/out |
| GET  | `/eventos?desde=&hasta=&employee_no=` | eventos crudos |

### Ejemplo `/sync`
```bash
curl -X POST http://localhost:8088/sync \
  -H 'Content-Type: application/json' \
  -d '{
    "start": "2026-05-01T00:00:00-03:00",
    "end":   "2026-05-11T23:59:59-03:00",
    "async_mode": false
  }'
```

Respuesta:
```json
{
  "job_id": 1, "status": "ok",
  "eventos_raw": 1842, "eventos_ok": 1530,
  "resumen_filas": 312, "duracion_seg": 6.4
}
```

Si `async_mode: true` → responde `queued` y corre en background (útil para rangos largos).

### Auth opcional
Si `API_TOKEN` no está vacío, todos los endpoints (salvo `/health`) exigen header:
```
X-Token: tu_token
```

## Tablas (schema `asistencia`)
- `evento` — eventos crudos deduplicados (unique por employee+time+device)
- `resumen_diario` — check_in/check_out/minutos por (empleado, día)
- `job` — auditoría de cada corrida

Ambos endpoints de sync son idempotentes (UPSERT por unique keys).

## Integración con n8n
Llamar `POST /sync` o `POST /sync/today` desde un workflow programado (cron node de n8n). Si el rango es grande usar `async_mode: true` y luego consultar `/resumen`.
# hikvsion_get_events
