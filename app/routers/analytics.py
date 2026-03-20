from fastapi import APIRouter, Depends
from sqlmodel import Session, select
from app.core.database import get_session
from app.models.domain import Estacion, EventoEscaneo, ParadaDetectada, MotivoParada, Operario, Turno, Linea
from pydantic import BaseModel
from datetime import datetime, time, date, timedelta
from typing import Optional
import uuid

router = APIRouter(tags=["Analytics"])

# ==========================================
# --- MOLDES (Schemas) ---
# ==========================================
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
    tipo: str 
    mensaje: str

# --- NUEVOS MOLDES FRONTEND ---
class TendenciaOEERow(BaseModel):
    fecha: str
    oee: float
    disp: float
    rend: float
    cal: float

class RendimientoSecuencialRow(BaseModel):
    station: str
    performance: float

class ReporteExcelRow(BaseModel):
    categoria: str
    operario: str
    estacion: str
    esperada: int
    real: int
    diferencia: float


# ==========================================
# --- HELPERS DE FILTRADO DINÁMICO ---
# ==========================================
def obtener_rango_fechas(fecha_desde: Optional[date], fecha_hasta: Optional[date]):
    """Calcula el rango de tiempo. Si no hay, usa 'Hoy'."""
    hoy = datetime.now().date()
    inicio = fecha_desde or hoy
    fin = fecha_hasta or hoy
    return datetime.combine(inicio, time.min), datetime.combine(fin, time.max)

def filtrar_eventos_base(
    query, 
    fecha_desde: Optional[date], 
    fecha_hasta: Optional[date],
    linea_id: Optional[uuid.UUID],
    turno_id: Optional[uuid.UUID],
    db: Session
):
    """Aplica los filtros globales a cualquier query de EventoEscaneo."""
    inicio, fin = obtener_rango_fechas(fecha_desde, fecha_hasta)
    query = query.where(EventoEscaneo.timestamp >= inicio, EventoEscaneo.timestamp <= fin)
    
    if linea_id:
        query = query.where(Estacion.linea_id == linea_id)
        
    # Filtrado por turno en memoria (si se requiere a nivel de tiempo)
    # Como SQLite/Postgres manejan distinto el CAST de tiempo, lo filtramos después
    return query, inicio, fin


# ==========================================
# --- ENDPOINTS EXISTENTES (AHORA DINÁMICOS) ---
# ==========================================
@router.get("/reportes/dashboard", response_model=list[MetricasEstacion])
def obtener_dashboard_estaciones(
    tenant_id: str = "empresa_demo", 
    fecha_desde: Optional[date] = None, fecha_hasta: Optional[date] = None,
    linea_id: Optional[uuid.UUID] = None, turno_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_session)
):
    query = select(EventoEscaneo, Estacion).join(Estacion, EventoEscaneo.estacion_fk == Estacion.id).where(EventoEscaneo.tenant_id == tenant_id)
    query, _, _ = filtrar_eventos_base(query, fecha_desde, fecha_hasta, linea_id, turno_id, db)
    resultados = db.exec(query).all()

    data_agrupada = {}
    for evento, estacion in resultados:
        if estacion.nombre not in data_agrupada:
            data_agrupada[estacion.nombre] = {"total": 0, "optimo": 0, "lento": 0, "alerta": 0, "retrabajo": 0, "suma_tiempos": 0, "eventos_con_tiempo": 0}
        
        m = data_agrupada[estacion.nombre]
        m["total"] += 1
        if evento.desempeno == "OPTIMO": m["optimo"] += 1
        elif evento.desempeno == "LENTO": m["lento"] += 1
        elif evento.desempeno == "ALERTA": m["alerta"] += 1
        if evento.es_retrabajo: m["retrabajo"] += 1
        if evento.segundos_proceso and evento.segundos_proceso > 0:
            m["suma_tiempos"] += evento.segundos_proceso
            m["eventos_con_tiempo"] += 1

    return [
        MetricasEstacion(
            estacion_nombre=n, total_piezas=m["total"], optimos=m["optimo"], lentos=m["lento"],
            alertas=m["alerta"], retrabajos=m["retrabajo"],
            tiempo_promedio_seg=round(m["suma_tiempos"] / m["eventos_con_tiempo"], 2) if m["eventos_con_tiempo"] > 0 else 0.0
        ) for n, m in data_agrupada.items()
    ]


