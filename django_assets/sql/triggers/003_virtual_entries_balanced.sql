DROP TRIGGER IF EXISTS virtual_entries_balanced ON django_assets_virtualentry;
CREATE CONSTRAINT TRIGGER virtual_entries_balanced
AFTER INSERT OR UPDATE OR DELETE ON django_assets_virtualentry
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION assert_virtual_entries_balanced();
