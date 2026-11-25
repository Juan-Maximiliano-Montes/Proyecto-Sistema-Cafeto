#!/bin/sh
# Respaldo de la base: un pg_dump comprimido cada CAFETO_RESPALDO_CADA_MINUTOS
# (60 por defecto: se pierde como mucho una hora, RPO < 1 h). Se guardan los
# de las últimas 48 horas y, de antes, uno por día (el de la medianoche)
# durante CAFETO_RESPALDOS_DIAS (14 por defecto). Corre en el servicio
# "respaldo" de docker-compose.prod.yml. Ver "Arquitectura de despliegue
# (VPS) y respaldos".
#
# Para restaurar uno (RTO < 15 min):
#   docker compose exec -T db pg_restore --clean --if-exists --no-owner \
#     --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
#     < respaldos/cafeto-AAAAMMDD-HHMM.dump
set -e
DIAS="${CAFETO_RESPALDOS_DIAS:-14}"
MINUTOS="${CAFETO_RESPALDO_CADA_MINUTOS:-60}"
while true; do
    ARCHIVO="/respaldos/cafeto-$(date +%Y%m%d-%H%M).dump"
    if pg_dump -Fc -Z 6 --no-owner -h db -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "$ARCHIVO.tmp" \
        && [ -s "$ARCHIVO.tmp" ] \
        && pg_restore -l "$ARCHIVO.tmp" >/dev/null; then
        mv "$ARCHIVO.tmp" "$ARCHIVO"
        echo "Respaldo listo: $ARCHIVO"
    else
        rm -f "$ARCHIVO.tmp"
        echo "ERROR: no se pudo hacer el respaldo" >&2
    fi
    # Más de 48 horas: queda sólo el de la medianoche de cada día.
    find /respaldos -name 'cafeto-*.dump' ! -name 'cafeto-*-00[0-5][0-9].dump' -mmin +2880 -delete
    find /respaldos -name 'cafeto-*.dump' -mtime +"$DIAS" -delete
    sleep $((MINUTOS * 60))
done
