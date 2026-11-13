from django.db import migrations

SQL = """
CREATE OR REPLACE FUNCTION audit_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Auditní záznam nelze měnit ani mazat' USING ERRCODE = 'insufficient_privilege';
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_append_only ON audit_auditevent;
CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON audit_auditevent
    FOR EACH ROW EXECUTE FUNCTION audit_append_only();
"""

REVERSE = """
DROP TRIGGER IF EXISTS audit_append_only ON audit_auditevent;
DROP FUNCTION IF EXISTS audit_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
