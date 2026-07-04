-- Deferred constraint trigger: the ledger's one universal integrity rule
-- (Product ADR-0004/0020). PostgreSQL has no CREATE TRIGGER IF NOT EXISTS.
DROP TRIGGER IF EXISTS transaction_legs_balanced ON django_assets_transactionleg;
CREATE CONSTRAINT TRIGGER transaction_legs_balanced
AFTER INSERT OR UPDATE OR DELETE ON django_assets_transactionleg
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION assert_transaction_balanced();
