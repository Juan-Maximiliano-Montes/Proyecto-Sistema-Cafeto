"""Reglas de validación comunes a todos los CRUD (sección 4.4.V).

El principio es que cada regla se controla en TRES capas y que las tres
dicen lo mismo:

  1. el formulario, con un mensaje claro al lado del campo;
  2. el modelo (``clean()``), para cualquier otro camino de entrada
     (shell, /admin, importaciones);
  3. la base (CHECK, UNIQUE, FK, NOT NULL, triggers), que es la última
     barrera y la única que nadie puede saltear.

Este módulo es la capa 1 y 2: funciones puras, sin Django adentro salvo
``ValidationError``, para poder usarlas igual desde un form, desde un
modelo o desde un test.
"""
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator


# ---------------------------------------------------------------------------
#  V-G1 · LIMPIEZA DE TEXTO
# ---------------------------------------------------------------------------
# Caracteres de control (tabulación, salto de línea, etc.): no entran en un
# campo de una sola línea.
CONTROL = re.compile(r'[\x00-\x1f\x7f]')

# V-G3: símbolos que no tienen nada que hacer en un nombre ni en una
# descripción. Se listan explícitamente para poder nombrarlos en el error.
SIMBOLOS_PROHIBIDOS = '<>{}[]\\|^`'

# Lo que SÍ admite un nombre: letras (con acentos y ñ), números, espacio y
# la puntuación que de verdad aparece en un nombre comercial.
NOMBRE_VALIDO = re.compile(r"^[^\W_]|[\w .,\-'()/&%+ºª°]+$", re.UNICODE)


def limpiar_texto(valor):
    """Recorta y colapsa los espacios internos. Vacío -> ``None`` (V-G1).

    ``"  Café   de  especialidad "`` queda ``"Café de especialidad"``, y
    ``"   "`` queda ``None`` para que el campo opcional se guarde NULL en
    vez de cadena vacía.
    """
    if valor is None:
        return None
    texto = re.sub(r'\s+', ' ', str(valor)).strip()
    return texto or None


def exigir_texto(valor, etiqueta='Este dato'):
    """Igual que ``limpiar_texto`` pero el vacío es un error (V-G1)."""
    texto = limpiar_texto(valor)
    if texto is None:
        raise ValidationError(f'{etiqueta} es obligatorio.')
    return texto


def validar_largo(valor, minimo, maximo, etiqueta='Este dato'):
    """Mínimo y máximo de un texto ya limpio (V-G2)."""
    largo = len(valor)
    if largo < minimo:
        raise ValidationError(
            f'{etiqueta} debe tener al menos {minimo} caracteres '
            f'(escribiste {largo}).')
    if largo > maximo:
        raise ValidationError(
            f'{etiqueta} no puede superar los {maximo} caracteres '
            f'(escribiste {largo}).')
    return valor


def validar_caracteres(valor, etiqueta='Este dato'):
    """Rechaza caracteres de control y los símbolos prohibidos (V-G3)."""
    if CONTROL.search(valor):
        raise ValidationError(
            f'{etiqueta} no puede contener saltos de línea ni tabulaciones.')
    usados = sorted({c for c in valor if c in SIMBOLOS_PROHIBIDOS})
    if usados:
        raise ValidationError(
            f'{etiqueta} no admite los símbolos {" ".join(usados)}.')
    return valor


def texto_de_catalogo(valor, minimo, maximo, etiqueta='Este dato',
                      obligatorio=True):
    """Las tres reglas generales de un nombre, en el orden correcto.

    Controla los caracteres ANTES de limpiar (V-G3), limpia (V-G1) y
    controla el largo (V-G2). El orden importa: ``limpiar_texto`` colapsa
    los saltos de línea en espacios, así que si se validara después, un
    texto pegado desde Word pasaría sin que nadie se entere de que traía
    saltos de línea.

    Devuelve ``None`` cuando el campo es opcional y vino vacío.
    """
    if valor is not None:
        validar_caracteres(str(valor), etiqueta)
    texto = limpiar_texto(valor)
    if texto is None:
        if obligatorio:
            raise ValidationError(f'{etiqueta} es obligatorio.')
        return None
    validar_largo(texto, minimo, maximo, etiqueta)
    return texto


