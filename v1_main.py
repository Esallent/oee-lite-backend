from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, APIRouter, Path
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import SQLModel, Session, select
from app.core.database import get_session, engine
from app.models.domain import Estacion, EventoEscaneo, MaestroSKU, OrdenProduccion, MotivoParada, TipoParada, EstadoParada, ParadaDetectada, Operario, Turno, AsignacionTurno
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, time, date
import uuid
import pandas as pd
import io

# Inicializamos la aplicación FastAPI
app = FastAPI(
    title="OEE Lite API",
    description="API B2B Multi-Tenant para captura de datos OEE en tiempo real",
    version="1.0.0"
)

# --- NUEVO: CONFIGURACIÓN CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Permite que cualquier front-end se conecte
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# --- Molde para el Reporte del Dashboard ---
class MetricasEstacion(BaseModel):
    estacion_nombre: str
    total_piezas: int
    optimos: int
    lentos: int
    alertas: int
    retrabajos: int
    tiempo_promedio_seg: float

class OeeGeneralCard(BaseModel):
    disponibilidad_pct: float
    rendimiento_pct: float
    calidad_pct: float
    oee_general_pct: float
    total_unidades: int
    unidades_con_retrabajo: int
    minutos_desvio_calidad: float

class ReporteOperarioSpringwall(BaseModel):
    operario_nombre: str
    estacion_nombre: str
    cantidad_real: int
    cantidad_esperada: int
    diferencia_pct: float

class ParetoParadas(BaseModel):
    motivo: str
    tipo: str
    frecuencia: int
    minutos_totales: float

class CuelloBotella(BaseModel):
    estacion: str
    tiempo_esperado_seg: float
    tiempo_promedio_real_seg: float
    desvio_pct: float

class AlertaActiva(BaseModel):
    hora: str
    estacion: str
    tipo: str # Ej: "PARADA_PENDIENTE", "RETRABAJO", "LENTITUD_EXTREMA"
    mensaje: str

class BarcodeDecodificado(BaseModel):
    secuencia: str
    orden_produccion: str
    codigo_sku: str
    codigo_original: str

# --- Molde para actualizar (solo los campos que queramos cambiar) ---
class EstacionUpdate(BaseModel):
    nombre: Optional[str] = None
    tipo: Optional[str] = None
    umbral_optimo: Optional[int] = None
    umbral_lento: Optional[int] = None
    umbral_alerta: Optional[int] = None
    activa: Optional[bool] = None
    posicion_linea: Optional[int] = None
    ramal: Optional[str] = None


class ClasificarParada(BaseModel):
    motivo_fk: uuid.UUID

class AsignacionRetroactiva(BaseModel):
    estacion_fk: uuid.UUID
    operario_fk: uuid.UUID
    inicio: datetime
    fin: datetime


# Molde para los datos que enviará el supervisor
class ParadaPlanificadaCreate(BaseModel):
    estacion_fk: uuid.UUID
    motivo_fk: uuid.UUID
    inicio: datetime
    fin: datetime

# ==========================================
# LÓGICA DE NEGOCIO: PARSER DE CÓDIGOS
# ==========================================

def parsear_barcode(barcode: str) -> BarcodeDecodificado:
    """Descompone el código de 25 caracteres de la fábrica."""
    barcode = barcode.strip()
    if len(barcode) < 25:
        raise ValueError(f"Código corto ({len(barcode)} caracteres). Se esperaban 25.")

    return BarcodeDecodificado(
        secuencia=barcode[0:3],
        orden_produccion=barcode[3:11],
        codigo_sku=barcode[11:],
        codigo_original=barcode
    )

@app.get("/test-parser/{barcode}", tags=["Pruebas"])
def probar_parser(barcode: str):
    try:
        return {"status": "ok", "data": parsear_barcode(barcode)}
    except Exception as e:
        return {"status": "error", "detalle": str(e)}


# ==========================================
# ENDPOINTS BÁSICOS Y ESTACIONES
# ==========================================
@app.get("/")
def health_check():
    return {"status": "ok", "mensaje": "¡El motor de OEE Lite está encendido!"}

@app.post("/estaciones/", response_model=Estacion, tags=["Configuracion"])
def crear_estacion(estacion: Estacion, db: Session = Depends(get_session)):
    db.add(estacion)
    db.commit()
    db.refresh(estacion)
    return estacion

@app.get("/estaciones/", response_model=list[Estacion], tags=["Configuracion"])
def obtener_estaciones(tenant_id: str, db: Session = Depends(get_session)):
    return db.exec(select(Estacion).where(Estacion.tenant_id == tenant_id)).all()

