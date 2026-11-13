from django.db import migrations

SQL = """
CREATE OR REPLACE FUNCTION invoicing_invoice_guard() RETURNS trigger AS $$
DECLARE
    allowed text[] := ARRAY['status', 'cancelled_at', 'cancelled_by_id', 'cancel_reason',
                            'delivery_status', 'version', 'updated_at', 'updated_by_id'];
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'draft' THEN
            RAISE EXCEPTION 'Vystavenou fakturu nelze smazat' USING ERRCODE = 'check_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status = 'draft' THEN
        RETURN NEW;
    END IF;
    IF (to_jsonb(NEW) - allowed) IS DISTINCT FROM (to_jsonb(OLD) - allowed) THEN
        RAISE EXCEPTION 'Vystavenou fakturu nelze přepsat' USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status = 'cancelled' AND NEW.status <> 'cancelled' THEN
        RAISE EXCEPTION 'Stornovanou fakturu nelze obnovit' USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status = 'issued' AND NEW.status NOT IN ('issued', 'cancelled') THEN
        RAISE EXCEPTION 'Nepovolený přechod stavu faktury' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS invoicing_invoice_guard ON invoicing_invoice;
CREATE TRIGGER invoicing_invoice_guard BEFORE UPDATE OR DELETE ON invoicing_invoice
    FOR EACH ROW EXECUTE FUNCTION invoicing_invoice_guard();

CREATE OR REPLACE FUNCTION invoicing_line_guard() RETURNS trigger AS $$
DECLARE
    st text;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT status INTO st FROM invoicing_invoice WHERE id = OLD.invoice_id;
        IF st IS NOT NULL AND st <> 'draft' THEN
            RAISE EXCEPTION 'Položky vystavené faktury nelze měnit' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        SELECT status INTO st FROM invoicing_invoice WHERE id = NEW.invoice_id;
        IF st IS NOT NULL AND st <> 'draft' THEN
            RAISE EXCEPTION 'Položky vystavené faktury nelze měnit' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    RETURN OLD;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS invoicing_line_guard ON invoicing_invoiceline;
CREATE TRIGGER invoicing_line_guard BEFORE INSERT OR UPDATE OR DELETE ON invoicing_invoiceline
    FOR EACH ROW EXECUTE FUNCTION invoicing_line_guard();

CREATE OR REPLACE FUNCTION invoicing_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Záznam (%) nelze měnit ani mazat', TG_TABLE_NAME USING ERRCODE = 'check_violation';
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS invoicing_payment_append_only ON invoicing_payment;
CREATE TRIGGER invoicing_payment_append_only BEFORE UPDATE OR DELETE ON invoicing_payment
    FOR EACH ROW EXECUTE FUNCTION invoicing_append_only();
DROP TRIGGER IF EXISTS invoicing_delivery_append_only ON invoicing_deliveryrecord;
CREATE TRIGGER invoicing_delivery_append_only BEFORE UPDATE OR DELETE ON invoicing_deliveryrecord
    FOR EACH ROW EXECUTE FUNCTION invoicing_append_only();
DROP TRIGGER IF EXISTS invoicing_reminder_append_only ON invoicing_reminderrecord;
CREATE TRIGGER invoicing_reminder_append_only BEFORE UPDATE OR DELETE ON invoicing_reminderrecord
    FOR EACH ROW EXECUTE FUNCTION invoicing_append_only();
"""

REVERSE = """
DROP TRIGGER IF EXISTS invoicing_invoice_guard ON invoicing_invoice;
DROP TRIGGER IF EXISTS invoicing_line_guard ON invoicing_invoiceline;
DROP TRIGGER IF EXISTS invoicing_payment_append_only ON invoicing_payment;
DROP TRIGGER IF EXISTS invoicing_delivery_append_only ON invoicing_deliveryrecord;
DROP TRIGGER IF EXISTS invoicing_reminder_append_only ON invoicing_reminderrecord;
DROP FUNCTION IF EXISTS invoicing_invoice_guard();
DROP FUNCTION IF EXISTS invoicing_line_guard();
DROP FUNCTION IF EXISTS invoicing_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("invoicing", "0001_initial")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
