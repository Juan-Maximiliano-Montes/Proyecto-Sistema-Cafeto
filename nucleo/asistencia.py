"""Ayudas mientras se carga un formulario de datos maestros (DD-70).

Dos cosas, las dos ANTES de apretar Guardar:

1. **Aviso de parecidos.** Al salir de un campo que identifica a la fila
   (el nombre de un producto, el CUIT de un proveedor, el DNI de un
   cliente), el navegador pregunta si ya hay algo igual o parecido y lo
   muestra al lado del campo. No bloquea: el bloqueo de un duplicado
   exacto ya lo hace el modelo al guardar (DD-50). Lo que se suma es
   enterarse antes, y también de los *parecidos* —"Leche" y "Leche
   entera", "Coca Cola" y "Coka Cola"—, que la base no puede rechazar
   porque son textos distintos.

2. **Alta rápida.** Al lado de un campo que apunta a otra tabla (la marca,
   el rubro, el proveedor de un producto) hay un "+" que despliega, en la
   misma pantalla, el alta de esa tabla. Al guardarla queda elegida en el
   campo y no se pierde lo que ya se había escrito. Es la misma alta de
   siempre: los mismos permisos, las mismas validaciones y el mismo asiento
   en el Registro de Actividad.

Por qué la similitud se calcula en Python y no con ``pg_trgm``: los
catálogos de un local tienen decenas o cientos de filas, y sumar una
extensión de PostgreSQL al despliegue no se justifica para eso.
"""
import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import urlencode

from django.db import models

# Desde cuánto se considera "parecido" (0 a 1). Calibrado con los casos
# de la prueba: "Coka Cola"/"Coca-Cola" 0,89; "Leche"/"Yerba" 0,2.
UMBRAL = 0.78
TOPE = 5


# ---------------------------------------------------------------------------
#  Comparación
# ---------------------------------------------------------------------------
def clave(texto):
    """Como ``f_clave_normalizada`` de la base: minúsculas, sin acentos, sin
    puntuación y con los espacios colapsados."""
    texto = unicodedata.normalize('NFKD', str(texto or ''))
    texto = ''.join(c for c in texto if not unicodedata.combining(c)).lower()
    return ' '.join(re.sub(r'[^0-9a-zñ]+', ' ', texto).split())


