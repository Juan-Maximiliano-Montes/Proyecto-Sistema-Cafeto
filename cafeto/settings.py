"""Configuración del sistema Cafeto.

Todo lo que cambia entre la compu de desarrollo y el NAS del local sale
de variables de entorno (docker-compose las toma del archivo .env). Los
valores por defecto son los de DESARROLLO; en producción
(``DJANGO_DEBUG=False``) el sistema se niega a arrancar si falta algo
crítico, en lugar de arrancar inseguro.

Referencia: https://docs.djangoproject.com/en/5.2/ref/settings/
y el checklist de despliegue: ``python manage.py check --deploy``.
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool(nombre, defecto):
    valor = os.environ.get(nombre)
    if valor is None:
        return defecto
    return valor.strip().lower() in ('1', 'true', 'si', 'sí', 'yes', 'on')


def _lista(nombre, defecto=''):
    return [v.strip() for v in os.environ.get(nombre, defecto).split(',')
            if v.strip()]


DEBUG = _bool('DJANGO_DEBUG', True)

# La clave secreta firma las cookies de sesión y los tokens CSRF. En
# producción es obligatoria y no puede ser la de ejemplo.
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', '')
if not SECRET_KEY or 'insecure' in SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured(
            'Falta DJANGO_SECRET_KEY (o es la de ejemplo). Generá una con: '
            'python -c "import secrets; print(secrets.token_urlsafe(50))"')
    SECRET_KEY = SECRET_KEY or 'django-insecure-solo-para-desarrollo-local'

# Los nombres con los que se entra al sistema (la IP del NAS, su nombre en
# la red del local, el dominio del acceso remoto). En producción no hay
# comodín: un Host desconocido se rechaza.
ALLOWED_HOSTS = _lista('DJANGO_ALLOWED_HOSTS', '*' if DEBUG else '')
if not DEBUG and (not ALLOWED_HOSTS or '*' in ALLOWED_HOSTS):
    raise ImproperlyConfigured(
        'En producción DJANGO_ALLOWED_HOSTS tiene que listar los nombres '
        'reales (por ejemplo: 192.168.0.10,cafeto.local).')

# Orígenes desde los que se aceptan formularios (con esquema):
# https://cafeto.midominio.com.ar
CSRF_TRUSTED_ORIGINS = _lista('DJANGO_CSRF_TRUSTED_ORIGINS') + ['https://*.trycloudflare.com', 'http://localhost:8000', 'http://127.0.0.1:8000']
CONFIAR_EN_PROXY = True
# Con el proxy de confianza, la IP del cliente (registro de actividad,
# bloqueo por intentos) sale primero de CF-Connecting-IP: la pone el
# túnel de Cloudflare y quien entra no la puede elegir. Si no viene, de
# X-Forwarded-For contando PROXIES_DE_CONFIANZA saltos desde la derecha
# (la primera de la izquierda la escribe cualquiera).
ENCABEZADO_IP_CLIENTE = os.environ.get('CAFETO_ENCABEZADO_IP', 'HTTP_CF_CONNECTING_IP')
PROXIES_DE_CONFIANZA = max(1, int(os.environ.get('CAFETO_PROXIES_DE_CONFIANZA', '1')))
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
# ¿Se entra por HTTPS? En el local puede ser HTTP plano dentro de la LAN;
# el acceso remoto, siempre HTTPS (o por VPN).
USAR_HTTPS = _bool('DJANGO_HTTPS', False)


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Módulos del sistema, uno por área del DER. `nucleo` va primero: sus
    # migraciones crean la extensión unaccent y f_clave_normalizada, de las
    # que dependen las restricciones de todos los demás.
    'nucleo',
    'seguridad',
    'geografia',
    'negocio',
    'stock',
    'ventas',
    'compras',
    'fidelizacion',
    # La API de la app móvil (/api/movil/): no tiene tablas propias, usa
    # las de los módulos de arriba con sus mismas reglas.
    'api_movil',
    'api_servicio',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # Sirve /static/ desde el mismo proceso (gunicorn en el NAS), antes que
    # cualquier middleware de sesión: un CSS no necesita estar logueado.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Interceptor de seguridad del Hito 2 (login + cambio de clave obligatorio).
    # Va AL FINAL: necesita que la sesión ya esté cargada.
    'seguridad.middleware.SeguridadMiddleware',
    # Publica el usuario logueado en el contexto del pedido, para que
    # `save()` pueda sellar `usuario_modificacion` sin recibir el request.
    # Va DESPUÉS de SeguridadMiddleware, que es el que deja
    # 'request.usuario_actual'.
    'seguridad.middleware_contexto.ContextoDeUsuarioMiddleware',
    # Lo que una vista no atrapó y es de negocio (ValidationError,
    # IntegrityError, ProtectedError, DecimalException) se muestra como
    # aviso en castellano, nunca como traceback (DD-100).
    'nucleo.errores.ExcepcionesDeNegocioMiddleware',
]

# El formulario vencido (CSRF) también con la página de Cafeto.
CSRF_FAILURE_VIEW = 'nucleo.errores.csrf_fallo'
# En modo DEBUG la página 404 también es la de Cafeto; para ver la página
# técnica de Django (con las rutas probadas): CAFETO_404_DE_DJANGO=1.
CAFETO_404_DE_DJANGO = os.environ.get('CAFETO_404_DE_DJANGO', '') == '1'

# A dónde manda Django cuando falta iniciar sesión.
LOGIN_URL = 'seguridad:login'


# ---------------------------------------------------------------------------
#  SESIONES Y COOKIES
# ---------------------------------------------------------------------------
# La sesión de negocio (tabla SESIONES) es la que manda: vence a las
# SESION_DURACION_HORAS desde el ingreso, o tras SESION_INACTIVIDAD_MINUTOS
# sin uso. La cookie de Django dura lo mismo, para no sobrevivirla.
SESION_DURACION_HORAS = int(os.environ.get('CAFETO_SESION_HORAS', '12'))
SESION_INACTIVIDAD_MINUTOS = int(
    os.environ.get('CAFETO_INACTIVIDAD_MINUTOS', '60'))
SESSION_COOKIE_AGE = SESION_DURACION_HORAS * 3600
SESSION_COOKIE_HTTPONLY = True          # el JavaScript no la puede leer
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = USAR_HTTPS      # sólo viaja por HTTPS
SESSION_COOKIE_NAME = 'cafeto_sesion'
CSRF_COOKIE_SECURE = USAR_HTTPS
CSRF_COOKIE_SAMESITE = 'Lax'

# ---------------------------------------------------------------------------
#  ENCABEZADOS DE SEGURIDAD
# ---------------------------------------------------------------------------
X_FRAME_OPTIONS = 'DENY'                # nadie nos mete en un iframe
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'
if USAR_HTTPS:
    SECURE_SSL_REDIRECT = _bool('DJANGO_REDIRIGIR_A_HTTPS', True)
    SECURE_HSTS_SECONDS = int(os.environ.get('DJANGO_HSTS_SEGUNDOS', '3600'))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False

# Dos avisos de ``check --deploy`` que se apagan a sabiendas: el HSTS no se
# extiende a subdominios ni se inscribe en la lista "preload" de los
# navegadores porque el dominio del acceso remoto puede ser compartido o
# cambiar, y esas dos marcas no se pueden deshacer rápido.
SILENCED_SYSTEM_CHECKS = ['security.W005', 'security.W021']

ROOT_URLCONF = 'cafeto.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                # Deja 'permisos_usuario' (set de códigos) en cada template
                # para poder ocultar botones según el perfil logueado.
                'seguridad.context_processors.permisos_del_usuario',
                # Menú de la barra de navegación, agrupado por área de
                # trabajo y filtrado por los permisos del perfil.
                'seguridad.context_processors.menu_del_sistema',
                # La caja en la que opera este dispositivo y si tiene turno.
                'ventas.context_processors.caja_del_puesto',
            ],
        },
    },
]

WSGI_APPLICATION = 'cafeto.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'cafeto_db'),
        'USER': os.environ.get('DB_USER', 'cafeto_user'),
        'PASSWORD': os.environ.get('DB_PASSWORD', 'cafeto_pass'),
        'HOST': os.environ.get('DB_HOST', 'db'),
        'PORT': os.environ.get('DB_PORT', '5432'),
        # Cada request corre dentro de UNA transacción. Es lo que permite que
        # el middleware use SET LOCAL (que sólo vive dentro de la transacción)
        # y que un alta con su auditoría se confirme o se descarte junta.
        'ATOMIC_REQUESTS': True,
    }
}


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

# Reglas de la clave de cada usuario (las aplica CambioClaveForm). El
# usuario del sistema es propio, así que se le dice al validador de
# parecido con qué campos comparar.
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
        'OPTIONS': {'user_attributes': ('nombre_usuario', 'nombre',
                                        'apellido', 'correo', 'dni_usuario')},
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 8},
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'es-ar'

TIME_ZONE = 'America/Argentina/Buenos_Aires'

USE_I18N = True

# Los formatos argentinos (fecha dd/mm/aaaa, coma decimal) salen del
# LANGUAGE_CODE: desde Django 5.0 la localización está siempre activa.

USE_TZ = True

# Los importes se tipean "1500,50" y se muestran con separador de miles.
USE_THOUSAND_SEPARATOR = True

# Formatos que acepta un campo de fecha, en orden de preferencia.
DATE_INPUT_FORMATS = ['%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y']


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = 'static/'
# Adonde junta todo ``collectstatic`` en el contenedor. Bootstrap, los
# íconos, las fuentes y los logos viven en nucleo/static/cafeto/: el
# sistema corre en el NAS del local y tiene que verse igual sin internet.
STATIC_ROOT = BASE_DIR / 'estaticos'

# Lo que suben los usuarios: hoy, las fotos de los productos (DD-83). Va
# fuera de la base y fuera de la imagen de Docker: en un volumen, para que
# no se pierda al reconstruir el contenedor.
MEDIA_URL = '/media/'
MEDIA_ROOT = Path(os.environ.get('CAFETO_MEDIA_ROOT') or BASE_DIR / 'media')
# Una foto de celular pasa de los 2,5 MB que Django guarda en memoria.
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
# La dirección con la que se llega al sistema desde afuera, sin la barra
# final (https://cafeto.ejemplo.com). La usa la app para las fotos subidas.
CAFETO_URL_PUBLICA = os.environ.get('CAFETO_URL_PUBLICA', '').rstrip('/')

# En producción los archivos van comprimidos y con un hash en el nombre
# (cafeto.3f2a1b.css): el navegador los guarda para siempre y, cuando
# cambian, el nombre cambia solo. Necesita haber corrido collectstatic,
# así que en desarrollo se usa el almacenamiento común.
# En desarrollo no hay collectstatic: whitenoise busca en las apps y no
# avisa que falta la carpeta de recolección.
WHITENOISE_USE_FINDERS = DEBUG
WHITENOISE_AUTOREFRESH = DEBUG
import warnings  # noqa: E402
warnings.filterwarnings('ignore', message='No directory at', module='whitenoise.base')

ESTATICOS_CON_HASH = _bool('CAFETO_ESTATICOS_CON_HASH', not DEBUG)
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {
        'BACKEND': ('whitenoise.storage.CompressedManifestStaticFilesStorage'
                    if ESTATICOS_CON_HASH else
                    'django.contrib.staticfiles.storage.StaticFilesStorage'),
    },
}

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Los mensajes de error de Django se llaman 'error', pero Bootstrap
# usa la clase 'danger'. Esto los traduce.
from django.contrib.messages import constants as messages

MESSAGE_TAGS = {
    messages.ERROR: 'danger',
}
# Los avisos viajan en la sesión (en la base), no en una cookie. Con la
# cookie, el texto del aviso —por ejemplo, la clave provisoria que muestra
# "Restablecer clave"— quedaba en el navegador en base64, firmado pero
# legible, hasta que se mostraba.
MESSAGE_STORAGE = 'django.contrib.messages.storage.session.SessionStorage'

# ---------------------------------------------------------------------------
#  Correo: enlaces para elegir o recuperar la clave (seguridad/recuperacion.py)
# ---------------------------------------------------------------------------
# En desarrollo cada correo se guarda como archivo en .correos/ (no sale a
# internet): se abre con cualquier editor para copiar el enlace. En el local
# se usa un servidor SMTP real; los datos van en el .env (ver .env.example).
#
# Cada dato se puede dar con el nombre nuevo (CAFETO_SMTP_*) o con el
# anterior (DJANGO_CORREO_*); si están los dos, manda el nuevo. Sirve
# cualquier SMTP: Resend (smtp.resend.com, usuario "resend", la clave es la
# API key), Gmail con contraseña de aplicación, el del proveedor de internet.
def _correo(nuevo, anterior, por_defecto=''):
    return os.environ.get(nuevo) or os.environ.get(anterior) or por_defecto


if _correo('CAFETO_SMTP_HOST', 'DJANGO_CORREO_SERVIDOR'):
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = _correo('CAFETO_SMTP_HOST', 'DJANGO_CORREO_SERVIDOR')
    EMAIL_PORT = int(_correo('CAFETO_SMTP_PORT', 'DJANGO_CORREO_PUERTO', '587'))
    EMAIL_HOST_USER = _correo('CAFETO_SMTP_USER', 'DJANGO_CORREO_USUARIO')
    EMAIL_HOST_PASSWORD = _correo('CAFETO_SMTP_PASSWORD', 'DJANGO_CORREO_CLAVE')
    # El 465 habla SSL desde el primer byte; el 587 arranca en claro y pasa
    # a TLS (STARTTLS). Django no admite las dos cosas a la vez.
    EMAIL_USE_SSL = EMAIL_PORT == 465
    EMAIL_USE_TLS = EMAIL_PORT == 587  # <--- Forzamos TLS explícito para el puerto 587
    EMAIL_TIMEOUT = 15
elif DEBUG:
    EMAIL_BACKEND = 'django.core.mail.backends.filebased.EmailBackend'
    EMAIL_FILE_PATH = BASE_DIR / '.correos'
else:
    # Sin servidor configurado en producción: el envío falla y el sistema
    # ofrece el camino en persona (clave provisoria en pantalla).
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = 'correo-no-configurado.invalid'
    EMAIL_TIMEOUT = 5
DEFAULT_FROM_EMAIL = _correo(
    'CAFETO_DEFAULT_FROM_EMAIL', 'DJANGO_CORREO_REMITENTE',
    'Sistema Cafeto <no-responder@cafeto.local>')

# ---------------------------------------------------------------------------
#  API de servicio para automatizaciones (api_servicio/, DD-84)
# ---------------------------------------------------------------------------
# Vacía = la API no existe (responde 503). Para habilitarla, una credencial
# larga y al azar:  python -c "import secrets; print(secrets.token_urlsafe(48))"
CAFETO_N8N_TOKEN = os.environ.get('CAFETO_N8N_TOKEN', '')
# Adónde avisa el sistema cuando la caja pide una autorización a distancia
# (DD-94): el webhook de n8n que la manda por WhatsApp a la titular.
# Vacío = sólo el correo. Dentro de Docker: http://n8n:5678/webhook/cafeto-autorizacion
CAFETO_N8N_WEBHOOK_AUTORIZACION = os.environ.get('CAFETO_N8N_WEBHOOK_AUTORIZACION', '')
# Adónde manda el sistema la foto o el PDF de una factura para que n8n la
# lea (DD-98). Vacío = el lector local (texto, PDF y, si el equipo tiene
# Tesseract, fotos). Dentro de Docker: http://n8n:5678/webhook/cafeto-leer-factura
CAFETO_N8N_WEBHOOK_FACTURA = os.environ.get('CAFETO_N8N_WEBHOOK_FACTURA', '')

# ---------------------------------------------------------------------------
#  App móvil: las cuentas de los clientes (api_movil/firebase.py)
# ---------------------------------------------------------------------------
# Ruta, DENTRO del contenedor, del archivo JSON de la cuenta de servicio de
# Firebase con la que se verifican los tokens de los clientes. Ese archivo
# es una credencial: nunca va en el repositorio. Vacío, los endpoints de
# /api/movil/clientes/ responden 503 y el resto de la API anda igual.
FIREBASE_CREDENCIAL = os.environ.get('CAFETO_FIREBASE_CREDENCIAL', '')
