"""Módulo Seguridad y Acceso.

Siete tablas del DER de alto refinamiento:

    PERFILES            un rol (Administrador, Operador...). El perfil
                        núcleo lleva ``es_sistema=True``: acceso total,
                        y no se renombra ni se da de baja.
    PERMISOS            una acción concreta que se puede habilitar
    PERFILES_PERMISOS   tabla intermedia: qué permisos tiene cada perfil
    USUARIOS            la persona que entra al sistema. Uno de ellos es
                        el TITULAR (``es_titular``): el dueño del sistema,
                        al que nadie más puede dar de baja, cambiarle el
                        perfil ni restablecerle la clave desde la app.
    SESIONES            la sesión de negocio que firma la auditoría
    LOG_AUDITORIA       qué hizo cada quién (libro de sólo agregado)
    INTENTOS_ACCESO     los ingresos rechazados (libro de sólo agregado)

El USUARIO de este módulo es propio: NO se usa el modelo User de Django.
La clave se guarda siempre **hasheada** con las funciones de
``django.contrib.auth.hashers``.

Los permisos del módulo SEGURIDAD (usuarios, perfiles, permisos y el
registro de actividad) son EXCLUSIVOS del perfil de sistema: no se le
pueden asignar a ningún otro perfil. Así, quien no es administrador nunca
puede darse más permisos a sí mismo ni a otro.

Convenciones que valen para todo el sistema:

  * La PK de cada tabla es un AutoField propio (``id_usuario``...).
  * ``usuario_modificacion`` guarda el último que tocó la fila. Es un
    entero SIN clave foránea (DD-17), por eso va con ``db_constraint=False``.
  * El borrado es LÓGICO. En las maestras se marca con ``borrado_logico``;
    en USUARIOS, con ``activo`` (False = dado de baja), como lo escribe el DER.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.core.validators import (
    MaxValueValidator,
    MinValueValidator,
    RegexValidator,
)
from django.db import models
from django.utils import timezone

from nucleo import contexto
from nucleo.modelos import (
    sello_fecha_modificacion,
    sello_usuario_modificacion,
)
from nucleo.motivos import MotivoCompleto
from nucleo.reglas import (
    LibroInmutable,
    ManagerDeLibro,
    ManagerSinBajaFisica,
    SinBajaFisica,
)
from nucleo.restricciones import CheckEnBase, CheckSQL, ClaveNormalizada
from nucleo.validaciones import (
    exigir_nombre_unico,
)
from nucleo.textos import pluralizar

# ---------------------------------------------------------------------------
#  VALIDADORES compartidos
# ---------------------------------------------------------------------------
validar_dni = RegexValidator(
    regex=r'^\d{7,8}$',
    message='El DNI debe tener 7 u 8 dígitos, sin puntos ni espacios.',
)

# Sólo letras (con tildes y ñ) y espacios: nada de números ni símbolos.
validar_solo_letras = RegexValidator(
    regex=r'^[A-Za-zÁÉÍÓÚáéíóúÑñÜü ]+$',
    message='Sólo se admiten letras y espacios.',
)


# ---------------------------------------------------------------------------
#  PERFILES
# ---------------------------------------------------------------------------
class Perfil(SinBajaFisica, models.Model):
    """Rol dentro del sistema. Un usuario tiene exactamente un perfil."""

    id_perfil = models.AutoField('ID', primary_key=True)
    nombre = models.CharField(
        'Nombre del perfil', max_length=20, db_column='nombre_perfil')
    descripcion = models.CharField(
        'Descripción', max_length=120, null=True, blank=True,
        db_column='descripcion_perfil')

    # Relación N a N con Permiso, a través de PERFILES_PERMISOS
    permisos = models.ManyToManyField(
        'Permiso',
        through='PerfilPermiso',
        related_name='perfiles',
        blank=True,
    )

    # Marca de NÚCLEO. Un perfil de sistema tiene acceso total y no se
    # puede renombrar, dar de baja ni dejar sin permisos.
    #
    # Antes esto se deducía de ``nombre == 'Administrador'``. El nombre es
    # un campo que el propio Administrador puede editar desde la pantalla
    # de Perfiles, así que la protección se caía sola: bastaba renombrar
    # "Administrador" a "Admin" para que el perfil dejara de estar
    # protegido —y, peor, para que ``tiene_permiso`` le cortara el acceso
    # total a todos sus usuarios—. Y al revés: llamar "Administrador" a un
    # perfil operativo le regalaba acceso total.
    #
    # La marca no es editable: no aparece en ningún formulario y sólo la
    # mueve una migración de datos.
    es_sistema = models.BooleanField(
        'Perfil del sistema', default=False, editable=False,
        db_column='es_perfil_sistema')

    # Hasta qué porcentaje puede descontar en un renglón de venta quien
    # opera con este perfil, sin pedir autorización. NULL = sin tope, y
    # sólo lo puede tener el perfil de sistema.
    tope_descuento_porcentaje = models.DecimalField(
        'Tope de descuento (%)', max_digits=5, decimal_places=2,
        null=True, blank=True, default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0')),
                    MaxValueValidator(Decimal('100'))],
        help_text='Porcentaje máximo de descuento por renglón sin pedir '
                  'autorización. 0 = no puede descontar.')

    borrado_logico = models.BooleanField('Borrado lógico', default=False)
    fecha_modificacion = sello_fecha_modificacion()
    usuario_modificacion = sello_usuario_modificacion('perfiles_modificados')

    # Acá la baja es lógica: el DELETE no corre (RN-8.1.1).
    objects = ManagerSinBajaFisica()

    class Meta:
        db_table = 'perfiles'
        verbose_name = 'Perfil'
        verbose_name_plural = 'Perfiles'
        ordering = ['nombre']
        constraints = [
            CheckEnBase(
                check=CheckSQL("trim(nombre_perfil) <> ''"),
                name='ck_perfiles_textos'),
            # Sin tope sólo el perfil de sistema; con tope, entre 0 y 100.
            CheckEnBase(
                check=CheckSQL(
                    '(tope_descuento_porcentaje IS NULL AND es_perfil_sistema)'
                    ' OR tope_descuento_porcentaje BETWEEN 0 AND 100'),
                name='ck_perfiles_tope_descuento'),
            # Dos perfiles no pueden llamarse igual salvo por mayúsculas o
            # acentos (DD-50): el índice compara la clave normalizada.
            models.UniqueConstraint(
                ClaveNormalizada('nombre'),
                name='uq_perfiles_nombre_norm'),
        ]

    def __str__(self):
        return self.nombre

    # ------------------------------------------------------------------
    #  Candados del perfil de sistema
    # ------------------------------------------------------------------
    def motivos_que_impiden_la_baja(self):
        """Por qué este perfil no se puede dar de baja todavía (V-G10)."""
        if self.es_sistema:
            # La razón de fondo alcanza: sumarle "tiene N usuarios" armaba
            # un mensaje con dos motivos donde uno ya decide.
            return [MotivoCompleto(
                f'El perfil "{self.nombre}" es el perfil administrador del '
                'sistema y no puede ser eliminado ni deshabilitado: sin él '
                'nadie podría volver a dar de alta usuarios ni asignar '
                'permisos.')]
        motivos = []
        activos = self.usuarios.filter(activo=True).count()
        if activos:
            motivos.append(
                f"tiene {pluralizar(activos, 'usuario activo asignado', 'usuarios activos asignados')}")
        return motivos

    def clean(self):
        """Un perfil de sistema conserva su nombre y su marca.

        El nombre de un perfil común se edita libremente. El del perfil de
        sistema no: aunque la autorización ya no dependa de él (para eso
        está ``es_sistema``), es el nombre con el que lo nombran los
        mensajes, la documentación y las migraciones de datos.
        """
        super().clean()
        self.nombre = (self.nombre or '').strip()
        exigir_nombre_unico(self, 'nombre', etiqueta='un perfil')
        if self.tope_descuento_porcentaje is None and not self.es_sistema:
            raise ValidationError({'tope_descuento_porcentaje': (
                'Indicá el tope de descuento (0 si no puede descontar). '
                'Sólo el perfil administrador opera sin tope.')})

        if not self.pk:
            return
        anterior = type(self).objects.filter(pk=self.pk).first()
        if anterior is None or not anterior.es_sistema:
            return

        # La marca viaja en el formulario como no editable, así que el POST
        # nunca la trae; se la devolvemos por si alguien armó la instancia
        # a mano.
        self.es_sistema = True
        if self.nombre != anterior.nombre:
            raise ValidationError({'nombre': (
                f'El perfil "{anterior.nombre}" es el perfil administrador '
                'del sistema: su nombre no se puede cambiar.')})

    # ------------------------------------------------------------------
    #  Catálogo completo para los perfiles de sistema
    # ------------------------------------------------------------------
    @classmethod
    def asegurar_catalogo_completo(cls):
        """Les asigna a los perfiles de sistema todos los permisos.

        Cada módulo nuevo agrega sus permisos por migración de datos. Si
        alguno quedara sin asignar, el administrador no se bloquea
        —``Usuario.tiene_permiso`` lo deja pasar siempre—, pero la pantalla
        de Perfiles mostraría el tilde apagado y daría a entender que no lo
        tiene. Este método cierra esa brecha: deja la matriz de permisos
        diciendo la verdad.

        Devuelve cuántas asignaciones agregó.
        """
        agregadas = 0
        for perfil in cls.objects.filter(es_sistema=True):
            ya_tiene = set(
                PerfilPermiso.objects
                .filter(perfil=perfil)
                .values_list('permiso_id', flat=True)
            )
            faltantes = list(Permiso.objects.exclude(pk__in=ya_tiene))
            PerfilPermiso.objects.bulk_create(
                [PerfilPermiso(perfil=perfil, permiso=permiso)
                 for permiso in faltantes]
            )
            agregadas += len(faltantes)
        return agregadas


# ---------------------------------------------------------------------------
#  PERMISOS
# ---------------------------------------------------------------------------
class ModuloPermiso(models.TextChoices):
    """Módulo del sistema al que pertenece un permiso.

    Agrupa el catálogo en pantalla. Los valores van en MAYÚSCULAS y sin
    acentos (DD-29).
    """
    SEGURIDAD = 'SEGURIDAD', 'Seguridad y acceso'
    GEOGRAFIA = 'GEOGRAFIA', 'Geografía'
    STOCK = 'STOCK', 'Stock e inventario'
    VENTAS = 'VENTAS', 'Ventas y caja'
    COMPRAS = 'COMPRAS', 'Compras y proveedores'
    FIDELIZACION = 'FIDELIZACION', 'Clientes y fidelización'
    NEGOCIO = 'NEGOCIO', 'Negocio y reportes'
    GENERAL = 'GENERAL', 'General'


# Los permisos de estos módulos sólo los tiene el perfil de sistema. Si un
# perfil cualquiera pudiera recibir "editar_perfil", quien lo tuviera
# podría darse a sí mismo todo lo demás.
MODULOS_EXCLUSIVOS_DEL_ADMINISTRADOR = frozenset({ModuloPermiso.SEGURIDAD})


class Permiso(SinBajaFisica, models.Model):
    """Acción concreta que un perfil puede tener habilitada."""

    id_permiso = models.AutoField('ID', primary_key=True)
    codigo = models.SlugField(
        'Código', max_length=40, db_column='codigo_permiso')
    nombre = models.CharField(
        'Nombre', max_length=50, db_column='nombre_permiso')
    descripcion = models.CharField(
        'Descripción', max_length=120, null=True, blank=True,
        db_column='descripcion_permiso')
    modulo = models.CharField(
        'Módulo', max_length=40,
        choices=ModuloPermiso.choices,
        default=ModuloPermiso.GENERAL)

    borrado_logico = models.BooleanField('Borrado lógico', default=False)
    fecha_modificacion = sello_fecha_modificacion()
    usuario_modificacion = sello_usuario_modificacion('permisos_modificados')

    # Acá la baja es lógica: el DELETE no corre (RN-8.1.1).
    objects = ManagerSinBajaFisica()

    class Meta:
        db_table = 'permisos'
        verbose_name = 'Permiso'
        verbose_name_plural = 'Permisos'
        ordering = ['modulo', 'nombre']
        constraints = [
            CheckEnBase(
                check=CheckSQL("codigo_permiso ~ '^[a-z][a-z0-9_]*$'"),
                name='ck_permisos_codigo'),
            CheckEnBase(
                check=CheckSQL(
                    "trim(codigo_permiso) <> '' AND trim(nombre_permiso) <> ''"
                    " AND trim(modulo) <> ''"),
                name='ck_permisos_textos'),
            models.UniqueConstraint(
                fields=['codigo'], name='uq_permisos_codigo_permiso'),
            CheckEnBase(
                check=CheckSQL("modulo IN (%s)" % ', '.join(
                    f"'{valor}'" for valor in ModuloPermiso.values)),
                name='ck_permisos_modulo'),
        ]

    def __str__(self):
        return self.nombre

    @property
    def es_exclusivo_del_administrador(self):
        return self.modulo in MODULOS_EXCLUSIVOS_DEL_ADMINISTRADOR


# ---------------------------------------------------------------------------
#  PERFILES_PERMISOS  (tabla intermedia de la relación N a N)
# ---------------------------------------------------------------------------
class PerfilPermiso(models.Model):
    """Fila de cruce: "el perfil X tiene el permiso Y".

    El DER le da clave primaria compuesta ``(id_perfil, id_permiso)``.
    Django no sabe manejar PK compuestas, así que acá va una PK técnica
    más un UNIQUE sobre las dos columnas: la regla que importa —no
    repetir el par— queda igual de garantizada. Es la diferencia de
    esquema que documenta la Fase 4 (y el mismo patrón se usa en
    BENEFICIOS_PRODUCTOS).
    """

    id_perfil_permiso = models.AutoField('ID', primary_key=True)
    perfil = models.ForeignKey(
        Perfil, on_delete=models.CASCADE, db_column='id_perfil',
        verbose_name='Perfil')
    permiso = models.ForeignKey(
        Permiso, on_delete=models.CASCADE, db_column='id_permiso',
        verbose_name='Permiso')
    fecha_asignacion = models.DateTimeField(
        'Fecha de asignación', auto_now_add=True)

    def save(self, *args, **kwargs):
        """Los permisos de Seguridad sólo van al perfil de sistema."""
        if (self.permiso.es_exclusivo_del_administrador
                and not self.perfil.es_sistema):
            raise ValidationError(
                f'El permiso "{self.permiso.nombre}" es exclusivo del perfil '
                f'administrador: no se le puede asignar a "{self.perfil}".')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Un perfil asignable conserva al menos un permiso (DD-14).

        Reemplaza a ``fn_perfil_conserva_permiso``. Si el perfil entero se
        está borrando, la fila se va con él y no hay nada que custodiar: lo
        que se cuida es que no quede un perfil VIVO sin ningún permiso, que
        es un perfil que no deja hacer nada y que igual se puede asignar.
        """
        from seguridad.models import Perfil

        if Perfil.objects.filter(pk=self.perfil_id).exists():
            quedan = PerfilPermiso.objects.filter(
                perfil_id=self.perfil_id).exclude(pk=self.pk).exists()
            if not quedan:
                raise ValidationError(
                    'El perfil "%s" quedaría sin permisos: un perfil '
                    'asignable conserva al menos uno (DD-14).' % self.perfil)
        return super().delete(*args, **kwargs)

    class Meta:
        db_table = 'perfiles_permisos'
        verbose_name = 'Permiso de perfil'
        verbose_name_plural = 'Permisos de perfil'
        constraints = [
            models.UniqueConstraint(
                fields=['perfil', 'permiso'],
                name='uq_perfiles_permisos',
            ),
        ]

    def __str__(self):
        return f'{self.perfil} → {self.permiso}'


