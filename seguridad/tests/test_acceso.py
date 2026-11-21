"""Pruebas de la base de seguridad: sesiones, bloqueo, titular y perfiles.

Cada clase cubre una amenaza concreta y dice cuál es:

    SesionEnElServidorTest     la cookie sola no alcanza: la sesión se
                               valida, vence y se revoca en el servidor
    BloqueoProgresivoTest      probar claves a mano o con un script
    AlertaIntentosTest         un ataque que nadie mira: el bloqueo frena,
                               la alerta avisa a la Administradora
    TitularTest                que otro administrador se quede con el
                               sistema del dueño
    CambioDePerfilTest         dar permisos desde una compu desatendida
    PerfilTemporalTest         el reemplazo que se olvida de terminar
    CambioDeClaveTest          claves débiles y cambios sin la clave actual
    EncabezadosTest            /admin, clickjacking, sniffing
"""
import re
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from seguridad.models import (
    IntentoAcceso,
    LogAuditoria,
    Perfil,
    Permiso,
    Sesion,
    Usuario,
)
from seguridad.servicios import (
    ESCALONES_DE_BLOQUEO,
    INTENTOS_POR_IP,
    OPERACION_ALERTA,
    OPERACION_REVISION,
    alertas_sin_revisar,
)

CLAVE = 'Clave.Segura.2026'


def crear(dni, apellido, nombre, perfil, clave=CLAVE, debe_cambiar=False):
    usuario = Usuario(dni_usuario=dni, apellido=apellido, nombre=nombre,
                      correo=f'{dni}@cafeto.test', id_perfil=perfil)
    usuario.set_clave(clave)
    usuario.debe_cambiar_clave = debe_cambiar
    usuario.save()
    return usuario


class Base(TestCase):

    def setUp(self):
        self.p_admin = Perfil.objects.get(nombre='Administrador')
        self.p_operador = Perfil.objects.get(nombre='Operador')
        self.p_encargado = Perfil.objects.get(nombre='Encargado')
        self.p_operador.permisos.add(Permiso.objects.get(codigo='ver_rubros'))
        self.p_encargado.permisos.add(
            Permiso.objects.get(codigo='ver_rubros'),
            Permiso.objects.get(codigo='ver_marcas'))
        self.titular = Usuario.objects.get(dni_usuario='30000000')
        self.titular.set_clave(CLAVE)
        self.titular.save()
        self.ana = crear('31000001', 'Admin', 'Ana', self.p_admin)
        self.beto = crear('40000002', 'Caja', 'Beto', self.p_operador)

    def entrar(self, identificador, clave=CLAVE, cliente=None):
        cliente = cliente or self.client
        return cliente.post(reverse('seguridad:login'),
                            {'identificador': identificador, 'clave': clave})

    def mensajes(self, respuesta):
        return ' '.join(m.message for m in respuesta.context['messages'])


