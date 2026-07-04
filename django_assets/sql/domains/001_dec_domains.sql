-- Scale-checked NUMERIC domains (Product ADR-0004). Idempotent.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'dec8') THEN
        CREATE DOMAIN dec8 AS numeric CHECK (scale(VALUE) <= 8);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'dec18') THEN
        CREATE DOMAIN dec18 AS numeric CHECK (scale(VALUE) <= 18);
    END IF;
END
$$;

-- The ledger amount column is governed by dec18: unconstrained precision,
-- scale capped at 18, and out-of-scale writes ERROR instead of PostgreSQL's
-- silent rounding for numeric(40,18).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'django_assets_transactionleg'
          AND column_name = 'amount'
          AND domain_name IS DISTINCT FROM 'dec18'
    ) THEN
        ALTER TABLE django_assets_transactionleg
            ALTER COLUMN amount TYPE dec18;
    END IF;
END
$$;
