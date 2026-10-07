# Cafeto — Sistema de Gestión

Aplicación web de gestión comercial para **Cafeto Las Lomitas** (cafetería de
especialidad y minisuper de conveniencia, Ruta Nacional 51 Km. 17, Campo
Quijano, Salta).

Materia: Práctica Profesional II · 2.º Año · Tecnicatura Superior en Análisis
de Sistemas · Instituto Superior Del Milagro N.º 8207.

---

## Levantarlo en tres comandos

```bash
cd Proyecto-Cafeto
cp .env.example .env          # y poné los valores reales
docker compose up -d --build
```

Listo: http://localhost:8000

La imagen se construye sola, las dependencias quedan adentro y las migraciones
se aplican al arrancar. No hay que entrar al contenedor a instalar nada.

**Primer ingreso:** usuario `30000000` (el DNI del administrador inicial) con
la clave por defecto que define `usuarios/migrations/0002_datos_iniciales_v2.py`.
El sistema obliga a cambiarla antes de dejar entrar.

---

## Stack

| Componente | Versión | Para qué |
|---|---|---|
| Django | 4.2.16 | Framework web (patrón MVT) |
| PostgreSQL | 16 | Base de datos |
| Adminer | latest | Administrador web de la base |
| Python | 3.11-slim | Runtime (imagen propia, con `Dockerfile`) |
| Docker Compose | — | Orquesta los tres contenedores |
| Bootstrap 5 · Bootstrap Icons · DataTables · SweetAlert2 | CDN | Front-end |

### Accesos

| Qué | Dirección |
|---|---|
| Aplicación | http://localhost:8000 |
| Adminer (base de datos) | http://localhost:9090 |
| Panel /admin de Django | http://localhost:8000/admin/ |

En Adminer: motor **PostgreSQL**, servidor `db`, y el usuario, la clave y la
base que tengas en `.env`.

---

## Quién es dueño de qué

**Django controla todo: las tablas Y la lógica de negocio.** 

| Dónde | Qué regla |
|---|---|
| `cafeto/reglas.py` | Los mixins de modelo: sello de modificación (DD-17), baja lógica (RN-8.1.1), libros de sólo agregado (DD-04), saldos derivados |
| `cafeto/contexto.py` | Quién está operando, en un `ContextVar` que fija el middleware |
| `operaciones/caja.py` | Apertura, movimientos, arqueo ciego y cierre |
| `operaciones/ventas.py` | Registrar y anular un ticket; FEFO del stock |
| `operaciones/puntos.py` | Acumular, canjear y revertir |
| `operaciones/compras.py` | Órdenes y recepción de mercadería |
| `operaciones/egresos.py` | Obligaciones, pagos y conciliación |
| `manage.py pasar_a_historico` | Archivado: la única baja física del sistema |

Cada regla se cubre por los **dos caminos** del ORM: el de la instancia
(`save()` / `delete()`) y el del conjunto (`QuerySet.update()` /
`.delete()`), que no pasa por los métodos de la instancia. Olvidar el
segundo deja un agujero por el que se escapa un borrado masivo.

**La única excepción** es `f_clave_normalizada()` y la extensión
`unaccent`, que se crean con SQL embebido en una migración. No son lógica
de negocio: son la definición de los índices únicos normalizados (DD-50),
y un índice de expresión no puede llamar a código Python.


## Módulos

| Módulo | Dónde | Estado |
|---|---|---|
| Autenticación y sesiones (DFD 1) | `usuarios` | Implementado |
| Usuarios, perfiles y permisos (DFD 8) | `usuarios` | Implementado |
| Proveedores (DFD 10) | `proveedores` | Implementado |
| **Mantener datos maestros (DFD 10)** | `maestros` + `proveedores` | **Implementado (Fase 4)** |
| Caja, ventas, compras, stock, egresos, fidelización | `operaciones` | Tablas y lógica en la base; pantallas en los próximos sprints |

### Pantallas de "Mantener datos maestros"

Las diez comparten una sola implementación (`maestros/crud.py`): cada entidad
aporta una configuración —modelo, formulario, columnas, filtros— y la máquina
pone el resto. Una corrección de usabilidad vale para las diez.

**El menú no se llama "Datos maestros".** Ése es el nombre del proceso N° 10
del DFD y sirve para hablar entre quienes diseñan el sistema, no para quien lo
usa. En la barra, las pantallas se agrupan por área de trabajo con el
vocabulario del negocio: **Inventario y Menú**, **Club Cafeto**, **Ajustes
del Local** y **Equipo y Permisos** (ver `usuarios/menu.py`). Cada ítem aparece sólo con su permiso
`ver_*`, y un grupo sin ítems visibles no se dibuja.

