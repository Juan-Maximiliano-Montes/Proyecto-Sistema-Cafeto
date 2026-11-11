#!/bin/sh
# Pase a histórico programado (RPH, DD-68): el día 1 de cada mes a las
# 03:30 (hora de Salta) corre "manage.py pasar_a_historico --ejecutar".
# Corre en el servicio "historico" de docker-compose.prod.yml, con la
# misma imagen que la aplicación.
#
# Lo que hizo queda en el registro del contenedor:
#   docker compose -f docker-compose.yml -f docker-compose.prod.yml logs historico
#
# Para correrlo a mano (por ejemplo, para ver qué se archivaría sin mover
# nada):  docker compose exec web python manage.py pasar_a_historico
set -u
while true; do
    ESPERA=$(python -c "import datetime as d; a = d.datetime.now(); p = a.replace(hour=3, minute=30, second=0, microsecond=0); p = p if p > a else p + d.timedelta(days=1); print(int((p - a).total_seconds()))")
    sleep "$ESPERA"
    if [ "$(date +%d)" = "01" ]; then
        echo "== Pase a histórico $(date '+%d/%m/%Y %H:%M') =="
        if python manage.py pasar_a_historico --ejecutar; then
            echo "Pase a histórico terminado."
        else
            echo "ERROR: el pase a histórico no terminó; no se movió nada (es una sola transacción)." >&2
        fi
    fi
done
