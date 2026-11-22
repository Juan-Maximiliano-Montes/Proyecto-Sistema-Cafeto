"""Importar proveedores desde Excel.

La identidad del proveedor es su CUIT (que además se valida con el dígito
verificador). Si ya existe, se actualizan sus datos de contacto; si no, es
un alta. La localidad tiene que existir: se busca por nombre (y por
provincia, si la planilla la trae).
"""
from django.core.exceptions import ValidationError

from compras.models import CondicionPago, Proveedor, TipoProveedor
from geografia.models import Localidad
from nucleo.importar import Columna, Importador, opcion, registrar
from nucleo.validaciones import clave_normalizada, normalizar_cuit

CAMPOS = ('nombre_proveedor', 'telefono_proveedor', 'email_proveedor', 'domicilio',
          'id_localidad_id', 'condicion_pago', 'tipo_proveedor')


@registrar
class ImportadorProveedores(Importador):
    clave = 'proveedores'
    titulo = 'Proveedores'
    permiso = 'importar_proveedores'
    modulo = 'COMPRAS'
    volver = 'compras:proveedores_lista'
    columnas = (
        Columna('nombre', 'Nombre', True, 'Razón social.', 'Distribuidora Norte SRL',
                ('razon social', 'proveedor')),
        Columna('cuit', 'CUIT', True, 'Con o sin guiones. Identifica al proveedor.', '30-71234567-1'),
        Columna('telefono', 'Teléfono', False, '', '387 4123456'),
        Columna('email', 'Email', False, '', 'ventas@norte.com', ('correo', 'mail')),
        Columna('domicilio', 'Domicilio', False, '', 'Av. Belgrano 1234'),
        Columna('localidad', 'Localidad', False, 'Tiene que existir en el sistema.', 'Salta Capital'),
        Columna('provincia', 'Provincia', False, 'Para distinguir localidades con el mismo nombre.', 'Salta'),
        Columna('condicion', 'Condición de pago', False, 'Contado, Cuenta corriente, 15/30/60 días. Vacío = Contado.',
                'Contado', ('condicion',)),
        Columna('tipo', 'Tipo', False, 'Mercadería, Servicios o Mixto. Vacío = Mercadería.', 'Mercadería'),
    )

    def preparar(self):
        self.localidades = {}
        for l in Localidad.objects.select_related('id_provincia'):
            self.localidades.setdefault(clave_normalizada(l.nombre_localidad), []).append(l)

    def _localidad(self, nombre, provincia):
        if not (nombre or '').strip():
            return None
        candidatas = self.localidades.get(clave_normalizada(nombre), [])
        if provincia:
            candidatas = [l for l in candidatas
                          if clave_normalizada(l.id_provincia.nombre_provincia) == clave_normalizada(provincia)]
        if not candidatas:
            raise ValidationError(f'La localidad "{nombre}" no existe: cargala primero en Localidades.')
        if len(candidatas) > 1:
            raise ValidationError(f'Hay varias "{nombre}": agregá la provincia en la planilla.')
        return candidatas[0]

    def validar(self, resultado, vistos):
        d = resultado.datos
        try:
            cuit = normalizar_cuit(d.get('cuit'))
        except (ValidationError, ValueError, TypeError):
            cuit = None
        if not cuit:
            resultado.errores.append(f'El CUIT "{d.get("cuit")}" no es válido.')
            return
        if cuit in vistos:
            resultado.errores.append(f'El CUIT {cuit} está repetido (fila {vistos[cuit]}).')
            return
        vistos[cuit] = resultado.numero
        try:
            localidad = self._localidad(d.get('localidad'), d.get('provincia'))
            condicion = opcion(d.get('condicion'), CondicionPago.choices, 'La condición de pago',
                               CondicionPago.CONTADO)
            tipo = opcion(d.get('tipo'), TipoProveedor.choices, 'El tipo', TipoProveedor.MERCADERIA)
        except ValidationError as error:
            resultado.errores.extend(error.messages)
            return
        proveedor = Proveedor.objects.filter(cuit_proveedor=cuit).first() or Proveedor(cuit_proveedor=cuit)
        antes = {c: getattr(proveedor, c) for c in CAMPOS} if proveedor.pk else None
        proveedor.nombre_proveedor = (d.get('nombre') or '').strip()
        for campo, clave in (('telefono_proveedor', 'telefono'), ('email_proveedor', 'email'),
                             ('domicilio', 'domicilio')):
            if (d.get(clave) or '').strip() or not proveedor.pk:
                setattr(proveedor, campo, (d.get(clave) or '').strip() or None)
        if localidad is not None:
            proveedor.id_localidad = localidad
        if (d.get('condicion') or '').strip() or not proveedor.pk:
            proveedor.condicion_pago = condicion
        if (d.get('tipo') or '').strip() or not proveedor.pk:
            proveedor.tipo_proveedor = tipo
        try:
            proveedor.full_clean()
        except ValidationError as error:
            resultado.errores.extend(error.messages)
            return
        if antes is None:
            resultado.accion = 'ALTA'
        else:
            cambios = [c for c in CAMPOS if getattr(proveedor, c) != antes[c]]
            resultado.accion = 'CAMBIO' if cambios else 'SIN CAMBIOS'
        if proveedor.pk and proveedor.borrado_logico:
            resultado.errores.append('Ese proveedor está dado de baja: reactivalo antes de importarlo.')
            return
        resultado.descripcion = f'{proveedor.nombre_proveedor} ({cuit})'
        resultado.preparado = proveedor

    def aplicar(self, resultado, usuario):
        resultado.preparado.save()
