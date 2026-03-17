from fastapi import APIRouter, Depends, HTTPException, Path, UploadFile, File
from sqlmodel import Session, select
from app.core.database import get_session
from app.models.domain import Estacion, MotivoParada, Operario, Turno, MaestroSKU, OrdenProduccion
from pydantic import BaseModel
from typing import Optional
import uuid
import pandas as pd
import io

router = APIRouter(tags=["Configuracion y Maestros"])

# --- MOLDES ---
class EstacionUpdate(BaseModel):
    nombre: Optional[str] = None
    tipo: Optional[str] = None
    umbral_optimo: Optional[int] = None
    umbral_lento: Optional[int] = None
    umbral_alerta: Optional[int] = None
    activa: Optional[bool] = None
    posicion_linea: Optional[int] = None
    ramal: Optional[str] = None

# --- ENDPOINTS ---
@router.post("/estaciones/", response_model=Estacion)
def crear_estacion(estacion: Estacion, db: Session = Depends(get_session)):
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    return estacion

@router.get("/estaciones/", response_model=list[Estacion])
def obtener_estaciones(tenant_id: str, db: Session = Depends(get_session)):
    return db.exec(select(Estacion).where(Estacion.tenant_id == tenant_id)).all()

@router.patch("/estaciones/{estacion_id}", response_model=Estacion)
def actualizar_estacion(
    estacion_id: uuid.UUID = Path(..., description="El ID de la estación a editar"),
    datos_update: EstacionUpdate = None,
    db: Session = Depends(get_session)
):
    """
    Permite modificar dinámicamente los parámetros de una estación (ej. umbrales de tiempo).
    Solo se actualizan los campos que se envían en el JSON.
    """
    # 1. Buscamos la estación existente
    estacion_db = db.get(Estacion, estacion_id)
    if not estacion_db:
        raise HTTPException(status_code=404, detail="Estación no encontrada")
    
    # 2. Extraemos solo los datos que nos enviaron para cambiar
    update_data = datos_update.model_dump(exclude_unset=True) 
    
    # 3. Aplicamos los cambios uno por uno
    for key, value in update_data.items():
        setattr(estacion_db, key, value)
        
    # 4. Guardamos en la base de datos
    db.add(estacion_db)
    db.commit()
    db.refresh(estacion_db)
    
    return estacion_db


@router.post("/motivos-parada/", response_model=MotivoParada)
def crear_motivo_parada(motivo: MotivoParada, db: Session = Depends(get_session)):
    """Crea un motivo de parada indicando si es PLANIFICADA o NO_PLANIFICADA"""
    db.add(motivo)
    db.commit()
    db.refresh(motivo)
    return motivo

@router.get("/motivos-parada/", response_model=list[MotivoParada])
def obtener_motivos_parada(tenant_id: str, db: Session = Depends(get_session)):
    """Lista todos los motivos de parada configurados para la empresa"""
    return db.exec(select(MotivoParada).where(MotivoParada.tenant_id == tenant_id)).all()

@router.post("/operarios/", response_model=Operario)
def crear_operario(operario: Operario, db: Session = Depends(get_session)):
    """Da de alta un nuevo operario en la planta"""
    db.add(operario)
    db.commit()
    db.refresh(operario)
    return operario

@router.get("/operarios/", response_model=list[Operario])
def obtener_operarios(tenant_id: str = "empresa_demo", db: Session = Depends(get_session)):
    """Lista todos los operarios activos"""
    return db.exec(select(Operario).where(Operario.tenant_id == tenant_id)).all()

@router.post("/turnos/", response_model=Turno)
def crear_turno(turno: Turno, db: Session = Depends(get_session)):
    """Crea una franja horaria de trabajo (Ej: Turno Mañana)"""
    db.add(turno)
    db.commit()
    db.refresh(turno)
    return turno

# ==========================================
# IMPORTADORES MASIVOS (FASE 2)
# ==========================================
@router.post("/upload/skus/")
def importar_maestro_skus(
    tenant_id: str, 
    file: UploadFile = File(...), 
    db: Session = Depends(get_session)
):
    """Sincroniza el catálogo de SKUs desde Excel/CSV."""
    contenido = file.file.read()
    try:
        if file.filename.lower().endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contenido), sep=None, engine='python', encoding='utf-8-sig')
        else:
            df = pd.read_excel(io.BytesIO(contenido))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error de lectura: {str(e)}")

    # Limpieza de columnas
    df.columns = [str(c).replace('\ufeff', '').replace(';', '').strip().upper() for c in df.columns]
    
    if "SKU" not in df.columns or "DESCRIPCION" not in df.columns:
        raise HTTPException(status_code=400, detail=f"Faltan columnas obligatorias. Detectadas: {df.columns.tolist()}")

    creados, actualizados = 0, 0
    for _, row in df.iterrows():
        sku_code = str(row["SKU"]).strip()
        if not sku_code or sku_code.lower() == "nan": continue
        
        sku_db = db.exec(select(MaestroSKU).where(MaestroSKU.tenant_id == tenant_id, MaestroSKU.codigo_sku == sku_code)).first()
        
        if sku_db:
            sku_db.descripcion = str(row["DESCRIPCION"]).strip()
            actualizados += 1
        else:
            db.add(MaestroSKU(tenant_id=tenant_id, codigo_sku=sku_code, descripcion=str(row["DESCRIPCION"]).strip(), tiempo_ciclo_teorico=240.0))
            creados += 1
    
    db.commit()
    return {"status": "ok", "mensaje": f"Catálogo actualizado. Creados: {creados}, Actualizados: {actualizados}."}