@app.patch("/estaciones/{estacion_id}", response_model=Estacion, tags=["Configuracion"])
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
    # Nota: Si usas Pydantic v2, puede que te pida usar .model_dump() en lugar de .dict()
    update_data = datos_update.model_dump(exclude_unset=True) 
    
    # 3. Aplicamos los cambios uno por uno
    for key, value in update_data.items():
        setattr(estacion_db, key, value)
        
    # 4. Guardamos en la base de datos
    db.add(estacion_db)
    db.commit()
    db.refresh(estacion_db)
    
    return estacion_db


# ==========================================
# TRANSACCIONES OEE
# ==========================================
@app.post("/eventos/", response_model=EventoEscaneo, tags=["Operacion"])
def registrar_evento(evento: EventoEscaneo, db: Session = Depends(get_session)):
    # --- PROTECCIÓN DE TIEMPO ---
    # Si el timestamp viene como string (desde Swagger), lo convertimos a datetime
    if isinstance(evento.timestamp, str):
        evento.timestamp = datetime.fromisoformat(evento.timestamp.replace("Z", ""))

    # 1. Traemos la configuración de la estación
    estacion = db.get(Estacion, evento.estacion_fk)
    if not estacion:
        raise HTTPException(status_code=404, detail="Estación no encontrada")

    # ==============================================================
    # --- NUEVO: DETECCIÓN DE CREDENCIAL DE OPERARIO (OPCIÓN 3) ---
    # ==============================================================
    # Si el código escaneado empieza con "OP-", no es un colchón, es un operario logueándose.
    if evento.barcode.startswith("OP-"):
        # Buscamos al operario por su legajo
        operario = db.exec(select(Operario).where(Operario.legajo == evento.barcode)).first()
        if not operario:
            raise HTTPException(status_code=404, detail="Credencial de operario no reconocida")
        
        # Buscamos si hay un turno activo ahora para crear la asignación
        hora_actual = evento.timestamp.time()
        turno_actual = db.exec(
            select(Turno).where(Turno.hora_inicio <= hora_actual, Turno.hora_fin >= hora_actual)
        ).first()

        if not turno_actual:
            raise HTTPException(status_code=400, detail="No hay un turno configurado para esta hora")

        # Creamos (o actualizamos) la asignación para el resto del turno
        nueva_asig = AsignacionTurno(
            tenant_id=evento.tenant_id,
            fecha=evento.timestamp.date(),
            estacion_fk=estacion.id,
            operario_fk=operario.id,
            turno_fk=turno_actual.id
        )
        db.add(nueva_asig)
        db.commit()
        
        # Devolvemos un evento especial o simplemente confirmamos el login
        evento.desempeno = "LOGIN_OPERARIO"
        return evento
    # ==============================================================

    # 2. Si no es un operario, seguimos con la lógica de Colchón...
    datos_barcode = parsear_barcode(evento.barcode)
    evento.orden_fk = datos_barcode.orden_produccion

    # --- Lógica de Pre-asignación (Opción 1) ---
    hora_actual = evento.timestamp.time()
    fecha_actual = evento.timestamp.date()

    asignacion_hoy = db.exec(
        select(AsignacionTurno, Turno)
        .join(Turno, AsignacionTurno.turno_fk == Turno.id)
        .where(
            AsignacionTurno.tenant_id == evento.tenant_id,
            AsignacionTurno.estacion_fk == estacion.id,
            AsignacionTurno.fecha == fecha_actual,
            Turno.hora_inicio <= hora_actual,
            Turno.hora_fin >= hora_actual
        )
    ).first()

    if asignacion_hoy:
        asignacion, turno = asignacion_hoy
        evento.operario_fk = asignacion.operario_fk

    # 3. Lógica Dinámica de Tiempos
    ultimo_evento = db.exec(
        select(EventoEscaneo)
        .where(EventoEscaneo.tenant_id == evento.tenant_id, EventoEscaneo.barcode == evento.barcode)
        .order_by(EventoEscaneo.timestamp.desc())
    ).first()

    # 4. Cálculo de Desempeño y Paradas (Igual que antes...)
    if ultimo_evento:
        diff_segundos = (evento.timestamp - ultimo_evento.timestamp).total_seconds()
        evento.segundos_proceso = int(diff_segundos) 
        
        if diff_segundos > 150: # Umbral de parada
            evento.desempeno = "ALERTA"
            nueva_parada = ParadaDetectada(
                tenant_id=evento.tenant_id, estacion_fk=estacion.id,
                inicio=ultimo_evento.timestamp, fin=evento.timestamp,
                duracion_segundos=diff_segundos, estado=EstadoParada.PENDIENTE
            )
            db.add(nueva_parada)
        elif diff_segundos <= estacion.umbral_optimo:
            evento.desempeno = "OPTIMO"
        elif diff_segundos <= estacion.umbral_lento:
            evento.desempeno = "LENTO"
        else:
            evento.desempeno = "ALERTA"
            if estacion.tipo.lower() == "calidad":
                evento.es_retrabajo = True
    else:
        evento.desempeno = "INICIO"
        evento.segundos_proceso = 0

    db.add(evento)
    db.commit()
    db.refresh(evento)
    return evento


