-- ADR-0031: per transfer, per instrument, virtual entries sum to zero —
-- the exact analog of core's ledger trigger, on the trades book's own
-- table. Trades structurally cannot deviate from the ledger.
CREATE OR REPLACE FUNCTION assert_virtual_entries_balanced()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    transfer_ids bigint[];
    bad record;
BEGIN
    IF TG_OP = 'INSERT' THEN
        transfer_ids := ARRAY[NEW.transfer_id];
    ELSIF TG_OP = 'UPDATE' THEN
        transfer_ids := ARRAY[NEW.transfer_id, OLD.transfer_id];
    ELSE
        transfer_ids := ARRAY[OLD.transfer_id];
    END IF;

    FOR bad IN
        SELECT transfer_id, instrument_id, SUM(amount) AS total
        FROM django_assets_virtualentry
        WHERE transfer_id = ANY(transfer_ids)
        GROUP BY transfer_id, instrument_id
        HAVING SUM(amount) <> 0
    LOOP
        RAISE EXCEPTION
            'Unbalanced virtual transfer %: instrument % sums to %',
            bad.transfer_id, bad.instrument_id, bad.total
            USING ERRCODE = 'integrity_constraint_violation';
    END LOOP;

    RETURN NULL;
END;
$$;
