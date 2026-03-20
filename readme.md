# 🏭 OEE Lite - Backend API

![Versión](https://img.shields.io/badge/Versi%C3%B3n-1.0.0-blue)
![Framework](https://img.shields.io/badge/Framework-FastAPI-009688)
![ORM](https://img.shields.io/badge/ORM-SQLModel-informational)
![Database](https://img.shields.io/badge/Database-PostgreSQL-316192)

Backend B2B Multi-Tenant de grado industrial para el cálculo del **OEE (Overall Equipment Effectiveness)** en tiempo real. Diseñado para procesar escaneos en líneas de producción, asignar operarios dinámicamente y alimentar dashboards gerenciales con latencia mínima.

---

## 🚀 Características Principales

* **Motor Transaccional Inteligente:** Procesa códigos de barras estándar de 25 caracteres y los asigna automáticamente a Órdenes de Producción y SKUs.
* **Trazabilidad de Tiempos y Paradas:** Mide dinámicamente el tiempo de ciclo. Si se supera el umbral configurado, autodetecta paradas y las deja pendientes de clasificación.
* **Gestión de Fuerza Laboral:** Soporta múltiples modalidades de log-in: pre-asignación planificada, post-asignación masiva y log-in físico en vivo mediante escaneo de credencial de operario (`OP-XXX`).
* **Suite Analítica Integrada:** Endpoints optimizados para alimentar aplicaciones Front-end con métricas consolidadas (Calidad, Rendimiento, Disponibilidad, Cuellos de Botella y Pareto).

---

## 🏗️ Arquitectura del Proyecto

El sistema utiliza una arquitectura modular basada en `APIRouter` para garantizar la separación de responsabilidades y facilitar el despliegue continuo (CI/CD) en la nube (GCP).

```text
oee-lite-backend/
│
├── app/
│   ├── core/
│   │   └── database.py       # Configuración y motor de base de datos PostgreSQL
│   ├── models/
│   │   └── domain.py         # Modelos de dominio (Tablas y Relaciones SQLModel)
│   └── routers/
│       ├── configuracion.py  # Módulo de ABMs, Maestros y Cargas Masivas (Excel/CSV)
│       ├── operacion.py      # Módulo Transaccional (Escaneos, Paradas, Asignaciones)
│       └── analytics.py      # Módulo de Lectura (Dashboards, Alertas Vivas, Reportes)
│
├── main.py                   # Entrypoint de la aplicación y configuración CORS
├── requirements.txt          # Dependencias del entorno
├── .gitignore                # Archivos excluidos del control de versiones
└── README.md                 # Documentación técnica
📚 Estructura de la API
La API está dividida en tres grandes dominios lógicos. Todas las peticiones requieren el parámetro tenant_id para garantizar el aislamiento de datos entre empresas.

1. ⚙️ Configuración y Maestros (/routers/configuracion.py)
Encargado del setup de la fábrica.

Catálogos: ABM de Estaciones, Operarios, Turnos, Motivos de Parada.

Cargas Masivas: Endpoints /upload/skus/ y /upload/plan/ para importar datos vía archivos .csv o Excel.

Utilería: Endpoints de inicialización de planta (ej. /setup-springwall/).

2. ⚡ Operación (/routers/operacion.py)
El núcleo transaccional en tiempo real.

Escaneos (POST /eventos/): Recibe los códigos físicos de la línea, calcula tiempos, cruza turnos y registra el desempeño (OPTIMO, LENTO, ALERTA).

Paradas: Endpoints para listar paradas pendientes (GET /paradas/pendientes/), justificarlas (PATCH /paradas/{id}/clasificar) y registrar paradas pre-planificadas.

Asignaciones: Gestión de la relación Operario-Máquina.

3. 📊 Analytics (/routers/analytics.py)
Endpoints de solo lectura (GET) protegidos con límites de paginación de seguridad, diseñados para consumo directo del Front-end.

/reportes/dashboard: Zoom-in por estación con contadores y semáforos.

/analytics/oee-general/: Tarjetas métricas gerenciales (Disponibilidad, Rendimiento, Calidad).

/analytics/reporte-operarios/: Rendimiento humano cruzando producción real vs. tiempo invertido.

/analytics/pareto-paradas/: Sumatoria de tiempos muertos para identificación de causa raíz.

/analytics/cuellos-botella/: Ranking de desviación de velocidad real vs. ideal por máquina.

/analytics/alertas-vivas/: Feed en tiempo real de paradas no clasificadas y defectos crónicos.

🛠️ Instalación y Entorno Local
Sigue estos pasos para levantar el servidor de desarrollo localmente.

1. Clonar el repositorio
Bash
git clone [https://github.com/TU_USUARIO/oee-lite-backend.git](https://github.com/TU_USUARIO/oee-lite-backend.git)
cd oee-lite-backend
2. Crear y activar el entorno virtual
Bash
# Crear entorno
python -m venv venv

# Activar en Windows:
venv\Scripts\activate
# Activar en Mac/Linux:
source venv/bin/activate
3. Instalar dependencias
Bash
pip install -r requirements.txt
4. Variables de Entorno (.env)
Crea un archivo llamado .env en la raíz del proyecto y configura tu conexión a la base de datos PostgreSQL:

Fragmento de código
DATABASE_URL=postgresql://tu_usuario:tu_password@localhost:5432/oee_lite_db
(Nota: Asegúrate de que tu PostgreSQL local esté corriendo y la base de datos exista).

5. Arrancar el servidor
Bash
uvicorn main:app --reload
La API estará disponible en: http://127.0.0.1:8000

📖 Documentación Interactiva
FastAPI provee interfaces visuales automáticas para interactuar con la API sin necesidad de clientes externos como Postman:

👉 Swagger UI (Recomendado) 👉 ReDoc

Desarrollado para la digitalización y mejora continua en entornos de manufactura.