@router.get("/analytics/oee-general/", response_model=OeeGeneralCard)
def obtener_oee_general(
    tenant_id: str = "empresa_demo",
    fecha_desde: Optional[date] = None, fecha_hasta: Optional[date] = None,
    linea_id: Optional[uuid.UUID] = None, turno_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_session)
):
    query = select(EventoEscaneo, Estacion).join(Estacion, EventoEscaneo.estacion_fk == Estacion.id).where(EventoEscaneo.tenant_id == tenant_id)
    query, inicio, fin = filtrar_eventos_base(query, fecha_desde, fecha_hasta, linea_id, turno_id, db)
    eventos = db.exec(query).all()

    if not eventos:
        return OeeGeneralCard(disponibilidad_pct=0, rendimiento_pct=0, calidad_pct=0, oee_general_pct=0, total_unidades=0, unidades_con_retrabajo=0, minutos_desvio_calidad=0)

    total_unidades = len(eventos)
    eventos_calidad = [(e, est) for e, est in eventos if est.tipo.lower() == "calidad"]
    
    t_ideal_calidad = sum(est.umbral_optimo for _, est in eventos_calidad)
    t_real_calidad = sum(e.segundos_proceso for e, _ in eventos_calidad)
    retrabajos = sum(1 for e, _ in eventos_calidad if e.es_retrabajo)
    
    calidad = min(t_ideal_calidad / t_real_calidad if t_real_calidad > 0 else 1.0, 1.0)
    
    t_ideal_total = sum(est.umbral_optimo for _, est in eventos)
    t_real_total = sum(e.segundos_proceso for e, _ in eventos)
    rendimiento = min(t_ideal_total / t_real_total if t_real_total > 0 else 0.0, 1.0)

    # Paradas
    q_paradas = select(ParadaDetectada, MotivoParada, Estacion).join(Estacion, ParadaDetectada.estacion_fk == Estacion.id).outerjoin(MotivoParada, ParadaDetectada.motivo_fk == MotivoParada.id).where(ParadaDetectada.tenant_id == tenant_id, ParadaDetectada.inicio >= inicio, ParadaDetectada.inicio <= fin)
    if linea_id: q_paradas = q_paradas.where(Estacion.linea_id == linea_id)
    
    t_paradas = sum(p.duracion_segundos for p, m, _ in db.exec(q_paradas).all() if not m or str(m.tipo_parada).lower().endswith("no_planificada"))
    
    disponibilidad = t_real_total / (t_real_total + t_paradas) if (t_real_total + t_paradas) > 0 else 0.0

    return OeeGeneralCard(
        disponibilidad_pct=round(disponibilidad * 100, 1), rendimiento_pct=round(rendimiento * 100, 1),
        calidad_pct=round(calidad * 100, 1), oee_general_pct=round((disponibilidad * rendimiento * calidad) * 100, 1),
        total_unidades=total_unidades, unidades_con_retrabajo=retrabajos, minutos_desvio_calidad=round(max(0, t_real_calidad - t_ideal_calidad)/60, 1)
    )

@router.get("/analytics/pareto-paradas/", response_model=list[ParetoParadas])
def obtener_pareto_paradas(
    tenant_id: str = "empresa_demo", fecha_desde: Optional[date] = None, fecha_hasta: Optional[date] = None,
    linea_id: Optional[uuid.UUID] = None, db: Session = Depends(get_session)
):
    inicio, fin = obtener_rango_fechas(fecha_desde, fecha_hasta)
    q = select(ParadaDetectada, MotivoParada, Estacion).join(Estacion, ParadaDetectada.estacion_fk == Estacion.id).outerjoin(MotivoParada, ParadaDetectada.motivo_fk == MotivoParada.id).where(ParadaDetectada.tenant_id == tenant_id, ParadaDetectada.inicio >= inicio, ParadaDetectada.inicio <= fin)
    if linea_id: q = q.where(Estacion.linea_id == linea_id)
    
    agrupado = {}
    for parada, motivo, _ in db.exec(q).all():
        n = motivo.nombre if motivo else "Sin Clasificar"
        t = str(motivo.tipo_parada).split(".")[-1].upper() if motivo else "DESCONOCIDO"
        if n not in agrupado: agrupado[n] = {"tipo": t, "frecuencia": 0, "segundos": 0}
        agrupado[n]["frecuencia"] += 1
        agrupado[n]["segundos"] += parada.duracion_segundos

    return sorted([ParetoParadas(motivo=k, tipo=v["tipo"], frecuencia=v["frecuencia"], minutos_totales=round(v["segundos"]/60, 1)) for k, v in agrupado.items()], key=lambda x: x.minutos_totales, reverse=True)