def puntaje(a, b):
    """Qué tan parecidos son dos textos ya pasados por ``clave``."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    pa, pb = set(a.split()), set(b.split())
    corto, largo = (pa, pb) if len(pa) <= len(pb) else (pb, pa)
    # "leche" dentro de "leche entera": una palabra de diferencia.
    if corto and corto <= largo and len(largo - corto) <= 1:
        return 0.9
    return max(SequenceMatcher(None, a, b).ratio(),
               len(pa & pb) / len(pa | pb))


# ---------------------------------------------------------------------------
#  Qué campos se vigilan
# ---------------------------------------------------------------------------
def campos_vigilados(configuracion):
    """``{campo: 'parecido' | 'exacto'}`` de la entidad.

    * el nombre (``campo_nombre``) se compara por parecido;
    * los campos únicos por sí solos (CUIT, DNI, código de barras, nombre
      de usuario) se comparan exacto: un DNI "parecido" no significa nada.
    """
    modelo = configuracion.modelo
    campos = {c.name: c for c in modelo._meta.concrete_fields}
    vigilados = {}
    if configuracion.campo_nombre in campos and configuracion.nombre_es_identidad:
        vigilados[configuracion.campo_nombre] = 'parecido'
    for campo in campos.values():
        if campo.unique and not campo.primary_key:
            vigilados.setdefault(campo.name, 'exacto')
    for regla in modelo._meta.constraints:
        if (isinstance(regla, models.UniqueConstraint) and regla.fields
                and len(regla.fields) == 1 and regla.fields[0] in campos):
            vigilados.setdefault(regla.fields[0], 'exacto')
    return vigilados


def buscar(configuracion, formulario, campo, valor, excluir=None):
    """Las filas iguales o parecidas a ``valor`` en ``campo``.

    Devuelve una lista de diccionarios listos para el JSON. Incluye las
    dadas de baja: si lo que se quiere cargar ya existió, lo que corresponde
    es reactivarlo, no crearlo de nuevo.
    """
    modo = campos_vigilados(configuracion).get(campo)
    if modo is None:
        return []
    modelo = configuracion.modelo
    filas = modelo.objects.all()
    if excluir:
        filas = filas.exclude(pk=excluir)

    if modo == 'exacto':
        # El valor se pasa por el campo del formulario: así un CUIT
        # tipeado sin guiones se compara ya normalizado, como se guarda.
        try:
            limpio = formulario.fields[campo].clean(valor)
            limpiar = getattr(formulario, f'clean_{campo}', None)
            if limpiar is not None:          # p. ej. normalizar_cuit()
                formulario.cleaned_data = {campo: limpio}
                limpio = limpiar()
        except Exception:
            return []
        if limpio in (None, ''):
            return []
        consulta = {f'{campo}__iexact' if isinstance(limpio, str) else campo: limpio}
        encontrados = [(1.0, fila) for fila in filas.filter(**consulta)[:TOPE]]
    else:
        buscado = clave(valor)
        if len(buscado) < 2:
            return []
        candidatos = []
        for pk, texto in filas.values_list('pk', campo):
            nota = puntaje(buscado, clave(texto))
            if nota >= UMBRAL:
                candidatos.append((nota, pk))
        candidatos.sort(key=lambda par: -par[0])
        candidatos = candidatos[:TOPE]
        por_pk = modelo.objects.in_bulk([pk for _, pk in candidatos])
        encontrados = [(nota, por_pk[pk]) for nota, pk in candidatos if pk in por_pk]

    resultado = []
    for nota, fila in encontrados:
        texto = getattr(fila, campo, '')
        resultado.append({
            'id': fila.pk,
            'texto': str(fila),
            'valor': str(texto),
            'igual': nota >= 1.0,
            'baja': bool(getattr(fila, 'borrado_logico', False)),
            'url': (f"{configuracion.url('lista')}?"
                    + urlencode({'busqueda': texto, 'estado': 'todos'})),
        })
    return resultado


# ---------------------------------------------------------------------------
#  Preparar un formulario
# ---------------------------------------------------------------------------
def configuracion_de(modelo):
    """La configuración CRUD que da de alta filas de ``modelo``, o None."""
    from nucleo.crud import REGISTRO
    for configuracion in REGISTRO.values():
        if configuracion.modelo is modelo:
            return configuracion
    return None


def preparar(formulario, configuracion, usuario=None, rapido=False):
    """Marca los campos del formulario para que el navegador los asista.

    ``data-parecidos`` en los campos vigilados y ``data-alta-rapida`` en
    los que apuntan a otra tabla que el usuario puede dar de alta. En una
    alta rápida no se ofrece otra alta rápida adentro: una marca dentro de
    un producto sí, un proveedor dentro de una marca dentro de un producto,
    no.
    """
    url = configuracion.url('parecidos')
    for nombre, modo in campos_vigilados(configuracion).items():
        campo = formulario.fields.get(nombre)
        if campo is not None:
            campo.widget.attrs['data-parecidos'] = f'{url}?campo={nombre}'
            campo.widget.attrs['data-parecidos-modo'] = modo

    if rapido or usuario is None:
        return formulario
    for nombre, campo in formulario.fields.items():
        relacion = getattr(campo, 'queryset', None)
        if relacion is None or nombre.startswith('renglones'):
            continue
        padre = configuracion_de(relacion.model)
        if (padre is None or padre is configuracion or padre.formset
                or not usuario.puede(padre.permiso('crear'))):
            continue
        campo.widget.attrs['data-alta-rapida'] = f"{padre.url('crear')}?rapido=1"
        campo.widget.attrs['data-alta-titulo'] = padre.nuevo
    return formulario
