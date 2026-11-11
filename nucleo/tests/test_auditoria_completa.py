"""Toda operación que cambia datos deja la foto de antes y de después.

El Registro de Actividad tiene que poder contestar "qué había y qué quedó"
en cada asiento (D-04, pedido de la cátedra para el Hito 4). Los CRUD de
maestros ya lo hacían; los procesos (ventas, compras, egresos, caja, stock)
sólo dejaban un texto. Esta prueba recorre el código y exige la foto en
cada llamada a ``registrar_auditoria``, así una operación nueva no puede
olvidarla sin que la suite lo marque.

Quedan afuera, a propósito, los asientos que no cambian ninguna fila: una
exportación, una impresión o una alerta. Los rechazos van por
``registrar_rechazo`` y, por regla de la base, no llevan fotos (DD-62).
"""
import ast
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

# Asientos que no cambian datos: no hay "antes" ni "después" que mostrar.
# SOLICITUD: pedir el enlace para recuperar la clave sólo manda un correo.
SIN_CAMBIO_DE_DATOS = {'EXPORTACION', 'IMPRESION', 'ALERTA', 'REVISIÓN', 'SOLICITUD'}

# Operaciones que crean la fila: su "antes" es, por definición, NULL.
CREAN_LA_FILA = {'ALTA', 'APERTURA', 'INICIO', 'CANJE'}


def _llamadas():
    raiz = Path(settings.BASE_DIR)
    for archivo in sorted(raiz.rglob('*.py')):
        partes = archivo.relative_to(raiz).parts
        if (partes[0] in ('venv', '.venv', 'env') or 'migrations' in partes
                or 'tests' in partes):
            continue
        arbol = ast.parse(archivo.read_text(encoding='utf-8'))
        for nodo in ast.walk(arbol):
            if (isinstance(nodo, ast.Call)
                    and getattr(nodo.func, 'id', None) == 'registrar_auditoria'):
                yield archivo.relative_to(raiz), nodo


def _constante(nodo):
    return nodo.value if isinstance(nodo, ast.Constant) else None


class FotosEnCadaOperacionTest(SimpleTestCase):

    def test_cada_operacion_que_cambia_datos_trae_sus_fotos(self):
        faltan = []
        for archivo, llamada in _llamadas():
            claves = {k.arg: k.value for k in llamada.keywords}
            operacion = (_constante(llamada.args[1])
                         if len(llamada.args) > 1 else None)
            if operacion in SIN_CAMBIO_DE_DATOS or 'resultado' in claves:
                continue   # sin cambios, o un rechazo (que no lleva fotos)
            # posicionales: request, operacion, entidad, anterior, nuevo
            anterior = claves.get('anterior',
                                  llamada.args[3] if len(llamada.args) > 3 else None)
            nuevo = claves.get('nuevo',
                               llamada.args[4] if len(llamada.args) > 4 else None)
            donde = f'{archivo}:{llamada.lineno} ({operacion or "dinámica"})'
            if nuevo is None or _constante(nuevo) is None and isinstance(nuevo, ast.Constant):
                faltan.append(f'{donde}: falta la foto de después')
            if operacion is not None and operacion not in CREAN_LA_FILA and (
                    anterior is None
                    or isinstance(anterior, ast.Constant) and anterior.value is None):
                faltan.append(f'{donde}: falta la foto de antes')
        self.assertEqual(faltan, [], '\n' + '\n'.join(faltan))
