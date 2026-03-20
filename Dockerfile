# Usa la imagen oficial de Python 3.11 en su versión slim para reducir tamaño
FROM python:3.11-slim

# Evita que Python escriba archivos .pyc y fuerza a que stdout/stderr no tengan buffer
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Directorio de trabajo
WORKDIR /app

# Instala dependencias del sistema necesarias para psycopg2
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Instala los requerimientos de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia el código de la aplicación
COPY ./app /app/app
COPY ./main.py /app/main.py
COPY ./alembic.ini /app/alembic.ini
COPY ./alembic /app/alembic

# Crea un usuario no root por seguridad (Best Practice en Cloud Run)
RUN adduser --disabled-password --gecos "" appuser \
    && chown -R appuser /app
USER appuser

# Expone el puerto que espera Cloud Run (por defecto 8080)
EXPOSE 8080

# Cambiamos main:app por app.main:app (porque está en la carpeta app)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]