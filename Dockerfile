# Imagen del contenedor "web" (Django).
#
# Antes este servicio usaba la imagen oficial de Python SIN Dockerfile y las
# librerias se instalaban A MANO entrando al contenedor. Con este Dockerfile
# la instalacion queda registrada y reproducible: "docker compose up --build"
# levanta el sistema completo sin pasos manuales.
FROM python:3.11-slim

# No escribe los .pyc al disco montado y manda los logs sin buffer, para
# que "docker compose logs web" muestre los mensajes en el momento.
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /opt/app

# Las dependencias van en una capa propia: mientras requirements.txt no
# cambie, Docker reutiliza la capa y el build es casi instantaneo.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Al arrancar: primero aplica las migraciones pendientes, despues levanta el
# servidor de desarrollo escuchando en todas las interfaces del contenedor.
CMD ["sh","-c","python manage.py migrate --noinput && python manage.py runserver 0.0.0.0:8000"]