@app.post("/operarios/asignar-retroactivo/", tags=["Operacion"])
def asignar_operario_retroactivo(datos: AsignacionRetroactiva, tenant_id: str = "empresa_demo", db: Session = Depends(get_session)):
    """Asigna masivamente un operario a todos los eventos de una estación en un rango de tiempo."""
    
    # 1. Validar que el operario exista
    operario = db.get(Operario, datos.operario_fk)
    if not operario:
        raise HTTPException(status_code=404, detail="Operario no encontrado")

    # 2. Buscar todos los eventos que coincidan con la estación y el rango de tiempo
    eventos = db.exec(
        select(EventoEscaneo).where(
            EventoEscaneo.tenant_id == tenant_id,
            EventoEscaneo.estacion_fk == datos.estacion_fk,
            EventoEscaneo.timestamp >= datos.inicio,
            EventoEscaneo.timestamp <= datos.fin
        )
    ).all()

    # Si no hay eventos, avisamos amablemente
    if not eventos:
        return {"mensaje": "No se encontraron colchones en ese rango de tiempo para esta estación.", "actualizados": 0}

    # 3. Asignar el operario a cada evento encontrado
    for evento in eventos:
        evento.operario_fk = operario.id
        db.add(evento)

    db.commit()

    return {
        "mensaje": f"Se asignaron {len(eventos)} colchones a {operario.nombre_completo}", 
        "actualizados": len(eventos)
    }

# ==========================================
# IMPORTADORES MASIVOS (FASE 2)
# ==========================================

@app.post("/upload/skus/", tags=["Maestros"])
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

@app.post("/upload/plan/", tags=["Maestros"])
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
            
            # 1. Usamos id_orden (el nombre que espera la DB)
            # 2. Usamos estado='abierta' (el valor que acepta el ENUM)
            nueva_op = OrdenProduccion(
                tenant_id=tenant_id,
                id_orden=f"OP-{sku_id[:5]}-{i}", # ID único para la orden
                plan_fecha=fecha_plan,
                estado="abierta"  # <--- CORREGIDO: 'activa' no existía en el Enum
            )
            db.add(nueva_op)
            lineas += 1
        except Exception as e:
            continue

    db.commit()
    return {"status": "ok", "mensaje": f"Plan cargado. {lineas} órdenes listas para fabricar."}

# Endpoints manuales (opcionales)
@app.post("/skus/", response_model=MaestroSKU, tags=["Maestros"])
def crear_sku_manual(sku: MaestroSKU, db: Session = Depends(get_session)):
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku

@app.post("/ordenes/", response_model=OrdenProduccion, tags=["Maestros"])
def crear_orden_manual(orden: OrdenProduccion, db: Session = Depends(get_session)):
    db.add(orden)
    db.commit()
    db.refresh(orden)
    return orden

@app.post("/setup-springwall/", tags=["Configuracion"])
def setup_springwall(tenant_id: str = "empresa_demo", db: Session = Depends(get_session)):
    # 1. Limpieza de estaciones previas
    viejas = db.exec(select(Estacion).where(Estacion.tenant_id == tenant_id)).all()
    for v in viejas: db.delete(v)
    db.commit()

    # 2. Estaciones Iniciales (E1, E2, E3)
    e1 = Estacion(tenant_id=tenant_id, nombre="E1 - Pedalera (Ingreso)", tipo="sensor", posicion_linea=1, umbral_optimo=240, umbral_lento=280, umbral_alerta=300)
    e2 = Estacion(tenant_id=tenant_id, nombre="E2 - Matelaceado", tipo="sensor", posicion_linea=2, umbral_optimo=240, umbral_lento=280, umbral_alerta=300)
    e3 = Estacion(tenant_id=tenant_id, nombre="E3 - Forro/Escaneo", tipo="escaneo_manual", posicion_linea=3, umbral_optimo=240, umbral_lento=280, umbral_alerta=300)
    db.add_all([e1, e2, e3])
    db.commit()

    # 3. CERRADORA A (Padre con 2 Subestaciones Lineales)
    # El padre actúa como contenedor. Las subestaciones tienen umbrales / 2.
    cerradora_a_padre = Estacion(
        tenant_id=tenant_id, nombre="E4 - Cerradora A (Total)", 
        tipo="escaneo_manual", posicion_linea=4, ramal="Línea A",
        umbral_optimo=240, umbral_lento=280, umbral_alerta=300
    )
    db.add(cerradora_a_padre)
    db.commit()
    db.refresh(cerradora_a_padre)

    sub_a1 = Estacion(
        tenant_id=tenant_id, nombre="E4.1 - Cerradora A (Etapa 1)", 
        tipo="escaneo_manual", parent_id=cerradora_a_padre.id,
        posicion_linea=4, ramal="Línea A",
        umbral_optimo=120, umbral_lento=140, umbral_alerta=150 # 240/2, 280/2, 300/2
    )
    sub_a2 = Estacion(
        tenant_id=tenant_id, nombre="E4.2 - Cerradora A (Etapa 2)", 
        tipo="escaneo_manual", parent_id=cerradora_a_padre.id,
        posicion_linea=4, ramal="Línea A",
        umbral_optimo=120, umbral_lento=140, umbral_alerta=150
    )
    db.add_all([sub_a1, sub_a2])

    # 4. Cerradora B y Calidad
    cerradora_b = Estacion(tenant_id=tenant_id, nombre="E5 - Cerradora B", tipo="escaneo_manual", posicion_linea=4, ramal="Línea B", umbral_optimo=240, umbral_lento=280, umbral_alerta=300)
    calidad_a = Estacion(tenant_id=tenant_id, nombre="E6 - Calidad A", tipo="calidad", posicion_linea=5, ramal="Línea A", umbral_optimo=120, umbral_lento=180, umbral_alerta=181)
    calidad_b = Estacion(tenant_id=tenant_id, nombre="E7 - Calidad B", tipo="calidad", posicion_linea=5, ramal="Línea B", umbral_optimo=120, umbral_lento=180, umbral_alerta=181)
    
    db.add_all([cerradora_b, calidad_a, calidad_b])
    db.commit()

    return {"status": "ok", "mensaje": "Línea Springwall cargada. Cerradora A dividida en 2 subestaciones lineales."}