# ---------------------------------------------------------------------------
#  USUARIOS
# ---------------------------------------------------------------------------
class Usuario(SinBajaFisica, models.Model):
    """Persona que inicia sesión en el sistema.

    La PK es ``id_usuario``. El DNI es un dato único aparte
    (``dni_usuario``), guardado como texto para no perder ceros a la
    izquierda ni arrastrar aritmética sobre algo que no es un número.
    """

    # --- Identidad ---
    id_usuario = models.AutoField('ID', primary_key=True)
    dni_usuario = models.CharField(
        'DNI', max_length=8, validators=[validar_dni])

    # --- Datos que SÍ carga el operador en el alta ---
    apellido = models.CharField(
        'Apellido', max_length=50, validators=[validar_solo_letras])
    nombre = models.CharField(
        'Nombre', max_length=50, validators=[validar_solo_letras])
    correo = models.EmailField('Correo', max_length=254)
    id_perfil = models.ForeignKey(
        Perfil, on_delete=models.RESTRICT, db_column='id_perfil',
        verbose_name='Perfil', related_name='usuarios')

    # --- Campos AUTOGENERADOS en el alta (el operador no los toca) ---
    nombre_usuario = models.CharField(
        'Usuario', max_length=35, editable=False)
    clave = models.CharField(
        'Clave (hash)', max_length=255, editable=False,
        db_column='contrasena_hash')
    debe_cambiar_clave = models.BooleanField('Debe cambiar la clave', default=True)
    # Cuándo se fijó la clave actual. El bloqueo por reintentos sólo cuenta
    # los intentos posteriores: restablecerle la clave a alguien lo
    # desbloquea, sin tocar el libro de intentos.
    fecha_cambio_clave = models.DateTimeField(
        'Último cambio de clave', default=timezone.now, editable=False)

    # --- Baja lógica. El DER la escribe en positivo: activo = True es
    #     "vigente". La propiedad 'borrado_logico' de más abajo conserva la
    #     lectura invertida que usan los templates. ---
    activo = models.BooleanField('Activo', default=True)

    fecha_alta = models.DateTimeField(
        'Fecha de alta', default=timezone.now, editable=False)
    fecha_ultima_modificacion = models.DateTimeField(
        'Última modificación', default=timezone.now, editable=False)
    fecha_baja = models.DateTimeField('Fecha de baja', null=True, blank=True)
    usuario_modificacion = sello_usuario_modificacion('usuarios_modificados')

    # --- Titularidad: el dueño del sistema. Hay uno solo (índice único
    #     parcial) y siempre es un administrador activo. No se edita desde
    #     ningún formulario: se transfiere con ``transferir_titularidad``. ---
    es_titular = models.BooleanField(
        'Titular del sistema', default=False, editable=False)

    # --- Perfil temporal: un reemplazo por vacaciones o por un franco. Rige
    #     entre ``temporal_desde`` y ``temporal_hasta``; fuera de ese rango
    #     el usuario vuelve solo a su perfil de siempre. Nunca puede ser el
    #     perfil de sistema. Los cinco campos van juntos o vacíos. ---
    id_perfil_temporal = models.ForeignKey(
        Perfil, on_delete=models.RESTRICT, db_column='id_perfil_temporal',
        null=True, blank=True, related_name='usuarios_temporales',
        verbose_name='Perfil temporal')
    temporal_desde = models.DateTimeField('Temporal desde', null=True,
                                          blank=True)
    temporal_hasta = models.DateTimeField('Temporal hasta', null=True,
                                          blank=True)
    temporal_otorgado_por = models.ForeignKey(
        'self', on_delete=models.RESTRICT, db_column='temporal_otorgado_por',
        null=True, blank=True, related_name='perfiles_temporales_otorgados',
        verbose_name='Otorgado por')
    temporal_motivo = models.CharField(
        'Motivo del perfil temporal', max_length=120, null=True, blank=True)

    # Acá la baja es lógica: el DELETE no corre (RN-8.1.1).
    objects = ManagerSinBajaFisica()

    class Meta:
        db_table = 'usuarios'
        verbose_name = 'Usuario'
        verbose_name_plural = 'Usuarios'
        ordering = ['apellido', 'nombre']
        constraints = [
            CheckEnBase(
                check=CheckSQL("dni_usuario ~ '^[0-9]{7,8}$'"),
                name='ck_usuarios_dni'),
            CheckEnBase(
                check=CheckSQL(
                    '(activo AND fecha_baja IS NULL)'
                    ' OR (NOT activo AND fecha_baja IS NOT NULL)'),
                name='ck_usuarios_baja'),
            CheckEnBase(
                check=CheckSQL(
                    'fecha_baja IS NULL OR fecha_baja >= fecha_alta'),
                name='ck_usuarios_fechas'),
            CheckEnBase(
                check=CheckSQL(
                    "trim(dni_usuario) <> '' AND trim(apellido) <> ''"
                    " AND trim(nombre) <> '' AND trim(correo) <> ''"
                    " AND trim(nombre_usuario) <> ''"
                    " AND trim(contrasena_hash) <> ''"),
                name='ck_usuarios_textos'),
            models.UniqueConstraint(
                fields=['dni_usuario'], name='uq_usuarios_dni_usuario'),
            models.UniqueConstraint(
                fields=['nombre_usuario'], name='uq_usuarios_nombre_usuario'),
            # Un solo titular, y siempre activo.
            models.UniqueConstraint(
                fields=['es_titular'], condition=models.Q(es_titular=True),
                name='uq_usuarios_un_titular'),
            CheckEnBase(
                check=CheckSQL('NOT es_titular OR activo'),
                name='ck_usuarios_titular_activo'),
            # El perfil temporal: todo o nada, con un rango que avanza y
            # distinto del perfil de siempre.
            CheckEnBase(
                check=CheckSQL(
                    '(id_perfil_temporal IS NULL AND temporal_desde IS NULL'
                    ' AND temporal_hasta IS NULL'
                    ' AND temporal_otorgado_por IS NULL'
                    ' AND temporal_motivo IS NULL)'
                    ' OR (id_perfil_temporal IS NOT NULL'
                    ' AND temporal_desde IS NOT NULL'
                    ' AND temporal_hasta > temporal_desde'
                    ' AND temporal_otorgado_por IS NOT NULL'
                    " AND trim(temporal_motivo) <> ''"
                    ' AND id_perfil_temporal <> id_perfil)'),
                name='ck_usuarios_perfil_temporal'),
        ]

    def __str__(self):
        return f'{self.apellido}, {self.nombre} ({self.dni_usuario})'

    # ------------------------------------------------------------------
    #  Lectura invertida de la baja lógica
    # ------------------------------------------------------------------
    @property
    def borrado_logico(self):
        """True si el usuario está dado de baja.

        En la base la columna es ``activo`` (DER). Los templates y las
        vistas venían preguntando por ``borrado_logico``; esta propiedad
        mantiene esa lectura. Para FILTRAR hay que usar el campo real:
        ``Usuario.objects.filter(activo=True)``.
        """
        return not self.activo

    # ------------------------------------------------------------------
    #  Generación del nombre de usuario:  Apellido + 1.ª letra del Nombre
    # ------------------------------------------------------------------
    def _generar_nombre_usuario(self):
        import unicodedata

        crudo = f'{self.apellido}{self.nombre[:1]}'.lower()
        # Saca tildes:  "peréz" -> "perez"
        sin_tildes = ''.join(
            c for c in unicodedata.normalize('NFKD', crudo)
            if not unicodedata.combining(c)
        )
        base = ''.join(c for c in sin_tildes if c.isalnum())  # saca espacios
        base = base[:33]              # deja lugar para el sufijo numérico
        candidato = base
        n = 1
        # Si ya existe (otro "Perez J"), agrega un número al final
        existentes = Usuario.objects.exclude(pk=self.pk)
        while existentes.filter(nombre_usuario=candidato).exists():
            n += 1
            candidato = f'{base}{n}'
        return candidato

    def clean(self):
        super().clean()
        if self.es_titular and not self.id_perfil.es_sistema:
            raise ValidationError(
                'El titular del sistema conserva el perfil administrador.')
        # El correo recibe el enlace para elegir la clave: si dos personas
        # lo compartieran, una podría elegir la clave de la otra.
        if self.correo and Usuario.objects.exclude(pk=self.pk).filter(
                correo__iexact=self.correo.strip()).exists():
            raise ValidationError({'correo': (
                'Ese correo ya es de otra persona del sistema. Cada usuario '
                'necesita el suyo: ahí le llega el enlace para elegir la clave.')})

    def save(self, *args, **kwargs):
        # Capitalización automática: "juan carlos" -> "Juan Carlos".
        # Evita registros desprolijos cargados en minúscula o en MAYÚSCULA.
        if self.apellido:
            self.apellido = self.apellido.strip().title()
        if self.nombre:
            self.nombre = self.nombre.strip().title()
        if self.dni_usuario:
            self.dni_usuario = str(self.dni_usuario).strip()
            from nucleo.validaciones import exigir_dni_creible
            try:
                exigir_dni_creible(self.dni_usuario)
            except ValidationError as error:
                raise ValidationError({'dni_usuario': error.messages})
        if self.correo:
            self.correo = self.correo.strip().lower()

        if not self.nombre_usuario:
            self.nombre_usuario = self._generar_nombre_usuario()

        if self.es_titular and not self.id_perfil.es_sistema:
            raise ValidationError(
                'El titular del sistema conserva el perfil administrador.')

        # ck_usuarios_baja: activo y fecha_baja tienen que contar lo mismo.
        if self.activo:
            self.fecha_baja = None
        elif self.fecha_baja is None:
            self.fecha_baja = timezone.now()

        # T01 · El sello de modificación (antes fn_usuario_ciclo_de_vida).
        # Sólo en la modificación: una fila recién creada no fue "modificada"
        # por nadie, y lo que registra el alta es la auditoría.
        modificacion = self.pk is not None and not self._state.adding
        if modificacion:
            self.fecha_ultima_modificacion = timezone.now()
            operador = contexto.usuario_actual()
            if operador is not None and operador.pk:
                self.usuario_modificacion_id = operador.pk

        # Se mira el estado ANTERIOR antes de escribir: después del UPDATE ya
        # no hay con qué comparar.
        se_esta_dando_de_baja = False
        if modificacion and not self.activo:
            se_esta_dando_de_baja = type(self).objects.filter(
                pk=self.pk, activo=True).exists()

        super().save(*args, **kwargs)

        # T02 · Dar de baja a un usuario cierra sus sesiones (DD-09).
        # Va DESPUÉS del super(): si el guardado falla, las sesiones no se
        # tocan. ATOMIC_REQUESTS mantiene las dos cosas en la misma
        # transacción, así que o pasan las dos o no pasa ninguna.
        if se_esta_dando_de_baja:
            from seguridad.models import Sesion

            Sesion.objects.filter(id_usuario=self, activa=True).update(
                activa=False, fecha_cierre=timezone.now(),
                motivo_cierre='REVOCACION')

    # ------------------------------------------------------------------
    #  Permisos — "¿este usuario puede hacer X?"
    # ------------------------------------------------------------------
    def tiene_permiso(self, codigo_permiso):
        """True si el perfil del usuario tiene ese permiso.

        Regla especial: un **perfil de sistema** siempre pasa, tenga o no
        la fila cargada en PERFILES_PERMISOS. Así, si mañana se agrega un
        permiso nuevo y alguien se olvida de asignárselo, el admin no
        queda bloqueado.

        El atajo mira ``Perfil.es_sistema``, no el nombre del perfil: el
        nombre se edita desde la pantalla, y hacer depender la
        autorización de un texto editable es regalar el sistema a quien
        bautice un perfil "Administrador".
        """
        perfil = self.perfil_efectivo
        if perfil.es_sistema:
            return True
        # Defensa en profundidad: aunque una fila de PERFILES_PERMISOS se
        # colara por fuera del modelo, un permiso de Seguridad no habilita
        # a un perfil que no es el de sistema.
        return perfil.permisos.filter(codigo=codigo_permiso).exclude(
            modulo__in=MODULOS_EXCLUSIVOS_DEL_ADMINISTRADOR).exists()

    # ------------------------------------------------------------------
    #  Perfil efectivo: el temporal mientras rige, si no el de siempre
    # ------------------------------------------------------------------
    @property
    def tiene_perfil_temporal_vigente(self):
        if not self.id_perfil_temporal_id:
            return False
        ahora = timezone.now()
        return self.temporal_desde <= ahora < self.temporal_hasta

    @property
    def perfil_efectivo(self):
        """Con qué perfil opera AHORA. Es el que firma la auditoría."""
        if self.tiene_perfil_temporal_vigente:
            return self.id_perfil_temporal
        return self.id_perfil

    @property
    def es_administrador(self):
        return self.activo and self.id_perfil.es_sistema

    def codigos_de_permiso(self):
        """Los códigos que habilita el perfil efectivo, en UNA consulta.

        Para la presentación (el menú, los botones de una pantalla), que
        pregunta por muchos permisos a la vez. Devuelve ``None`` para un
        perfil de sistema, que pasa siempre. Se guarda en la instancia,
        que vive lo que dura el pedido.
        """
        if not hasattr(self, '_codigos_de_permiso'):
            perfil = self.perfil_efectivo
            if perfil.es_sistema:
                self._codigos_de_permiso = None
            else:
                self._codigos_de_permiso = frozenset(
                    perfil.permisos.exclude(
                        modulo__in=MODULOS_EXCLUSIVOS_DEL_ADMINISTRADOR)
                    .values_list('codigo', flat=True))
        return self._codigos_de_permiso

    def puede(self, codigo_permiso):
        """Como ``tiene_permiso``, pero con los códigos ya leídos."""
        codigos = self.codigos_de_permiso()
        return codigos is None or codigo_permiso in codigos

    # ------------------------------------------------------------------
    #  Regla de negocio: "último administrador en pie"
    # ------------------------------------------------------------------
    @property
    def es_ultimo_administrador(self):
        """True si este usuario es el ÚNICO administrador vigente que queda.

        El sistema tiene que conservar siempre al menos un administrador
        que pueda entrar: si no, nadie podría volver a dar de alta usuarios
        ni tocar perfiles.

        "Administrador" es quien tiene un perfil marcado ``es_sistema``, no
        quien tiene un perfil llamado así: el nombre es editable y este
        candado no puede depender de él.
        """
        if not self.activo:
            return False
        if not self.id_perfil.es_sistema:
            return False
        vigentes = Usuario.objects.filter(
            id_perfil__es_sistema=True, activo=True).count()
        return vigentes <= 1

    # ------------------------------------------------------------------
    #  Manejo de la clave — SIEMPRE hasheada
    # ------------------------------------------------------------------
    def set_clave(self, clave_plana):
        """Guarda la clave hasheada (no la escribe nunca en texto plano)."""
        self.clave = make_password(clave_plana)
        self.debe_cambiar_clave = False
        self.fecha_cambio_clave = timezone.now()

    def verificar_clave(self, clave_plana):
        """Devuelve True si la clave tipeada coincide con el hash guardado.

        Si el hash se generó con parámetros viejos (Django sube las
        iteraciones en cada versión), se aprovecha que la clave es correcta
        para regrabarlo con los parámetros actuales.
        """
        def regrabar(clave):
            self.clave = make_password(clave)
            if self.pk:
                type(self).objects.filter(pk=self.pk).update(clave=self.clave)

        return check_password(clave_plana, self.clave, setter=regrabar)

    # Clave provisoria (alta y restablecimiento). Se le dicta o se le
    # muestra a la persona EN EL LOCAL, así que no lleva letras que se
    # confunden (l/1, o/0). 10 caracteres de 31 son ~49 bits: alcanza para
    # un secreto de un solo uso, que vence y con el ingreso limitado por
    # reintentos (NIST SP 800-63B, 5.1.1 y 6.1.2.3).
    ALFABETO_PROVISORIA = 'abcdefghjkmnpqrstuvwxyz23456789'
    VIGENCIA_PROVISORIA = timedelta(hours=24)

    def generar_clave_provisoria(self):
        """Pone una clave provisoria al azar y devuelve su texto UNA vez.

        No guarda: quien llama decide cuándo (en el alta, el save de la
        vista; en el restablecimiento, ``restablecer_clave``).
        """
        from django.utils.crypto import get_random_string

        nueva = get_random_string(10, self.ALFABETO_PROVISORIA)
        self.clave = make_password(nueva)
        self.debe_cambiar_clave = True
        self.fecha_cambio_clave = timezone.now()
        return nueva

    @property
    def vence_clave_provisoria(self):
        if not self.debe_cambiar_clave or self.es_titular:
            return None
        return self.fecha_cambio_clave + self.VIGENCIA_PROVISORIA

    @property
    def clave_provisoria_vencida(self):
        """Una clave provisoria que nadie usó en 24 h ya no abre la cuenta.

        El titular queda afuera de la regla: su clave inicial es la de la
        instalación, y si la pierde se recupera en el servidor.
        """
        vence = self.vence_clave_provisoria
        return vence is not None and timezone.now() > vence

    def restablecer_clave(self):
        """Genera una clave nueva al azar, la hashea y obliga a cambiarla.

        Devuelve la clave en texto plano UNA sola vez para poder
        comunicársela al usuario; después ya no se puede recuperar.
        """
        if self.es_titular:
            raise ValidationError(
                'La clave del titular del sistema no se restablece desde la '
                'aplicación: se recupera en el servidor con el comando '
                '"manage.py recuperar_titular".')
        nueva = self.generar_clave_provisoria()
        self.save(update_fields=['clave', 'debe_cambiar_clave',
                                 'fecha_cambio_clave',
                                 'fecha_ultima_modificacion'])
        return nueva

    def dar_de_baja(self):
        """Baja LÓGICA: no borra la fila, apaga 'activo'.

        Levanta ``ValidationError`` si el usuario es el último
        Administrador vigente del sistema (ver ``es_ultimo_administrador``).
        """
        if self.es_titular:
            raise ValidationError(
                'El titular del sistema no se puede dar de baja. Si el '
                'negocio cambia de dueño, primero se le transfiere la '
                'titularidad a otro administrador.')
        if self.es_ultimo_administrador:
            raise ValidationError(
                'El sistema debe tener al menos un administrador activo.')
        abierto = self.turnos_abiertos.filter(estado_turno='ABIERTA').first()
        if abierto is not None:
            raise ValidationError(
                f'{self} abrió la caja "{abierto.id_caja_registradora}" y el '
                'turno sigue abierto. Cerrá la caja o hacé el relevo antes de '
                'darle de baja: la plata del cajón está a su nombre.')
        self.activo = False
        self.fecha_baja = timezone.now()
        self.save(update_fields=['activo', 'fecha_baja',
                                 'fecha_ultima_modificacion'])

    def reactivar(self):
        self.activo = True
        self.fecha_baja = None
        self.save(update_fields=['activo', 'fecha_baja',
                                 'fecha_ultima_modificacion'])

    # ------------------------------------------------------------------
    #  Cambios de perfil
    # ------------------------------------------------------------------
    def cambiar_perfil(self, nuevo_perfil):
        """Cambio PERMANENTE de perfil (un ascenso, un cambio de puesto).

        La re-autenticación de quien lo pide la exige la vista; acá viven
        las reglas que valen venga de donde venga el cambio.
        """
        if nuevo_perfil.pk == self.id_perfil_id:
            raise ValidationError(
                f'{self.nombre} ya tiene el perfil "{nuevo_perfil}".')
        if nuevo_perfil.borrado_logico:
            raise ValidationError(
                f'El perfil "{nuevo_perfil}" está dado de baja.')
        if not self.activo:
            raise ValidationError(
                'Un usuario dado de baja no cambia de perfil: reactivalo '
                'primero.')
        if self.es_titular:
            raise ValidationError(
                'El titular del sistema conserva el perfil administrador.')
        if self.es_ultimo_administrador and not nuevo_perfil.es_sistema:
            raise ValidationError(
                'El sistema debe tener al menos un administrador activo.')
        anterior = self.id_perfil
        self.id_perfil = nuevo_perfil
        # Si el temporal era justo el perfil nuevo, ya no tiene sentido.
        if self.id_perfil_temporal_id == nuevo_perfil.pk:
            self._limpiar_temporal()
        self.save()
        return anterior

    DURACION_MAXIMA_TEMPORAL = timedelta(days=31)

    def asignar_perfil_temporal(self, perfil, desde, hasta, otorgado_por,
                                motivo):
        """Le presta otro perfil por un rango de fechas (DD de seguridad).

        Nunca el de sistema: lo temporal es para cubrir un puesto, no para
        abrir la administración. Y nunca más de un mes: un reemplazo que
        dura más es un cambio de puesto, y se registra como tal.
        """
        motivo = (motivo or '').strip()
        errores = {}
        if perfil.es_sistema:
            errores['id_perfil_temporal'] = (
                'El perfil administrador no se presta: si la persona '
                'necesita administrar, es un cambio de perfil permanente.')
        elif perfil.borrado_logico:
            errores['id_perfil_temporal'] = (
                f'El perfil "{perfil}" está dado de baja.')
        elif perfil.pk == self.id_perfil_id:
            errores['id_perfil_temporal'] = (
                f'{self.nombre} ya tiene el perfil "{perfil}".')
        if self.id_perfil.es_sistema:
            errores['id_perfil_temporal'] = (
                'Un administrador ya tiene acceso total: no necesita un '
                'perfil temporal.')
        if not desde or not hasta or hasta <= desde:
            errores['temporal_hasta'] = (
                'La fecha de fin tiene que ser posterior a la de inicio.')
        elif hasta <= timezone.now():
            errores['temporal_hasta'] = 'La fecha de fin ya pasó.'
        elif hasta - desde > self.DURACION_MAXIMA_TEMPORAL:
            errores['temporal_hasta'] = (
                'Un perfil temporal dura como máximo 31 días. Si el cambio '
                'es más largo, cambiale el perfil de forma permanente.')
        if not motivo:
            errores['temporal_motivo'] = (
                'Contá por qué (por ejemplo: "cubre vacaciones de Lucía").')
        if not self.activo:
            errores['id_perfil_temporal'] = (
                'Un usuario dado de baja no recibe perfiles temporales.')
        if errores:
            raise ValidationError(errores)

        self.id_perfil_temporal = perfil
        self.temporal_desde = desde
        self.temporal_hasta = hasta
        self.temporal_otorgado_por = otorgado_por
        self.temporal_motivo = motivo[:120]
        self.save()

    def _limpiar_temporal(self):
        self.id_perfil_temporal = None
        self.temporal_desde = None
        self.temporal_hasta = None
        self.temporal_otorgado_por = None
        self.temporal_motivo = None

    def revocar_perfil_temporal(self):
        if not self.id_perfil_temporal_id:
            raise ValidationError(
                f'{self.nombre} no tiene un perfil temporal asignado.')
        self._limpiar_temporal()
        self.save()

    # ------------------------------------------------------------------
    #  Titularidad
    # ------------------------------------------------------------------
    def transferir_titularidad(self, nuevo_titular):
        """El titular le pasa la titularidad a otro administrador activo.

        Es lo que se hace si el negocio cambia de dueño: se le crea un
        usuario administrador al nuevo y el titular actual le transfiere
        la titularidad. Después, el anterior ya puede ser dado de baja.
        """
        from django.db import transaction

        if not self.es_titular:
            raise ValidationError(
                'Sólo el titular del sistema puede transferir la titularidad.')
        if nuevo_titular.pk == self.pk:
            raise ValidationError('Ya sos el titular del sistema.')
        if not nuevo_titular.activo or not nuevo_titular.id_perfil.es_sistema:
            raise ValidationError(
                'La titularidad sólo se le transfiere a un usuario activo '
                'con perfil administrador.')
        with transaction.atomic():
            # El índice único parcial no admite dos titulares ni por un
            # instante: primero se apaga el actual.
            type(self).objects.filter(pk=self.pk).update(es_titular=False)
            type(self).objects.filter(pk=nuevo_titular.pk).update(
                es_titular=True)
        self.es_titular = False
        nuevo_titular.es_titular = True


