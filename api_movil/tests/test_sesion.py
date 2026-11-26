"""Entrar y salir desde el celular, y lo que vale (y lo que no) un token.

Cada clase cubre una amenaza o una regla y dice cuál es:

    IngresoTest     el mismo usuario y la misma clave que en la web, con los
                    mismos rechazos, y sin cerrar la sesión de la web
    BloqueoTest     probar claves desde el celular: el bloqueo es de la
                    cuenta, no del canal
    TokenTest       un token vencido, tocado, firmado por otro, o de alguien
                    que cambió la clave o fue dado de baja
    PermisosTest    401 sin token, 403 sin permiso (y queda en el registro),
                    y que la API no le abra la puerta a la web
"""
import time
from datetime import timedelta
from unittest import mock

from django.core import signing
from django.urls import reverse
from django.utils import timezone

from api_movil.autenticacion import (
    DURACION,
    PERMISOS_MOVIL,
    SAL,
    emitir_token,
    huella,
)
from api_movil.models import SesionMovil
from api_movil.tests.base import BaseApi
from nucleo.tests import fabrica as f
from seguridad.middleware import PREFIJO_API_MOVIL
from seguridad.models import IntentoAcceso, LogAuditoria, Sesion, Usuario
from seguridad.servicios import (
    ESCALONES_DE_BLOQUEO,
    OPERACION_ALERTA,
    OPERACION_INGRESO_MOVIL,
)

NO_COINCIDEN = 'El usuario o la contraseña no coinciden.'


class Base(BaseApi):

    def entrar(self, usuario, clave=f.CLAVE):
        return self.pedir('post', '/sesion/', {'usuario': usuario, 'clave': clave})


