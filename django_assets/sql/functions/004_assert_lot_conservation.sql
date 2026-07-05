-- ADR-0032 §8: per affected lot at COMMIT,
--   quantity_remaining = quantity − Σ matches.quantity
--   basis_remaining    = cost_basis − Σ matches.basis_recovered
--   0 ≤ remaining ≤ opening.
-- Rebuild needs no bypass: deferred semantics check final state.
CREATE OR REPLACE FUNCTION assert_lot_conservation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    lot_ids bigint[];
    checked bigint;
    lot record;
    matched numeric;
    recovered numeric;
BEGIN
    IF TG_TABLE_NAME = 'django_assets_lot' THEN
        lot_ids := ARRAY[NEW.id];
    ELSIF TG_OP = 'INSERT' THEN
        lot_ids := ARRAY[NEW.lot_id];
    ELSIF TG_OP = 'UPDATE' THEN
        lot_ids := ARRAY[NEW.lot_id, OLD.lot_id];
    ELSE
        lot_ids := ARRAY[OLD.lot_id];
    END IF;

    FOREACH checked IN ARRAY lot_ids LOOP
        SELECT * INTO lot FROM django_assets_lot WHERE id = checked;
        IF lot.id IS NULL THEN
            CONTINUE;  -- lot deleted in this transaction (rebuild truncation)
        END IF;
        SELECT COALESCE(SUM(quantity), 0), COALESCE(SUM(basis_recovered), 0)
        INTO matched, recovered
        FROM django_assets_lotmatch WHERE lot_id = checked;
        IF lot.quantity_remaining <> lot.quantity - matched
           OR lot.cost_basis_remaining <> lot.cost_basis - recovered
           OR lot.quantity_remaining < 0
           OR lot.quantity_remaining > lot.quantity THEN
            RAISE EXCEPTION
                'Lot conservation violated for lot %: remaining %/% against matches %',
                checked, lot.quantity_remaining, lot.quantity, matched
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END LOOP;

    RETURN NULL;
END;
$$;
