"""Modelos de Seguridad y Usuarios del sistema Cafeto.

Cuatro tablas del DER de alto refinamiento:

    PERFILES            un rol (Administrador, Operador...)
    PERMISOS            una acción concreta que se puede habilitar
    PERFILES_PERMISOS   tabla intermedia: qué permisos tiene cada perfil
    USUARIOS            la persona que entra al sistema

El USUARIO de este módulo es propio: NO usamos el modelo User de Django.
La clave se guarda siempre **hasheada** con las funciones de
``django.contrib.auth.hashers``.

Alineación con la base (Fase 4)
-------------------------------
Las tablas y las columnas se llaman EXACTAMENTE como en
``04 - Base de Datos/01_esquema.sql``: ``perfiles.nombre_perfil``,
``usuarios.contrasena_hash``, etc. Los atributos de Python conservan los
nombres cortos que ya usaban las vistas y los templates (``perfil.nombre``,
``usuario.clave``) y se mapean con ``db_column``. Así el esquema físico es
el del DER y el código existente sigue andando.

Tres convenciones que valen para todo el sistema:

  * La PK de cada tabla es un AutoField propio (``id_usuario``,
    ``id_perfil``...). El DNI es un dato único más.
  * ``usuario_modificacion`` guarda el último que tocó la fila. Es un
    entero SIN clave foránea (DD-17: el sello no se traza), por eso va con
    ``db_constraint=False``.
  * El borrado es LÓGICO. En las maestras se marca con ``borrado_logico``
    (True = eliminado); en USUARIOS, con ``activo`` (False = dado de baja),
    que es como lo escribe el DER.
"""
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from cafeto import contexto
from cafeto.reglas import ManagerSinBajaFisica, SinBajaFisica
from cafeto.restricciones import CheckEnBase, CheckSQL, ClaveNormalizada


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


def sello_usuario_modificacion(related_name):
    """Campo ``usuario_modificacion`` tal como lo define el DER (DD-17).

    Es un INTEGER suelto en la base: NO lleva clave foránea, porque el
    sello no se traza y no tiene que frenar nada. Del lado de Django lo
    declaramos igual como ForeignKey —para poder escribir
    ``fila.usuario_modificacion.nombre``— pero con ``db_constraint=False``,
    que le dice a Django que no cree la restricción en PostgreSQL.
    """
    return models.ForeignKey(
        'usuarios.Usuario',
        db_column='usuario_modificacion',
        db_constraint=False,
        on_delete=models.DO_NOTHING,
        null=True, blank=True, editable=False,
        related_name=related_name,
        verbose_name='Modificado por',
    )


def sello_fecha_modificacion():
    """``fecha_modificacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP``.

    No lleva ``auto_now``: en la base, el trigger ``trg_sello_modificacion``
    es el que pisa la fecha en cada UPDATE (T01). Django sólo aporta el
    valor del alta.
    """
    return models.DateTimeField(
        'Última modificación', default=timezone.now, editable=False)


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
            # Dos perfiles no pueden llamarse igual salvo por mayúsculas o
            # acentos (DD-50): el índice compara la clave normalizada.
            models.UniqueConstraint(
                ClaveNormalizada('nombre'),
                name='uq_perfiles_nombre_norm'),
        ]

    def __str__(self):
        return self.nombre

    # ------------------------------------------------------------------
    #  Catálogo completo para el Administrador
    # ------------------------------------------------------------------
    @classmethod
    def asegurar_catalogo_completo(cls):
        """Le asigna al Administrador todos los permisos del catálogo.

        Cada módulo nuevo agrega sus permisos por migración de datos. Si
        alguno quedara sin asignar, el Administrador no se bloquea
        —``Usuario.tiene_permiso`` lo deja pasar siempre—, pero la pantalla
        de Perfiles mostraría el tilde apagado y daría a entender que no lo
        tiene. Este método cierra esa brecha: deja la matriz de permisos
        diciendo la verdad.

        Devuelve cuántas asignaciones agregó.
        """
        administrador = cls.objects.filter(nombre='Administrador').first()
        if administrador is None:
            return 0

        ya_tiene = set(
            PerfilPermiso.objects
            .filter(perfil=administrador)
            .values_list('permiso_id', flat=True)
        )
        faltantes = Permiso.objects.exclude(pk__in=ya_tiene)
        PerfilPermiso.objects.bulk_create(
            [PerfilPermiso(perfil=administrador, permiso=permiso)
             for permiso in faltantes]
        )
        return len(faltantes)


