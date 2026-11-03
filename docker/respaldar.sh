#!/bin/sh
# Respaldo diario de la base: un pg_dump comprimido por día, se guardan los
# últimos CAFETO_RESPALDOS_DIAS (14 por defecto). Corre en el servicio
# "respaldo" de docker-compose.prod.yml.
#
# Para restaurar uno:
#   gunzip -c respaldos/cafeto-AAAAMMDD-HHMM.sql.gz | \
#     docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
set -e
DIAS="${CAFETO_RESPALDOS_DIAS:-14}"
while true; do
    ARCHIVO="/respaldos/cafeto-$(date +%Y%m%d-%H%M).sql.gz"
    if pg_dump -h db -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner | gzip > "$ARCHIVO.tmp"; then
        mv "$ARCHIVO.tmp" "$ARCHIVO"
        echo "Respaldo listo: $ARCHIVO"
    else
        rm -f "$ARCHIVO.tmp"
        echo "ERROR: no se pudo hacer el respaldo" >&2
    fi
    find /respaldos -name 'cafeto-*.sql.gz' -mtime +"$DIAS" -delete
    sleep 86400
done