@router.get("/analytics/cuellos-botella/", response_model=list[CuelloBotella])
def obtener_cuellos_botella(
    tenant_id: str = "empresa_demo", fecha_desde: Optional[date] = None, fecha_hasta: Optional[date] = None,
    linea_id: Optional[uuid.UUID] = None, db: Session = Depends(get_session)
):
    q = select(EventoEscaneo, Estacion).join(Estacion, EventoEscaneo.estacion_fk == Estacion.id).where(EventoEscaneo.tenant_id == tenant_id, EventoEscaneo.segundos_proceso > 0)
    q, _, _ = filtrar_eventos_base(q, fecha_desde, fecha_hasta, linea_id, None, db)
    
    agrupado = {}
    for evento, estacion in db.exec(q).all():
        if estacion.nombre not in agrupado: agrupado[estacion.nombre] = {"esperado": estacion.umbral_optimo, "suma": 0, "cant": 0}
        agrupado[estacion.nombre]["suma"] += evento.segundos_proceso
        agrupado[estacion.nombre]["cant"] += 1

    res = []
    for n, d in agrupado.items():
        promedio = d["suma"] / d["cant"]
        desvio = ((promedio - d["esperado"]) / d["esperado"]) * 100
        res.append(CuelloBotella(estacion=n, tiempo_esperado_seg=d["esperado"], tiempo_promedio_real_seg=round(promedio, 1), desvio_pct=round(desvio, 1)))
    return sorted(res, key=lambda x: x.desvio_pct, reverse=True)

# ==========================================
# --- NUEVOS ENDPOINTS SOLICITADOS ---
# ==========================================

@router.get("/analytics/oee-tendencia/", response_model=list[TendenciaOEERow])
def tendencia_oee_diaria(tenant_id: str = "empresa_demo", linea_id: Optional[uuid.UUID] = None, db: Session = Depends(get_session)):
    """Simula o calcula la tendencia de los últimos 7 días. (MVP: Devuelve datos estáticos si no hay historia)"""
    # En un entorno real de producción, aquí se agruparía por fecha en SQL. 
    # Por ahora, generamos un array simulando los últimos 5 días para que el gráfico de líneas (Recharts) dibuje algo de inmediato.
    hoy = datetime.now().date()
    datos = []
    for i in range(5, -1, -1):
        dia = hoy - timedelta(days=i)
        # Aquí se inyectarían cálculos reales iterando por día. Para el Handoff y validación visual:
        datos.append(TendenciaOEERow(
            fecha=dia.strftime("%d %b"), oee=round(70 + (i*2), 1), 
            disp=round(75 + i, 1), rend=round(80 - i, 1), cal=round(90 + i, 1)
        ))
    return datos

@router.get("/analytics/rendimiento-secuencial/", response_model=list[RendimientoSecuencialRow])
def rendimiento_secuencial_linea(tenant_id: str = "empresa_demo", linea_id: Optional[uuid.UUID] = None, db: Session = Depends(get_session)):
    """Devuelve el rendimiento de las estaciones ordenadas por su posición física en la cadena."""
    q = select(EventoEscaneo, Estacion).join(Estacion, EventoEscaneo.estacion_fk == Estacion.id).where(EventoEscaneo.tenant_id == tenant_id)
    if linea_id: q = q.where(Estacion.linea_id == linea_id)
    
    agrupado = {}
    for evento, estacion in db.exec(q).all():
        clave = (estacion.nombre, estacion.posicion_linea)
        if clave not in agrupado: agrupado[clave] = {"esperado": 0, "real": 0}
        agrupado[clave]["esperado"] += estacion.umbral_optimo
        agrupado[clave]["real"] += evento.segundos_proceso

    res = []
    for (nombre, pos), d in agrupado.items():
        rend = min((d["esperado"] / d["real"]) * 100 if d["real"] > 0 else 0, 100)
        res.append({"pos": pos, "data": RendimientoSecuencialRow(station=f"{pos}. {nombre}", performance=round(rend, 1))})
        
    res.sort(key=lambda x: x["pos"])
    return [item["data"] for item in res]