# ==========================================
# REPORTES Y DASHBOARDS
# ==========================================
@app.get("/reportes/dashboard", response_model=list[MetricasEstacion], tags=["Reportes"])
def obtener_dashboard_estaciones(tenant_id: str = "empresa_demo", db: Session = Depends(get_session)):
    """
    Devuelve las métricas consolidadas por estación para alimentar el front-end.
    Calcula totales, rendimientos (semáforo) y promedios de tiempo.
    """
    # 1. Traemos todos los eventos junto con los datos de su estación
    # (En producción le agregaríamos un filtro para traer solo los de "hoy")
    resultados = db.exec(
        select(EventoEscaneo, Estacion)
        .join(Estacion, EventoEscaneo.estacion_fk == Estacion.id)
        .where(EventoEscaneo.tenant_id == tenant_id)
    ).all()

    # 2. Diccionario para agrupar los datos temporalmente
    data_agrupada = {}

    for evento, estacion in resultados:
        # Si es la primera vez que vemos esta estación en el bucle, la creamos
        if estacion.nombre not in data_agrupada:
            data_agrupada[estacion.nombre] = {
                "total": 0, "optimo": 0, "lento": 0, "alerta": 0, 
                "retrabajo": 0, "suma_tiempos": 0, "eventos_con_tiempo": 0
            }
        
        m = data_agrupada[estacion.nombre]
        
        # 3. Sumamos la pieza al total
        m["total"] += 1
        
        # 4. Clasificamos por semáforo
        if evento.desempeno == "OPTIMO": m["optimo"] += 1
        elif evento.desempeno == "LENTO": m["lento"] += 1
        elif evento.desempeno == "ALERTA": m["alerta"] += 1
        
        # 5. Defectos de calidad
        if evento.es_retrabajo:
            m["retrabajo"] += 1
            
        # 6. Sumamos los tiempos (ignoramos los eventos de "INICIO" que tienen tiempo 0)
        if evento.segundos_proceso and evento.segundos_proceso > 0:
            m["suma_tiempos"] += evento.segundos_proceso
            m["eventos_con_tiempo"] += 1

    # 7. Formateamos la respuesta usando nuestro BaseModel
    reporte_final = []
    for nombre, metricas in data_agrupada.items():
        # Evitamos división por cero al calcular el promedio
        promedio = 0.0
        if metricas["eventos_con_tiempo"] > 0:
            promedio = round(metricas["suma_tiempos"] / metricas["eventos_con_tiempo"], 2)
            
        reporte_final.append(
            MetricasEstacion(
                estacion_nombre=nombre,
                total_piezas=metricas["total"],
                optimos=metricas["optimo"],
                lentos=metricas["lento"],
                alertas=metricas["alerta"],
                retrabajos=metricas["retrabajo"],
                tiempo_promedio_seg=promedio
            )
        )

    return reporte_final

