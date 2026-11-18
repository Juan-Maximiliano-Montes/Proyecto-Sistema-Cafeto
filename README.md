# Cafeto — Sistema de Gestión

Aplicación web de gestión comercial para **Cafeto Las Lomitas** (cafetería de
especialidad y minisuper de conveniencia, Ruta Nacional 51 Km. 17, Campo
Quijano, Salta).

Materia: Práctica Profesional II · 2.º Año · Tecnicatura Superior en Análisis
de Sistemas · Instituto Superior Del Milagro N.º 8207.

Arquitectura: **Alternativa II** del estudio previo — aplicación web en un NAS
del local, a la que se entra desde las tablets y teléfonos del mostrador y,
de manera segura, desde afuera.

---

## Levantarlo (desarrollo)

```bash
cd Proyecto-Cafeto
cp .env.example .env          # y poné los valores reales
docker compose up -d --build
```

Listo: http://localhost:8000

La imagen se construye sola, las dependencias quedan adentro y las migraciones
se aplican al arrancar.

**Primer ingreso:** usuario `30000000` (el DNI del administrador inicial, que es
el **titular** del sistema) con la clave por defecto que define
`seguridad/migrations/0003_datos_iniciales.py`. El sistema obliga a cambiarla
antes de dejar entrar.

> **Si venías de una versión anterior** (la que tenía las apps `usuarios`,
> `maestros`, `proveedores` y `operaciones`), el esquema cambió de raíz:
> corré **una sola vez** `docker compose down -v` y después
> `docker compose up -d --build`. El `-v` borra la base de desarrollo; no lo
> uses nunca en el NAS con datos reales.

**Datos de demostración** (sólo desarrollo): con las cajas cerradas,

```bash
docker compose exec web python manage.py cargar_datos_demo
```

carga dos semanas de operación de un local (ventas, cierres con diferencia,
devoluciones, anulaciones, compras con faltante, pagos vencidos, mermas, lotes
por vencer), entre 7 y 20 casos por tabla, pasando por las pantallas del
sistema. Al final lista los usuarios de la demo y su clave, y cualquier
operación que el sistema haya rechazado. No corre con `DJANGO_DEBUG=False` ni
dos veces sobre la misma base.

## Ponerlo en el NAS (producción)

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Con `docker-compose.prod.yml`:

* la base **no publica su puerto** y **Adminer no se levanta**;
* el código no se monta: corre lo que quedó en la imagen, con **gunicorn**;
* los estáticos (Bootstrap, íconos, fuentes y logos) van **dentro** de la
  imagen, comprimidos: el sistema se ve igual sin internet;
* un servicio **`respaldo`** hace un `pg_dump` diario a `./respaldos/` y
  guarda los últimos 14 (`CAFETO_RESPALDOS_DIAS`). Cómo restaurar uno está en
  `docker/respaldar.sh`;
* un servicio **`historico`** pasa a las tablas históricas lo viejo el día 1
  de cada mes a las 03:30 (`docker/pasar_a_historico.sh`). Lo que hizo se ve
  con `docker compose -f docker-compose.yml -f docker-compose.prod.yml logs historico`.

En el `.env` del NAS: `DJANGO_DEBUG=False`, una `DJANGO_SECRET_KEY` larga y
aleatoria, `DJANGO_ALLOWED_HOSTS` con el nombre o la IP con que se entra, y
`DJANGO_CSRF_TRUSTED_ORIGINS` con la dirección completa (`https://…`).

**Acceso desde afuera del local:** por el proxy inverso del NAS con HTTPS
(entonces `DJANGO_CONFIAR_EN_PROXY=True` y `DJANGO_HTTPS=True`) o por una VPN.
Nunca abriendo el puerto 8000 directo a internet.

---

## Stack