# ---------------------------------------------------------------------------
#  PERMISOS
# ---------------------------------------------------------------------------
class ModuloPermiso(models.TextChoices):
    """Módulo del sistema al que pertenece un permiso.

    Agrupa el catálogo en pantalla. Los valores van en MAYÚSCULAS y sin
    acentos (DD-29).
    """
    USUARIOS = 'USUARIOS', 'Usuarios'
    PERFILES = 'PERFILES', 'Perfiles'
    PERMISOS = 'PERMISOS', 'Permisos'
    PROVEEDORES = 'PROVEEDORES', 'Proveedores'
    UBICACIONES = 'UBICACIONES', 'Ubicaciones'
    DATOS_MAESTROS = 'DATOS_MAESTROS', 'Datos maestros'
    GENERAL = 'GENERAL', 'General'


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
        ]

    def __str__(self):
        return self.nombre


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

    def delete(self, *args, **kwargs):
        """Un perfil asignable conserva al menos un permiso (DD-14).

        Reemplaza a ``fn_perfil_conserva_permiso``. Si el perfil entero se
        está borrando, la fila se va con él y no hay nada que custodiar: lo
        que se cuida es que no quede un perfil VIVO sin ningún permiso, que
        es un perfil que no deja hacer nada y que igual se puede asignar.
        """
        from usuarios.models import Perfil

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

    def save(self, *args, **kwargs):
        # Capitalización automática: "juan carlos" -> "Juan Carlos".
        # Evita registros desprolijos cargados en minúscula o en MAYÚSCULA.
        if self.apellido:
            self.apellido = self.apellido.strip().title()
        if self.nombre:
            self.nombre = self.nombre.strip().title()
        if self.dni_usuario:
            self.dni_usuario = str(self.dni_usuario).strip()
        if self.correo:
            self.correo = self.correo.strip().lower()

        if not self.nombre_usuario:
            self.nombre_usuario = self._generar_nombre_usuario()

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
            from operaciones.models import Sesion

            Sesion.objects.filter(id_usuario=self, activa=True).update(
                activa=False, fecha_cierre=timezone.now(),
                motivo_cierre='REVOCACION')

    # ------------------------------------------------------------------
    #  Permisos — "¿este usuario puede hacer X?"
    # ------------------------------------------------------------------
    def tiene_permiso(self, codigo_permiso):
        """True si el perfil del usuario tiene ese permiso.

        Regla especial: el perfil **Administrador** siempre pasa, tenga o
        no la fila cargada en PERFILES_PERMISOS. Así, si mañana se agrega
        un permiso nuevo y alguien se olvida de asignárselo, el admin no
        queda bloqueado.
        """
        if self.id_perfil.nombre == 'Administrador':
            return True
        return self.id_perfil.permisos.filter(codigo=codigo_permiso).exists()

    # ------------------------------------------------------------------
    #  Regla de negocio: "último administrador en pie"
    # ------------------------------------------------------------------
    @property
    def es_ultimo_administrador(self):
        """True si este usuario es el ÚNICO Administrador vigente que queda.

        El sistema tiene que conservar siempre al menos un administrador
        que pueda entrar: si no, nadie podría volver a dar de alta usuarios
        ni tocar perfiles.
        """
        if not self.activo:
            return False
        if self.id_perfil.nombre != 'Administrador':
            return False
        vigentes = Usuario.objects.filter(
            id_perfil__nombre='Administrador', activo=True).count()
        return vigentes <= 1

    # ------------------------------------------------------------------
    #  Manejo de la clave — SIEMPRE hasheada
    # ------------------------------------------------------------------
    def set_clave(self, clave_plana):
        """Guarda la clave hasheada (no la escribe nunca en texto plano)."""
        self.clave = make_password(clave_plana)
        self.debe_cambiar_clave = False

    def verificar_clave(self, clave_plana):
        """Devuelve True si la clave tipeada coincide con el hash guardado."""
        return check_password(clave_plana, self.clave)

    def restablecer_clave(self):
        """Genera una clave nueva al azar, la hashea y obliga a cambiarla.

        Devuelve la clave en texto plano UNA sola vez para poder
        comunicársela al usuario; después ya no se puede recuperar.
        """
        from django.utils.crypto import get_random_string

        nueva = get_random_string(10)
        self.clave = make_password(nueva)
        self.debe_cambiar_clave = True
        self.save(update_fields=['clave', 'debe_cambiar_clave',
                                 'fecha_ultima_modificacion'])
        return nueva

    def dar_de_baja(self):
        """Baja LÓGICA: no borra la fila, apaga 'activo'.

        Levanta ``ValidationError`` si el usuario es el último
        Administrador vigente del sistema (ver ``es_ultimo_administrador``).
        """
        if self.es_ultimo_administrador:
            raise ValidationError(
                'El sistema debe tener al menos un administrador activo.')
        self.activo = False
        self.fecha_baja = timezone.now()
        self.save(update_fields=['activo', 'fecha_baja',
                                 'fecha_ultima_modificacion'])

    def reactivar(self):
        self.activo = True
        self.fecha_baja = None
        self.save(update_fields=['activo', 'fecha_baja',
                                 'fecha_ultima_modificacion'])
