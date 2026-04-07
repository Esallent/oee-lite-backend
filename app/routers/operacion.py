from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.core.database import get_session
# --- ACTUALIZACIÓN DE IMPORTACIONES ---
from app.models.domain import (
    EventoEscaneo, Estacion, Operario, Turno, ParadaDetectada, 
    MotivoParada, EstadoParada, AsignacionMatriz, Linea, ParadaProgramada, AsignacionSupervisor # <-- Nuevos modelos
)
from pydantic import BaseModel
from datetime import datetime, date
from typing import Optional
import uuid

router = APIRouter(tags=["Operacion"])

# --- MOLDES ---
class BarcodeDecodificado(BaseModel):
    secuencia: str
    orden_produccion: str
    codigo_sku: str
    codigo_original: str

class ClasificarParada(BaseModel):
    motivo_fk: uuid.UUID

class AsignacionRetroactiva(BaseModel):
    estacion_fk: uuid.UUID
    operario_fk: uuid.UUID
    inicio: datetime
    fin: datetime

# Nuevo Molde para el enriquecimiento de paradas
class ParadaPendienteResponse(BaseModel):
    id: uuid.UUID
    estacion: str
    linea: str
    inicio: datetime
    duracion_segundos: int
    operario: Optional[str] = None
    turno: Optional[str] = None

# --- HELPER ---
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

@router.get("/test-parser/{barcode}", tags=["Pruebas"])
def probar_parser(barcode: str):
    try:
        return {"status": "ok", "data": parsear_barcode(barcode)}
    except Exception as e:
        return {"status": "error", "detalle": str(e)}

# --- ENDPOINTS ---

@router.post("/eventos/", response_model=EventoEscaneo)
def registrar_evento(evento: EventoEscaneo, db: Session = Depends(get_session)):
    if isinstance(evento.timestamp, str):
        evento.timestamp = datetime.fromisoformat(evento.timestamp.replace("Z", ""))

    estacion = db.get(Estacion, evento.estacion_fk)
    if not estacion:
        raise HTTPException(status_code=404, detail="Estación no encontrada")

    # ==============================================================
    # --- LOG-IN POR CREDENCIAL DE OPERARIO (NUEVO MATRICIAL) ---
    # ==============================================================
    if evento.barcode.startswith("OP-"):
        operario = db.exec(select(Operario).where(Operario.legajo == evento.barcode)).first()
        if not operario:
            raise HTTPException(status_code=404, detail="Credencial de operario no reconocida")
        
        hora_actual = evento.timestamp.time()
        # Buscamos el turno que corresponda a esa hora en LA LÍNEA de esta estación
        turno_actual = db.exec(
            select(Turno).where(
                Turno.linea_id == estacion.linea_id,
                Turno.hora_inicio <= hora_actual, 
                Turno.hora_fin >= hora_actual
            )
        ).first()

        if not turno_actual:
            raise HTTPException(status_code=400, detail="No hay un turno configurado para esta hora en esta línea")

        # Intentamos buscar si ya existe la matriz, si no, la creamos
        fecha_actual = evento.timestamp.date()
        asignacion_existente = db.exec(
            select(AsignacionMatriz).where(
                AsignacionMatriz.tenant_id == evento.tenant_id,
                AsignacionMatriz.fecha == fecha_actual,
                AsignacionMatriz.estacion_id == estacion.id,
                AsignacionMatriz.turno_id == turno_actual.id
            )
        ).first()

        if asignacion_existente:
            asignacion_existente.operario_id = operario.id
            db.add(asignacion_existente)
        else:
            nueva_asig = AsignacionMatriz(
                tenant_id=evento.tenant_id,
                fecha=fecha_actual,
                linea_id=estacion.linea_id,
                turno_id=turno_actual.id,
                estacion_id=estacion.id,
                operario_id=operario.id
            )
            db.add(nueva_asig)
            
        db.commit()
        evento.desempeno = "LOGIN_OPERARIO"
        return evento

    # ==============================================================
    # --- PROCESAMIENTO DE COLCHONES Y MATRIZ ---
    # ==============================================================
    datos_barcode = parsear_barcode(evento.barcode)
    evento.orden_fk = datos_barcode.orden_produccion

    hora_actual = evento.timestamp.time()
    fecha_actual = evento.timestamp.date()

    # Buscamos en la Matriz quién está trabajando aquí ahora
    asignacion_hoy = db.exec(
        select(AsignacionMatriz, Turno)
        .join(Turno, AsignacionMatriz.turno_id == Turno.id)
        .where(
            AsignacionMatriz.tenant_id == evento.tenant_id,
            AsignacionMatriz.estacion_id == estacion.id,
            AsignacionMatriz.fecha == fecha_actual,
            Turno.hora_inicio <= hora_actual,
            Turno.hora_fin >= hora_actual
        )
    ).first()

    if asignacion_hoy:
        asignacion, turno = asignacion_hoy
        evento.operario_fk = asignacion.operario_id

    # 3. Lógica Dinámica de Tiempos
    ultimo_evento = db.exec(
        select(EventoEscaneo)
        .where(EventoEscaneo.tenant_id == evento.tenant_id, EventoEscaneo.barcode == evento.barcode)
        .order_by(EventoEscaneo.timestamp.desc())
    ).first()

    if ultimo_evento:
        diff_segundos = (evento.timestamp - ultimo_evento.timestamp).total_seconds()
        evento.segundos_proceso = int(diff_segundos) 
        
        if diff_segundos > 150:
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