# ===========================================================================
class IngresoTest(Base):

    def test_entra_y_recibe_el_token_y_los_permisos_de_la_app(self):
        respuesta = self.entrar(self.operador.nombre_usuario)
        self.assertEqual(respuesta.status_code, 200)
        datos = respuesta.json()
        self.assertEqual(datos['usuario'], {
            'nombre': 'Cami', 'apellido': 'Prueba', 'perfil': 'Operador',
            'es_administrador': False, 'es_titular': False,
            'permisos': ['ver_productos', 'ver_stock', 'contar_stock',
                         'registrar_merma', 'registrar_produccion',
                         'imprimir_ofertas']})
        consulta = self.pedir('get', '/stock/productos/', token=datos['token'])
        self.assertEqual(consulta.status_code, 200)

    def test_entra_con_el_dni_o_el_usuario_como_en_la_web(self):
        self.assertEqual(self.entrar('40000001').status_code, 200)
        self.assertEqual(self.entrar('40.000.001').status_code, 200)
        self.assertEqual(
            self.entrar(self.operador.nombre_usuario.upper()).status_code, 200)

    def test_la_clave_se_recorta_como_en_la_web(self):
        """El formulario de la web recorta la clave: la que entra allá entra acá."""
        self.assertEqual(self.entrar('40000001', f' {f.CLAVE} ').status_code, 200)

    def test_la_administradora_recibe_todos_los_permisos_de_la_app(self):
        usuario = self.entrar('40000003').json()['usuario']
        self.assertEqual(usuario['permisos'], list(PERMISOS_MOVIL))
        self.assertTrue(usuario['es_administrador'])
        self.assertFalse(usuario['es_titular'])

    def test_el_titular_se_anuncia_como_tal(self):
        titular = Usuario.objects.get(es_titular=True)
        titular.set_clave(f.CLAVE)
        titular.save()
        usuario = self.entrar(titular.nombre_usuario).json()['usuario']
        self.assertTrue(usuario['es_titular'])
        self.assertTrue(usuario['es_administrador'])

    def test_clave_incorrecta(self):
        respuesta = self.entrar('40000001', 'clave-mala')
        self.assertEqual(respuesta.status_code, 401)
        self.assertEqual(respuesta.json(), {'detail': NO_COINCIDEN})
        self.assertTrue(IntentoAcceso.objects.filter(
            id_usuario=self.operador, motivo_rechazo='CLAVE_INCORRECTA').exists())

    def test_un_usuario_inexistente_recibe_el_mismo_mensaje(self):
        """Si el mensaje cambiara, el celular diría qué usuarios existen."""
        respuesta = self.entrar('nadie99', 'x')
        self.assertEqual(respuesta.status_code, 401)
        self.assertEqual(respuesta.json(), {'detail': NO_COINCIDEN})
        self.assertTrue(IntentoAcceso.objects.filter(
            nombre_usuario_ingresado='nadie99',
            motivo_rechazo='USUARIO_INEXISTENTE').exists())

    def test_con_clave_provisoria_primero_pasa_por_la_web(self):
        provisoria = self.operador.restablecer_clave()
        respuesta = self.entrar('40000001', provisoria)
        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(respuesta.json(), {'detail': (
            'Tenés que cambiar tu contraseña provisoria desde la web antes '
            'de entrar desde el celular.')})

    def test_una_clave_provisoria_vencida_no_entra(self):
        provisoria = self.operador.restablecer_clave()
        Usuario.objects.filter(pk=self.operador.pk).update(
            fecha_cambio_clave=timezone.now() - timedelta(hours=25))
        respuesta = self.entrar('40000001', provisoria)
        self.assertEqual(respuesta.status_code, 403)
        self.assertIn('venció', respuesta.json()['detail'])

    def test_un_usuario_dado_de_baja_no_entra(self):
        self.operador.activo = False
        self.operador.save()
        respuesta = self.entrar('40000001')
        self.assertEqual(respuesta.status_code, 403)
        self.assertIn('dado de baja', respuesta.json()['detail'])
        self.assertNotIn('token', respuesta.json())
        self.assertTrue(IntentoAcceso.objects.filter(
            id_usuario=self.operador, motivo_rechazo='USUARIO_INACTIVO').exists())

    def test_un_pedido_incompleto_o_roto_es_400_y_no_suma_intentos(self):
        for cuerpo in ({'usuario': '40000001'}, {'usuario': 4, 'clave': 'x'},
                       'esto no es json', [1, 2]):
            with self.subTest(cuerpo=cuerpo):
                respuesta = self.pedir('post', '/sesion/', cuerpo)
                self.assertEqual(respuesta.status_code, 400)
                self.assertIn('detail', respuesta.json())
        self.assertFalse(IntentoAcceso.objects.exists())

    def test_solo_se_entra_y_se_sale(self):
        respuesta = self.pedir('get', '/sesion/')
        self.assertEqual(respuesta.status_code, 405)
        self.assertEqual(respuesta['Allow'], 'POST, DELETE')

    def test_el_ingreso_queda_en_el_registro_de_actividad(self):
        self.entrar('40000001')
        asiento = LogAuditoria.objects.get(
            operacion_realizada=OPERACION_INGRESO_MOVIL)
        self.assertEqual(asiento.id_usuario, self.operador)
        self.assertEqual(asiento.id_perfil_efectivo.nombre, 'Operador')
        self.assertEqual(asiento.modulo_afectado, 'SEGURIDAD')
        self.assertIsNone(asiento.id_sesion)        # no hay sesión web
        sesion = SesionMovil.objects.get(id_usuario=self.operador)
        self.assertIn(f'app móvil (IP 127.0.0.1, sesión móvil N° {sesion.pk})',
                      asiento.detalle_operacion)
        # La foto de la sesión que se abrió: desde dónde, hasta cuándo, y
        # nunca el token.
        self.assertEqual(asiento.registro_nuevo['canal'], 'APP_MOVIL')
        self.assertEqual(asiento.registro_nuevo['id_sesion_movil'], sesion.pk)
        self.assertEqual(asiento.registro_nuevo['ip_origen'], '127.0.0.1')
        self.assertNotIn('token', asiento.registro_nuevo)

    def test_entrar_desde_el_celular_no_cierra_la_sesion_de_la_web(self):
        """La web admite una sesión por usuario (DD-01): el celular no la toca."""
        sesion_web = f.entrar(self.client, self.operador)
        self.assertEqual(self.entrar('40000001').status_code, 200)
        sesion_web.refresh_from_db()
        self.assertTrue(sesion_web.activa)
        self.assertEqual(Sesion.objects.filter(id_usuario=self.operador).count(), 1)

    def test_salir_cierra_la_sesion_y_el_token_deja_de_valer(self):
        token = self.entrar('40000001').json()['token']
        self.assertEqual(self.pedir('get', '/stock/productos/', token=token).status_code, 200)
        respuesta = self.pedir('delete', '/sesion/', token=token)
        self.assertEqual(respuesta.status_code, 204)
        self.assertEqual(respuesta.content, b'')
        self.assertEqual(self.pedir('get', '/stock/productos/', token=token).status_code, 401)
        sesion = SesionMovil.objects.get(id_usuario=self.operador)
        self.assertEqual(sesion.motivo_revocacion, 'SALIDA')
        self.assertIsNotNone(sesion.revocada_en)
        # Salir nunca falla, aunque el token ya no valga.
        self.assertEqual(self.pedir('delete', '/sesion/', token=token).status_code, 204)
        self.assertEqual(self.pedir('delete', '/sesion/').status_code, 204)


