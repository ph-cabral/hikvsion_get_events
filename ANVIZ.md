# Reloj Anviz — integración

Descarga los fichajes del reloj Anviz (protocolo TCP, puerto 5010) y los inserta
en `asistencia.evento`, junto con los de Hikvision. El dashboard de RRHH ya los
ve (lee `asistencia.evento`, columna `device = "anviz"`).

## Variables de entorno (`.env`)
```
ANVIZ_ENABLED=1
ANVIZ_IP=10.10.0.147
ANVIZ_PORT=5010
ANVIZ_DEVICE=anviz
```
 
## Mapeo de usuarios
El reloj identifica por su propio ID de usuario. Hay que vincularlo al legajo:
```bash
psql "$DATABASE_URL" -f sql/002_anviz.sql      # agrega la columna anvizId
# luego cargar el anvizId de cada empleado, p.ej.:
# UPDATE everwear.legajo SET "anvizId" = '1024' WHERE "employeeNo" = '235';
```
Los registros del reloj cuyo ID no esté mapeado se cuentan en `sin_mapeo` y se ignoran
(no se guardan en ningún lado — por eso existe `/debug/anviz`, ver abajo).

### Ver qué IDs tiene el reloj (para ir vinculando)
`GET /debug/anviz` lee el buffer del reloj en vivo (no toca la DB) y devuelve,
por cada `anviz_id`, cuántas fichadas tiene, primera/última fecha, y si ya está
vinculado a un legajo. Con eso se identifica el ID de cada persona (por
horario/cantidad) y se carga con el UPDATE de arriba.

## Uso
- Manual:  `POST /sync/anviz`  → descarga el buffer del reloj y upserta.
- CLI:     `python -m app.anviz_poller`
- Automático: si `ANVIZ_ENABLED=1`, corre junto al poll de Hikvision (cada 15 min, 7-19h).

`/health` muestra el estado de Anviz. La clasificación ENTRADA/SALIDA usa la hora
de corte `CLASIF_CUTOFF_HOUR` (igual que fabrica/oficina).