| Pantalla | URL | Permisos |
|---|---|---|
| Marcas | `/maestros/marcas/` | `ver_marcas` · `crear_marca` · `editar_marca` · `eliminar_marca` · `reactivar_marca` |
| Rubros | `/maestros/rubros/` | `ver_rubros` · `crear_rubro` · `editar_rubro` · `eliminar_rubro` · `reactivar_rubro` |
| Productos | `/maestros/productos/` | `ver_productos` · `crear_producto` · `editar_producto` · `eliminar_producto` · `reactivar_producto` |
| Tipos de egreso | `/maestros/tipos_egreso/` | `ver_tipos_egreso` · `crear_tipo_egreso` · `editar_tipo_egreso` · `eliminar_tipo_egreso` · `reactivar_tipo_egreso` |
| Medios de pago | `/maestros/medios_pago/` | `ver_medios_pago` · `crear_medio_pago` · `editar_medio_pago` · `eliminar_medio_pago` · `reactivar_medio_pago` |
| Cajas registradoras | `/maestros/cajas_registradoras/` | `ver_cajas_registradoras` · `crear_caja_registradora` · `editar_caja_registradora` · `eliminar_caja_registradora` · `reactivar_caja_registradora` |
| Categorías de fidelización | `/maestros/categorias/` | `ver_categorias` · `crear_categoria` · `editar_categoria` · `eliminar_categoria` · `reactivar_categoria` |
| Beneficios | `/maestros/beneficios/` | `ver_beneficios` · `crear_beneficio` · `editar_beneficio` · `eliminar_beneficio` · `reactivar_beneficio` |
| Provincias | `/proveedores/provincias/` | `ver_provincias` · `crear_provincia` · `editar_provincia` |
| Localidades | `/proveedores/localidades/` | `ver_localidades` · `crear_localidad` · `editar_localidad` |

Provincias y localidades **no tienen baja**: son un catálogo geográfico. Una
provincia no deja de existir, y esconderla sólo dejaría sin leer las
direcciones históricas que la usan.

Los permisos se cargan por migración y se le asignan al **Administrador**. El
**Operador** no recibe ninguno: lo que necesite se le tilda a mano desde la
pantalla de Perfiles. Si un perfil no tiene ningún `ver_*` de un grupo, ese
grupo del menú ni se dibuja.

---

## Registro de Actividad

`/actividad/` muestra `LOG_AUDITORIA` —quién hizo qué y cuándo— sin pasar por
Adminer. Permiso `ver_auditoria`, del Administrador.

Es **de sólo lectura, y a propósito**: hay dos vistas y las dos son GET. No
existe ruta de alta, edición ni baja, porque el libro es append-only y el
mixin `LibroInmutable` (`cafeto/reglas.py`) rechaza cualquier UPDATE o
DELETE (RN-8.1.1), tanto por la instancia como por el QuerySet. Un botón de
editar prometería algo imposible y rompería la única garantía que hace que
una auditoría sirva.

Filtros por usuario, módulo, rango de fechas y texto de la operación, todos
por GET: la búsqueda queda en la URL y se puede compartir o volver con Atrás.

El modal del ojo traduce las dos fotos JSONB a castellano
(`operaciones/auditoria.py`): la columna `id_marca` se muestra como "Marca",
el `2` como "La Morenita" y el `false` como "No". Lo que cambió va primero y
resaltado.

---

## Cómo se valida un dato

Cada regla se controla en **tres capas**, y las tres dicen lo mismo:

1. **Formulario** (`maestros/forms.py`): mensaje claro al lado del campo.
2. **Modelo** (`clean()`, los mixins de `cafeto/reglas.py` y los servicios de
   `operaciones/`): vale para cualquier otro camino de entrada — shell,
   `/admin`, importaciones. Acá vive TODA la lógica transaccional y de
   validación del sistema: no hay triggers ni procedimientos almacenados.
3. **Base** (CHECK, UNIQUE, FK, NOT NULL): la última barrera, declarada en el
   `Meta` de cada modelo y creada por las migraciones. Si la base rechaza, la
   vista lo traduce a castellano; nunca se muestra el SQL ni el nombre de una
   restricción.