# ===========================================================================
#  SESIONES, AUDITORÍA E INTENTOS DE ACCESO
# ===========================================================================
class Sesion(models.Model):
    """Sesión abierta por un usuario. Única por usuario a la vez (DD-01).

    Es la que firma la auditoría: ``log_auditoria.id_sesion`` apunta acá,
    así cada operación registrada queda atada a quién la hizo, desde qué
    IP y con qué cajón.
    """

    MOTIVOS = (
        ('EXPIRACION', 'Expiró'),
        ('INACTIVIDAD', 'Inactividad'),
        ('REVOCACION', 'Revocada'),
        ('CIERRE_MANUAL', 'Cierre manual'),
    )

    id_sesion = models.AutoField('ID', primary_key=True)
    id_usuario = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
        related_name='sesiones', verbose_name='Usuario')
    # El cajón con el que está trabajando este equipo (la tablet del
    # Minisuper, la PC de la Cafetería). Lo elige la persona al vender; el
    # turno es del cajón, así que de acá se llega al turno abierto.
    id_caja_registradora = models.ForeignKey(
        'ventas.CajaRegistradora', on_delete=models.RESTRICT,
        db_column='id_caja_registradora', null=True, blank=True,
        related_name='sesiones', verbose_name='Caja en uso')
    token_hash = models.CharField('Token (hash)', max_length=255)
    fecha_inicio = models.DateTimeField('Inicio', default=timezone.now)
    fecha_ultimo_acceso = models.DateTimeField(
        'Último acceso', default=timezone.now)
    fecha_expiracion = models.DateTimeField('Expira')
    fecha_cierre = models.DateTimeField('Cierre', null=True, blank=True)
    motivo_cierre = models.CharField(
        'Motivo del cierre', max_length=20, choices=MOTIVOS,
        null=True, blank=True)
    ip_origen = models.CharField('IP de origen', max_length=45)
    activa = models.BooleanField('Activa', default=True)

    class Meta:
        db_table = 'sesiones'
        verbose_name = 'Sesión'
        verbose_name_plural = 'Sesiones'
        ordering = ['-fecha_inicio']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    "motivo_cierre IS NULL OR motivo_cierre IN "
                    "('EXPIRACION','INACTIVIDAD','REVOCACION','CIERRE_MANUAL')"),
                name='ck_sesiones_motivo'),
            CheckEnBase(
                check=CheckSQL(
                    '(activa AND fecha_cierre IS NULL AND motivo_cierre IS NULL)'
                    ' OR (NOT activa AND fecha_cierre IS NOT NULL AND '
                    'motivo_cierre IS NOT NULL)'),
                name='ck_sesiones_estado'),
            CheckEnBase(
                check=CheckSQL('fecha_expiracion > fecha_inicio'),
                name='ck_sesiones_expiracion'),
            CheckEnBase(
                check=CheckSQL(
                    'fecha_cierre IS NULL OR fecha_cierre >= fecha_inicio'),
                name='ck_sesiones_fechas'),
            CheckEnBase(
                check=CheckSQL('fecha_ultimo_acceso >= fecha_inicio'),
                name='ck_sesiones_ultimo_acceso'),
            CheckEnBase(
                check=CheckSQL(
                    "trim(token_hash) <> '' AND trim(ip_origen) <> ''"),
                name='ck_sesiones_textos'),
            models.UniqueConstraint(
                fields=['token_hash'], name='uq_sesiones_token_hash'),
            # Una sola sesión activa por usuario (DD-01).
            models.UniqueConstraint(
                fields=['id_usuario'], condition=models.Q(activa=True),
                name='uq_sesion_activa_por_usuario'),
        ]

    def __str__(self):
        return f'Sesión {self.pk} de {self.id_usuario}'

    def vencio(self, ahora, inactividad):
        """El motivo por el que la sesión ya no vale, o None."""
        if ahora >= self.fecha_expiracion:
            return 'EXPIRACION'
        if ahora - self.fecha_ultimo_acceso > inactividad:
            return 'INACTIVIDAD'
        return None

    def cerrar(self, motivo='CIERRE_MANUAL'):
        self.activa = False
        self.fecha_cierre = timezone.now()
        self.motivo_cierre = motivo
        self.save(update_fields=['activa', 'fecha_cierre', 'motivo_cierre'])


