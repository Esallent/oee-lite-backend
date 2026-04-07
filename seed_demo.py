import uuid
import random
from datetime import datetime, date, time, timedelta
from sqlmodel import Session, select
from app.core.database import engine
from app.models.domain import (
    Linea, Turno, Operario, Estacion, MotivoParada, 
    AsignacionMatriz, EventoEscaneo, ParadaDetectada, EstadoParada,
    Supervisor, AsignacionSupervisor
)

def poblar_demo_springwall():
    print("🧹 Iniciando el poblado de datos Mock para la Demo de Springwall...")
    
    with Session(engine) as db:
        tenant = "empresa_demo"
        hoy = datetime.now().date()
        ahora = datetime.now()

        # ==========================================
        # 1. MAESTROS (Línea y Turno)
        # ==========================================
        linea = Linea(tenant_id=tenant, nombre="Ensamblaje Principal")
        db.add(linea)
        db.commit()

        turno_m = Turno(tenant_id=tenant, linea_id=linea.id, nombre="Turno Mañana", hora_inicio=time(6, 0), hora_fin=time(14, 0))
        db.add(turno_m)

        # Motivos de parada
        m_falta_material = MotivoParada(tenant_id=tenant, nombre="Falta de Material", tipo_parada="NO_PLANIFICADA")
        m_hilo = MotivoParada(tenant_id=tenant, nombre="Corte de Hilo", tipo_parada="NO_PLANIFICADA")
        db.add_all([m_falta_material, m_hilo])

        # ==========================================
        # 2. EQUIPO DE TRABAJO (8 Operarios + 1 Supervisor)
        # ==========================================
        supervisor = Supervisor(tenant_id=tenant, legajo="SUP-001", nombre_completo="Supervisor General")
        db.add(supervisor)
        db.commit()

        operarios = []
        for i in range(1, 9):
            op = Operario(tenant_id=tenant, legajo=f"OP-{100+i}", nombre_completo=f"Operario Genérico {i}")
            db.add(op)
            operarios.append(op)
        db.commit()

        # Asignar Supervisor a la Línea
        asig_sup = AsignacionSupervisor(tenant_id=tenant, fecha=hoy, linea_id=linea.id, turno_id=turno_m.id, supervisor_id=supervisor.id)
        db.add(asig_sup)

        # ==========================================
        # 3. LAYOUT DE ESTACIONES (Con Cerradora A dividida)
        # ==========================================
        estaciones_data = [
            ("1. Pedalera (Ingreso)", "sensor", 60, 80),
            ("2. Matelaceado", "sensor", 120, 150),
            ("3. Forro / Escaneo", "escaneo_manual", 150, 180),
            ("4. Cerradora A - Puesto 1", "escaneo_manual", 120, 150),  # Dividida
            ("5. Cerradora A - Puesto 2", "escaneo_manual", 120, 150),  # Dividida
            ("6. Cerradora B", "escaneo_manual", 240, 280),             # Cuello de Botella
            ("7. Calidad A", "calidad", 90, 120),
            ("8. Calidad B", "calidad", 90, 120)
        ]

        estaciones = []
        for idx, (nombre, tipo, optimo, lento) in enumerate(estaciones_data):
            est = Estacion(
                tenant_id=tenant, linea_id=linea.id, nombre=nombre, tipo=tipo,
                umbral_optimo=optimo, umbral_lento=lento, posicion_linea=idx+1
            )
            db.add(est)
            estaciones.append(est)
        db.commit()

        # ==========================================
        # 4. MATRIZ DE ASIGNACIONES (1 Operario por Estación)
        # ==========================================
        for i, est in enumerate(estaciones):
            db.add(AsignacionMatriz(
                tenant_id=tenant, fecha=hoy, linea_id=linea.id, turno_id=turno_m.id,
                estacion_id=est.id, operario_id=operarios[i].id
            ))
        db.commit()

        # ==========================================
        # 5. SIMULACIÓN DE PRODUCCIÓN (2 Horas | 30 Colchones)
        # ==========================================
        print("🏭 Simulando 2 horas de producción (30 colchones del Plan)...")
        # Datos extraídos de tu CSV real
        orden_real = "OP-10232-1"
        sku_real = "102323080190"  # COL RES SOÑ BAHAMAS 080 X 190

        # Vamos a ir hacia atrás en el tiempo: Empezamos hace 2 horas y 15 minutos.
        tiempo_inicio_fabrica = ahora - timedelta(hours=2, minutes=15)
        
        for num_colchon in range(1, 31):
            secuencia = f"{num_colchon:03d}"
            # Ej: 00100000095102323080190
            barcode_colchon = f"{secuencia}{orden_real}{sku_real}"
            
            # El colchón empieza su recorrido por la línea
            tiempo_actual = tiempo_inicio_fabrica + timedelta(minutes=(num_colchon * 4)) # Entra un colchón cada 4 mins
            
            for i, est in enumerate(estaciones):
                # Generamos un tiempo de proceso realista basado en la estación
                if "Cerradora B" in est.nombre:
                    # La cerradora B tiene problemas constantes hoy (Lentos y Alertas)
                    t_proceso = random.randint(230, 290) 
                else:
                    # El resto trabaja normal
                    t_proceso = random.randint(est.umbral_optimo - 10, est.umbral_optimo + 15)

                tiempo_actual += timedelta(seconds=t_proceso)
                
                # Determinamos el semáforo y si es retrabajo
                desempeno = "OPTIMO"
                if t_proceso > est.umbral_optimo: desempeno = "LENTO"
                if t_proceso > est.umbral_lento: desempeno = "ALERTA"
                
                es_fallo = False
                if est.tipo == "calidad" and random.random() < 0.10: # 10% de retrabajo en calidad
                    es_fallo = True
                    desempeno = "ALERTA"

                db.add(EventoEscaneo(
                    tenant_id=tenant, barcode=barcode_colchon, orden_fk=orden_real, 
                    estacion_fk=est.id, operario_fk=operarios[i].id, 
                    timestamp=tiempo_actual, segundos_proceso=t_proceso, 
                    desempeno=desempeno, es_retrabajo=es_fallo
                ))

        # ==========================================
        # 6. INYECTAR PARADAS PARA LOS DASHBOARDS
        # ==========================================
        # Parada 1: Cerradora B se quedó sin material hace 1 hora (Ya resuelta)
        inicio_p1 = ahora - timedelta(hours=1)
        db.add(ParadaDetectada(
            tenant_id=tenant, estacion_fk=estaciones[5].id, motivo_fk=m_falta_material.id, 
            inicio=inicio_p1, fin=inicio_p1 + timedelta(minutes=12), duracion_segundos=720, estado="clasificada"
        ))

        # Parada 2: Forro/Escaneo tuvo corte de hilo (Ya resuelta)
        inicio_p2 = ahora - timedelta(minutes=40)
        db.add(ParadaDetectada(
            tenant_id=tenant, estacion_fk=estaciones[2].id, motivo_fk=m_hilo.id, 
            inicio=inicio_p2, fin=inicio_p2 + timedelta(minutes=6), duracion_segundos=360, estado="clasificada"
        ))

        # Parada 3: ¡ALERTA VIVA! Cerradora A (Puesto 1) está detenida AHORA MISMO
        inicio_p3 = ahora - timedelta(minutes=4)
        db.add(ParadaDetectada(
            tenant_id=tenant, estacion_fk=estaciones[3].id,
            inicio=inicio_p3, fin=ahora, duracion_segundos=240, estado=EstadoParada.PENDIENTE
        ))

        db.commit()
        print("✅ ¡La Fábrica Virtual está viva! 2 horas de producción registradas con éxito.")

if __name__ == "__main__":
    poblar_demo_springwall()