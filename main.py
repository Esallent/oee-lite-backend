from app.core.auth import verificar_token_auth0, get_usuario_actual
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from app.routers import analytics, operacion, configuracion, admin # Importamos las rutas
from app.core.database import get_session, engine
from sqlmodel import Session, select, SQLModel
from app.models.domain import UsuarioSaaS, RolUsuario

# Escanea tus modelos y crea las tablas que falten en Postgres
SQLModel.metadata.create_all(engine)

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

@app.get("/ruta-secreta")
def ver_secreto(usuario_validado: dict = Depends(get_usuario_actual)):
    return {
        "mensaje": "¡Entraste a la bóveda de TYMEO!",
        "datos_del_token": usuario_validado
    }

# Conectamos el módulo de administración
app.include_router(admin.router)

@app.post("/setup/primer-admin")
def crear_primer_superadmin(
    payload: dict = Depends(verificar_token_auth0), 
    db: Session = Depends(get_session)
):
    """
    Ruta temporal: Registra el token de Auth0 actual como SUPERADMIN.
    """
    auth0_sub = payload.get("sub")
    
    # 1. Verificar si este usuario ya existe
    usuario_existente = db.exec(select(UsuarioSaaS).where(UsuarioSaaS.auth0_id == auth0_sub)).first()
    if usuario_existente:
        return {"mensaje": "Ya estás registrado en la base de datos.", "usuario": usuario_existente}
        
    # 2. Si no existe, lo creamos como dueño de TYMEO
    nuevo_admin = UsuarioSaaS(
        auth0_id=auth0_sub,
        tenant_id="tymeo_core",  # El tenant_id maestro
        rol=RolUsuario.SUPERADMIN
    )
    
    db.add(nuevo_admin)
    db.commit()
    db.refresh(nuevo_admin)
    
    return {
        "mensaje": "¡Nacimiento de TYMEO exitoso! Has sido coronado como SuperAdmin.",
        "usuario": nuevo_admin
    }

# 3. Incluimos los módulos (Routers)
app.include_router(configuracion.router)
app.include_router(operacion.router)
app.include_router(analytics.router)

# 4. Endpoints base
@app.get("/")
def health_check():
    return {"status": "ok", "mensaje": "¡El motor de OEE Lite está encendido y refactorizado!"}