# ===========================================================================
class BloqueoTest(Base):

    UMBRAL = ESCALONES_DE_BLOQUEO[0][0]

    def fallar(self, veces):
        for _ in range(veces):
            self.entrar('40000001', 'clave-mala')

    def test_despues_del_umbral_ni_la_clave_correcta_entra(self):
        self.fallar(self.UMBRAL)
        respuesta = self.entrar('40000001')
        self.assertEqual(respuesta.status_code, 429)
        self.assertIn('bloqueado', respuesta.json()['detail'])
        self.assertGreater(int(respuesta['Retry-After']), 0)
        self.assertNotIn('token', respuesta.json())
        self.assertTrue(IntentoAcceso.objects.filter(
            id_usuario=self.operador, motivo_rechazo='CUENTA_BLOQUEADA').exists())

    def test_el_intento_que_activa_el_bloqueo_ya_lo_avisa(self):
        self.fallar(self.UMBRAL - 1)
        self.assertEqual(self.entrar('40000001', 'otra-mala').status_code, 429)

    def test_avisa_cuando_quedan_pocos_intentos(self):
        self.fallar(self.UMBRAL - 2)
        respuesta = self.entrar('40000001', 'otra-mala')
        self.assertEqual(respuesta.status_code, 401)
        self.assertIn('Te queda 1 intento', respuesta.json()['detail'])

    def test_lo_que_se_probo_en_la_web_frena_al_celular(self):
        for _ in range(self.UMBRAL):
            self.client.post(reverse('seguridad:login'),
                             {'identificador': '40000001', 'clave': 'clave-mala'})
        self.assertEqual(self.entrar('40000001').status_code, 429)

    def test_lo_que_se_probo_en_el_celular_frena_a_la_web(self):
        self.fallar(self.UMBRAL)
        self.client.post(reverse('seguridad:login'),
                         {'identificador': '40000001', 'clave': f.CLAVE})
        self.assertFalse(Sesion.objects.filter(id_usuario=self.operador).exists())

    def test_un_ingreso_correcto_desde_el_celular_pone_la_cuenta_en_cero(self):
        self.fallar(self.UMBRAL - 1)
        self.assertEqual(self.entrar('40000001').status_code, 200)
        self.fallar(self.UMBRAL - 1)
        self.assertEqual(self.entrar('40000001').status_code, 200)

    def test_al_llegar_al_umbral_queda_la_alerta_para_la_administradora(self):
        self.fallar(self.UMBRAL)
        self.assertTrue(LogAuditoria.objects.filter(
            operacion_realizada=OPERACION_ALERTA,
            id_registro=self.operador.pk).exists())


