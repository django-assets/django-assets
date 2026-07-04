DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'django_assets_transactionleg'
          AND column_name = 'amount' AND domain_name = 'dec18'
    ) THEN
        ALTER TABLE django_assets_transactionleg
            ALTER COLUMN amount TYPE numeric(40, 18);
    END IF;
END
$$;
DROP DOMAIN IF EXISTS dec8;
DROP DOMAIN IF EXISTS dec18;