# ---------------------------------------------------------------------------
#  V-G4 · NÚMEROS
# ---------------------------------------------------------------------------
def validar_decimal(valor, minimo=None, maximo=None, decimales=2,
                    etiqueta='El importe', incluye_minimo=True):
    """Controla un ``Decimal``: finito, en rango y sin decimales de más.

    Rechaza NaN e Infinity (que ``Decimal`` acepta sin chistar y la base
    no) y la notación científica, que es siempre un error de tipeo en un
    importe.
    """
    if valor is None:
        raise ValidationError(f'{etiqueta} es obligatorio.')
    try:
        numero = Decimal(valor)
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f'{etiqueta} tiene que ser un número.')
    if not numero.is_finite():
        raise ValidationError(f'{etiqueta} tiene que ser un número finito.')
    if numero.as_tuple().exponent < -decimales:
        raise ValidationError(
            f'{etiqueta} admite como mucho {decimales} decimales.')
    if minimo is not None:
        if incluye_minimo and numero < minimo:
            raise ValidationError(f'{etiqueta} no puede ser menor que {minimo}.')
        if not incluye_minimo and numero <= minimo:
            raise ValidationError(f'{etiqueta} tiene que ser mayor que {minimo}.')
    if maximo is not None and numero > maximo:
        raise ValidationError(f'{etiqueta} no puede ser mayor que {maximo}.')
    return numero


def validar_entero(valor, minimo=None, maximo=None, etiqueta='El valor'):
    """Controla un entero en rango (V-G4)."""
    if valor is None:
        raise ValidationError(f'{etiqueta} es obligatorio.')
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        raise ValidationError(f'{etiqueta} tiene que ser un número entero.')
    if minimo is not None and numero < minimo:
        raise ValidationError(f'{etiqueta} no puede ser menor que {minimo}.')
    if maximo is not None and numero > maximo:
        raise ValidationError(f'{etiqueta} no puede ser mayor que {maximo}.')
    return numero


# ---------------------------------------------------------------------------
#  V-G5 · FECHAS
# ---------------------------------------------------------------------------
ANIO_MINIMO = 2000
ANIO_MAXIMO = 2100


def validar_anio_de_negocio(fecha, etiqueta='La fecha'):
    """El año tiene que ser verosímil: entre 2000 y 2100 (V-G5).

    Ataja los "0202" y los "20266" que salen de un tipeo apurado y que una
    validación de formato sola deja pasar.
    """
    if fecha is None:
        return None
    if not (ANIO_MINIMO <= fecha.year <= ANIO_MAXIMO):
        raise ValidationError(
            f'{etiqueta} tiene que estar entre el año {ANIO_MINIMO} y el '
            f'{ANIO_MAXIMO}; escribiste el año {fecha.year}.')
    return fecha


def validar_no_futura(fecha, hoy, etiqueta='La fecha'):
    """Una fecha de algo YA OCURRIDO no puede estar en el futuro (V-G5)."""
    if fecha is None:
        return None
    if fecha > hoy:
        raise ValidationError(f'{etiqueta} no puede ser posterior a hoy.')
    return fecha


def validar_orden_de_fechas(desde, hasta, etiqueta_desde='La fecha de inicio',
                            etiqueta_hasta='La fecha de fin'):
    """El fin nunca es anterior al inicio (V-G5)."""
    if desde is not None and hasta is not None and hasta < desde:
        raise ValidationError(
            f'{etiqueta_hasta} no puede ser anterior a {etiqueta_desde.lower()}.')
    return hasta


# ---------------------------------------------------------------------------
#  V-G8 · CLAVE NORMALIZADA (DD-50)
# ---------------------------------------------------------------------------
def clave_normalizada(texto):
    """Misma normalización que ``f_clave_normalizada`` en PostgreSQL.

    Minúsculas, sin acentos y con los espacios internos colapsados. Sirve
    para encontrar el duplicado ANTES de que lo frene el índice único y
    poder dar un mensaje entendible (o el botón "Reactivar" de D-10).
    """
    if texto is None:
        return None
    un_espacio = re.sub(r'\s+', ' ', str(texto).strip())
    sin_tildes = ''.join(
        c for c in unicodedata.normalize('NFKD', un_espacio)
        if not unicodedata.combining(c)
    )
    return sin_tildes.lower()