# ===========================================================================
class SesionEnElServidorTest(Base):

    def test_el_ingreso_queda_en_la_auditoria(self):
        self.entrar('40000002')
        asiento = LogAuditoria.objects.get(
            operacion_realizada='INICIO DE SESION')
        self.assertEqual(asiento.id_usuario, self.beto)
        self.assertEqual(asiento.id_sesion.id_usuario, self.beto)
        self.assertEqual(asiento.modulo_afectado, 'SEGURIDAD')

    def test_el_ingreso_dice_que_hacer_si_se_olvido_la_clave(self):
        """Mismo texto para cualquiera: no revela si un usuario existe."""
        html = self.client.get(reverse('seguridad:login')).content.decode()
        self.assertIn('¿Te olvidaste la clave?', html)
        self.assertIn(reverse('seguridad:olvide_clave'), html)
        pagina = self.client.get(reverse('seguridad:olvide_clave')).content.decode()
        self.assertNotIn('recuperar_titular', pagina)
        self.assertIn('quien administra el servidor', pagina)
        con_error = self.client.post(reverse('seguridad:login'),
                                     {'identificador': 'nadie99', 'clave': 'x'}).content.decode()
        self.assertIn('¿Te olvidaste la clave?', con_error)

    def test_el_login_cambia_la_llave_de_la_sesion(self):
        """Fijación de sesión: la llave de antes del login no sirve después."""
        self.client.get(reverse('seguridad:login'))
        sesion = self.client.session
        sesion['marca'] = 'x'
        sesion.save()
        antes = sesion.session_key
        self.entrar('40000002')
        self.assertNotEqual(self.client.session.session_key, antes)

    def test_entrar_desde_otro_equipo_saca_al_primero(self):
        from django.test import Client

        self.entrar('40000002')
        otro = Client()
        self.entrar('40000002', cliente=otro)
        respuesta = self.client.get(reverse('mi_perfil'), follow=True)
        self.assertEqual(respuesta.request['PATH_INFO'],
                         reverse('seguridad:login'))
        self.assertIn('otro equipo', self.mensajes(respuesta))
        self.assertEqual(otro.get(reverse('mi_perfil')).status_code, 200)

    def test_un_token_alterado_no_pasa(self):
        self.entrar('40000002')
        sesion = self.client.session
        sesion['token_sesion'] = 'inventado'
        sesion.save()
        respuesta = self.client.get(reverse('mi_perfil'))
        self.assertRedirects(respuesta, reverse('seguridad:login'))

    def test_la_sesion_vence(self):
        self.entrar('40000002')
        Sesion.objects.filter(id_usuario=self.beto, activa=True).update(
            fecha_inicio=timezone.now() - timedelta(hours=13),
            fecha_ultimo_acceso=timezone.now() - timedelta(minutes=1),
            fecha_expiracion=timezone.now() - timedelta(hours=1))
        respuesta = self.client.get(reverse('mi_perfil'), follow=True)
        self.assertIn('venció', self.mensajes(respuesta))
        cerrada = Sesion.objects.filter(id_usuario=self.beto).latest(
            'fecha_inicio')
        self.assertFalse(cerrada.activa)
        self.assertEqual(cerrada.motivo_cierre, 'EXPIRACION')

    @override_settings(SESION_INACTIVIDAD_MINUTOS=30)
    def test_la_sesion_se_cierra_por_inactividad(self):
        self.entrar('40000002')
        hace_una_hora = timezone.now() - timedelta(hours=1)
        Sesion.objects.filter(id_usuario=self.beto, activa=True).update(
            fecha_inicio=hace_una_hora, fecha_ultimo_acceso=hace_una_hora)
        respuesta = self.client.get(reverse('mi_perfil'), follow=True)
        self.assertIn('inactividad', self.mensajes(respuesta))
        self.assertEqual(
            Sesion.objects.filter(id_usuario=self.beto).latest(
                'fecha_inicio').motivo_cierre, 'INACTIVIDAD')

    def test_el_uso_mantiene_viva_la_sesion(self):
        self.entrar('40000002')
        hace_dos_minutos = timezone.now() - timedelta(minutes=2)
        Sesion.objects.filter(id_usuario=self.beto, activa=True).update(
            fecha_inicio=hace_dos_minutos,
            fecha_ultimo_acceso=hace_dos_minutos)
        self.client.get(reverse('mi_perfil'))
        sesion = Sesion.objects.get(id_usuario=self.beto, activa=True)
        self.assertGreater(sesion.fecha_ultimo_acceso, hace_dos_minutos)

    def test_un_pedido_ajax_sin_sesion_recibe_401_en_json(self):
        respuesta = self.client.get(
            reverse('stock:buscar_productos'),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(respuesta.status_code, 401)
        self.assertIn('error', respuesta.json())

    def test_salir_es_por_post(self):
        self.entrar('40000002')
        self.client.get(reverse('seguridad:logout'))
        self.assertTrue(Sesion.objects.filter(
            id_usuario=self.beto, activa=True).exists())
        self.client.post(reverse('seguridad:logout'))
        self.assertFalse(Sesion.objects.filter(
            id_usuario=self.beto, activa=True).exists())

    def test_dar_de_baja_saca_al_usuario_en_su_proximo_clic(self):
        self.entrar('40000002')
        self.beto.dar_de_baja()
        respuesta = self.client.get(reverse('mi_perfil'), follow=True)
        self.assertEqual(respuesta.request['PATH_INFO'],
                         reverse('seguridad:login'))


# ===========================================================================
class BloqueoProgresivoTest(Base):

    UMBRAL = ESCALONES_DE_BLOQUEO[0][0]

    def fallar(self, identificador, veces):
        for _ in range(veces):
            self.entrar(identificador, 'clave-mala')

    def test_el_intento_que_activa_el_bloqueo_ya_lo_avisa(self):
        """Prueba en el navegador: el 5.º intento bloqueaba, pero el aviso
        recién salía en el 6.º."""
        self.fallar('40000002', self.UMBRAL - 1)
        respuesta = self.entrar('40000002', 'clave-mala')
        self.assertIn('queda bloqueado', self.mensajes(respuesta))

    def test_despues_del_umbral_ni_la_clave_correcta_entra(self):
        self.fallar('40000002', self.UMBRAL)
        respuesta = self.client.post(
            reverse('seguridad:login'),
            {'identificador': '40000002', 'clave': CLAVE}, follow=True)
        self.assertIn('bloqueado', self.mensajes(respuesta))
        self.assertFalse(Sesion.objects.filter(id_usuario=self.beto).exists())
        self.assertTrue(IntentoAcceso.objects.filter(
            id_usuario=self.beto, motivo_rechazo='CUENTA_BLOQUEADA').exists())

    def test_avisa_cuando_quedan_pocos_intentos(self):
        self.fallar('40000002', self.UMBRAL - 2)
        respuesta = self.client.post(
            reverse('seguridad:login'),
            {'identificador': '40000002', 'clave': 'otra-mala'}, follow=True)
        self.assertIn('Te queda 1 intento', self.mensajes(respuesta))

    def test_un_dni_inexistente_se_bloquea_igual(self):
        """Si sólo se bloquearan los que existen, el bloqueo diría cuáles
        DNI están cargados."""
        self.fallar('99999999', self.UMBRAL)
        respuesta = self.client.post(
            reverse('seguridad:login'),
            {'identificador': '99999999', 'clave': 'x'}, follow=True)
        self.assertIn('bloqueado', self.mensajes(respuesta))

    def test_el_bloqueo_vence_solo(self):
        hace_un_rato = timezone.now() - ESCALONES_DE_BLOQUEO[0][1] - \
            timedelta(seconds=5)
        for _ in range(self.UMBRAL):
            IntentoAcceso.objects.create(
                id_usuario=self.beto, nombre_usuario_ingresado='40000002',
                ip_origen='127.0.0.1', motivo_rechazo='CLAVE_INCORRECTA',
                fecha_hora=hace_un_rato)
        self.entrar('40000002')
        self.assertTrue(Sesion.objects.filter(
            id_usuario=self.beto, activa=True).exists())

    def test_un_ingreso_correcto_pone_la_cuenta_en_cero(self):
        self.fallar('40000002', self.UMBRAL - 1)
        self.entrar('40000002')
        self.client.logout()
        self.fallar('40000002', self.UMBRAL - 1)
        self.entrar('40000002')
        self.assertTrue(Sesion.objects.filter(
            id_usuario=self.beto, activa=True).exists())

    def test_restablecer_la_clave_desbloquea(self):
        self.fallar('40000002', self.UMBRAL)
        provisoria = self.beto.restablecer_clave()
        respuesta = self.entrar('40000002', provisoria)
        self.assertRedirects(respuesta, reverse('seguridad:cambiar_clave'))

    def test_muchos_usuarios_desde_la_misma_ip_frenan_a_esa_ip(self):
        for n in range(INTENTOS_POR_IP):
            IntentoAcceso.objects.create(
                nombre_usuario_ingresado=f'1000{n:04d}', ip_origen='127.0.0.1',
                motivo_rechazo='USUARIO_INEXISTENTE')
        respuesta = self.client.post(
            reverse('seguridad:login'),
            {'identificador': '40000002', 'clave': CLAVE}, follow=True)
        self.assertIn('bloqueado', self.mensajes(respuesta))

    def test_el_libro_de_intentos_no_se_puede_borrar(self):
        self.fallar('40000002', 1)
        with self.assertRaises(Exception):
            IntentoAcceso.objects.all().delete()


# ===========================================================================
class AlertaIntentosTest(Base):

    UMBRAL = ESCALONES_DE_BLOQUEO[0][0]

    def fallar(self, identificador, veces):
        for _ in range(veces):
            self.entrar(identificador, 'clave-mala')

    def alertas(self):
        return LogAuditoria.objects.filter(operacion_realizada=OPERACION_ALERTA)

    def test_al_llegar_al_umbral_queda_una_alerta_sobre_la_cuenta(self):
        self.fallar('40000002', self.UMBRAL - 1)
        self.assertFalse(self.alertas().exists())
        self.fallar('40000002', 1)
        alerta = self.alertas().get()
        self.assertEqual(alerta.tabla_afectada, 'usuarios')
        self.assertEqual(alerta.id_registro, self.beto.pk)
        self.assertIn(self.beto.nombre_usuario, alerta.detalle_operacion)
        self.assertIsNone(alerta.id_usuario)    # la alerta no la "hace" nadie

    def test_una_alerta_por_escalon_y_no_por_intento(self):
        """Los rebotes durante el bloqueo no cuentan: no repiten la alerta."""
        self.fallar('40000002', self.UMBRAL + 3)
        self.assertEqual(self.alertas().count(), 1)

    def test_un_usuario_inexistente_tambien_alerta(self):
        self.fallar('99999999', self.UMBRAL)
        alerta = self.alertas().get()
        self.assertIsNone(alerta.id_registro)
        self.assertIn('inexistente', alerta.detalle_operacion)

    def test_el_freno_por_ip_alerta(self):
        for n in range(INTENTOS_POR_IP - 1):
            IntentoAcceso.objects.create(
                nombre_usuario_ingresado=f'1000{n:04d}', ip_origen='127.0.0.1',
                motivo_rechazo='USUARIO_INEXISTENTE')
        self.entrar('20000000', 'x')
        self.assertTrue(self.alertas().filter(
            detalle_operacion__contains='desde la IP 127.0.0.1 en').exists())

    def test_la_administradora_la_ve_en_el_inicio_y_en_el_registro(self):
        self.fallar('40000002', self.UMBRAL)
        self.entrar('31000001')
        inicio = self.client.get(reverse('home'), follow=True)
        self.assertContains(inicio, 'alerta(s) de seguridad')
        registro = self.client.get(reverse('seguridad:actividad'))
        self.assertContains(registro, 'sin revisar')

    def test_revisar_agrega_un_asiento_y_las_saca_de_pendientes(self):
        self.fallar('40000002', self.UMBRAL)
        self.entrar('31000001')
        self.client.post(reverse('seguridad:revisar_alertas'))
        self.assertTrue(LogAuditoria.objects.filter(
            operacion_realizada=OPERACION_REVISION, id_usuario=self.ana).exists())
        self.assertFalse(alertas_sin_revisar(self.ana).exists())
        # la alerta sigue en el libro: revisar no borra ni marca nada
        self.assertTrue(self.alertas().exists())
        # y otra persona con el permiso la sigue viendo pendiente
        self.assertTrue(alertas_sin_revisar(self.titular).exists())

    def test_revisar_es_por_post(self):
        self.entrar('31000001')
        respuesta = self.client.get(reverse('seguridad:revisar_alertas'))
        self.assertEqual(respuesta.status_code, 405)

    def test_un_operador_no_ve_las_alertas(self):
        self.fallar('31000001', self.UMBRAL)
        self.entrar('40000002')
        inicio = self.client.get(reverse('home'), follow=True)
        self.assertNotContains(inicio, 'alerta(s) de seguridad')


# ===========================================================================
class TitularTest(Base):

    def test_la_semilla_deja_un_titular(self):
        self.assertTrue(self.titular.es_titular)
        self.assertEqual(Usuario.objects.filter(es_titular=True).count(), 1)

    def test_la_base_no_admite_dos_titulares(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Usuario.objects.filter(pk=self.ana.pk).update(es_titular=True)

    def test_el_titular_no_se_da_de_baja_ni_se_le_restablece_la_clave(self):
        with self.assertRaises(ValidationError):
            self.titular.dar_de_baja()
        with self.assertRaises(ValidationError):
            self.titular.restablecer_clave()

    def test_otro_admin_no_le_restablece_la_clave_por_url(self):
        self.entrar('31000001')
        respuesta = self.client.post(
            reverse('seguridad:restablecer_clave', args=[self.titular.pk]),
            follow=True)
        self.assertIn('recuperar_titular', self.mensajes(respuesta))
        self.assertTrue(Usuario.objects.get(pk=self.titular.pk)
                        .verificar_clave(CLAVE))

    def test_el_titular_conserva_el_perfil_administrador(self):
        with self.assertRaises(ValidationError):
            self.titular.cambiar_perfil(self.p_encargado)

    def test_transferir_pide_la_clave_y_solo_la_puede_hacer_el_titular(self):
        self.entrar('31000001')
        self.client.post(reverse('seguridad:transferir_titularidad'),
                         {'nuevo_titular': self.ana.pk,
                          'clave_operador': CLAVE})
        self.assertTrue(Usuario.objects.get(pk=self.titular.pk).es_titular)

        self.client.logout()
        self.entrar('30000000')
        self.client.post(reverse('seguridad:transferir_titularidad'),
                         {'nuevo_titular': self.ana.pk,
                          'clave_operador': 'mala'})
        self.assertTrue(Usuario.objects.get(pk=self.titular.pk).es_titular)

        self.client.post(reverse('seguridad:transferir_titularidad'),
                         {'nuevo_titular': self.ana.pk,
                          'clave_operador': CLAVE})
        self.assertFalse(Usuario.objects.get(pk=self.titular.pk).es_titular)
        self.assertTrue(Usuario.objects.get(pk=self.ana.pk).es_titular)
        self.assertTrue(LogAuditoria.objects.filter(
            operacion_realizada__startswith='TRANSFERENCIA').exists())

    def test_despues_de_transferir_el_anterior_ya_se_puede_dar_de_baja(self):
        self.titular.transferir_titularidad(self.ana)
        self.titular.refresh_from_db()
        self.titular.dar_de_baja()
        self.assertFalse(self.titular.activo)

    def test_no_se_transfiere_a_quien_no_es_administrador(self):
        with self.assertRaises(ValidationError):
            self.titular.transferir_titularidad(self.beto)

    def test_el_comando_de_recuperacion(self):
        self.entrar('30000000')
        salida = StringIO()
        call_command('recuperar_titular', stdout=salida)
        clave = salida.getvalue().split('Clave provisoria: ')[1].split()[0]
        # La sesión que estaba abierta se cerró.
        self.assertFalse(Sesion.objects.filter(
            id_usuario=self.titular, activa=True).exists())
        self.client.logout()
        respuesta = self.entrar('30000000', clave)
        self.assertRedirects(respuesta, reverse('seguridad:cambiar_clave'))
        asiento = LogAuditoria.objects.filter(
            operacion_realizada__startswith='RECUPERACION').get()
        self.assertIsNone(asiento.id_sesion)
        self.assertEqual(asiento.id_registro, self.titular.pk)


# ===========================================================================
class CambioDePerfilTest(Base):

    def setUp(self):
        super().setUp()
        self.entrar('30000000')

    def cambiar(self, perfil, clave=CLAVE, usuario=None):
        usuario = usuario or self.beto
        return self.client.post(
            reverse('seguridad:cambiar_perfil', args=[usuario.pk]),
            {'id_perfil': perfil.pk, 'clave_operador': clave}, follow=True)

    def test_con_la_clave_correcta_cambia_y_se_audita(self):
        self.cambiar(self.p_encargado)
        self.beto.refresh_from_db()
        self.assertEqual(self.beto.id_perfil, self.p_encargado)
        asiento = LogAuditoria.objects.filter(
            operacion_realizada='CAMBIO DE PERFIL DE USUARIO').get()
        self.assertIn('Operador', asiento.detalle_operacion)
        self.assertIn('Encargado', asiento.detalle_operacion)

    def test_sin_la_clave_no_cambia(self):
        respuesta = self.cambiar(self.p_admin, clave='otra')
        self.beto.refresh_from_db()
        self.assertEqual(self.beto.id_perfil, self.p_operador)
        self.assertContains(respuesta, 'La clave no es correcta')

    def test_un_perfil_dado_de_baja_no_se_asigna(self):
        Perfil.objects.filter(pk=self.p_encargado.pk).update(
            borrado_logico=True)
        self.cambiar(self.p_encargado)
        self.beto.refresh_from_db()
        self.assertEqual(self.beto.id_perfil, self.p_operador)

    def test_alta_de_un_administrador_pide_la_clave(self):
        datos = {'dni_usuario': '41000003', 'apellido': 'Nuevo',
                 'nombre': 'Admin', 'correo': 'n@cafeto.test',
                 'id_perfil': self.p_admin.pk}
        self.client.post(reverse('seguridad:crear'), datos)
        self.assertFalse(Usuario.objects.filter(dni_usuario='41000003').exists())
        self.client.post(reverse('seguridad:crear'),
                         {**datos, 'clave_operador': CLAVE})
        self.assertTrue(Usuario.objects.filter(dni_usuario='41000003').exists())

    def test_alta_de_un_operador_no_la_pide(self):
        self.client.post(reverse('seguridad:crear'), {
            'dni_usuario': '41000004', 'apellido': 'Nuevo',
            'nombre': 'Cajero', 'correo': 'c@cafeto.test',
            'id_perfil': self.p_operador.pk})
        self.assertTrue(Usuario.objects.filter(dni_usuario='41000004').exists())

    def test_la_clave_inicial_no_es_el_dni_y_llega_un_enlace(self):
        """Con el DNI se entra: si también fuera la clave, cualquiera que lo
        conociera estrenaba la cuenta antes que su dueño. Ahora la persona
        recibe un enlace y elige su clave; nadie más la conoce."""
        respuesta = self.client.post(reverse('seguridad:crear'), {
            'dni_usuario': '41000005', 'apellido': 'Nueva',
            'nombre': 'Cajera', 'correo': 'cj@cafeto.test',
            'id_perfil': self.p_operador.pk})
        nueva = Usuario.objects.get(dni_usuario='41000005')
        self.assertFalse(nueva.verificar_clave('41000005'))
        self.assertRedirects(respuesta, reverse('seguridad:lista'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['cj@cafeto.test'])
        self.assertIn('/elegir-clave/activar/', mail.outbox[0].body)


# ===========================================================================
class ClaveProvisoriaTest(Base):

    def test_la_provisoria_vence_a_las_24_horas(self):
        provisoria = self.beto.restablecer_clave()
        Usuario.objects.filter(pk=self.beto.pk).update(
            fecha_cambio_clave=timezone.now() - timedelta(hours=25))
        respuesta = self.entrar('40000002', provisoria)
        self.assertEqual(respuesta.status_code, 200)        # sigue en el ingreso
        self.assertContains(respuesta, 'clave provisoria venció')
        self.assertFalse(Sesion.objects.filter(id_usuario=self.beto, activa=True).exists())
        # No es un intento fallido: no suma al bloqueo.
        self.assertFalse(IntentoAcceso.objects.filter(id_usuario=self.beto).exists())

    def test_antes_de_vencer_entra_y_la_cambia(self):
        provisoria = self.beto.restablecer_clave()
        Usuario.objects.filter(pk=self.beto.pk).update(
            fecha_cambio_clave=timezone.now() - timedelta(hours=23))
        self.assertRedirects(self.entrar('40000002', provisoria),
                             reverse('seguridad:cambiar_clave'))

    def test_una_clave_propia_no_vence(self):
        self.beto.set_clave('Otra-clave-larga-1')
        self.beto.save()
        Usuario.objects.filter(pk=self.beto.pk).update(
            fecha_cambio_clave=timezone.now() - timedelta(days=400))
        self.beto.refresh_from_db()
        self.assertFalse(self.beto.clave_provisoria_vencida)

    def test_la_del_titular_no_vence(self):
        self.titular.debe_cambiar_clave = True
        self.titular.fecha_cambio_clave = timezone.now() - timedelta(days=30)
        self.assertFalse(self.titular.clave_provisoria_vencida)

    def test_sin_correo_que_salga_la_provisoria_va_en_persona(self):
        """El respaldo: sin internet en el local el correo no sale, y la
        clave se muestra una vez para darla en persona."""
        self.entrar('31000001')
        with mock.patch('seguridad.recuperacion.send_mail', side_effect=OSError):
            respuesta = self.client.post(
                reverse('seguridad:restablecer_clave', args=[self.beto.pk]))
        self.assertTemplateUsed(respuesta, 'seguridad/clave_provisoria.html')
        self.assertEqual(respuesta['Cache-Control'], 'no-store')
        self.assertContains(respuesta, 'No se pudo mandar el correo')
        provisoria = ''.join(respuesta.context['grupos'])
        self.beto.refresh_from_db()
        self.assertTrue(self.beto.verificar_clave(provisoria))
        siguiente = self.client.get(reverse('seguridad:lista')).content.decode()
        self.assertNotIn(provisoria, siguiente)


# ===========================================================================
class RecuperacionPorCorreoTest(Base):
    """El enlace del correo: pedirlo, usarlo una vez y que venza."""

    def pedir(self, identificador):
        return self.client.post(reverse('seguridad:olvide_clave'),
                                {'identificador': identificador})

    def enlace(self):
        return re.search(r'http://testserver(/elegir-clave/\S+/)', mail.outbox[-1].body).group(1)

    def elegir(self, url, clave='Una-clave-nueva-77'):
        return self.client.post(url, {'clave_nueva': clave, 'clave_repetir': clave})

    def test_pide_el_enlace_y_elige_la_clave(self):
        respuesta = self.pedir('40000002')
        self.assertContains(respuesta, 'Revisá tu correo')
        self.assertEqual(mail.outbox[0].to, ['40000002@cafeto.test'])
        url = self.enlace()
        self.assertContains(self.client.get(url), 'Elegí tu clave nueva')
        self.assertRedirects(self.elegir(url), reverse('seguridad:login'))
        self.beto.refresh_from_db()
        self.assertTrue(self.beto.verificar_clave('Una-clave-nueva-77'))
        self.assertTrue(LogAuditoria.objects.filter(
            operacion_realizada='RECUPERACION DE CLAVE POR CORREO').exists())

    def test_el_enlace_sirve_una_sola_vez(self):
        self.pedir('cajab')
        url = self.enlace()
        self.elegir(url)
        respuesta = self.elegir(url, 'Otra-clave-mas-88')
        self.assertEqual(respuesta.status_code, 400)
        self.assertContains(respuesta, 'ya no sirve', status_code=400)
        self.beto.refresh_from_db()
        self.assertFalse(self.beto.verificar_clave('Otra-clave-mas-88'))

    def test_el_enlace_vence(self):
        self.pedir('40000002')
        url = self.enlace()
        with mock.patch.dict('seguridad.recuperacion.VIGENCIA',
                             {'recuperar': timedelta(seconds=-1)}):
            self.assertEqual(self.client.get(url).status_code, 400)

    def test_un_enlace_tocado_o_de_otro_proposito_no_sirve(self):
        self.pedir('40000002')
        url = self.enlace()
        self.assertEqual(self.client.get(url[:-3] + 'xx/').status_code, 400)
        self.assertEqual(self.client.get(url.replace('/recuperar/', '/activar/')).status_code, 400)

    def test_la_respuesta_no_dice_si_la_cuenta_existe(self):
        existe = self.pedir('40000002').content.decode()
        no_existe = self.pedir('99999999').content.decode()
        self.assertEqual(len(mail.outbox), 1)
        quitar = lambda html: re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', '', html)
        self.assertEqual(quitar(existe), quitar(no_existe))

    def test_no_se_puede_inundar_la_casilla_de_otro(self):
        for _ in range(6):
            self.pedir('40000002')
        self.assertEqual(len(mail.outbox), 3)

    def test_el_titular_no_recupera_por_correo(self):
        self.pedir('30000000')
        self.assertEqual(len(mail.outbox), 0)

    def test_elegir_la_clave_echa_a_quien_tenia_la_cuenta_abierta(self):
        otro = Client()
        self.entrar('40000002', cliente=otro)
        self.assertTrue(Sesion.objects.filter(id_usuario=self.beto, activa=True).exists())
        self.pedir('40000002')
        self.elegir(self.enlace())
        self.assertFalse(Sesion.objects.filter(id_usuario=self.beto, activa=True).exists())

    def test_el_enlace_en_un_navegador_con_otra_sesion_abierta(self):
        """La administradora tiene su sesión abierta y en el mismo navegador
        Beto abre su enlace: antes de ver el formulario, la sesión de ella se
        cierra (y queda dicho por qué), y el cambio lo firma Beto."""
        self.entrar('31000001')
        admin = Usuario.objects.get(dni_usuario='31000001')
        abierta = Sesion.objects.get(id_usuario=admin, activa=True)
        self.pedir('40000002')
        url = self.enlace()

        # Un enlace tocado no toca la sesión de nadie.
        self.assertEqual(self.client.get(url[:-3] + 'xx/').status_code, 400)
        abierta.refresh_from_db()
        self.assertTrue(abierta.activa)

        # El enlace válido: la sesión de ella se cierra antes de mostrar el formulario.
        self.assertContains(self.client.get(url), 'Elegí tu clave nueva')
        abierta.refresh_from_db()
        self.assertFalse(abierta.activa)
        self.assertEqual(abierta.motivo_cierre, 'REVOCACION')
        self.assertNotIn('usuario_id', self.client.session)
        cierre = LogAuditoria.objects.get(operacion_realizada='CIERRE DE SESION', id_usuario=admin)
        self.assertIn(self.beto.nombre_usuario, cierre.detalle_operacion)

        # Si ella vuelve a entrar en otra pestaña antes de guardar, se cierra otra vez.
        self.entrar('31000001')
        otra_vez = Sesion.objects.get(id_usuario=admin, activa=True)
        self.assertRedirects(self.elegir(url), reverse('seguridad:login'))
        otra_vez.refresh_from_db()
        self.assertFalse(otra_vez.activa)
        registro = LogAuditoria.objects.get(operacion_realizada='RECUPERACION DE CLAVE POR CORREO')
        self.assertEqual(registro.id_usuario, self.beto)
        # La pantalla de ingreso queda limpia: no entra como la administradora.
        self.assertNotContains(self.client.get(reverse('home'), follow=True), admin.apellido)

    def test_la_clave_nueva_cierra_las_sesiones_web_y_del_celular(self):
        from django.test import RequestFactory

        from api_movil.autenticacion import emitir_token, sesion_del_token
        from api_movil.models import SesionMovil

        en_la_pc = Client()
        self.entrar('40000002', cliente=en_la_pc)
        token = emitir_token(self.beto)
        pedido = RequestFactory().get('/api/movil/panel/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(sesion_del_token(pedido)[0], self.beto)
        self.pedir('40000002')
        self.elegir(self.enlace())
        self.assertFalse(Sesion.objects.filter(id_usuario=self.beto, activa=True).exists())
        movil = SesionMovil.objects.get(id_usuario=self.beto)
        self.assertIsNotNone(movil.revocada_en)
        self.assertEqual(movil.motivo_revocacion, 'CAMBIO_DE_CLAVE')
        self.assertEqual(sesion_del_token(pedido), (None, None))

    @override_settings(CAFETO_URL_PUBLICA='https://cafeto.ejemplo.com')
    def test_el_enlace_usa_la_direccion_publica(self):
        self.pedir('40000002')
        cuerpo = mail.outbox[-1].body
        self.assertIn('https://cafeto.ejemplo.com/elegir-clave/recuperar/', cuerpo)
        self.assertNotIn('testserver', cuerpo)

    def test_sin_direccion_publica_usa_la_del_pedido_detras_del_proxy(self):
        self.client.post(reverse('seguridad:olvide_clave'), {'identificador': '40000002'},
                         HTTP_HOST='cafeto.ejemplo.com', HTTP_X_FORWARDED_PROTO='https')
        self.assertIn('https://cafeto.ejemplo.com/elegir-clave/recuperar/', mail.outbox[-1].body)

    def test_el_restablecimiento_del_admin_manda_el_enlace_y_anula_la_clave_vieja(self):
        self.entrar('31000001')
        respuesta = self.client.post(
            reverse('seguridad:restablecer_clave', args=[self.beto.pk]))
        self.assertRedirects(respuesta, reverse('seguridad:lista'))
        self.beto.refresh_from_db()
        self.assertFalse(self.beto.verificar_clave(CLAVE))
        self.assertIn('/elegir-clave/activar/', mail.outbox[0].body)

    def test_dos_usuarios_no_comparten_correo(self):
        repetido = Usuario(dni_usuario='41000009', apellido='Otra', nombre='Persona',
                           correo='40000002@CAFETO.test', id_perfil=self.p_operador)
        with self.assertRaisesMessage(ValidationError, 'ya es de otra persona'):
            repetido.full_clean()


# ===========================================================================
class PerfilTemporalTest(Base):

    def asignar(self, perfil=None, horas=8, desde=None, motivo='Vacaciones'):
        desde = desde or timezone.now() - timedelta(minutes=1)
        self.beto.asignar_perfil_temporal(
            perfil or self.p_encargado, desde, desde + timedelta(hours=horas),
            self.titular, motivo)

    def test_mientras_rige_opera_con_el_temporal(self):
        self.assertFalse(self.beto.tiene_permiso('ver_marcas'))
        self.asignar()
        self.assertEqual(self.beto.perfil_efectivo, self.p_encargado)
        self.assertTrue(self.beto.tiene_permiso('ver_marcas'))

    def test_al_vencer_vuelve_solo_a_su_perfil(self):
        self.asignar()
        Usuario.objects.filter(pk=self.beto.pk).update(
            temporal_desde=timezone.now() - timedelta(days=2),
            temporal_hasta=timezone.now() - timedelta(days=1))
        self.beto.refresh_from_db()
        self.assertEqual(self.beto.perfil_efectivo, self.p_operador)
        self.assertFalse(self.beto.tiene_permiso('ver_marcas'))

    def test_el_perfil_administrador_no_se_presta(self):
        with self.assertRaises(ValidationError):
            self.asignar(self.p_admin)

    def test_no_dura_mas_de_un_mes(self):
        with self.assertRaises(ValidationError):
            self.asignar(horas=24 * 40)

    def test_pide_motivo(self):
        with self.assertRaises(ValidationError):
            self.asignar(motivo='   ')

    def test_la_base_exige_los_cinco_campos_juntos(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Usuario.objects.filter(pk=self.beto.pk).update(
                id_perfil_temporal=self.p_encargado)

    def test_la_auditoria_registra_el_perfil_con_el_que_opero(self):
        self.asignar()
        self.entrar('40000002')
        from seguridad.servicios import registrar_auditoria
        from django.test import RequestFactory

        pedido = RequestFactory().get('/')
        pedido.usuario_actual = self.beto
        pedido.session = self.client.session
        asiento = registrar_auditoria(pedido, 'PRUEBA', 'RUBRO')
        self.assertEqual(asiento.id_perfil_efectivo, self.p_encargado)
        self.assertEqual(asiento.id_usuario, self.beto)

    def test_por_pantalla_y_se_revoca(self):
        self.entrar('30000000')
        desde = timezone.localtime().replace(second=0, microsecond=0)
        hasta = desde + timedelta(days=3)
        self.client.post(
            reverse('seguridad:perfil_temporal', args=[self.beto.pk]),
            {'id_perfil_temporal': self.p_encargado.pk,
             'temporal_desde': desde.strftime('%Y-%m-%dT%H:%M'),
             'temporal_hasta': hasta.strftime('%Y-%m-%dT%H:%M'),
             'temporal_motivo': 'Cubre a Lucía'})
        self.beto.refresh_from_db()
        self.assertEqual(self.beto.id_perfil_temporal, self.p_encargado)
        self.assertEqual(self.beto.temporal_otorgado_por, self.titular)

        self.client.post(
            reverse('seguridad:revocar_temporal', args=[self.beto.pk]))
        self.beto.refresh_from_db()
        self.assertIsNone(self.beto.id_perfil_temporal)


# ===========================================================================
class CambioDeClaveTest(Base):

    def cambiar(self, nueva, actual=CLAVE, repetir=None):
        return self.client.post(reverse('seguridad:cambiar_clave'), {
            'clave_actual': actual, 'clave_nueva': nueva,
            'clave_repetir': repetir or nueva})

    def test_el_cambio_voluntario_pide_la_actual(self):
        self.entrar('40000002')
        respuesta = self.cambiar('Otra.Clave.2027', actual='equivocada')
        self.assertContains(respuesta, 'La clave actual no es correcta')
        self.cambiar('Otra.Clave.2027')
        self.assertTrue(Usuario.objects.get(pk=self.beto.pk)
                        .verificar_clave('Otra.Clave.2027'))

    def test_el_cambio_obligatorio_no_la_pide(self):
        crear('42000005', 'Nueva', 'Ema', self.p_operador, clave='42000005',
              debe_cambiar=True)
        self.entrar('42000005', '42000005')
        self.client.post(reverse('seguridad:cambiar_clave'), {
            'clave_nueva': 'Primera.Clave.1', 'clave_repetir': 'Primera.Clave.1'})
        self.assertFalse(Usuario.objects.get(dni_usuario='42000005')
                         .debe_cambiar_clave)

    def test_rechaza_claves_debiles(self):
        self.entrar('40000002')
        for debil in ('corta', '12345678', 'password', '40000002'):
            with self.subTest(clave=debil):
                self.cambiar(debil)
                self.assertTrue(Usuario.objects.get(pk=self.beto.pk)
                                .verificar_clave(CLAVE))

    def test_el_cambio_se_audita_sin_el_hash(self):
        self.entrar('40000002')
        self.cambiar('Otra.Clave.2027')
        asiento = LogAuditoria.objects.filter(
            operacion_realizada='CAMBIO DE CLAVE DE USUARIO').get()
        self.assertNotIn('contrasena_hash', asiento.registro_nuevo)


# ===========================================================================
class EncabezadosTest(Base):

    def test_el_admin_de_django_no_existe_fuera_de_desarrollo(self):
        self.assertEqual(self.client.get('/admin/').status_code, 302)
        self.entrar('30000000')
        self.assertEqual(self.client.get('/admin/').status_code, 404)

    def test_no_se_deja_meter_en_un_iframe(self):
        respuesta = self.client.get(reverse('seguridad:login'))
        self.assertEqual(respuesta['X-Frame-Options'], 'DENY')
        self.assertEqual(respuesta['X-Content-Type-Options'], 'nosniff')

    def test_la_cookie_de_sesion_no_la_lee_el_javascript(self):
        self.entrar('40000002')
        cookie = self.client.cookies['cafeto_sesion']
        self.assertTrue(cookie['httponly'])
