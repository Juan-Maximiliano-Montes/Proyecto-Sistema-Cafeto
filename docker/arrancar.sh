#!/bin/sh
# Arranque del contenedor web.
#
# 1. Aplica las migraciones pendientes (la base puede venir de una versión
#    anterior del sistema).
# 2. En desarrollo (DJANGO_DEBUG=True) levanta el servidor de Django, que
#    recarga solo al editar el código.
# 3. En producción, gunicorn: varios procesos, y si uno se cuelga lo
#    reemplaza sin cortar a los demás.
set -e

python manage.py migrate --noinput

case "$(echo "${DJANGO_DEBUG:-True}" | tr '[:upper:]' '[:lower:]')" in
    true|1|si|sí|yes)
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