# ===========================================================================
class TokenTest(Base):

    def consultar(self, token=None, **encabezados):
        return self.pedir('get', '/stock/productos/', token=token, **encabezados)

    def emitido_hace(self, segundos):
        with mock.patch('django.core.signing.time.time',
                        return_value=time.time() - segundos):
            return emitir_token(self.operador)

    def test_sin_token(self):
        respuesta = self.consultar()
        self.assertEqual(respuesta.status_code, 401)
        self.assertEqual(respuesta.json(), {'detail': 'Tu sesión venció. Volvé a entrar.'})

    def test_un_token_vencido_no_vale(self):
        vencido = self.emitido_hace(DURACION.total_seconds() + 60)
        self.assertEqual(self.consultar(vencido).status_code, 401)

    def test_un_token_de_hace_once_horas_todavia_vale(self):
        self.assertEqual(self.consultar(self.emitido_hace(11 * 3600)).status_code, 200)

    def test_un_token_manipulado_no_vale(self):
        token = emitir_token(self.operador)
        otra_firma = token[:-1] + ('A' if token[-1] != 'A' else 'B')
        self.assertEqual(self.consultar(otra_firma).status_code, 401)
        # El contenido del token de la administradora con la firma del
        # operador: cambiar de quién es el token rompe la firma.
        del_admin = emitir_token(self.admin)
        cruzado = del_admin.split(':', 1)[0] + ':' + token.split(':', 1)[1]
        self.assertEqual(self.consultar(cruzado).status_code, 401)
        self.assertEqual(self.consultar('cualquier-cosa').status_code, 401)

    def test_un_token_firmado_por_otro_no_vale(self):
        datos = {'u': self.admin.pk, 'h': huella(self.admin)}
        self.assertEqual(self.consultar(signing.dumps(datos, salt='otra')).status_code, 401)
        self.assertEqual(self.consultar(signing.dumps(
            datos, key='otra-clave-secreta', salt=SAL)).status_code, 401)

    def test_otro_esquema_no_vale(self):
        token = emitir_token(self.operador)
        self.assertEqual(
            self.consultar(HTTP_AUTHORIZATION=f'Bearer {token}').status_code, 401)

    def test_cambiar_la_clave_corta_los_tokens(self):
        token = emitir_token(self.operador)
        self.operador.set_clave('Otra.Clave.Segura.2027')
        self.operador.save()
        self.assertEqual(self.consultar(token).status_code, 401)

    def test_restablecer_la_clave_corta_los_tokens(self):
        token = emitir_token(self.operador)
        self.operador.restablecer_clave()
        self.assertEqual(self.consultar(token).status_code, 401)

    def test_dar_de_baja_al_usuario_corta_los_tokens(self):
        token = emitir_token(self.operador)
        self.operador.activo = False
        self.operador.save()
        self.assertEqual(self.consultar(token).status_code, 401)

    def test_un_token_sin_sesion_movil_no_vale(self):
        """La firma sola no alcanza: el token tiene que ser de una sesión abierta."""
        datos = {'u': self.operador.pk, 'j': 'una-sesion-que-no-existe',
                 'h': huella(self.operador)}
        self.assertEqual(self.consultar(signing.dumps(datos, salt=SAL)).status_code, 401)