# ==========================================
# ANALYTICA Y DASHBOARDS
# ==========================================
@app.get("/analytics/oee-general/", response_model=OeeGeneralCard, tags=["Analytics"])
def obtener_oee_general(tenant_id: str = "empresa_demo", db: Session = Depends(get_session)):
    """Devuelve las métricas top-level para el Dashboard Gerencial del día actual (Calidad por desvío de tiempo)."""
    
    hoy = datetime.now().date()
    
    eventos = db.exec(
        select(EventoEscaneo, Estacion)
        .join(Estacion, EventoEscaneo.estacion_fk == Estacion.id)
        .where(
            EventoEscaneo.tenant_id == tenant_id,
            EventoEscaneo.timestamp >= datetime.combine(hoy, time.min),
            EventoEscaneo.timestamp <= datetime.combine(hoy, time.max)
        )
    ).all()

    if not eventos:
        return OeeGeneralCard(
            disponibilidad_pct=0.0, rendimiento_pct=0.0, calidad_pct=0.0, oee_general_pct=0.0,
            total_unidades=0, unidades_con_retrabajo=0, minutos_desvio_calidad=0.0
        )

    # --- 1. CÁLCULO DE CALIDAD (Adaptación por Desvío de Tiempos) ---
    total_unidades = len(eventos)
    
    # Filtramos solo los eventos que pasaron por estaciones de tipo "calidad"
    eventos_calidad = [(e, est) for e, est in eventos if est.tipo.lower() == "calidad"]
    
    tiempo_ideal_calidad = sum(est.umbral_optimo for _, est in eventos_calidad)
    tiempo_real_calidad = sum(e.segundos_proceso for e, _ in eventos_calidad)
    
    # Unidades que superaron el umbral en calidad (es_retrabajo = True)
    unidades_con_retrabajo = sum(1 for e, _ in eventos_calidad if e.es_retrabajo)
    
    # Minutos netos de desvío (lo que tardaron de más)
    desvio_segundos = max(0, tiempo_real_calidad - tiempo_ideal_calidad)
    minutos_desvio_calidad = round(desvio_segundos / 60, 1)

    # Fórmula de Calidad
    if tiempo_real_calidad > 0:
        calidad = tiempo_ideal_calidad / tiempo_real_calidad
    else:
        # Si hoy no pasó nada por la estación de calidad, asumimos 100%
        calidad = 1.0 
        
    calidad = min(calidad, 1.0) # Topeamos en 100%

    # --- 2. CÁLCULO DE RENDIMIENTO ---
    tiempo_ideal_total = sum(estacion.umbral_optimo for _, estacion in eventos)
    tiempo_real_total = sum(e.segundos_proceso for e, _ in eventos)
    
    rendimiento = tiempo_ideal_total / tiempo_real_total if tiempo_real_total > 0 else 0.0
    rendimiento = min(rendimiento, 1.0) 

    # --- 3. CÁLCULO DE DISPONIBILIDAD ---
    paradas = db.exec(
        select(ParadaDetectada, MotivoParada)
        .outerjoin(MotivoParada, ParadaDetectada.motivo_fk == MotivoParada.id)
        .where(
            ParadaDetectada.tenant_id == tenant_id,
            ParadaDetectada.inicio >= datetime.combine(hoy, time.min),
            ParadaDetectada.inicio <= datetime.combine(hoy, time.max)
        )
    ).all()

    tiempo_paradas_no_planificadas = 0
    for parada, motivo in paradas:
        if not motivo or str(motivo.tipo_parada).lower().replace("tipoparada.", "") == "no_planificada":
            tiempo_paradas_no_planificadas += parada.duracion_segundos

    tiempo_planificado = tiempo_real_total + tiempo_paradas_no_planificadas
    disponibilidad = tiempo_real_total / tiempo_planificado if tiempo_planificado > 0 else 0.0

    # --- 4. OEE GENERAL ---
    oee_general = disponibilidad * rendimiento * calidad

    return OeeGeneralCard(
        disponibilidad_pct=round(disponibilidad * 100, 1),
        rendimiento_pct=round(rendimiento * 100, 1),
        calidad_pct=round(calidad * 100, 1),
        oee_general_pct=round(oee_general * 100, 1),
        total_unidades=total_unidades,
        unidades_con_retrabajo=unidades_con_retrabajo,
        minutos_desvio_calidad=minutos_desvio_calidad
    )

