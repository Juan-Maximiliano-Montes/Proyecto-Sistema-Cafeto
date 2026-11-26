"""Los clientes del club con su cuenta de Firebase.

    SinCredencialTest      sin la cuenta de servicio configurada: 503 claro
    VerificacionTest       sólo vale un token válido, con uid y correo verificado
    CuentaTest             su QR real, sus puntos y su alta como en la web
    VinculoTest            por uid; si no, por correo; con correo repetido,
                           por DNI y con freno; nunca por teléfono

La verificación real necesita ``firebase-admin`` y la cuenta de servicio
del proyecto. Las pruebas no salen a internet: reemplazan el paquete por
uno de mentira que contesta lo que haría Firebase.
"""
import sys
import types
from unittest import mock

from django.test import override_settings

from api_movil import firebase
from api_movil.models import IntentoDeVinculacion
from api_movil.tests.base import BaseApi
from api_movil.views_clientes import INTENTOS_CON_DNI
from fidelizacion.forms import ClienteForm
from fidelizacion.models import CategoriaFidelizacion, Cliente, MovimientoPuntos
from seguridad.models import LogAuditoria

CORREO = 'sofia@correo.test'
UID = 'uid-de-sofia'


def firebase_de_mentira(contesta):
    """Un ``firebase_admin`` que en ``verify_id_token`` hace ``contesta``."""
    excepciones = types.ModuleType('firebase_admin.exceptions')

    class FirebaseError(Exception):
        pass

    excepciones.FirebaseError = FirebaseError
    auth = types.ModuleType('firebase_admin.auth')
    auth.CertificateFetchError = type('CertificateFetchError', (FirebaseError,), {})
    auth.InvalidIdTokenError = type('InvalidIdTokenError', (FirebaseError, ValueError), {})
    auth.verify_id_token = lambda token, app=None: contesta(auth)
    paquete = types.ModuleType('firebase_admin')
    paquete.auth, paquete.exceptions = auth, excepciones
    return {'firebase_admin': paquete, 'firebase_admin.auth': auth,
            'firebase_admin.exceptions': excepciones}


def ficha(**datos):
    """Una ficha cargada en la caja (sin pasar por ``clean``, como las viejas)."""
    datos.setdefault('apellido_cliente', 'López')
    datos.setdefault('nombre_cliente', 'Sofía')
    return Cliente.objects.create(**datos)


class Base(BaseApi):

    def setUp(self):
        super().setUp()
        firebase._aplicacion = None

    def tearDown(self):
        firebase._aplicacion = None
        super().tearDown()

    def pedir_cliente(self, metodo, ruta, datos=None, token='token-de-firebase'):
        encabezados = {'HTTP_AUTHORIZATION': f'Firebase {token}'} if token else {}
        return self.pedir(metodo, ruta, datos, **encabezados)


class ConCuenta(Base):
    """La cuenta de Firebase ya verificada (``cuenta_verificada`` de mentira)."""

    uid, correo = UID, CORREO

    def setUp(self):
        super().setUp()
        verificar = mock.patch.object(
            firebase, 'cuenta_verificada',
            side_effect=lambda token: firebase.Cuenta(self.uid, self.correo))
        verificar.start()
        self.addCleanup(verificar.stop)

    def vincular(self, **cambios):
        datos = {'nombre': 'Sofía', 'apellido': 'López', 'telefono': '3874123456',
                 'fecha_nacimiento': '1995-04-12'}
        datos.update(cambios)
        return self.pedir_cliente('post', '/clientes/vincular/', datos)

    def yo(self):
        return self.pedir_cliente('get', '/clientes/yo/')


# ===========================================================================
@override_settings(FIREBASE_CREDENCIAL='')
class SinCredencialTest(Base):

    def test_sin_la_cuenta_de_servicio_responde_503_sin_romper(self):
        for metodo, ruta in (('get', '/clientes/yo/'), ('patch', '/clientes/yo/'),
                             ('post', '/clientes/vincular/')):
            with self.subTest(metodo=metodo, ruta=ruta):
                respuesta = self.pedir_cliente(metodo, ruta, {})
                self.assertEqual(respuesta.status_code, 503)
                self.assertEqual(respuesta.json(), {'detail': firebase.NO_DISPONIBLE,
                                                    'codigo': 'CLIENTES_SIN_CONFIGURAR'})
        # El resto de la API sigue andando.
        self.assertEqual(self.pedir('get', '/catalogo/destacados/').status_code, 200)

    @override_settings(FIREBASE_CREDENCIAL='/no/existe/firebase.json')
    def test_con_la_variable_pero_sin_el_archivo_tambien_503(self):
        # El motivo queda en el log del servidor; el cliente recibe el 503.
        with self.assertLogs('api_movil.firebase', level='ERROR'):
            respuesta = self.pedir_cliente('get', '/clientes/yo/')
        self.assertEqual(respuesta.json()['codigo'], 'CLIENTES_SIN_CONFIGURAR')

    def test_sin_token_401(self):
        self.assertEqual(self.pedir_cliente('get', '/clientes/yo/', token=None).status_code, 401)
        respuesta = self.pedir('get', '/clientes/yo/', HTTP_AUTHORIZATION='Token abc')
        self.assertEqual(respuesta.status_code, 401)


