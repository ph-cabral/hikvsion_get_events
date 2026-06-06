-- Mapeo del ID de usuario del reloj Anviz al legajo de EVER WEAR.
ALTER TABLE everwear.legajo ADD COLUMN IF NOT EXISTS "anvizId" VARCHAR(50);
CREATE INDEX IF NOT EXISTS idx_legajo_anvizid ON everwear.legajo ("anvizId");
