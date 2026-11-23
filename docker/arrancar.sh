#!/bin/sh
# Arranque del contenedor web.
#
# 1. Aplica las migraciones pendientes (la base puede venir de una versión
#    anterior del sistema).
# 2. En desarrollo (DJANGO_DEBUG=True) levanta el servidor de Django.
#
#    Con DJANGO_NORELOAD=True (el valor por defecto) arranca con
#    --noreload: UN solo proceso de Python.
#
#    El autoreload de Django levanta DOS procesos completos —el vigilante
#    (StatReloader) y el hijo que atiende—, cada uno con su copia de Django,
#    de los modelos y de las plantillas: unos 200 MB más. En una máquina de
#    4 GB corriendo además PostgreSQL, n8n y WAHA, eso terminaba en
#    "OSError: [Errno 12] Cannot allocate memory" al importar cualquier cosa
#    y el contenedor reiniciando en loop.
#
#    En un equipo con 8 GB o más conviene DJANGO_NORELOAD=False: se recarga
#    solo al editar el código y no hay que reiniciar el contenedor.
# 3. En producción, gunicorn: varios procesos, y si uno se cuelga lo
#    reemplaza sin cortar a los demás.
set -e

python manage.py migrate --noinput

es_si() {
    case "$(echo "${1:-}" | tr '[:upper:]' '[:lower:]')" in
        true|1|si|sí|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

case "$(echo "${DJANGO_DEBUG:-True}" | tr '[:upper:]' '[:lower:]')" in
    true|1|si|sí|yes)
        if es_si "${DJANGO_NORELOAD:-True}"; then
            exec python manage.py runserver --noreload 0.0.0.0:8000
        fi
        exec python manage.py runserver 0.0.0.0:8000
        ;;
    *)
        exec gunicorn cafeto.wsgi:application \
            --bind 0.0.0.0:8000 \
            --workers "${GUNICORN_WORKERS:-3}" \
            --timeout 60 \
            --access-logfile - \
            --error-logfile -
        ;;
esac