# ===========================================================================
@override_settings(FIREBASE_CREDENCIAL='/run/secrets/firebase.json')
class VerificacionTest(Base):

    def cuenta(self, contesta):
        with mock.patch.dict(sys.modules, firebase_de_mentira(contesta)), \
                mock.patch.object(firebase, '_aplicacion_firebase', return_value=object()):
            return firebase.cuenta_verificada('token-de-firebase')

    def test_una_cuenta_con_el_correo_verificado(self):
        cuenta = self.cuenta(lambda auth: {'uid': UID, 'email': 'Sofia@Correo.TEST',
                                           'email_verified': True})
        self.assertEqual(cuenta, firebase.Cuenta(UID, CORREO))

    def test_un_correo_sin_verificar_no_vale(self):
        with self.assertRaises(firebase.CuentaRechazada) as rechazo:
            self.cuenta(lambda auth: {'uid': UID, 'email': CORREO, 'email_verified': False})
        self.assertEqual(rechazo.exception.estado, 403)

    def test_sin_uid_no_vale(self):
        with self.assertRaises(firebase.CuentaRechazada) as rechazo:
            self.cuenta(lambda auth: {'email': CORREO, 'email_verified': True})
        self.assertEqual(rechazo.exception.estado, 401)

    def test_un_token_invalido_o_vencido(self):
        def invalido(auth):
            raise auth.InvalidIdTokenError('firma')
        with self.assertRaises(firebase.CuentaRechazada) as rechazo:
            self.cuenta(invalido)
        self.assertEqual(rechazo.exception.estado, 401)

    def test_si_firebase_no_contesta_es_503(self):
        def sin_certificados(auth):
            raise auth.CertificateFetchError('sin red')
        with self.assertRaises(firebase.NoDisponible), \
                self.assertLogs('api_movil.firebase', level='ERROR'):
            self.cuenta(sin_certificados)