# ---------------------------------------------------------------------------
#  CUIT  (V-E proveedores)
# ---------------------------------------------------------------------------
PREFIJOS_CUIT = ('20', '23', '24', '27', '30', '33', '34')
PESOS_CUIT = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


def normalizar_cuit(valor):
    """Deja el CUIT como ``NN-NNNNNNNN-N``, se haya tipeado como se haya.

    Se aceptan "30712345679", "30-71234567-9" y "30 71234567 9".
    """
    texto = limpiar_texto(valor)
    if texto is None:
        return None
    digitos = re.sub(r'\D', '', texto)
    if len(digitos) != 11:
        raise ValidationError(
            'El CUIT debe tener 11 dígitos, con el formato 30-71234567-9.')
    return f'{digitos[:2]}-{digitos[2:10]}-{digitos[10]}'


def validar_cuit(valor):
    """Prefijo válido y dígito verificador módulo 11 (algoritmo de ARCA).

    No alcanza con el formato: "30-11111111-1" tiene la pinta correcta y
    no existe. El verificador es lo que distingue un CUIT real de once
    dígitos cualquiera.
    """
    formateado = normalizar_cuit(valor)
    if formateado is None:
        return None
    digitos = re.sub(r'\D', '', formateado)

    if digitos[:2] not in PREFIJOS_CUIT:
        raise ValidationError(
            'El CUIT tiene que empezar con '
            f'{", ".join(PREFIJOS_CUIT[:-1])} o {PREFIJOS_CUIT[-1]}.')

    suma = sum(int(d) * p for d, p in zip(digitos[:10], PESOS_CUIT))
    resto = 11 - (suma % 11)
    if resto == 11:
        verificador = 0
    elif resto == 10:
        verificador = 9
    else:
        verificador = resto

    if verificador != int(digitos[10]):
        raise ValidationError(
            'El CUIT debe tener el formato 30-71234567-9 y un dígito '
            'verificador válido; el que escribiste no verifica.')
    return formateado


# ---------------------------------------------------------------------------
#  TELÉFONO  (V-E proveedores)
# ---------------------------------------------------------------------------
def normalizar_telefono(valor):
    """Deja sólo los dígitos del número, sin prefijos de marcado.

    El operador puede escribir "(0387) 412-3456", "+54 387 4123456" o
    "0387 15 4123456": a la base entra siempre la misma cadena de dígitos.
    Se descartan el prefijo de país (+54), el 0 de larga distancia y el 15
    del celular, que no forman parte del número.
    """
    texto = limpiar_texto(valor)
    if texto is None:
        return None

    # Prefijo de país: "+54 387..." / "0054 387..."
    texto = re.sub(r'^\+?(?:00)?54\s*', '', texto)
    digitos = re.sub(r'\D', '', texto)
    digitos = digitos.lstrip('0')             # 0 de larga distancia

    # El 15 del celular va pegado al número local, nunca al principio del
    # área: sólo se saca si el número queda largo de más sin él.
    if len(digitos) > 10 and digitos[3:5] == '15':
        digitos = digitos[:3] + digitos[5:]

    return digitos or None


def validar_telefono(valor):
    """7 a 15 dígitos una vez normalizado (V-E)."""
    digitos = normalizar_telefono(valor)
    if digitos is None:
        return None
    if not 7 <= len(digitos) <= 15:
        raise ValidationError(
            'El teléfono debe tener entre 7 y 15 dígitos; el que escribiste '
            f'quedó en {len(digitos)}.')
    return digitos


# ---------------------------------------------------------------------------
#  CÓDIGO DE BARRAS GS1  (V-E productos)
# ---------------------------------------------------------------------------
LARGOS_GS1 = (8, 12, 13, 14)      # EAN-8, UPC-A, EAN-13, GTIN-14