# ===========================================================================
class SesionMovilTest(Base):
    """Una sesión móvil por persona, que se cierra al salir o al entrar de nuevo."""

    def consultar(self, token):
        return self.pedir('get', '/stock/productos/', token=token)

    def test_entrar_de_nuevo_cierra_la_sesion_anterior(self):
        primero = self.entrar('40000001').json()['token']
        segundo = self.entrar('40000001').json()['token']
        self.assertEqual(self.consultar(primero).status_code, 401)
        self.assertEqual(self.consultar(segundo).status_code, 200)
        anterior, actual = SesionMovil.objects.filter(
            id_usuario=self.operador).order_by('pk')
        self.assertEqual(anterior.motivo_revocacion, 'NUEVO_INGRESO')
        self.assertIsNone(actual.revocada_en)

    def test_no_puede_haber_dos_sesiones_abiertas(self):
        """Lo asegura también la base (como uq_sesion_activa_por_usuario)."""
        from django.db import IntegrityError, transaction

        emitir_token(self.operador)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SesionMovil.objects.create(id_usuario=self.operador, jti='otro-jti',
                                       ip_origen='127.0.0.1')

    def test_una_sesion_de_mas_de_12_horas_no_vale(self):
        token = emitir_token(self.operador)
        SesionMovil.objects.filter(id_usuario=self.operador).update(
            creada=timezone.now() - DURACION - timedelta(minutes=1))
        self.assertEqual(self.consultar(token).status_code, 401)

    def test_el_ultimo_uso_se_anota_cada_cinco_minutos(self):
        token = emitir_token(self.operador)
        sesiones = SesionMovil.objects.filter(id_usuario=self.operador)
        hace_un_rato = timezone.now() - timedelta(minutes=2)
        sesiones.update(ultima_actividad=hace_un_rato)
        self.consultar(token)
        self.assertEqual(sesiones.get().ultima_actividad, hace_un_rato)   # no escribió
        hace_mas = timezone.now() - timedelta(minutes=6)
        sesiones.update(ultima_actividad=hace_mas)
        self.consultar(token)
        self.assertGreater(sesiones.get().ultima_actividad, hace_mas)      # ahora sí

    def test_cada_asiento_dice_desde_que_sesion_movil(self):
        token = self.entrar('40000003').json()['token']
        self.pedir('post', '/marcas/', {'nombre': 'Cabrales'}, token=token)
        sesion = SesionMovil.objects.get(id_usuario=self.admin, revocada_en__isnull=True)
        asiento = LogAuditoria.objects.get(operacion_realizada='ALTA DE MARCA')
        self.assertIn(f'sesión móvil N° {sesion.pk}', asiento.detalle_operacion)

    def test_cambiar_la_clave_sigue_cortando_todo(self):
        token = self.entrar('40000001').json()['token']
        self.operador.set_clave('Otra.Clave.Segura.2027')
        self.operador.save()
        self.assertEqual(self.consultar(token).status_code, 401)


# ===========================================================================
class PermisosTest(Base):

    def test_sin_permiso_es_403_y_queda_rechazado_en_el_registro(self):
        respuesta = self.pedir('get', '/marcas/', como=self.operador)
        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(respuesta.json(),
                         {'detail': 'Tu usuario no tiene permiso para hacer esto.'})
        rechazo = LogAuditoria.objects.get(resultado_operacion='RECHAZADA')
        self.assertEqual(rechazo.id_usuario, self.operador)
        self.assertIn('ver_marcas', rechazo.detalle_operacion)
        self.assertIn('/api/movil/marcas/', rechazo.detalle_operacion)

    def test_el_permiso_se_controla_en_cada_metodo(self):
        """La encargada da de alta marcas, pero la baja es de la dueña."""
        alta = self.pedir('post', '/marcas/', {'nombre': 'Cabrales'},
                          como=self.encargada)
        self.assertEqual(alta.status_code, 201)
        baja = self.pedir('delete', f'/marcas/{alta.json()["id"]}/',
                          como=self.encargada)
        self.assertEqual(baja.status_code, 403)

    def test_un_metodo_que_no_corresponde_es_405(self):
        respuesta = self.pedir('put', '/marcas/', {}, como=self.admin)
        self.assertEqual(respuesta.status_code, 405)
        self.assertEqual(respuesta['Allow'], 'GET, POST')
        respuesta = self.pedir('post', '/catalogo/destacados/', {})
        self.assertEqual(respuesta.status_code, 405)
        self.assertIn('detail', respuesta.json())

    def test_la_api_no_pasa_por_el_login_de_la_web_pero_la_web_si(self):
        self.assertEqual(self.client.get('/api/movil/catalogo/destacados/').status_code, 200)
        self.assertRedirects(self.client.get(reverse('stock:alertas')),
                             reverse('seguridad:login'),
                             fetch_redirect_response=False)
        self.assertTrue(reverse('api_movil:sesion').startswith(PREFIJO_API_MOVIL))


# ===========================================================================
class SaludTest(Base):
    """GET /api/movil/salud/: público, exento de token y sin consultas a la base."""

    def test_responde_ok_sin_token_y_sin_tocar_la_base(self):
        with self.assertNumQueries(0):
            respuesta = self.client.get('/api/movil/salud/')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json(), {'ok': True})
        self.assertIn('no-store', respuesta.get('Cache-Control', ''))

    def test_solo_acepta_get(self):
        respuesta = self.pedir('post', '/salud/', {})
        self.assertEqual(respuesta.status_code, 405)
        self.assertEqual(respuesta['Allow'], 'GET')