| Componente | Versión | Para qué |
|---|---|---|
| Python | 3.11-slim | Runtime (imagen propia, `Dockerfile`) |
| Django | 5.2 | Framework web (patrón MVT) |
| PostgreSQL | 16 | Base de datos |
| gunicorn · whitenoise | — | Servidor de producción y estáticos |
| openpyxl · WeasyPrint | — | Excel (exportar e importar) y PDF |
| pandas · numpy · matplotlib | 3.1 · 2.4 · 3.11 | Gráficos de los reportes, en SVG (DD-72, DD-74) |
| Bootstrap 5.3 · Bootstrap Icons · Select2 | servidos por el sistema | Front-end, sin CDN |
| Caveat · Lora · Inter | servidas por el sistema | Tipografías del manual de identidad |

Cada reporte trae su gráfico arriba de la tabla (y en el PDF). El tipo sale de
la pregunta que contesta: evolución en el tiempo, partes de un total (con la
opción de verlo en torta), ranking o diferencias de signo. Los límites —10
barras, 6 porciones, sin torta con negativos— están en `negocio/graficos.py`
y su fundamento en DD-74.

| Qué | Dirección |
|---|---|
| Aplicación | http://localhost:8000 |
| Adminer (sólo desarrollo) | http://localhost:9090 |
| Panel /admin de Django (sólo con DEBUG) | http://localhost:8000/admin/ |

---

## Organización: una app por módulo del DER

Si algo falla en la caja, se busca en `ventas/`; si falla un conteo, en
`stock/`. Cada app tiene sus modelos, sus **servicios** (las reglas
transaccionales, en Python), sus pantallas y sus pruebas.

| App | Tablas | Procesos y pantallas |
|---|---|---|
| `nucleo` | — | Lo común: mixins de modelo, validaciones, CRUD genérico, búsqueda sin acentos, paginación, exportación a Excel/PDF, importación con vista previa, sistema de diseño (`static/cafeto/`) |
| `seguridad` | USUARIOS · PERFILES · PERMISOS · SESIONES · LOG_AUDITORIA · INTENTOS_ACCESO | Login con bloqueo progresivo, sesiones del lado del servidor, usuarios, perfiles (con perfil temporal y titular), permisos, registro de actividad |
| `geografia` | PROVINCIAS · LOCALIDADES | Catálogo geográfico |
| `negocio` | PARAMETROS_NEGOCIO | Datos del local, reglas de operación, panel de inicio, reportes |
| `stock` | RUBROS · MARCAS · PRODUCTOS · COMPOSICION · LOTES · MOVIMIENTOS_STOCK · AJUSTES · PRODUCCIONES | Productos (5 tipos), FEFO, rebaja por vencimiento, conteo ciego, mermas, traspasos, producción por tanda, alertas, etiquetas de oferta, aumento masivo de precios, importación |
| `ventas` | MEDIOS_PAGO · CAJAS_REGISTRADORAS · TURNOS_CAJA · MOVIMIENTOS_CAJA · VENTAS · DETALLES · PAGOS · CAMBIOS | Puesto de caja, arqueo ciego en dos pasos, relevo, mostrador, pago mixto, cambios y devoluciones, consultas |
| `compras` | PROVEEDORES · ORDENES · COMPRAS · DETALLES · TIPOS_EGRESO · EGRESOS | Recepción con lotes, órdenes de compra (con PDF), pagos, gastos de caja, conciliación, importación de proveedores |
| `fidelizacion` | CATEGORIAS · BENEFICIOS · CLIENTES · PUNTOS | **Hito 5** (las tablas y los catálogos ya están) |
| `api_movil` | OPERACIONES_CELULAR (las claves `Idempotency-Key`) | API JSON de la app móvil: catálogo público y beneficios; para el equipo, panel, consulta con escáner, alertas, ofertas, recepción contra la orden, conteo, mermas, traspasos, autorización de ajustes, producción y ABM liviano de marcas y rubros; para los clientes, su QR y sus puntos |

Dentro de cada app, las pantallas van en `views/` (un archivo por proceso:
`ventas/views/caja.py`, `ventas/views/mostrador.py`…) y las reglas en los
servicios (`ventas/caja.py`, `ventas/servicios.py`, `stock/servicios.py`…).
Una vista nunca escribe una regla de negocio: pide, valida el formulario y
llama al servicio.

---

## Reglas que conviene conocer

**Dónde vive cada regla.** En tres capas que dicen lo mismo:

1. **Formulario**: el mensaje en rojo al lado del campo.
2. **Modelo y servicios** (`clean()`, los mixins de `nucleo/reglas.py`, los
   servicios de cada app): valen para cualquier camino de entrada — pantalla,
   importación, shell. No hay triggers ni procedimientos almacenados.
3. **Base** (CHECK, UNIQUE, FK, NOT NULL): la última barrera. Si la base
   rechaza, se traduce a castellano; nunca se muestra el SQL.

**Sin duplicados disfrazados.** Los nombres se comparan sin mayúsculas, acentos
ni puntuación (DD-50): "Coca-Cola", "COCA COLA" y "coca cola" son lo mismo. La
búsqueda de todas las pantallas usa la misma regla.

**Nada se borra.** La baja es lógica (RN-8.1.1) y los libros (ventas,
movimientos, auditoría) son de sólo agregado. Una venta no se anula: lo que
vuelve se registra como un **cambio** que referencia el ticket.

**Un doble toque no repite nada.** Cada formulario que mueve plata o
mercadería lleva un token de un solo uso (`nucleo/idempotencia.py`); la venta,
además, tiene su token UNIQUE en la base.

**Lo que no se ve, no se adivina.** El arqueo de caja y el conteo de stock son
ciegos: quien cuenta declara lo que hay sin ver lo que el sistema espera. Lo
declarado queda fijo y auditado antes de mostrar la diferencia.

**Permisos.** Cada pantalla y cada acción exige su permiso en el servidor. Los
permisos de Seguridad son exclusivos del perfil administrador. Lo que excede
el perfil de quien opera (un descuento sobre su tope, devolver plata, cerrar
con diferencia) lo autoriza otra persona con su usuario y su clave en el
mismo dispositivo, y queda escrito quién fue.

---

## La app móvil

La app «Sistema Cafeto Móvil» habla con `/api/movil/` (app `api_movil`). Lo
público es el catálogo y los beneficios; el equipo entra con **el mismo
usuario y la misma clave que en la web** y recibe un token que vence a las
12 horas. Cada pedido vuelve a controlar el permiso en el servidor.

* El ingreso pasa por los mismos controles que el de la web: bloqueo
  progresivo por reintentos (la cuenta queda frenada en los dos canales),
  intentos en INTENTOS_ACCESO y asiento en el Registro de Actividad.
* No abre una fila en SESIONES (DD-01): entrar desde el celular no cierra
  la sesión de la web. Cambiar o restablecer la clave, o dar de baja al
  usuario, corta todos sus tokens.
* Lo que se modifica desde el celular queda en el Registro de Actividad con
  el usuario, su perfil y la IP («Desde la app móvil»).
* Las tareas del equipo (recepción, conteo, mermas, traspasos, ajustes,
  producción) llaman a los mismos servicios que la web. Lo que mueve
  mercadería trae una `Idempotency-Key`: un reintento no lo repite.
* Los clientes entran con Firebase. Para ver su QR y sus puntos, el sistema
  verifica su token con `firebase-admin` y la cuenta de servicio del
  proyecto. Sin esa credencial, `/api/movil/clientes/` responde 503
  (`CLIENTES_SIN_CONFIGURAR`) y el resto anda igual.
* La ficha del cliente se encuentra por su cuenta de la app; la primera vez,
  por su correo verificado. Si dos fichas comparten el correo, la app pide
  el DNI. El correo ya no se puede repetir entre clientes vigentes, y los
  repetidos de antes se ven con `manage.py clientes_correo_repetido` (y en
  el inicio, para quien ve clientes). Para una ficha que se cargó en la caja
  sin correo, se le carga el correo desde la pantalla de Clientes con el
  cliente presente: la próxima vez que abra la app, la encuentra sola.

**La cuenta de servicio de Firebase** (una sola vez, y en el NAS igual):