def validar_codigo_barras(valor):
    """Largo GS1 válido y dígito verificador correcto.

    El verificador GS1 se calcula sobre todos los dígitos menos el último:
    desde la derecha se multiplican alternadamente por 3 y por 1, se suman,
    y el verificador es lo que falta para la decena siguiente.
    """
    texto = limpiar_texto(valor)
    if texto is None:
        return None
    digitos = texto.replace(' ', '').replace('-', '')

    if not digitos.isdigit():
        raise ValidationError('El código de barras sólo admite dígitos.')
    if len(digitos) not in LARGOS_GS1:
        raise ValidationError(
            'El código de barras debe tener 8, 12, 13 o 14 dígitos '
            f'(EAN-8, UPC-A, EAN-13 o GTIN-14); escribiste {len(digitos)}.')

    cuerpo, verificador = digitos[:-1], int(digitos[-1])
    suma = 0
    for posicion, caracter in enumerate(reversed(cuerpo)):
        suma += int(caracter) * (3 if posicion % 2 == 0 else 1)
    esperado = (10 - suma % 10) % 10
    if esperado != verificador:
        raise ValidationError(
            'El código de barras no verifica: el último dígito debería ser '
            f'{esperado}. Revisá que esté bien copiado.')
    return digitos


# ---------------------------------------------------------------------------
#  CÓDIGO POSTAL  (V-E localidades)
# ---------------------------------------------------------------------------
CPA = re.compile(r'^[A-Z]\d{4}[A-Z]{3}$')


def validar_codigo_postal(valor):
    """Código postal clásico de 4 dígitos o CPA de 8 (ej. A4400XAB)."""
    texto = limpiar_texto(valor)
    if texto is None:
        return None
    texto = texto.replace(' ', '').upper()
    if re.fullmatch(r'\d{4}', texto):
        return texto
    if CPA.fullmatch(texto):
        return texto
    raise ValidationError(
        'El código postal debe ser de 4 dígitos (4400) o un CPA de 8 '
        'caracteres (A4400XAB).')


# ---------------------------------------------------------------------------
#  V-G7 · CLAVES FORÁNEAS
# ---------------------------------------------------------------------------
def exigir_vigente(objeto, etiqueta, campo_baja='borrado_logico'):
    """El registro referenciado tiene que existir y estar vigente (V-G7).

    Los combos ya muestran sólo los vigentes, pero un POST armado a mano
    puede mandar cualquier id: el servidor lo vuelve a controlar.
    """
    if objeto is None:
        raise ValidationError(f'{etiqueta} es obligatorio.')
    if getattr(objeto, campo_baja, False):
        raise ValidationError(
            f'{etiqueta} "{objeto}" está dado de baja: elegí uno vigente.')
    return objeto


# ---------------------------------------------------------------------------
#  V-G15 · VALIDADORES NATIVOS DE DJANGO
# ---------------------------------------------------------------------------
# Las funciones de arriba son la capa 2 (el modelo). Estos validadores son la
# capa 1 y viajan PEGADOS AL CAMPO: Django los corre solo en el formulario, en
# el admin y en cualquier `full_clean()`, y además los deja escritos en la
# migración, así que quedan documentados en el historial del esquema.
#
# Qué rechazan y qué no.
#
# Rechazan LETRAS, que es la clase de error que ninguna normalización puede
# arreglar: "3875A51234" no es un teléfono mal escrito, es otra cosa. En
# cambio TOLERAN los separadores que la gente efectivamente tipea —el CUIT
# con guiones, el DNI con puntos, el teléfono con espacios— porque
# `normalizar_cuit()` y `normalizar_telefono()` los sacan un paso después, y
# porque la forma guardada ya la garantizan los CHECK de la base
# (`ck_clientes_dni`, `ck_proveedores_cuit`).
#
# No es una concesión de comodidad. Un validador de campo corre dentro de
# `clean_fields()`, y `clean_fields()` corre ANTES que `clean()`. Si el
# patrón exigiera acá los 11 dígitos pelados, le ganaría de mano a la
# normalización del propio modelo y rechazaría un CUIT correcto nada más que
# por venir con los guiones de siempre.
#
# El `$` de cada patrón es `\Z` y no `$` a secas: en Python `$` también casa
# justo antes de un salto de línea final, y "12345678\n" pasaría por DNI.
VALIDADOR_DNI = RegexValidator(
    regex=r'^[\d.\s]{7,10}\Z',
    message='El DNI son 7 u 8 dígitos. No puede llevar letras.')