# ===========================================================================
@override_settings(FIREBASE_CREDENCIAL='/run/secrets/firebase.json')
class CuentaTest(ConCuenta):

    def test_sin_vincular_todavia(self):
        self.assertEqual(self.yo().status_code, 404)

    def test_el_alta_es_la_de_la_web_y_el_qr_es_el_de_la_caja(self):
        respuesta = self.vincular()
        self.assertEqual(respuesta.status_code, 201)
        cliente = Cliente.objects.get(email_cliente=CORREO)
        self.assertEqual(cliente.uid_firebase, UID)
        self.assertEqual(cliente.id_categoria.nombre_categoria, 'Bronze')   # la de ingreso
        self.assertTrue(cliente.qr_cliente.startswith('CAF-'))
        cuenta = respuesta.json()
        self.assertEqual(cuenta['qr'], cliente.qr_cliente)
        self.assertEqual(cuenta['nombre'], 'Sofía')
        self.assertEqual(cuenta['apellido'], 'López')
        self.assertEqual(cuenta['telefono'], '3874123456')
        self.assertEqual(cuenta['fecha_nacimiento'], '1995-04-12')
        self.assertFalse(cuenta['acepta_promociones'])
        self.assertEqual(Cliente.identificar(cuenta['qr']), cliente)          # lo lee la caja
        self.assertEqual((cuenta['categoria'], cuenta['proxima_categoria'],
                          cuenta['puntos_proxima_categoria']), ('Bronze', 'Silver', 1000))
        self.assertTrue(LogAuditoria.objects.filter(operacion_realizada='ALTA DE CLIENTE',
                                                    id_registro=cliente.pk).exists())
        # Después, la misma cuenta: no se da de alta dos veces.
        datos_yo = self.yo().json()
        self.assertEqual(datos_yo['qr'], cliente.qr_cliente)
        self.assertEqual(
            (datos_yo['nombre'], datos_yo['apellido'], datos_yo['telefono'],
             datos_yo['fecha_nacimiento'], datos_yo['acepta_promociones']),
            ('Sofía', 'López', '3874123456', '1995-04-12', False),
        )
        self.assertEqual(self.vincular().status_code, 200)
        self.assertEqual(Cliente.objects.filter(email_cliente=CORREO).count(), 1)

    def test_las_categorias_son_las_que_existan(self):
        """Hoy son cuatro (con Platinum): la app no depende de una lista fija."""
        self.vincular()
        categorias = list(CategoriaFidelizacion.objects.filter(borrado_logico=False)
                          .order_by('puntos_minimos'))
        cuenta = self.yo().json()
        self.assertEqual([(c['nombre'], c['puntos_minimos']) for c in cuenta['categorias']],
                         [(c.nombre_categoria, c.puntos_minimos) for c in categorias])
        self.assertIn('Platinum', [c['nombre'] for c in cuenta['categorias']])
        self.assertEqual(cuenta['categorias'][0]['desde'], 'Desde que creás tu cuenta')
        self.assertEqual(cuenta['categorias'][2]['desde'], 'Desde 5.000 puntos')

    def test_el_alta_pide_lo_mismo_que_la_web(self):
        sin_telefono = self.vincular(telefono='')
        self.assertEqual(sin_telefono.status_code, 400)
        self.assertIn('teléfono o el DNI', sin_telefono.json()['detail'])
        # Con el código, la app pide el teléfono o el DNI ahí mismo.
        self.assertEqual(sin_telefono.json()['codigo'], 'FALTA_TELEFONO_O_DNI')
        letras = self.vincular(telefono='abc')
        self.assertIn('telefono', letras.json()['errores'])
        self.assertNotIn('codigo', letras.json())
        self.assertFalse(Cliente.objects.exists())

    def test_el_alta_con_el_dni_en_lugar_del_telefono(self):
        respuesta = self.vincular(telefono='', dni='30.123.456')
        self.assertEqual(respuesta.status_code, 201)
        cliente = Cliente.objects.get(uid_firebase=UID)
        self.assertEqual((cliente.dni_cliente, cliente.telefono_cliente), ('30123456', None))

    def test_un_dni_que_ya_es_de_otro_cliente(self):
        ficha(apellido_cliente='Pérez', nombre_cliente='Ana', dni_cliente='30123456')
        respuesta = self.vincular(telefono='', dni='30123456')
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()['codigo'], 'DNI_DE_OTRO_CLIENTE')
        self.assertIn('Acercate a la caja', respuesta.json()['detail'])
        self.assertIn('dni', respuesta.json()['errores'])
        self.assertEqual(Cliente.objects.filter(uid_firebase=UID).count(), 0)

    def test_un_telefono_que_ya_es_de_otro_cliente(self):
        ficha(apellido_cliente='Pérez', nombre_cliente='Ana', telefono_cliente='3874123456')
        respuesta = self.vincular()
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()['codigo'], 'TELEFONO_DE_OTRO_CLIENTE')
        self.assertIn('Acercate a la caja', respuesta.json()['detail'])
        self.assertIn('telefono', respuesta.json()['errores'])
        # Ni el teléfono ni el DNI vinculan solos: no están verificados.
        self.assertEqual(Cliente.objects.filter(uid_firebase=UID).count(), 0)

    def test_los_puntos_y_los_movimientos(self):
        self.vincular()
        cliente = Cliente.objects.get(email_cliente=CORREO)
        MovimientoPuntos.objects.create(id_cliente=cliente, tipo_movimiento='AJUSTE', puntos=120)
        cuenta = self.yo().json()
        self.assertEqual((cuenta['puntos_disponibles'], cuenta['puntos_acumulados']), (120, 0))
        self.assertEqual([(m['concepto'], m['puntos']) for m in cuenta['movimientos']],
                         [('Ajuste del local', 120)])


