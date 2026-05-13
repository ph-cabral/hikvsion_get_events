-- Maestro de personas (cargado desde XLSX/CSV de Hikvision).
-- El nombre del resumen sale de acá, no del evento.
CREATE TABLE IF NOT EXISTS asistencia.persona (
    employee_no   VARCHAR(50)  PRIMARY KEY,
    nombre        VARCHAR(200) NOT NULL,
    departamento  VARCHAR(100),
    activo        BOOLEAN      NOT NULL DEFAULT TRUE,
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_persona_nombre ON asistencia.persona (lower(nombre));
