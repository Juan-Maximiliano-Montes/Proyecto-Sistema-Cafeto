"""Importación desde Excel con vista previa: todo o nada.

El circuito es siempre el mismo, para cualquier entidad:

1. **Subir** la planilla (.xlsx o .csv). Se lee entera, se reconocen las
   columnas por su título (sin importar mayúsculas, acentos ni el orden) y
   se guarda lo leído en la sesión. Todavía no se escribe nada.
2. **Vista previa**: cada fila dice qué va a pasar (alta, cambio, sin
   cambios) o, en rojo, por qué no se puede. Si hay UN error, no se puede
   confirmar: se corrige la planilla y se vuelve a subir.
3. **Confirmar**: se vuelve a validar todo (pudo cambiar algo entre la
   vista previa y el clic) y se aplica en UNA transacción. Si algo falla a
   mitad de camino, no queda nada a medias.

Cada entidad aporta un ``Importador``: sus columnas y cómo validar y
aplicar una fila. La lectura, la sesión, los límites y la planilla modelo
están acá, una sola vez.
"""
import csv
import io
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction

from nucleo.validaciones import clave_normalizada

MAXIMO_DE_FILAS = 2000
MAXIMO_DE_BYTES = 2 * 1024 * 1024
CLAVE_SESION = 'cafeto_importacion_{}'


@dataclass(frozen=True)
class Columna:
    clave: str
    titulo: str
    obligatoria: bool = False
    ayuda: str = ''
    ejemplo: str = ''
    sinonimos: tuple = ()

    def nombres(self):
        return {clave_normalizada(n) for n in (self.titulo, self.clave, *self.sinonimos)}


@dataclass
class Resultado:
    """Lo que va a pasar con una fila."""
    numero: int
    datos: dict
    accion: str = ''            # 'ALTA' | 'CAMBIO' | 'SIN CAMBIOS'
    descripcion: str = ''
    errores: list = field(default_factory=list)
    avisos: list = field(default_factory=list)   # no frenan: piden que se mire
    preparado: object = None    # lo que necesita aplicar(); no se serializa
    foto_anterior: dict = None  # la fila antes de aplicar (sólo en un CAMBIO)
    foto_nueva: dict = None     # la fila después de aplicar

    @property
    def ok(self):
        return not self.errores


