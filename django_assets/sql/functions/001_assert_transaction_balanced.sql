-- Per-instrument zero-sum across a transaction's legs (Product ADR-0004).
-- Fires from a deferred constraint trigger at COMMIT; must handle INSERT,
-- UPDATE (both old and new transaction ids), and DELETE (OLD only).
CREATE OR REPLACE FUNCTION assert_transaction_balanced() RETURNS trigger AS $$
DECLARE
    tx_ids bigint[];
    bad_tx bigint;
BEGIN
    IF TG_OP = 'INSERT' THEN
        tx_ids := ARRAY[NEW.transaction_id];
    ELSIF TG_OP = 'DELETE' THEN
        tx_ids := ARRAY[OLD.transaction_id];
    ELSE
        tx_ids := ARRAY[NEW.transaction_id, OLD.transaction_id];
    END IF;

    SELECT l.transaction_id INTO bad_tx
    FROM django_assets_transactionleg l
    WHERE l.transaction_id = ANY (tx_ids)
    GROUP BY l.transaction_id, l.instrument_id
    HAVING SUM(l.amount) <> 0
    LIMIT 1;

    IF bad_tx IS NOT NULL THEN
        RAISE EXCEPTION 'Unbalanced transaction %', bad_tx
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