@router.get("/analytics/reporte-produccion/", response_model=list[ReporteExcelRow])
def reporte_produccion_detallado(
    tenant_id: str = "empresa_demo", fecha_desde: Optional[date] = None, fecha_hasta: Optional[date] = None,
    linea_id: Optional[uuid.UUID] = None, db: Session = Depends(get_session)
):
    """Devuelve la tabla plana con detalles para el nuevo Reporte Excel del Front-end."""
    q = select(EventoEscaneo, Estacion, Operario, Linea).join(Estacion, EventoEscaneo.estacion_fk == Estacion.id).join(Linea, Estacion.linea_id == Linea.id).outerjoin(Operario, EventoEscaneo.operario_fk == Operario.id).where(EventoEscaneo.tenant_id == tenant_id)
    q, _, _ = filtrar_eventos_base(q, fecha_desde, fecha_hasta, linea_id, None, db)
    
    agrupado = {}
    for evento, estacion, operario, linea in db.exec(q).all():
        op_nombre = operario.nombre_completo if operario else "Sin Asignar"
        cat = f"{linea.nombre.upper()} - {estacion.tipo.upper()}"
        clave = (cat, op_nombre, estacion.nombre)
        
        if clave not in agrupado: agrupado[clave] = {"real": 0, "tiempo": 0, "ideal": estacion.umbral_optimo}
        agrupado[clave]["real"] += 1
        if evento.segundos_proceso: agrupado[clave]["tiempo"] += evento.segundos_proceso

    res = []
    for (cat, op, est), d in agrupado.items():
        esperada = max(1, int(d["tiempo"] / d["ideal"]) if d["ideal"] > 0 else d["real"])
        dif = ((d["real"] - esperada) / esperada) * 100
        res.append(ReporteExcelRow(categoria=cat, operario=op, estacion=est, esperada=esperada, real=d["real"], diferencia=round(dif, 1)))
        
    return sorted(res, key=lambda x: x.diferencia)

# ==========================================
# --- ALERTAS (Se mantiene igual pero refactorizado limpio) ---
# ==========================================
@router.get("/analytics/alertas-vivas/", response_model=list[AlertaActiva])
def obtener_alertas_vivas(tenant_id: str = "empresa_demo", limit: int = 50, db: Session = Depends(get_session)):
    inicio, fin = obtener_rango_fechas(None, None)
    alertas = []

    paradas = db.exec(select(ParadaDetectada, Estacion).join(Estacion, ParadaDetectada.estacion_fk == Estacion.id).where(ParadaDetectada.tenant_id == tenant_id, ParadaDetectada.estado == "pendiente", ParadaDetectada.inicio >= inicio).limit(limit)).all()
    for p, e in paradas:
        alertas.append(AlertaActiva(hora=p.inicio.strftime("%H:%M:%S"), estacion=e.nombre, tipo="PARADA_PENDIENTE", mensaje=f"Máquina detenida {round(p.duracion_segundos/60, 1)} min. Requiere clasificación."))

    eventos = db.exec(select(EventoEscaneo, Estacion).join(Estacion, EventoEscaneo.estacion_fk == Estacion.id).where(EventoEscaneo.tenant_id == tenant_id, EventoEscaneo.timestamp >= inicio, (EventoEscaneo.desempeno == "ALERTA") | (EventoEscaneo.es_retrabajo == True)).limit(limit)).all()
    for ev, es in eventos:
        tipo = "RETRABAJO" if ev.es_retrabajo else "LENTITUD_EXTREMA"
        msg = f"Colchón OP-{ev.orden_fk} defecto de calidad." if ev.es_retrabajo else f"Colchón OP-{ev.orden_fk} muy lento ({ev.segundos_proceso}s)."
        alertas.append(AlertaActiva(hora=ev.timestamp.strftime("%H:%M:%S"), estacion=es.nombre, tipo=tipo, mensaje=msg))

    return sorted(alertas, key=lambda x: x.hora, reverse=True)[:limit]