1. En la [consola de Firebase](https://console.firebase.google.com/), abrir
   el proyecto de la app → ⚙ **Configuración del proyecto** → pestaña
   **Cuentas de servicio** → **Generar nueva clave privada**. Se descarga un
   JSON.
2. Guardarlo como `secretos/firebase.json` en la carpeta del proyecto. La
   carpeta `secretos/` está en `.gitignore`: esa clave **nunca** se sube ni
   se comparte (con ella se administra el proyecto de Firebase).
3. `docker compose up -d --build` (instala `firebase-admin` y monta
   `secretos/` de sólo lectura en `/run/secrets/`).
4. Comprobar:

```bash
docker compose exec web python manage.py probar_firebase
```

  Dice qué falta (la librería, la variable o el archivo) o que está todo
  bien.

Para probarla desde el celular, en la misma red Wi-Fi que la PC:

```bash
ipconfig        # la "Dirección IPv4" de la PC, por ejemplo 192.168.0.10
```

En el `.env` de la app móvil: `EXPO_PUBLIC_API_URL=http://192.168.0.10:8000/api/movil`
(nunca `localhost`: desde el celular es el propio celular), y reiniciar Expo
con `npx expo start -c`. En desarrollo `DJANGO_ALLOWED_HOSTS` vale `*`; en el
NAS tiene que incluir la IP o el nombre con que entra el celular, y fuera
de la red local la API va por HTTPS.

---

## Correr las pruebas

```bash
docker compose exec web python manage.py test --parallel 4
```

**Prueba en un navegador real.** Las pruebas de arriba no abren un navegador:
no corren el JavaScript ni las ventanas. `nucleo/tests/test_navegador.py` sí:
entra con cada perfil, recorre todas las pantallas, toca cada botón que abre
una ventana y manda los formularios vacíos o con datos inválidos; falla ante
un error de JavaScript, una respuesta 500 o una pantalla trabada. Necesita
Playwright, que no va en la imagen; se corre fuera del contenedor, con la base
de desarrollo levantada (sin Playwright, la prueba se saltea sola):

```bash
pip install playwright==1.56.0 && playwright install chromium
python manage.py test nucleo.tests.test_navegador
```

Conviene correrla antes de cada commit que toque pantallas o JavaScript.

Otras comprobaciones útiles:

```bash
# ¿Los modelos y las migraciones están sincronizados?
docker compose exec web python manage.py makemigrations --check --dry-run

# ¿La configuración está lista para producción?
docker compose exec -e DJANGO_DEBUG=False -e DJANGO_SECRET_KEY=x \
  -e DJANGO_ALLOWED_HOSTS=cafeto.local web python manage.py check --deploy

# ¿Quedó algún dato viejo que hoy el sistema ya no aceptaría?
docker compose exec web python manage.py auditar_consistencia

# Pasar a las tablas históricas lo que tiene más de dos años
docker compose exec web python manage.py pasar_a_historico            # simula
docker compose exec web python manage.py pasar_a_historico --ejecutar

# Si el titular perdió su clave (sólo desde el servidor)
docker compose exec web python manage.py recuperar_titular --dni 30000000

# Los 4 gráficos del Parcial N° 2 en PNG (quedan en reportes_png/ del proyecto)
docker compose exec web python manage.py generar_graficos_parcial              # últimos 90 días
docker compose exec web python manage.py generar_graficos_parcial --desde 2026-08-01 --hasta 2026-10-31
docker compose exec web python manage.py generar_graficos_parcial --dias 30 --horas 8-22
```

`generar_graficos_parcial` arma, con los datos de la base, barras comparativas
(vendido por rubro contra el período anterior del mismo largo), una dona con la
participación de cada medio de pago, las líneas de lo vendido por día con su
promedio de 7 días y un mapa de calor de tickets por día de la semana y hora.
Usa las mismas consultas que la pantalla de Reportes (`negocio/reportes.py`),
pandas para armar los datos y `negocio/graficos.py` (matplotlib) para dibujar,
con una paleta pensada para fondo blanco. Imprime la ruta de cada PNG.

---

## Uso diario

```bash
docker compose up -d          # levantar
docker compose logs -f web    # ver qué pasa
docker compose stop           # apagar sin borrar nada
```

**`docker compose down` nunca con `-v` en el NAS**: el `-v` borra el volumen
de la base y con él todos los datos.
