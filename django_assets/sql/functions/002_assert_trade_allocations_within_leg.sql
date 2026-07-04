-- ADR-0030 partition rule (trades spec 2.4): per leg, all allocations
-- share the leg's sign and ABS(SUM(amount)) <= ABS(leg.amount) across
-- all trades and categories. Reads the core leg row, never modifies it.
-- Concurrent allocators of one leg serialize on pg_advisory_xact_lock.
CREATE OR REPLACE FUNCTION assert_trade_allocations_within_leg()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    leg_ids bigint[];
    checked_leg bigint;
    leg_amount numeric;
    allocated numeric;
    mismatched integer;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        leg_ids := ARRAY[NEW.leg_id, OLD.leg_id];
    ELSE
        leg_ids := ARRAY[NEW.leg_id];
    END IF;

    FOREACH checked_leg IN ARRAY leg_ids LOOP
        PERFORM pg_advisory_xact_lock(checked_leg);

        SELECT amount INTO leg_amount
        FROM django_assets_transactionleg WHERE id = checked_leg;
        IF leg_amount IS NULL THEN
            CONTINUE;  -- leg deleted in this transaction; cascade owns it
        END IF;

        SELECT count(*) INTO mismatched
        FROM django_assets_tradeallocation
        WHERE leg_id = checked_leg
          AND (sign(amount) <> sign(leg_amount) OR amount = 0);
        IF mismatched > 0 THEN
            RAISE EXCEPTION 'Allocation sign mismatch on leg % (leg amount %)',
                checked_leg, leg_amount
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;

        SELECT COALESCE(SUM(amount), 0) INTO allocated
        FROM django_assets_tradeallocation WHERE leg_id = checked_leg;
        IF ABS(allocated) > ABS(leg_amount) THEN
            RAISE EXCEPTION 'Over-allocated leg %: allocations sum to % against leg amount %',
                checked_leg, allocated, leg_amount
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END LOOP;

    RETURN NULL;
END;
$$;