Lo común a todos los campos está en `cafeto/validaciones.py`: limpieza de
espacios, largos, caracteres admitidos, números con coma decimal, fechas
`dd/mm/aaaa`, clave normalizada para los duplicados y dígitos verificadores
(CUIT módulo 11, código de barras GS1).

Tres reglas que conviene conocer porque sorprenden:

* **Los duplicados se buscan sin distinguir mayúsculas, acentos ni espacios
  repetidos** (DD-50). "Café", "CAFE" y "cafe " son el mismo rubro. Si el
  duplicado está dado de baja, el sistema no crea una fila nueva: avisa que
  hay que reactivar la que ya está.
* **Un producto se identifica por marca + nombre + presentación**, no por un
  nombre largo escrito a mano (DD-55). Por eso la marca es su propia tabla y
  el contenido su propia columna: así "Café molido La Morenita 500 g" y
  "Café molido La morenita" dejan de poder convivir como dos artículos.
  Dos presentaciones del mismo producto (500 g y 250 g) sí son dos
  productos, que es lo correcto.
* **Nada se borra** (RN-8.1.1). La baja es lógica y la base rechaza cualquier
  `DELETE` sobre las tablas con `borrado_logico`. Una baja se puede deshacer;
  un borrado, no.

---

## Correr las pruebas

```bash
docker compose exec web python manage.py test
```

Otras comprobaciones útiles:

```bash
# ¿Los modelos y las migraciones están sincronizados?
docker compose exec web python manage.py makemigrations --check --dry-run

# ¿Quedó algún dato viejo que hoy el sistema ya no aceptaría?
docker compose exec web python manage.py auditar_consistencia
# ...y para darlo de baja (baja LÓGICA, se reactiva desde la pantalla):
docker compose exec web python manage.py auditar_consistencia --dar-de-baja

# Logs del arranque (migraciones aplicadas, sin error)
docker compose logs web --tail 50
```

---

## Estructura del proyecto

```
Proyecto-Cafeto/
├── Dockerfile              # Imagen del servicio web
├── .dockerignore
├── docker-compose.yml      # db · adminer · web
├── .env.example            # Plantilla de variables (el .env NO se versiona)
├── requirements.txt
├── manage.py
│
├── cafeto/                 # Configuración del proyecto
│   ├── settings.py
│   ├── urls.py
│   ├── restricciones.py    # CheckSQL / CheckEnBase / ClaveNormalizada
│   ├── reglas.py           # Mixins de modelo: sello, baja lógica, libros
│   ├── motivos.py          # Cómo se redacta un "no se puede dar de baja"
│   ├── contexto.py         # Quién opera, visible desde cualquier modelo
│   └── validaciones.py     # Reglas comunes a todos los CRUD (4.4.V)
│
├── templates/base.html     # Navbar, menú de Datos maestros, mensajes
│
├── usuarios/               # PERFILES · PERMISOS · PERFILES_PERMISOS · USUARIOS
│   └── menu.py             # La barra de navegación, por área de trabajo
├── proveedores/            # PROVINCIAS · LOCALIDADES · PROVEEDORES
│
├── maestros/               # RUBROS · MARCAS · PRODUCTOS · TIPOS_EGRESO
│   ├── crud.py             #   MEDIOS_PAGO · CAJAS_REGISTRADORAS · CATEGORIAS
│   │                       #   BENEFICIOS (+ receta)
│   ├── views.py            # La configuración de cada entidad
│   ├── forms.py
│   ├── templates/maestros/lista.html     # Una pantalla para las diez
│   ├── templates/maestros/_ficha.html    # El modal de sólo lectura (el ojo)
│   ├── templates/maestros/_renglones.html # La receta de un beneficio
│   └── management/commands/
│       └── auditar_consistencia.py       # datos viejos que ya no pasarían
│
└── operaciones/            # Las 18 tablas operativas + sesiones y auditoría
    ├── middleware.py       # Publica el usuario del pedido en el contexto
    ├── servicios.py        # Sesiones de negocio y registrar_auditoria()
    ├── caja.py · ventas.py · compras.py · egresos.py · puntos.py
    │                       # Los servicios transaccionales, en Python
    └── migrations/         # La ÚNICA fuente del esquema
```

---

## Uso diario

```bash
docker compose up -d          # levantar
docker compose logs -f web    # ver qué pasa
docker compose stop           # apagar sin borrar nada
```

**`docker compose down` nunca con `-v`**: el `-v` borra el volumen `db_data`
y con él toda la base.