class ResultadoOperacion(models.TextChoices):
    """Cómo terminó lo que se intentó (DD-62, NIST SP 800-53 AU-3 e).

    EXITOSA: la operación se hizo y cambió datos. RECHAZADA: el sistema la
    frenó por un control de seguridad —un acceso sin permiso, una
    autorización que no correspondía, una anulación fuera de regla— y no
    cambió nada. Los errores comunes de carga (un campo vacío, un stock
    que no alcanza) no se asientan: no son un intento de saltear un
    control, y llenarían el libro de ruido.
    """
    EXITOSA = 'EXITOSA', 'Exitosa'
    RECHAZADA = 'RECHAZADA', 'Rechazada'


class LogAuditoria(LibroInmutable, models.Model):
    """Qué hizo cada quién. Libro append-only (DD-04).

    ``registro_anterior`` y ``registro_nuevo`` guardan el estado de la fila
    antes y después, en JSONB: con eso se puede reconstruir cualquier
    cambio sin versionar las tablas.

    ``resultado_operacion`` distingue lo que se hizo de lo que el sistema
    RECHAZÓ (DD-62). Un rechazo no lleva fotos: no cambió ninguna fila.
    """

    id_auditoria = models.AutoField('ID', primary_key=True)
    operacion_realizada = models.CharField('Operación', max_length=50)
    modulo_afectado = models.CharField('Módulo', max_length=40)
    detalle_operacion = models.TextField('Detalle', null=True, blank=True)
    fecha_hora = models.DateTimeField('Fecha y hora', default=timezone.now)
    # Quién y con qué perfil. Van aparte de la sesión porque una
    # operación puede no tener sesión web (un comando en el servidor) y
    # porque el perfil con el que se operó puede ser uno TEMPORAL: la
    # auditoría tiene que decir con qué permisos se hizo, no con cuáles
    # cuenta hoy esa persona.
    id_usuario = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT,
        db_column='id_usuario', null=True, blank=True,
        related_name='auditoria', verbose_name='Usuario')
    id_perfil_efectivo = models.ForeignKey(
        Perfil, on_delete=models.RESTRICT, db_column='id_perfil_efectivo',
        null=True, blank=True, related_name='+',
        verbose_name='Perfil con el que operó')
    id_sesion = models.ForeignKey(
        Sesion, on_delete=models.RESTRICT, db_column='id_sesion',
        null=True, blank=True,
        related_name='auditoria', verbose_name='Sesión')
    # Qué fila: la tabla y su PK, para poder ir del asiento al registro.
    tabla_afectada = models.CharField(
        'Tabla', max_length=40, null=True, blank=True)
    id_registro = models.BigIntegerField('ID del registro', null=True,
                                         blank=True)
    registro_anterior = models.JSONField('Antes', null=True, blank=True)
    registro_nuevo = models.JSONField('Después', null=True, blank=True)
    resultado_operacion = models.CharField(
        'Resultado', max_length=10, choices=ResultadoOperacion.choices,
        default=ResultadoOperacion.EXITOSA,
        db_default=ResultadoOperacion.EXITOSA)

    # Un asiento no se corrige: es el rastro de quién hizo qué, y si se
    # pudiera editar no probaría nada. El manager corta también el camino
    # del conjunto, porque QuerySet.update()/delete() no pasan por la
    # instancia.
    objects = ManagerDeLibro()

    class Meta:
        db_table = 'log_auditoria'
        verbose_name = 'Registro de auditoría'
        verbose_name_plural = 'Log de auditoría'
        ordering = ['-fecha_hora']
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    "trim(operacion_realizada) <> '' AND trim(modulo_afectado) <> ''"),
                name='ck_log_auditoria_textos'),
            CheckEnBase(
                check=CheckSQL(
                    '(tabla_afectada IS NULL) = (id_registro IS NULL)'),
                name='ck_log_auditoria_registro'),
            # Un rechazo no cambió nada: no puede traer foto de antes ni
            # de después.
            CheckEnBase(
                check=CheckSQL(
                    "resultado_operacion IN ('EXITOSA','RECHAZADA') AND "
                    "(resultado_operacion = 'EXITOSA' OR "
                    '(registro_anterior IS NULL AND registro_nuevo IS NULL))'),
                name='ck_log_auditoria_resultado'),
        ]
        indexes = [
            models.Index(fields=['-fecha_hora'], name='ix_log_auditoria_fecha'),
            models.Index(fields=['resultado_operacion', '-fecha_hora'],
                         name='ix_log_auditoria_resultado'),
            models.Index(fields=['tabla_afectada', 'id_registro'],
                         name='ix_log_auditoria_registro'),
        ]

    def __str__(self):
        return f'{self.fecha_hora:%d/%m/%Y %H:%M} · {self.operacion_realizada}'

    def save(self, *args, **kwargs):
        # Quién firmó sale de la sesión si no vino dicho: así el filtro
        # "por usuario" mira UNA columna y no se pierde ningún asiento.
        if self._state.adding and self.id_usuario_id is None and self.id_sesion_id:
            self.id_usuario_id = self.id_sesion.id_usuario_id
            if self.id_perfil_efectivo_id is None and self.id_usuario_id:
                self.id_perfil_efectivo = self.id_sesion.id_usuario.perfil_efectivo
        super().save(*args, **kwargs)