# ===========================================================================
@override_settings(FIREBASE_CREDENCIAL='/run/secrets/firebase.json')
class VinculoTest(ConCuenta):

    def test_la_ficha_de_la_caja_se_vincula_sola_por_el_correo(self):
        """El cajero le carga el correo con el cliente presente; la app la toma."""
        formulario = ClienteForm({'apellido_cliente': 'López', 'nombre_cliente': 'Sofía',
                                  'telefono_cliente': '3874123456'})
        self.assertTrue(formulario.is_valid(), formulario.errors)
        de_la_caja = formulario.save()
        self.assertEqual(self.yo().status_code, 404)      # sin correo, no hay cómo saber
        edicion = ClienteForm({'apellido_cliente': 'López', 'nombre_cliente': 'Sofía',
                               'telefono_cliente': '3874123456', 'email_cliente': CORREO},
                              instance=de_la_caja)
        self.assertTrue(edicion.is_valid(), edicion.errors)   # el campo se edita en la web
        edicion.save()
        self.assertEqual(self.yo().json()['qr'], de_la_caja.qr_cliente)
        de_la_caja.refresh_from_db()
        self.assertEqual(de_la_caja.uid_firebase, UID)
        asiento = LogAuditoria.objects.get(operacion_realizada='VINCULACION DE CLIENTE')
        self.assertIsNone(asiento.registro_anterior['uid_firebase'])
        self.assertEqual(asiento.registro_nuevo['uid_firebase'], UID)

    def test_una_vez_vinculada_el_correo_deja_de_importar(self):
        cliente = ficha(email_cliente=CORREO, telefono_cliente='3871111111')
        self.yo()
        Cliente.objects.filter(pk=cliente.pk).update(email_cliente='otro@correo.test')
        self.correo = 'sofia.nueva@correo.test'     # cambió el correo de su cuenta
        self.assertEqual(self.yo().json()['qr'], cliente.qr_cliente)

    def test_con_el_correo_repetido_pide_el_dni(self):
        suya = ficha(email_cliente=CORREO, telefono_cliente='3871111111', dni_cliente='35111222')
        ficha(email_cliente=CORREO.upper(), telefono_cliente='3872222222', dni_cliente='36333444')
        respuesta = self.yo()
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(respuesta.json(), {
            'codigo': 'CORREO_REPETIDO',
            'detail': 'Hay más de una ficha de cliente con tu correo. Escribí tu DNI '
                      'para encontrar la tuya o acercate a la caja.'})
        vinculada = self.vincular(dni='35.111.222')
        self.assertEqual(vinculada.status_code, 200)
        self.assertEqual(vinculada.json()['qr'], suya.qr_cliente)
        suya.refresh_from_db()
        self.assertEqual(suya.uid_firebase, UID)
        self.assertEqual(self.yo().json()['qr'], suya.qr_cliente)     # ya por uid

    def test_un_dni_que_no_coincide(self):
        ficha(email_cliente=CORREO, telefono_cliente='3871111111', dni_cliente='35111222')
        ficha(email_cliente=CORREO, telefono_cliente='3872222222', dni_cliente='36333444')
        respuesta = self.vincular(dni='20999888')
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(respuesta.json()['codigo'], 'DNI_NO_COINCIDE')
        self.assertFalse(Cliente.objects.filter(uid_firebase=UID).exists())

    def test_los_intentos_con_dni_tienen_freno(self):
        ficha(email_cliente=CORREO, telefono_cliente='3871111111', dni_cliente='35111222')
        ficha(email_cliente=CORREO, telefono_cliente='3872222222', dni_cliente='36333444')
        for n in range(INTENTOS_CON_DNI):
            self.assertEqual(self.vincular(dni=f'2099988{n}').status_code, 409)
        frenado = self.vincular(dni='35111222')               # ni el correcto pasa
        self.assertEqual(frenado.status_code, 429)
        self.assertEqual(frenado.json()['codigo'], 'DEMASIADOS_INTENTOS')
        self.assertGreater(int(frenado['Retry-After']), 0)
        self.assertEqual(IntentoDeVinculacion.objects.filter(uid_firebase=UID).count(),
                         INTENTOS_CON_DNI)

    def test_una_ficha_de_otra_cuenta_no_se_toma(self):
        ficha(email_cliente=CORREO, telefono_cliente='3871111111', uid_firebase='otra-cuenta')
        respuesta = self.yo()
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(respuesta.json()['codigo'], 'FICHA_DE_OTRA_CUENTA')

    def test_una_ficha_dada_de_baja(self):
        ficha(email_cliente=CORREO, telefono_cliente='3871111111', uid_firebase=UID,
              borrado_logico=True)
        respuesta = self.yo()
        self.assertEqual(respuesta.status_code, 404)
        self.assertEqual(respuesta.json()['codigo'], 'FICHA_DADA_DE_BAJA')


