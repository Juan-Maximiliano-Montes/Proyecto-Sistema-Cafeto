"""El trigger de los libros inalterables (DD-68).

Prueba la base directamente, sin pasar por los modelos: es justamente el
caso que la regla de la aplicación no cubre (alguien con ``psql``).
"""
from io import StringIO
from datetime import timedelta

from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.utils import timezone

from seguridad.models import LogAuditoria


class LibrosInalterablesTest(TestCase):

    def setUp(self):
        self.asiento = LogAuditoria.objects.create(
            operacion_realizada='ALTA DE RUBRO', modulo_afectado='STOCK')

    def sql(self, sentencia):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sentencia, [self.asiento.pk])

    def test_no_se_modifica_por_sql(self):
        with self.assertRaisesMessage(IntegrityError, 'libro de solo agregado'):
            self.sql('UPDATE log_auditoria SET detalle_operacion = %s::text '
                     "|| 'x' WHERE id_auditoria = %s".replace('%s::text', "'otro'", 1))

    def test_no_se_borra_por_sql(self):
        with self.assertRaisesMessage(IntegrityError, 'libro de solo agregado'):
            self.sql('DELETE FROM log_auditoria WHERE id_auditoria = %s')

    def test_el_pase_a_historico_si_lo_mueve(self):
        LogAuditoria.objects.filter(pk=self.asiento.pk)   # sigue ahí
        viejo = LogAuditoria.objects.create(
            operacion_realizada='ALTA DE RUBRO', modulo_afectado='STOCK',
            fecha_hora=timezone.now() - timedelta(days=800))
        call_command('pasar_a_historico', '--ejecutar', stdout=StringIO())
        self.assertFalse(LogAuditoria.objects.filter(pk=viejo.pk).exists())
        # y la marca no queda puesta después
        with self.assertRaises(IntegrityError):
            self.sql('DELETE FROM log_auditoria WHERE id_auditoria = %s')