class IntentoAcceso(LibroInmutable, models.Model):
    """Intento de login rechazado. Libro append-only."""

    MOTIVOS = (
        ('CLAVE_INCORRECTA', 'Clave incorrecta'),
        ('USUARIO_INEXISTENTE', 'Usuario inexistente'),
        ('USUARIO_INACTIVO', 'Usuario inactivo'),
        ('CUENTA_BLOQUEADA', 'Bloqueo por reintentos'),
    )

    id_intento = models.AutoField('ID', primary_key=True)
    id_usuario = models.ForeignKey(
        'seguridad.Usuario', on_delete=models.RESTRICT, db_column='id_usuario',
        null=True, blank=True, related_name='intentos', verbose_name='Usuario')
    nombre_usuario_ingresado = models.CharField(
        'Usuario tipeado', max_length=35, blank=True)
    ip_origen = models.CharField('IP de origen', max_length=45)
    fecha_hora = models.DateTimeField('Fecha y hora', default=timezone.now)
    motivo_rechazo = models.CharField(
        'Motivo', max_length=25, choices=MOTIVOS)

    # Libro de sólo agregado: los intentos rechazados son la base del
    # bloqueo progresivo, y un bloqueo que se pudiera borrar no protege.
    objects = ManagerDeLibro()

    class Meta:
        db_table = 'intentos_acceso'
        verbose_name = 'Intento de acceso'
        verbose_name_plural = 'Intentos de acceso'
        ordering = ['-fecha_hora']
        indexes = [
            models.Index(fields=['ip_origen', 'fecha_hora'],
                         name='ix_intentos_ip_fecha'),
        ]
        constraints = [
            CheckEnBase(
                check=CheckSQL(
                    "motivo_rechazo IN ('CLAVE_INCORRECTA','USUARIO_INEXISTENTE',"
                    "'USUARIO_INACTIVO','CUENTA_BLOQUEADA')"),
                name='ck_intentos_motivo'),
            # Inexistente => sin usuario; clave mala o inactivo => con
            # usuario. El bloqueo puede caer sobre cualquiera de los dos.
            CheckEnBase(
                check=CheckSQL(
                    "(motivo_rechazo <> 'USUARIO_INEXISTENTE' OR id_usuario IS NULL)"
                    " AND (motivo_rechazo NOT IN ('CLAVE_INCORRECTA','USUARIO_INACTIVO')"
                    " OR id_usuario IS NOT NULL)"),
                name='ck_intentos_usuario'),
            CheckEnBase(
                check=CheckSQL(
                    "trim(ip_origen) <> '' AND trim(motivo_rechazo) <> ''"),
                name='ck_intentos_acceso_textos'),
        ]

    def __str__(self):
        return f'{self.nombre_usuario_ingresado} · {self.motivo_rechazo}'
