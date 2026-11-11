"""Los datos que crean las migraciones cumplen las reglas de su modelo.

Un dato inicial que no pasa su propia validación no se puede ni editar
desde la pantalla: pasó con el medio de pago "QR", que tenía menos letras
de las que pedía el formulario. Esta prueba corre ``full_clean()`` sobre
cada fila que dejan las migraciones.
"""
from django.apps import apps
from django.core.exceptions import ValidationError
from django.test import TestCase

PROPIAS = ('compras', 'fidelizacion', 'geografia', 'negocio', 'nucleo',
           'seguridad', 'stock', 'ventas')


class DatosInicialesTest(TestCase):

    def test_cada_dato_inicial_pasa_su_validacion(self):
        fallas = []
        for modelo in apps.get_models():
            if modelo._meta.app_label not in PROPIAS or modelo._meta.proxy:
                continue
            for fila in modelo._default_manager.all():
                try:
                    fila.full_clean(validate_unique=False, validate_constraints=False)
                except ValidationError as error:
                    fallas.append(f'{modelo.__name__} «{fila}»: {error.messages}')
        self.assertEqual(fallas, [], '\n'.join(fallas))
