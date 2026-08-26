-- Limpieza de las fichadas de Anviz re-insertadas por el backfill del 2026-08-25.
--
-- CONTEXTO
-- `fix_anviz_fecha` MUEVE filas (+1 dia) y deja libre el event_time original.
-- El backfill posterior, con el epoch todavia mal, volvio a insertar las mismas
-- fichadas en ese hueco. Conviven las dos versiones: la corregida y la corrida.
--
-- COMO SE IDENTIFICA LA QUE SOBRA
-- `asistencia.evento_fix_backup` guarda, por cada fila movida, su `id` y su
-- `event_time_orig`. La fila que hoy ocupa ese `event_time_orig` con un id
-- DISTINTO al del backup es necesariamente la re-insertada.
--
-- POR QUE NO ALCANZA CON COMPARAR event_time_orig (bug de la version anterior)
-- Desde que agosto/2026 tambien se corrio con `fix_anviz_fecha` (2026-08-26),
-- hay cadenas: una fila legitima movida de Aug-02 a Aug-03 ocupa el Aug-02 que
-- es el `event_time_orig` de OTRA fila. Sin el chequeo `b.id <> e.id` y sin
-- acotar el rango, la query borraba filas buenas.
-- Guardas: 1) `b.id <> e.id`  2) solo el periodo duplicado (< 2026-08-01)
--          3) solo backups anteriores al fix de agosto.
--
-- Correr con psql, paso por paso. NO dejar la transaccion abierta pasadas
-- las 7 AM: toma locks sobre asistencia.evento y el scheduler queda esperando.

\set corte_periodo '''2026-08-01 00:00:00-03'''
\set corte_backup  '''2026-08-26 00:00:00-03'''

-- ---------------------------------------------------------------------------
-- PASO 1 — cuantas filas se van a borrar (esperado: 663)
-- ---------------------------------------------------------------------------
SELECT count(*) AS borrables
FROM asistencia.evento e
JOIN asistencia.evento_fix_backup b
  ON  b.employee_no     = e.employee_no
  AND b.event_time_orig = e.event_time
  AND b.id             <> e.id
  AND b.fixed_at        < :corte_backup
WHERE e.device = 'anviz'
  AND e.event_time < :corte_periodo
  AND EXISTS (SELECT 1 FROM asistencia.evento t
              WHERE t.id = b.id
                AND t.event_time = e.event_time
                          + (b.dias_aplicados || ' days')::interval);

-- ---------------------------------------------------------------------------
-- PASO 2 — HUERFANAS: filas corridas SIN su par corregido.
-- Estas NO se borran: hay que moverlas. Esperado: 0.
-- ---------------------------------------------------------------------------
SELECT e.id, e.employee_no, e.employee_name,
       e.event_time AT TIME ZONE 'America/Argentina/Buenos_Aires' AS fecha_ar
FROM asistencia.evento e
JOIN asistencia.evento_fix_backup b
  ON  b.employee_no     = e.employee_no
  AND b.event_time_orig = e.event_time
  AND b.id             <> e.id
  AND b.fixed_at        < :corte_backup
WHERE e.device = 'anviz'
  AND e.event_time < :corte_periodo
  AND NOT EXISTS (SELECT 1 FROM asistencia.evento t
                  WHERE t.id = b.id
                    AND t.event_time = e.event_time
                              + (b.dias_aplicados || ' days')::interval)
ORDER BY e.event_time;

-- ---------------------------------------------------------------------------
-- PASO 3 — los pares a 1 dia que NO vienen del backup (esperado: 3).
-- Revisarlos a ojo antes de seguir: pueden ser fichadas reales de dias
-- consecutivos a la misma hora, no duplicados.
-- ---------------------------------------------------------------------------
SELECT a.id AS id_a, b2.id AS id_b, a.employee_no, a.employee_name,
       a.event_time AT TIME ZONE 'America/Argentina/Buenos_Aires' AS fecha_a
FROM asistencia.evento a
JOIN asistencia.evento b2
  ON  b2.employee_no = a.employee_no
  AND b2.device      = 'anviz'
  AND b2.event_time  = a.event_time + interval '1 day'
WHERE a.device = 'anviz'
  AND NOT EXISTS (SELECT 1 FROM asistencia.evento_fix_backup b
                  WHERE b.employee_no     = a.employee_no
                    AND b.event_time_orig = a.event_time
                    AND b.id             <> a.id)
ORDER BY a.event_time;

-- ---------------------------------------------------------------------------
-- PASO 4 — borrado. Solo si PASO 1 da ~663 y PASO 2 da 0 filas.
-- ---------------------------------------------------------------------------
BEGIN;

CREATE TABLE IF NOT EXISTS asistencia.evento_dup_backup AS
SELECT e.*, now() AS borrado_at FROM asistencia.evento e WHERE false;

INSERT INTO asistencia.evento_dup_backup
SELECT e.*, now()
FROM asistencia.evento e
JOIN asistencia.evento_fix_backup b
  ON  b.employee_no     = e.employee_no
  AND b.event_time_orig = e.event_time
  AND b.id             <> e.id
  AND b.fixed_at        < :corte_backup
WHERE e.device = 'anviz'
  AND e.event_time < :corte_periodo
  AND EXISTS (SELECT 1 FROM asistencia.evento t
              WHERE t.id = b.id
                AND t.event_time = e.event_time
                          + (b.dias_aplicados || ' days')::interval);

DELETE FROM asistencia.evento e
USING asistencia.evento_dup_backup d
WHERE e.id = d.id;

-- ---------------------------------------------------------------------------
-- PASO 5 — verificar ANTES del COMMIT.
-- Esperado: dom = 0 en todos los meses, vie > 0, totales a la mitad.
-- ---------------------------------------------------------------------------
SELECT to_char(t,'YYYY-MM') AS mes,
       count(*) FILTER (WHERE extract(dow from t) = 0) AS dom,
       count(*) FILTER (WHERE extract(dow from t) = 5) AS vie,
       count(*)                                        AS total
FROM (SELECT event_time AT TIME ZONE 'America/Argentina/Buenos_Aires' AS t
      FROM asistencia.evento WHERE device = 'anviz') s
GROUP BY 1 ORDER BY 1;

-- Si el cuadro cierra:   COMMIT;
-- Si algo no cuadra:     ROLLBACK;
