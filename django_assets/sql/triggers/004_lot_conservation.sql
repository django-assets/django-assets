DROP TRIGGER IF EXISTS lot_conservation ON django_assets_lot;
CREATE CONSTRAINT TRIGGER lot_conservation
AFTER UPDATE ON django_assets_lot
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION assert_lot_conservation();

DROP TRIGGER IF EXISTS lot_conservation_matches ON django_assets_lotmatch;
CREATE CONSTRAINT TRIGGER lot_conservation_matches
AFTER INSERT OR UPDATE OR DELETE ON django_assets_lotmatch
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION assert_lot_conservation();
