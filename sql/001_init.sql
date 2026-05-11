-- Schema para asistencia Hikvision
CREATE SCHEMA IF NOT EXISTS asistencia;

-- Eventos crudos deduplicados (lo que devuelve cada reloj)
CREATE TABLE IF NOT EXISTS asistencia.evento (
    id            BIGSERIAL PRIMARY KEY,
    device        VARCHAR(50)  NOT NULL,
    employee_no   VARCHAR(50)  NOT NULL,
    employee_name VARCHAR(200),
    event_time    TIMESTAMPTZ  NOT NULL,
    major         INT,
    minor         INT,
    card_no       VARCHAR(50),
    tipo          VARCHAR(20),                -- ENTRADA | SALIDA
    job_id        BIGINT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (employee_no, event_time, device)
);
CREATE INDEX IF NOT EXISTS idx_evento_emp_fecha ON asistencia.evento (employee_no, event_time);
CREATE INDEX IF NOT EXISTS idx_evento_job       ON asistencia.evento (job_id);

-- Resumen por (empleado, día)
CREATE TABLE IF NOT EXISTS asistencia.resumen_diario (
    id            BIGSERIAL PRIMARY KEY,
    employee_no   VARCHAR(50) NOT NULL,
    employee_name VARCHAR(200),
    fecha         DATE        NOT NULL,
    check_in      TIMESTAMPTZ,
    check_out     TIMESTAMPTZ,
    minutos       INT,
    eventos_dia   INT,
    devices       TEXT,                       -- "oficina,fabrica"
    job_id        BIGINT,
    UNIQUE (employee_no, fecha)
);
CREATE INDEX IF NOT EXISTS idx_resumen_fecha ON asistencia.resumen_diario (fecha);

-- Tracking de cada corrida del job
CREATE TABLE IF NOT EXISTS asistencia.job (
    id           BIGSERIAL PRIMARY KEY,
    start_ts     TIMESTAMPTZ NOT NULL,
    end_ts       TIMESTAMPTZ NOT NULL,
    status       VARCHAR(20) DEFAULT 'running',  -- running | ok | error
    eventos_raw  INT,
    eventos_ok   INT,
    resumen_filas INT,
    error_msg    TEXT,
    duracion_seg REAL,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);
