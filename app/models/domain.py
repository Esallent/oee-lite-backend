import uuid
from datetime import datetime, time, date
from enum import Enum
from typing import List, Optional
from sqlmodel import SQLModel, Field, Relationship, Index

# ==========================================
# 1. ENUMS (Lógica de Negocio B2B)
# ==========================================
class TipoParada(str, Enum):
    PLANIFICADA = "planificada"
    NO_PLANIFICADA = "no_planificada"

class EstadoOrden(str, Enum):
    ABIERTA = "abierta"
    EN_PROGRESO = "en_progreso"
    CERRADA = "cerrada"

class EstadoParada(str, Enum):
    PENDIENTE = "pendiente"       # Gap detectado automáticamente, esperando al supervisor
    CLASIFICADA = "clasificada"   # El supervisor ya le asignó un motivo

# ==========================================
# 2. MIXIN B2B MULTI-TENANT
# ==========================================
class TenantBase(SQLModel):
    """Garantiza el aislamiento B2B. Todas las tablas lo heredan."""
    tenant_id: str = Field(index=True, description="ID del cliente/tenant")

# ==========================================
# 3. PLANTA FÍSICA Y PERSONAL
# ==========================================
class Estacion(TenantBase, table=True):
    __tablename__ = "dim_estaciones"
    
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    nombre: str
    tipo: str  # Ej: "sensor", "escaneo_manual", "calidad"
    
    # --- NUEVOS CAMPOS: Configuración Dinámica OEE ---
    umbral_optimo: int = Field(default=240, description="Tiempo ideal en segundos")
    umbral_lento: int = Field(default=280, description="Límite de tiempo aceptable")
    umbral_alerta: int = Field(default=300, description="Tiempo que dispara alerta")
    
    # Grafo y Topología (Intacto)
    activa: bool = Field(default=True, description="Apagar si hoy no se usa")
    posicion_linea: int = Field(default=1, description="Secuencia lógica (1,2,3...)")
    ramal: str = Field(default="Principal", description="Ej: Principal, Ramal A, Ramal B")
    
    # Jerarquía Padre-Hijo (Intacto)
    parent_id: Optional[uuid.UUID] = Field(default=None, foreign_key="dim_estaciones.id")

class Operario(TenantBase, table=True):
    __tablename__ = "dim_operarios"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    legajo: str = Field(index=True)
    nombre_completo: str

class Turno(TenantBase, table=True):
    __tablename__ = "dim_turnos"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    nombre: str
    hora_inicio: time
    hora_fin: time
    descanso_minutos: int = Field(default=0, description="Minutos a descontar de la Disponibilidad")

class AsignacionTurno(TenantBase, table=True):
    __tablename__ = "asignaciones_turno"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    fecha: date = Field(default_factory=datetime.today)
    
    estacion_fk: uuid.UUID = Field(foreign_key="dim_estaciones.id")
    operario_fk: uuid.UUID = Field(foreign_key="dim_operarios.id")
    turno_fk: uuid.UUID = Field(foreign_key="dim_turnos.id")

# ==========================================
# 4. CATÁLOGO Y ÓRDENES (Input del ERP)
# ==========================================
class MaestroSKU(TenantBase, table=True):
    __tablename__ = "maestro_skus"
    codigo_sku: str = Field(primary_key=True, description="El código real del ERP")
    descripcion: str
    modelo: Optional[str] = None   # Ej: "KING REST"
    medida: Optional[str] = None   # Ej: "140x190"
    
    tiempo_ciclo_teorico: float = Field(default=240.0, description="Segundos ideales por unidad")
    umbral_calidad: float = Field(default=1800.0, description="Tolerancia en estación de calidad")

class OrdenProduccion(TenantBase, table=True):
    __tablename__ = "ordenes_produccion"
    id_orden: str = Field(primary_key=True, description="Número de OP del ERP (Ej: 20157385)")
    plan_fecha: Optional[str] = Field(default=None, description="Ej: DIA09")
    estado: EstadoOrden = Field(default=EstadoOrden.ABIERTA)

class ItemOrden(TenantBase, table=True):
    """Detalle de la Orden (Relación Maestro-Detalle)"""
    __tablename__ = "items_orden"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    orden_fk: str = Field(foreign_key="ordenes_produccion.id_orden")
    sku_fk: str = Field(foreign_key="maestro_skus.codigo_sku")
    cantidad_target: int

class MotivoParada(TenantBase, table=True):
    __tablename__ = "dim_motivos_parada"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    nombre: str
    tipo_parada: TipoParada

# ==========================================
# 5. TRANSACCIONES (Motor OEE)
# ==========================================
class EventoEscaneo(TenantBase, table=True):
    __tablename__ = "eventos_escaneo"
    __table_args__ = (
        Index("ix_tenant_barcode", "tenant_id", "barcode"),
    )
    
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    barcode: str = Field(description="El código completo de 25 caracteres")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    # --- VINCULACIONES ---
    estacion_fk: uuid.UUID = Field(foreign_key="dim_estaciones.id")
    orden_fk: Optional[str] = Field(default=None, foreign_key="ordenes_produccion.id_orden")
    operario_fk: Optional[uuid.UUID] = Field(default=None, foreign_key="dim_operarios.id")
    
    # --- RESULTADOS CALCULADOS (NUEVOS) ---
    # Este campo se llena automáticamente al comparar con la estación_fk
    desempeno: Optional[str] = Field(default=None, description="OPTIMO, LENTO o ALERTA")
    
    # Tiempo real detectado entre estaciones (Opcional, muy útil para reportes)
    segundos_proceso: Optional[int] = Field(default=None)
    
    # Flag de calidad (ALERTA en estación tipo 'calidad' activa esto)
    es_retrabajo: bool = Field(default=False)
    
class ParadaDetectada(TenantBase, table=True):
    __tablename__ = "paradas_detectadas"
    
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    estacion_fk: uuid.UUID = Field(foreign_key="dim_estaciones.id")
    inicio: datetime
    fin: Optional[datetime] = None
    duracion_segundos: Optional[float] = None
    
    estado: EstadoParada = Field(default=EstadoParada.PENDIENTE)
    motivo_fk: Optional[uuid.UUID] = Field(default=None, foreign_key="dim_motivos_parada.id")