class Importador:
    """Base de los importadores de cada módulo."""

    clave = ''
    titulo = ''
    permiso = ''
    modulo = 'GENERAL'
    volver = 'home'         # a qué pantalla se vuelve al terminar
    columnas = ()

    # ------------------------------------------------------------------
    #  Para implementar en cada entidad
    # ------------------------------------------------------------------
    def preparar(self):
        """Carga lo que se usa en todas las filas (catálogos, índices)."""

    def validar(self, resultado, vistos):
        """Completa ``resultado`` (acción, descripción, errores, preparado).

        ``vistos`` es un dict compartido entre filas, para detectar la
        misma identidad repetida dentro de la planilla.
        """
        raise NotImplementedError

    def aplicar(self, resultado, usuario):
        raise NotImplementedError

    # ------------------------------------------------------------------
    #  Lectura
    # ------------------------------------------------------------------
    def leer(self, archivo):
        """Archivo subido -> lista de dicts {clave: texto}."""
        if archivo.size > MAXIMO_DE_BYTES:
            raise ValidationError('La planilla pesa más de 2 MB. Partila en varias.')
        nombre = (archivo.name or '').lower()
        if nombre.endswith('.csv'):
            filas = self._leer_csv(archivo)
        elif nombre.endswith('.xlsx'):
            filas = self._leer_xlsx(archivo)
        else:
            raise ValidationError('Subí una planilla .xlsx (Excel) o .csv.')
        if not filas:
            raise ValidationError('La planilla no tiene filas con datos debajo de los títulos.')
        if len(filas) > MAXIMO_DE_FILAS:
            raise ValidationError(f'La planilla tiene más de {MAXIMO_DE_FILAS} filas. Partila en varias.')
        return filas

    def _leer_xlsx(self, archivo):
        from openpyxl import load_workbook
        try:
            libro = load_workbook(archivo, read_only=True, data_only=True)
        except Exception:
            raise ValidationError('No se pudo abrir el archivo como Excel. ¿Es un .xlsx?')
        hoja = libro.worksheets[0]
        crudas = [[self._texto(c) for c in fila] for fila in hoja.iter_rows(values_only=True)]
        libro.close()
        return self._mapear(crudas)

    def _leer_csv(self, archivo):
        contenido = archivo.read()
        for codificacion in ('utf-8-sig', 'latin-1'):
            try:
                texto = contenido.decode(codificacion)
                break
            except UnicodeDecodeError:
                continue
        separador = ';' if texto.count(';') > texto.count(',') else ','
        crudas = [[c.strip() for c in fila] for fila in csv.reader(io.StringIO(texto), delimiter=separador)]
        return self._mapear(crudas)

    @staticmethod
    def _texto(valor):
        if valor is None:
            return ''
        if isinstance(valor, float) and valor.is_integer():
            return str(int(valor))
        if hasattr(valor, 'isoformat'):
            return valor.isoformat()[:10]
        return str(valor).strip()

    def _mapear(self, crudas):
        """Ubica la fila de títulos y arma {clave: valor} por fila."""
        # La fila de títulos es la que más columnas conocidas tiene entre las
        # primeras diez: una lista de un proveedor suele traer arriba un
        # título ("Lista de precios de noviembre") o el membrete.
        conocidas = set().union(*(c.nombres() for c in self.columnas))
        candidatas = [(sum(1 for t in fila if clave_normalizada(t) in conocidas), -i)
                      for i, fila in enumerate(crudas[:10]) if any(fila)]
        if not candidatas:
            return []
        aciertos, menos_i = max(candidatas)
        indice_titulos = -menos_i
        titulos = [clave_normalizada(t) for t in crudas[indice_titulos]]
        posiciones = {}
        for columna in self.columnas:
            nombres = columna.nombres()
            for i, titulo in enumerate(titulos):
                if titulo in nombres:
                    posiciones[columna.clave] = i
                    break
        faltan = [c.titulo for c in self.columnas if c.obligatoria and c.clave not in posiciones]
        if faltan:
            raise ValidationError(
                'A la planilla le faltan columnas obligatorias: ' + ', '.join(faltan)
                + '. Descargá la planilla modelo para ver cómo va.')
        filas = []
        for numero, fila in enumerate(crudas[indice_titulos + 1:], start=indice_titulos + 2):
            datos = {clave: (fila[i] if i < len(fila) else '') for clave, i in posiciones.items()}
            if any(datos.values()):
                datos['_fila'] = numero
                filas.append(datos)
        return filas

    # ------------------------------------------------------------------
    #  Vista previa y aplicación
    # ------------------------------------------------------------------
    def revisar(self, filas):
        self.preparar()
        vistos = {}
        resultados = []
        for datos in filas:
            resultado = Resultado(numero=datos.get('_fila'), datos=datos)
            try:
                self.validar(resultado, vistos)
            except ValidationError as error:
                resultado.errores.extend(error.messages)
            resultados.append(resultado)
        return resultados

    def confirmar(self, filas, usuario):
        """Vuelve a validar y aplica todo junto. Devuelve los resultados."""
        resultados = self.revisar(filas)
        if any(not r.ok for r in resultados):
            raise ValidationError('La planilla tiene errores: no se importó nada.')
        from seguridad.servicios import instantanea

        with transaction.atomic():
            for resultado in resultados:
                if resultado.accion != 'SIN CAMBIOS':
                    fila = self.instancia_de(resultado)
                    if fila is not None and fila.pk is not None:
                        resultado.foto_anterior = instantanea(
                            type(fila).objects.get(pk=fila.pk))
                    try:
                        self.aplicar(resultado, usuario)
                    except ValidationError as error:
                        raise ValidationError(
                            f'Fila {resultado.numero}: {" ".join(error.messages)} '
                            'No se importó nada.')
                    resultado.foto_nueva = instantanea(fila)
        return resultados

    def instancia_de(self, resultado):
        """La fila que ``aplicar`` va a guardar, para fotografiarla antes
        y después (el Registro de Actividad muestra qué cambió)."""
        return resultado.preparado

    # ------------------------------------------------------------------
    #  Planilla modelo
    # ------------------------------------------------------------------
    def ejemplo_de(self, columna):
        """El valor de la fila de ejemplo. Un importador lo cambia cuando el
        ejemplo tiene que existir en ESTE local (un proveedor, un rubro)."""
        return columna.ejemplo

    def planilla_modelo(self):
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter

        libro = Workbook()
        hoja = libro.active
        hoja.title = self.titulo[:31]
        for i, columna in enumerate(self.columnas, start=1):
            celda = hoja.cell(row=1, column=i, value=columna.titulo)
            celda.font = Font(bold=True)
            celda.fill = PatternFill('solid', fgColor='FAB41F' if columna.obligatoria else 'FFF3D6')
            hoja.cell(row=2, column=i, value=self.ejemplo_de(columna) or None)
            hoja.column_dimensions[get_column_letter(i)].width = max(14, len(columna.titulo) + 4)
        hoja.freeze_panes = 'A2'
        ayuda = libro.create_sheet('Cómo completarla')
        ayuda.append(['Columna', 'Obligatoria', 'Qué va'])
        for celda in ayuda[1]:
            celda.font = Font(bold=True)
        for columna in self.columnas:
            ayuda.append([columna.titulo, 'Sí' if columna.obligatoria else 'No', columna.ayuda])
        ayuda.column_dimensions['A'].width = 24
        ayuda.column_dimensions['C'].width = 90
        for fila in ayuda.iter_rows(min_row=2):
            fila[2].alignment = Alignment(wrap_text=True)
        salida = io.BytesIO()
        libro.save(salida)
        return salida.getvalue()


# ---------------------------------------------------------------------------
#  Ayudas para los importadores
# ---------------------------------------------------------------------------
SI = {'si', 's', 'x', '1', 'verdadero', 'true', 'yes'}
NO = {'no', 'n', '0', 'falso', 'false', ''}


def si_no(texto, etiqueta):
    clave = clave_normalizada(texto or '')
    if clave in SI:
        return True
    if clave in NO:
        return False
    raise ValidationError(f'{etiqueta}: poné «Sí» o «No» (dice «{texto}»).')


def opcion(texto, opciones, etiqueta, por_defecto=None):
    """Busca ``texto`` entre (valor, etiqueta) sin importar acentos ni mayúsculas."""
    if not (texto or '').strip():
        if por_defecto is not None:
            return por_defecto
        raise ValidationError(f'Falta {etiqueta.lower()}.')
    clave = clave_normalizada(texto)
    for valor, nombre in opciones:
        if clave in (clave_normalizada(valor), clave_normalizada(nombre),
                     clave_normalizada(nombre.split(' (')[0])):
            return valor
    validas = ', '.join(n.split(' (')[0] for _, n in opciones)
    raise ValidationError(f'{etiqueta} «{texto}» no existe. Puede ser: {validas}.')


# ---------------------------------------------------------------------------
#  Registro: cada módulo anota sus importadores al cargar la app
# ---------------------------------------------------------------------------
IMPORTADORES = {}


def registrar(importador_clase):
    IMPORTADORES[importador_clase.clave] = importador_clase
    return importador_clase