@app.get("/analytics/reporte-operarios/", response_model=list[ReporteOperarioSpringwall], tags=["Analytics"])
def obtener_reporte_springwall(tenant_id: str = "empresa_demo", fecha: date = None, db: Session = Depends(get_session)):
    """
    Replica el reporte operativo de Springwall: Producción real vs esperada por operario.
    """
    # Si no nos pasan una fecha por la URL, usamos la de hoy
    if not fecha:
        fecha = datetime.now().date()
        
    # Traemos los eventos del día, incluyendo datos de estación y operario
    eventos = db.exec(
        select(EventoEscaneo, Estacion, Operario)
        .join(Estacion, EventoEscaneo.estacion_fk == Estacion.id)
        # Hacemos un outer join con operario porque podría haber colchones "huérfanos" (sin asignar)
        .outerjoin(Operario, EventoEscaneo.operario_fk == Operario.id)
        .where(
            EventoEscaneo.tenant_id == tenant_id,
            EventoEscaneo.timestamp >= datetime.combine(fecha, time.min),
            EventoEscaneo.timestamp <= datetime.combine(fecha, time.max)
        )
    ).all()

    # Agrupamos los datos por la combinación (Operario, Estacion)
    data_agrupada = {}
    
    for evento, estacion, operario in eventos:
        # Si no hay operario asignado, lo agrupamos bajo "Sin Asignar"
        nombre_op = operario.nombre_completo if operario else "Sin Asignar"
        clave = (nombre_op, estacion.nombre)
        
        if clave not in data_agrupada:
            data_agrupada[clave] = {
                "cantidad_real": 0,
                "tiempo_invertido": 0,
                "umbral_optimo": estacion.umbral_optimo
            }
            
        grupo = data_agrupada[clave]
        grupo["cantidad_real"] += 1
        # Sumamos el tiempo (evitando los de inicio que tienen 0)
        if evento.segundos_proceso and evento.segundos_proceso > 0:
            grupo["tiempo_invertido"] += evento.segundos_proceso

    # Calculamos las métricas finales para cada grupo
    reporte_final = []
    for (nombre_op, nombre_est), metricas in data_agrupada.items():
        
        # ¿Cuántos colchones debió hacer en ese tiempo?
        if metricas["umbral_optimo"] > 0:
            esperada = metricas["tiempo_invertido"] / metricas["umbral_optimo"]
            esperada = int(esperada) # Redondeamos a enteros
        else:
            esperada = metricas["cantidad_real"]
            
        # Para que no diga que esperaba 0 si recién arranca
        esperada = max(1, esperada) 
        
        # Cálculo de la diferencia porcentual
        # Si real = 5 y esperada = 10, la dif es -50%
        diferencia = ((metricas["cantidad_real"] - esperada) / esperada) * 100
        
        reporte_final.append(
            ReporteOperarioSpringwall(
                operario_nombre=nombre_op,
                estacion_nombre=nombre_est,
                cantidad_real=metricas["cantidad_real"],
                cantidad_esperada=esperada,
                diferencia_pct=round(diferencia, 1)
            )
        )
        
    # Opcional: Ordenamos para que los peores rendimientos (más negativos) salgan primero
    reporte_final.sort(key=lambda x: x.diferencia_pct)

    return reporte_final

@app.get("/analytics/pareto-paradas/", response_model=list[ParetoParadas], tags=["Analytics"])
def obtener_pareto_paradas(tenant_id: str = "empresa_demo", fecha: date = None, db: Session = Depends(get_session)):
    """Ranking de los motivos que más tiempo le quitan a la fábrica."""
    if not fecha:
        fecha = datetime.now().date()
        
    paradas = db.exec(
        select(ParadaDetectada, MotivoParada)
        .outerjoin(MotivoParada, ParadaDetectada.motivo_fk == MotivoParada.id)
        .where(
            ParadaDetectada.tenant_id == tenant_id,
            ParadaDetectada.inicio >= datetime.combine(fecha, time.min),
            ParadaDetectada.inicio <= datetime.combine(fecha, time.max)
        )
    ).all()

    agrupado = {}
    for parada, motivo in paradas:
        nombre_motivo = motivo.nombre if motivo else "Sin Clasificar (Pendiente)"
        tipo_motivo = str(motivo.tipo_parada).split(".")[-1].upper() if motivo else "DESCONOCIDO"
        
        if nombre_motivo not in agrupado:
            agrupado[nombre_motivo] = {"tipo": tipo_motivo, "frecuencia": 0, "segundos": 0}
            
        agrupado[nombre_motivo]["frecuencia"] += 1
        agrupado[nombre_motivo]["segundos"] += parada.duracion_segundos

    reporte = [
        ParetoParadas(
            motivo=k,
            tipo=v["tipo"],
            frecuencia=v["frecuencia"],
            minutos_totales=round(v["segundos"] / 60, 1)
        )
        for k, v in agrupado.items()
    ]
    
    # Ordenamos de mayor a menor tiempo perdido (El verdadero Pareto)
    reporte.sort(key=lambda x: x.minutos_totales, reverse=True)
    return reporte


