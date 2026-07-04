-- Deferred constraint trigger: the partition rule validates final state
-- at COMMIT (trades spec 2.4). DELETE can only shrink sums, so it does
-- not fire.
DROP TRIGGER IF EXISTS trade_allocations_within_leg ON django_assets_tradeallocation;
CREATE CONSTRAINT TRIGGER trade_allocations_within_leg
AFTER INSERT OR UPDATE ON django_assets_tradeallocation
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION assert_trade_allocations_within_leg();