# ===========================================================================
@override_settings(FIREBASE_CREDENCIAL='/run/secrets/firebase.json')
class EditarPerfilClienteTest(ConCuenta):
    """PATCH /api/movil/clientes/yo/: validación de dominio, teléfono único y auditoría."""

    def actualizar(self, datos, token='token-de-firebase'):
        return self.pedir_cliente('patch', '/clientes/yo/', datos, token=token)

    def test_sin_token_401_y_metodo_invalido_405(self):
        self.assertEqual(self.actualizar({}, token=None).status_code, 401)
        mal = self.pedir_cliente('delete', '/clientes/yo/')
        self.assertEqual(mal.status_code, 405)
        self.assertEqual(mal['Allow'], 'GET, PATCH')

    def test_sin_ficha_vinculada_responde_404(self):
        self.assertEqual(self.actualizar({'nombre': 'Sofía'}).status_code, 404)

    def test_actualiza_datos_deja_auditoria_y_devuelve_cuenta_completa(self):
        self.vincular()
        cliente = Cliente.objects.get(uid_firebase=UID)
        respuesta = self.actualizar({
            'nombre': '  Sofía   María ',
            'apellido': 'López-Gómez',
            'telefono': '+54 387 15 499-8877',
            'fecha_nacimiento': '1994-08-20',
            'acepta_promociones': True,
        })
        self.assertEqual(respuesta.status_code, 200)
        datos = respuesta.json()
        self.assertEqual(datos['qr'], cliente.qr_cliente)
        self.assertEqual(datos['nombre'], 'Sofía María')
        self.assertEqual(datos['apellido'], 'López-Gómez')
        self.assertEqual(datos['telefono'], '3874998877')
        self.assertEqual(datos['fecha_nacimiento'], '1994-08-20')
        self.assertTrue(datos['acepta_promociones'])
        self.assertIn('puntos_disponibles', datos)
        self.assertIn('movimientos', datos)

        cliente.refresh_from_db()
        self.assertEqual(
            (cliente.nombre_cliente, cliente.apellido_cliente,
             cliente.telefono_cliente, cliente.fecha_nacimiento.isoformat(),
             cliente.acepta_promociones),
            ('Sofía María', 'López-Gómez', '3874998877', '1994-08-20', True),
        )
        asiento = LogAuditoria.objects.get(
            operacion_realizada='MODIFICACION DE CLIENTE', id_registro=cliente.pk)
        self.assertEqual(asiento.registro_anterior['telefono_cliente'], '3874123456')
        self.assertFalse(asiento.registro_anterior['acepta_promociones'])
        self.assertEqual(asiento.registro_nuevo['telefono_cliente'], '3874998877')
        self.assertTrue(asiento.registro_nuevo['acepta_promociones'])

        # Limpiar fecha_nacimiento a null funciona y mandar lo mismo no duplica auditoría.
        limpieza = self.actualizar({'fecha_nacimiento': None})
        self.assertEqual(limpieza.status_code, 200)
        self.assertIsNone(limpieza.json()['fecha_nacimiento'])
        cantidad_auditorias = LogAuditoria.objects.filter(
            operacion_realizada='MODIFICACION DE CLIENTE').count()
        igual = self.actualizar({'nombre': 'Sofía María', 'telefono': '387 499-8877'})
        self.assertEqual(igual.status_code, 200)
        self.assertEqual(
            LogAuditoria.objects.filter(operacion_realizada='MODIFICACION DE CLIENTE').count(),
            cantidad_auditorias,
        )

    def test_telefono_de_otra_ficha_vigente_responde_400_con_codigo_y_error_exacto(self):
        self.vincular()
        ficha(apellido_cliente='Pérez', nombre_cliente='Ana', telefono_cliente='3875556677')
        respuesta = self.actualizar({'telefono': '(0387) 15 555-6677'})
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json(), {
            'codigo': 'TELEFONO_DE_OTRO_CLIENTE',
            'errores': {
                'telefono': ['Ese teléfono ya está registrado en otra ficha.'],
            },
        })
        self.assertEqual(Cliente.objects.get(uid_firebase=UID).telefono_cliente, '3874123456')

    def test_reutiliza_las_validaciones_de_dominio(self):
        self.vincular()
        respuesta = self.actualizar({
            'nombre': 'Sofía123',
            'apellido': 'X',
            'telefono': 'abc',
            'fecha_nacimiento': '2025-01-01',
            'acepta_promociones': 'si',
        })
        self.assertEqual(respuesta.status_code, 400)
        errores = respuesta.json()['errores']
        for campo in ('nombre', 'apellido', 'telefono', 'fecha_nacimiento', 'acepta_promociones'):
            self.assertIn(campo, errores)
        self.assertNotIn('codigo', respuesta.json())
