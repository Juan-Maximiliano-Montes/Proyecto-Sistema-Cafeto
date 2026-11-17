# Imagen del contenedor "web" (Django).
#
# La misma imagen sirve para desarrollo y para el NAS del local: lo que
# cambia es DJANGO_DEBUG. Con DEBUG el arranque usa el servidor de
# desarrollo; sin DEBUG, gunicorn (ver docker/arrancar.sh).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# WeasyPrint (los PDF: listados, etiquetas de oferta, órdenes de compra)
# necesita Pango y HarfBuzz del sistema para componer el texto.
# Tesseract (con el idioma español) lee las fotos de las facturas sin
# depender de internet ni de un servicio pago (DD-98). Sólo corre cuando
# alguien escanea una foto; no ocupa memoria el resto del tiempo.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fontconfig \
        tesseract-ocr tesseract-ocr-spa \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/app

# Las dependencias en su propia capa: mientras requirements.txt no cambie,
# Docker la reutiliza y el build es casi instantáneo.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Los estáticos (Bootstrap, íconos, fuentes, logos) se juntan y comprimen
# en la imagen: en el NAS se sirven sin internet y sin un servidor aparte.
RUN CAFETO_ESTATICOS_CON_HASH=True DJANGO_DEBUG=True \
    python manage.py collectstatic --noinput --verbosity 0

# Corre con un usuario sin privilegios: si alguien encontrara una falla en
# la aplicación, no tendría root dentro del contenedor.
RUN useradd --create-home --uid 1000 cafeto && chown -R cafeto /opt/app
USER cafeto

EXPOSE 8000
CMD ["sh", "docker/arrancar.sh"]