@app.get("/analytics/cuellos-botella/", response_model=list[CuelloBotella], tags=["Analytics"])
def obtener_cuellos_botella(tenant_id: str = "empresa_demo", fecha: date = None, db: Session = Depends(get_session)):
    """Mide la desviación de velocidad promedio de cada estación en el día."""
    if not fecha:
        fecha = datetime.now().date()
        
    eventos = db.exec(
        select(EventoEscaneo, Estacion)
        .join(Estacion, EventoEscaneo.estacion_fk == Estacion.id)
        .where(
            EventoEscaneo.tenant_id == tenant_id,
            EventoEscaneo.timestamp >= datetime.combine(fecha, time.min),
            EventoEscaneo.timestamp <= datetime.combine(fecha, time.max),
            EventoEscaneo.segundos_proceso > 0 # Ignoramos los de inicio
        )
    ).all()

    agrupado = {}
    for evento, estacion in eventos:
        if estacion.nombre not in agrupado:
            agrupado[estacion.nombre] = {"esperado": estacion.umbral_optimo, "suma_real": 0, "cantidad": 0}
            
        agrupado[estacion.nombre]["suma_real"] += evento.segundos_proceso
        agrupado[estacion.nombre]["cantidad"] += 1

    reporte = []
    for nombre, datos in agrupado.items():
        promedio_real = datos["suma_real"] / datos["cantidad"]
        # Ej: Si esperado es 100s y real es 150s -> ((150-100)/100)*100 = +50% de desvío
        desvio = ((promedio_real - datos["esperado"]) / datos["esperado"]) * 100
        
        reporte.append( CuelloBotella(
            estacion=nombre,
            tiempo_esperado_seg=datos["esperado"],
            tiempo_promedio_real_seg=round(promedio_real, 1),
            desvio_pct=round(desvio, 1)
        ))

    # Ordenamos poniendo primero las estaciones más atrasadas (mayor % de desvío positivo)
    reporte.sort(key=lambda x: x.desvio_pct, reverse=True)
    return reporte


@app.get("/analytics/alertas-vivas/", response_model=list[AlertaActiva], tags=["Analytics"])
def obtener_alertas_vivas(tenant_id: str = "empresa_demo", db: Session = Depends(get_session)):
    """Un feed en tiempo real con los problemas que requieren atención inmediata hoy."""
    hoy = datetime.now().date()
    alertas = []

    # 1. Buscar Paradas Pendientes (Las que la máquina detectó pero nadie justificó)
    paradas_huerfanas = db.exec(
        select(ParadaDetectada, Estacion)
        .join(Estacion, ParadaDetectada.estacion_fk == Estacion.id)
        .where(
            ParadaDetectada.tenant_id == tenant_id,
            ParadaDetectada.estado == "pendiente",
            ParadaDetectada.inicio >= datetime.combine(hoy, time.min)
        )
    ).all()

    for parada, estacion in paradas_huerfanas:
        alertas.append(AlertaActiva(
            hora=parada.inicio.strftime("%H:%M:%S"),
            estacion=estacion.nombre,
            tipo="PARADA_PENDIENTE",
            mensaje=f"Máquina detenida durante {round(parada.duracion_segundos/60, 1)} min. Requiere clasificación."
        ))

    # 2. Buscar Eventos Críticos (Retrabajos o Lentitud Extrema)
    eventos_criticos = db.exec(
        select(EventoEscaneo, Estacion)
        .join(Estacion, EventoEscaneo.estacion_fk == Estacion.id)
        .where(
            EventoEscaneo.tenant_id == tenant_id,
            EventoEscaneo.timestamp >= datetime.combine(hoy, time.min),
            # Traemos los que son alerta o retrabajo
            (EventoEscaneo.desempeno == "ALERTA") | (EventoEscaneo.es_retrabajo == True)
        )
    ).all()

    for evento, estacion in eventos_criticos:
        if evento.es_retrabajo:
            tipo = "RETRABAJO"
            msg = f"Colchón OP-{evento.orden_fk} marcado como defecto de calidad."
        else:
            tipo = "LENTITUD_EXTREMA"
            msg = f"Colchón OP-{evento.orden_fk} superó el umbral de alerta ({evento.segundos_proceso} seg)."
            
        alertas.append(AlertaActiva(
            hora=evento.timestamp.strftime("%H:%M:%S"),
            estacion=estacion.nombre,
            tipo=tipo,
            mensaje=msg
        ))

    # Ordenamos el feed para que lo más reciente salga primero (como Twitter)
    alertas.sort(key=lambda x: x.hora, reverse=True)
    return alertas

# ==========================================
# ABM: MOTIVOS DE PARADA
# ==========================================
@app.post("/motivos-parada/", response_model=MotivoParada, tags=["Configuracion"])
def crear_motivo_parada(motivo: MotivoParada, db: Session = Depends(get_session)):
    """Crea un motivo de parada indicando si es PLANIFICADA o NO_PLANIFICADA"""
    db.add(motivo)
    db.commit()
    db.refresh(motivo)
    return motivo

