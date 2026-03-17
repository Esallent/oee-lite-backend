from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import analytics, operacion, configuracion # Importamos las rutas

# 1. Inicializamos la app
app = FastAPI(
    title="OEE Lite API",
    description="API B2B Multi-Tenant para captura de datos OEE en tiempo real",
    version="1.0.0"
)

# 2. Configuración CORS (Vital para que el Front-end se conecte)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. Incluimos los módulos (Routers)
app.include_router(configuracion.router)
app.include_router(operacion.router)
app.include_router(analytics.router)

# 4. Endpoints base
@app.get("/")
def health_check():
    return {"status": "ok", "mensaje": "¡El motor de OEE Lite está encendido y refactorizado!"}