# ==============================================================
# --- NUEVO: TELEMETRÍA EN VIVO (Para OperadorPage) ---
# ==============================================================
@router.get("/eventos/live/", response_model=list[EventoEscaneo])
def telemetria_en_vivo(tenant_id: str, limit: int = 50, db: Session = Depends(get_session)):
    """Devuelve los últimos N eventos de toda la fábrica en tiempo real."""
    return db.exec(
        select(EventoEscaneo)
        .where(EventoEscaneo.tenant_id == tenant_id)
        .order_by(EventoEscaneo.timestamp.desc())
        .limit(limit)
    ).all()


# ==============================================================
# --- REFACTOR: ASIGNACIONES MATRICIALES ---
# ==============================================================
@router.post("/asignaciones/", response_model=AsignacionMatriz)
def crear_asignacion_matricial(asignacion: AsignacionMatriz, db: Session = Depends(get_session)):
    """El supervisor planifica quién opera qué máquina usando Día+Línea+Turno+Estación."""
    try:
        db.add(asignacion)
        db.commit()
        db.refresh(asignacion)
        return asignacion
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail="Ya existe una asignación para esta estación en este turno y día.")

# ==========================================
# ASIGNACIÓN DE SUPERVISORES (NUEVO)
# ==========================================
@router.post("/asignaciones/supervisores/", response_model=AsignacionSupervisor)
def asignar_supervisor(asignacion: AsignacionSupervisor, db: Session = Depends(get_session)):
    """Asigna un supervisor a una Línea completa en un Turno y Día específicos."""
    try:
        db.add(asignacion)
        db.commit()
        db.refresh(asignacion)
        return asignacion
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=400, 
            detail="Ya existe un supervisor asignado a esta línea en este turno y día."
        )

# ==============================================================
# --- ENRIQUECIMIENTO: PARADAS PENDIENTES ---
# ==============================================================
@router.get("/paradas/pendientes/", response_model=list[ParadaPendienteResponse])
def obtener_paradas_pendientes(tenant_id: str, db: Session = Depends(get_session)):
    """Muestra las paradas unidas con la línea y la matriz de asignaciones para saber quién estaba."""
    paradas = db.exec(
        select(ParadaDetectada, Estacion, Linea)
        .join(Estacion, ParadaDetectada.estacion_fk == Estacion.id)
        .join(Linea, Estacion.linea_id == Linea.id)
        .where(
            ParadaDetectada.tenant_id == tenant_id,
            ParadaDetectada.estado == EstadoParada.PENDIENTE
        )
    ).all()

    resultado = []
    for parada, estacion, linea in paradas:
        # Intentamos buscar al operario en la matriz en ese momento
        fecha_parada = parada.inicio.date()
        hora_parada = parada.inicio.time()
        
        asignacion = db.exec(
            select(AsignacionMatriz, Turno, Operario)
            .join(Turno, AsignacionMatriz.turno_id == Turno.id)
            .join(Operario, AsignacionMatriz.operario_id == Operario.id)
            .where(
                AsignacionMatriz.estacion_id == estacion.id,
                AsignacionMatriz.fecha == fecha_parada,
                Turno.hora_inicio <= hora_parada,
                Turno.hora_fin >= hora_parada
            )
        ).first()

        resultado.append(ParadaPendienteResponse(
            id=parada.id,
            estacion=estacion.nombre,
            linea=linea.nombre,
            inicio=parada.inicio,
            duracion_segundos=parada.duracion_segundos,
            operario=asignacion[2].nombre_completo if asignacion else "Sin asignar",
            turno=asignacion[1].nombre if asignacion else "Fuera de turno"
        ))
        
    return resultado

@router.patch("/paradas/{parada_id}/clasificar", response_model=ParadaDetectada)
def clasificar_parada(parada_id: uuid.UUID, datos: ClasificarParada, db: Session = Depends(get_session)):
    parada = db.get(ParadaDetectada, parada_id)
    if not parada:
        raise HTTPException(status_code=404, detail="Parada no encontrada")
    
    motivo = db.get(MotivoParada, datos.motivo_fk)
    if not motivo:
        raise HTTPException(status_code=404, detail="Motivo de parada no válido")

    parada.motivo_fk = motivo.id
    parada.estado = "clasificada"
    
    db.add(parada)
    db.commit()
    db.refresh(parada)
    return parada

# Los endpoints de asignación retroactiva y parada planificada se mantienen iguales (con los nuevos modelos)...
@router.post("/operarios/asignar-retroactivo/")
def asignar_operario_retroactivo(datos: AsignacionRetroactiva, tenant_id: str = "empresa_demo", db: Session = Depends(get_session)):
    operario = db.get(Operario, datos.operario_fk)
    if not operario:
        raise HTTPException(status_code=404, detail="Operario no encontrado")

    eventos = db.exec(
        select(EventoEscaneo).where(
            EventoEscaneo.tenant_id == tenant_id,
            EventoEscaneo.estacion_fk == datos.estacion_fk,
            EventoEscaneo.timestamp >= datos.inicio,
            EventoEscaneo.timestamp <= datos.fin
        )
    ).all()

    if not eventos:
        return {"mensaje": "No se encontraron colchones en ese rango de tiempo para esta estación.", "actualizados": 0}

    for evento in eventos:
        evento.operario_fk = operario.id
        db.add(evento)

    db.commit()
    return {"mensaje": f"Se asignaron {len(eventos)} colchones a {operario.nombre_completo}", "actualizados": len(eventos)}

@router.post("/paradas/planificadas/", response_model=ParadaProgramada)
def registrar_parada_planificada(datos: ParadaProgramada, db: Session = Depends(get_session)):
    """Registra un descanso programado en la tabla fact_paradas_programadas."""
    db.add(datos)
    db.commit()
    db.refresh(datos)
    return datos