VALIDADOR_TELEFONO = RegexValidator(
    regex=r'^[\d\s().+-]{7,20}\Z',
    message='El teléfono son entre 7 y 15 dígitos. No puede llevar letras.')

VALIDADOR_CUIT = RegexValidator(
    regex=r'^[\d\s.-]{11,15}\Z',
    message='El CUIT son 11 dígitos (30-71234567-1). No puede llevar letras.')

# Para precios, importes y cantidades. Lo negativo nunca es un dato válido
# en este sistema: un descuento se carga como descuento, no como precio en
# menos.
NO_NEGATIVO = MinValueValidator(
    0, message='No puede ser un número negativo.')


# ---------------------------------------------------------------------------
#  V-G16 · EDAD A PARTIR DE LA FECHA DE NACIMIENTO
# ---------------------------------------------------------------------------
def anios_cumplidos(nacimiento, hoy):
    """Edad en años cumplidos, contando bien el día del cumpleaños."""
    cumplio = (hoy.month, hoy.day) >= (nacimiento.month, nacimiento.day)
    return hoy.year - nacimiento.year - (0 if cumplio else 1)


def validar_edad(nacimiento, hoy, minimo=14, maximo=100,
                 etiqueta='La fecha de nacimiento'):
    """La persona tiene entre ``minimo`` y ``maximo`` años (V-G16).

    Esta regla NO puede ser un CHECK: se mide contra la fecha de hoy, y un
    CHECK se evalúa una sola vez, al grabar. Escrito en la base, el mismo
    dato pasaría a estar mal sólo porque la persona cumplió años. La base
    guarda una cota fija (``ck_clientes_nacimiento``, no antes de 1900) y
    la regla viva se controla acá y en el formulario.

    El piso de 14 años cubre de paso la fecha futura: nadie nace mañana.
    """
    if nacimiento is None:
        return None
    if nacimiento > hoy:
        raise ValidationError(f'{etiqueta} no puede ser posterior a hoy.')
    edad = anios_cumplidos(nacimiento, hoy)
    if edad < minimo:
        raise ValidationError(
            f'{etiqueta} da {edad} año{"s" if edad != 1 else ""}: el programa '
            f'de fidelización es desde los {minimo}.')
    if edad > maximo:
        raise ValidationError(
            f'{etiqueta} da {edad} años. Revisá el año: el máximo que acepta '
            f'el sistema es {maximo}.')
    return nacimiento


# ---------------------------------------------------------------------------
#  V-G17 · EL DUPLICADO QUE ESTÁ DADO DE BAJA
# ---------------------------------------------------------------------------
def exigir_que_no_este_dado_de_baja(modelo, instancia, filtros,
                                    etiqueta='Este registro'):
    """Corta el duplicado contra una fila dada de baja, antes de PostgreSQL.

    Un índice único no distingue vigentes de dados de baja: si alguien
    carga de nuevo un cliente que se había dado de baja, la base devuelve
    IntegrityError y el usuario ve un error 500 que no le dice nada.

    Acá se busca primero y se responde con la indicación útil, que es la
    única acción que resuelve el caso: reactivar la fila que ya está.

    ``filtros`` es el diccionario que identifica al duplicado (``{'dni_cliente':
    '40125874'}``). Se excluye la propia fila para no trabarse al editar.
    """
    if not filtros or any(valor in (None, '') for valor in filtros.values()):
        return None
    candidatos = modelo.objects.filter(**filtros)
    if instancia.pk is not None:
        candidatos = candidatos.exclude(pk=instancia.pk)
    duplicado = candidatos.first()
    if duplicado is None:
        return None
    if getattr(duplicado, 'borrado_logico', False):
        raise ValidationError(
            f'{etiqueta} fue dado de baja. Presione el botón Reactivar en la '
            f'lista: así se recupera «{duplicado}» con su historial, en vez '
            'de crear un registro nuevo.')
    raise ValidationError(f'{etiqueta} ya existe: «{duplicado}».')
