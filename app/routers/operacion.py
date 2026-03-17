from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.core.database import get_session
from app.models.domain import EventoEscaneo, Estacion, Operario, Turno, AsignacionTurno, ParadaDetectada, MotivoParada, EstadoParada
from pydantic import BaseModel
from datetime import datetime
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

class ParadaPlanificadaCreate(BaseModel):
    estacion_fk: uuid.UUID
    motivo_fk: uuid.UUID
    inicio: datetime
    fin: datetime

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


@router.post("/operarios/asignar-retroactivo/")
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

@router.get("/paradas/pendientes/", response_model=list[ParadaDetectada])
def obtener_paradas_pendientes(tenant_id: str, db: Session = Depends(get_session)):
    """Muestra las paradas que el sistema detectó y el supervisor aún no justificó"""
    return db.exec(
        select(ParadaDetectada)
        .where(
            ParadaDetectada.tenant_id == tenant_id,
            ParadaDetectada.estado == EstadoParada.PENDIENTE
        )
    ).all()

@router.patch("/paradas/{parada_id}/clasificar", response_model=ParadaDetectada)
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

@router.post("/paradas/planificadas/", response_model=ParadaDetectada)
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

@router.post("/asignaciones/", response_model=AsignacionTurno)
def crear_asignacion(asignacion: AsignacionTurno, db: Session = Depends(get_session)):
    """El supervisor planifica quién opera qué máquina en un turno y fecha específicos."""
    db.add(asignacion)
    db.commit()
    db.refresh(asignacion)
    return asignacion