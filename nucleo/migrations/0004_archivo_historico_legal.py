"""Archivo histórico con conservación legal (DD-99).

1. Tres tablas ``hist_*`` nuevas, con la misma receta que las de la
   migración 0002 (``LIKE`` sin claves foráneas y ``fecha_archivo``): la
   cuenta corriente (``movimientos_cuenta``), los pedidos de autorización a
   distancia y las facturas recibidas por WhatsApp o correo. Sin ellas, el
   pasaje de un turno con ventas fiadas o con un pedido de autorización
   chocaba contra sus claves foráneas y se deshacía entero.
2. ``hist_compras`` recibe la marca de compra ocasional (DD-98) al final.
3. El archivo histórico se vuelve inalterable también en la base: un
   trigger rechaza todo UPDATE, todo DELETE y todo TRUNCATE sobre las
   tablas ``hist_*``. Se conservan diez años (art. 328 del Código Civil y
   Comercial); después, sólo una depuración explícita, que marca su
   transacción con ``SET LOCAL cafeto.depuracion_historica = 'on'``, puede
   borrar. El sistema no trae ningún proceso que lo haga solo.
"""
from django.db import migrations

NUEVAS = ('movimientos_cuenta', 'solicitudes_autorizacion', 'facturas_recibidas')

CREAR = """
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[%s] LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS hist_%%1$s (LIKE %%1$s INCLUDING DEFAULTS)', t);
        EXECUTE format(
            'ALTER TABLE hist_%%s ADD COLUMN IF NOT EXISTS fecha_archivo '
            'TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP', t);
        EXECUTE format('COMMENT ON TABLE hist_%%1$s IS %%2$L', t,
                       'Histórico de ' || upper(t) || ': fotografía sin FK, '
                       'de sólo lectura, cargada por pasar_a_historico.');
    END LOOP;
END $$;
ALTER TABLE hist_compras ADD COLUMN IF NOT EXISTS compra_ocasional boolean NOT NULL DEFAULT false;
""" % ', '.join("'%s'" % t for t in NUEVAS)

BORRAR = ('ALTER TABLE hist_compras DROP COLUMN IF EXISTS compra_ocasional;\n'
          + '\n'.join('DROP TABLE IF EXISTS hist_%s;' % t for t in NUEVAS))

FUNCION = r"""
CREATE OR REPLACE FUNCTION fn_historico_inalterable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE')
       AND coalesce(current_setting('cafeto.depuracion_historica', true), '') = 'on' THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NULL END;
    END IF;
    RAISE EXCEPTION
        'La tabla % es el archivo histórico: se conserva diez años tal como se archivó y es de sólo lectura (RPH, DD-99).',
        TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$;
"""

TRIGGERS = """
DO $$
DECLARE t TEXT;
BEGIN
    FOR t IN SELECT tablename FROM pg_tables
              WHERE schemaname = current_schema() AND tablename LIKE 'hist\\_%' LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS tr_%1$s_inalterable ON %1$I', t);
        EXECUTE format('CREATE TRIGGER tr_%1$s_inalterable BEFORE UPDATE OR DELETE ON %1$I '
                       'FOR EACH ROW EXECUTE FUNCTION fn_historico_inalterable()', t);
        EXECUTE format('DROP TRIGGER IF EXISTS tr_%1$s_sin_vaciar ON %1$I', t);
        EXECUTE format('CREATE TRIGGER tr_%1$s_sin_vaciar BEFORE TRUNCATE ON %1$I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION fn_historico_inalterable()', t);
    END LOOP;
END $$;
"""

SIN_TRIGGERS = """
DO $$
DECLARE t TEXT;
BEGIN
    FOR t IN SELECT tablename FROM pg_tables
              WHERE schemaname = current_schema() AND tablename LIKE 'hist\\_%' LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS tr_%1$s_inalterable ON %1$I', t);
        EXECUTE format('DROP TRIGGER IF EXISTS tr_%1$s_sin_vaciar ON %1$I', t);
    END LOOP;
END $$;
DROP FUNCTION IF EXISTS fn_historico_inalterable();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('nucleo', '0003_libros_inalterables'),
        ('compras', '0007_facturas_recibidas_y_compra_ocasional'),
        ('fidelizacion', '0009_plazo_del_fiado'),
        ('ventas', '0012_relevo_y_revision_del_turno'),
    ]

    operations = [
        migrations.RunSQL(CREAR, BORRAR),
        migrations.RunSQL(FUNCION + TRIGGERS, SIN_TRIGGERS),
    ]