@router.post("/upload/plan/")
def importar_plan_produccion(
    tenant_id: str, 
    file: UploadFile = File(...), 
    db: Session = Depends(get_session)
):
    """Carga el plan de producción diario con los campos exactos de la DB."""
    contenido = file.file.read()
    try:
        df = pd.read_csv(io.BytesIO(contenido), sep=None, engine='python', header=None, encoding='utf-8-sig')
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error en Plan: {str(e)}")

    lineas = 0
    for i in range(len(df)):
        try:
            fila = df.iloc[i]
            sku_id = str(fila[0]).strip()      # Columna A
            cantidad = int(fila[3])            # Columna D
            fecha_plan = str(fila[4]).strip()  # Columna E
            
            nueva_op = OrdenProduccion(
                tenant_id=tenant_id,
                id_orden=f"OP-{sku_id[:5]}-{i}", 
                plan_fecha=fecha_plan,
                estado="abierta" 
            )
            db.add(nueva_op)
            lineas += 1
        except Exception as e:
            continue

    db.commit()
    return {"status": "ok", "mensaje": f"Plan cargado. {lineas} órdenes listas para fabricar."}

# ==========================================
# ENDPOINTS MANUALES Y UTILERÍA (ADMIN)
# ==========================================
@router.post("/skus/", response_model=MaestroSKU)
def crear_sku_manual(sku: MaestroSKU, db: Session = Depends(get_session)):
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku

@router.post("/ordenes/", response_model=OrdenProduccion)
def crear_orden_manual(orden: OrdenProduccion, db: Session = Depends(get_session)):
    db.add(orden)
    db.commit()
    db.refresh(orden)
    return orden

@router.post("/setup-springwall/")
def setup_springwall(tenant_id: str = "empresa_demo", db: Session = Depends(get_session)):
    """Carga las estaciones predeterminadas de la fábrica."""
    viejas = db.exec(select(Estacion).where(Estacion.tenant_id == tenant_id)).all()
    for v in viejas: db.delete(v)
    db.commit()

    e1 = Estacion(tenant_id=tenant_id, nombre="E1 - Pedalera (Ingreso)", tipo="sensor", posicion_linea=1, umbral_optimo=240, umbral_lento=280, umbral_alerta=300)
    e2 = Estacion(tenant_id=tenant_id, nombre="E2 - Matelaceado", tipo="sensor", posicion_linea=2, umbral_optimo=240, umbral_lento=280, umbral_alerta=300)
    e3 = Estacion(tenant_id=tenant_id, nombre="E3 - Forro/Escaneo", tipo="escaneo_manual", posicion_linea=3, umbral_optimo=240, umbral_lento=280, umbral_alerta=300)
    db.add_all([e1, e2, e3])
    db.commit()

    cerradora_a_padre = Estacion(tenant_id=tenant_id, nombre="E4 - Cerradora A (Total)", tipo="escaneo_manual", posicion_linea=4, ramal="Línea A", umbral_optimo=240, umbral_lento=280, umbral_alerta=300)
    db.add(cerradora_a_padre)
    db.commit()
    db.refresh(cerradora_a_padre)

    sub_a1 = Estacion(tenant_id=tenant_id, nombre="E4.1 - Cerradora A (Etapa 1)", tipo="escaneo_manual", parent_id=cerradora_a_padre.id, posicion_linea=4, ramal="Línea A", umbral_optimo=120, umbral_lento=140, umbral_alerta=150)
    sub_a2 = Estacion(tenant_id=tenant_id, nombre="E4.2 - Cerradora A (Etapa 2)", tipo="escaneo_manual", parent_id=cerradora_a_padre.id, posicion_linea=4, ramal="Línea A", umbral_optimo=120, umbral_lento=140, umbral_alerta=150)
    db.add_all([sub_a1, sub_a2])

    cerradora_b = Estacion(tenant_id=tenant_id, nombre="E5 - Cerradora B", tipo="escaneo_manual", posicion_linea=4, ramal="Línea B", umbral_optimo=240, umbral_lento=280, umbral_alerta=300)
    calidad_a = Estacion(tenant_id=tenant_id, nombre="E6 - Calidad A", tipo="calidad", posicion_linea=5, ramal="Línea A", umbral_optimo=120, umbral_lento=180, umbral_alerta=181)
    calidad_b = Estacion(tenant_id=tenant_id, nombre="E7 - Calidad B", tipo="calidad", posicion_linea=5, ramal="Línea B", umbral_optimo=120, umbral_lento=180, umbral_alerta=181)
    
    db.add_all([cerradora_b, calidad_a, calidad_b])
    db.commit()

    return {"status": "ok", "mensaje": "Línea Springwall cargada."}

@router.delete("/reset-db-danger/")
def reset_base_de_datos():
    """
    ¡PELIGRO! Solo para desarrollo. 
    Borra TODA la base de datos y la recrea.
    """
    from app.core.database import engine
    from sqlmodel import SQLModel
    try:
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        return {"status": "ok", "mensaje": "Base de datos reseteada. ¡Las columnas nuevas ya existen!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al resetear: {str(e)}")