@app.get("/motivos-parada/", response_model=list[MotivoParada], tags=["Configuracion"])
def obtener_motivos_parada(tenant_id: str, db: Session = Depends(get_session)):
    """Lista todos los motivos de parada configurados para la empresa"""
    return db.exec(select(MotivoParada).where(MotivoParada.tenant_id == tenant_id)).all()

@app.get("/paradas/pendientes/", response_model=list[ParadaDetectada], tags=["Operacion"])
def obtener_paradas_pendientes(tenant_id: str, db: Session = Depends(get_session)):
    """Muestra las paradas que el sistema detectó y el supervisor aún no justificó"""
    return db.exec(
        select(ParadaDetectada)
        .where(
            ParadaDetectada.tenant_id == tenant_id,
            ParadaDetectada.estado == EstadoParada.PENDIENTE
        )
    ).all()

@app.patch("/paradas/{parada_id}/clasificar", response_model=ParadaDetectada, tags=["Operacion"])
def clasificar_parada(parada_id: uuid.UUID, datos: ClasificarParada, db: Session = Depends(get_session)):
    """El supervisor asigna un motivo a una parada detectada."""
    # 1. Buscar la parada
    parada = db.get(ParadaDetectada, parada_id)
    if not parada:
        raise HTTPException(status_code=404, detail="Parada no encontrada")
    
    # 2. Verificar que el motivo existe
    motivo = db.get(MotivoParada, datos.motivo_fk)
    if not motivo:
        raise HTTPException(status_code=404, detail="Motivo de parada no válido")

    # 3. Actualizar y cambiar estado
    parada.motivo_fk = motivo.id
    parada.estado = "clasificada" # Usamos el string o EstadoParada.CLASIFICADA
    
    db.add(parada)
    db.commit()
    db.refresh(parada)
    return parada


@app.post("/paradas/planificadas/", response_model=ParadaDetectada, tags=["Operacion"])
def registrar_parada_planificada(datos: ParadaPlanificadaCreate, tenant_id: str = "empresa_demo", db: Session = Depends(get_session)):
    """Registra una parada pre-acordada (ej. almuerzo, mantenimiento). Nace ya clasificada."""
    
    # 1. Validamos que el motivo exista y sea de tipo PLANIFICADA
    motivo = db.get(MotivoParada, datos.motivo_fk)
    if not motivo:
        raise HTTPException(status_code=404, detail="Motivo no encontrado")
        
    # Comprobamos el Enum o string dependiendo de cómo lo guarde SQLAlchemy
    if str(motivo.tipo_parada).lower().replace("tipoparada.", "") != "planificada":
        raise HTTPException(status_code=400, detail="El motivo seleccionado no es del tipo PLANIFICADA")
    
    # 2. Calculamos la duración exacta en segundos
    duracion = (datos.fin - datos.inicio).total_seconds()
    if duracion <= 0:
         raise HTTPException(status_code=400, detail="La fecha de fin debe ser mayor a la de inicio")

    # 3. Creamos el registro directamente clasificado
    nueva_parada = ParadaDetectada(
        tenant_id=tenant_id,
        estacion_fk=datos.estacion_fk,
        motivo_fk=motivo.id,
        inicio=datos.inicio,
        fin=datos.fin,
        duracion_segundos=duracion,
        estado="clasificada"  # Ya entra resuelta
    )
    
    db.add(nueva_parada)
    db.commit()
    db.refresh(nueva_parada)
    
    return nueva_parada


# ==========================================
# MAESTROS: OPERARIOS
# ==========================================
@app.post("/operarios/", response_model=Operario, tags=["Maestros"])
def crear_operario(operario: Operario, db: Session = Depends(get_session)):
    """Da de alta un nuevo operario en la planta"""
    db.add(operario)
    db.commit()
    db.refresh(operario)
    return operario

@app.get("/operarios/", response_model=list[Operario], tags=["Maestros"])
def obtener_operarios(tenant_id: str = "empresa_demo", db: Session = Depends(get_session)):
    """Lista todos los operarios activos"""
    return db.exec(select(Operario).where(Operario.tenant_id == tenant_id)).all()

# ==========================================
# MAESTROS: TURNOS Y ASIGNACIONES
# ==========================================
@app.post("/turnos/", response_model=Turno, tags=["Maestros"])
def crear_turno(turno: Turno, db: Session = Depends(get_session)):
    """Crea una franja horaria de trabajo (Ej: Turno Mañana)"""
    db.add(turno)
    db.commit()
    db.refresh(turno)
    return turno

@app.post("/asignaciones/", response_model=AsignacionTurno, tags=["Operacion"])
def crear_asignacion(asignacion: AsignacionTurno, db: Session = Depends(get_session)):
    """El supervisor planifica quién opera qué máquina en un turno y fecha específicos."""
    db.add(asignacion)
    db.commit()
    db.refresh(asignacion)
    return asignacion