# OEE Lite — Documentación Técnica Completa

> **Objetivo**: Servir como especificación funcional y técnica para que el equipo de backend implemente las APIs REST/RPC, tablas de base de datos y lógica de negocio necesarias para reemplazar los datos mock del frontend.

---

## 1. Visión General del Producto

**OEE Lite** es un SaaS B2B multi-tenant para el seguimiento de la Eficiencia General de los Equipos (OEE) en entornos de fábrica. El frontend está 100% operativo con datos mock; el backend debe proveer las APIs reales.

### 1.1 Stack Tecnológico (Frontend)

| Tecnología | Versión | Uso |
|---|---|---|
| React | 18.x | UI Framework |
| Vite | 8.x | Bundler |
| TypeScript | 5.x | Tipado |
| Tailwind CSS | 3.x | Estilos |
| shadcn/ui | — | Componentes UI |
| @tanstack/react-query | 5.x | Data fetching & cache |
| react-router-dom | 6.x | Routing |
| react-i18next | 16.x | Internacionalización (ES/EN) |
| recharts | 2.x | Gráficos |
| date-fns | 3.x | Formateo de fechas |
| lucide-react | — | Iconos |

### 1.2 Arquitectura Frontend

```
src/
├── App.tsx                  # Router + Providers
├── layouts/MainLayout.tsx   # Sidebar + Header + Outlet
├── pages/                   # 5 páginas principales + Index + NotFound
├── components/
│   ├── layout/              # Header, Sidebar, LiveAlertsPanel
│   ├── home/                # Centro de Comando
│   ├── operador/            # Monitor de Telemetría
│   ├── dashboard/           # Dashboard Gerencial
│   ├── supervisor/          # Consola del Supervisor
│   ├── config/              # ABM de Catálogos
│   └── ui/                  # shadcn primitives
├── hooks/                   # Custom hooks (data fetching)
└── i18n/config.ts           # Traducciones ES/EN
```

---

## 2. Rutas y Páginas

| Ruta | Componente | Descripción | Roles Sugeridos |
|---|---|---|---|
| `/` | `HomePage` | Centro de Comando — vista general de fábrica en tiempo real | Todos |
| `/operador` | `OperadorPage` | Monitor de Telemetría — logs de dispositivos IoT en vivo | Operador |
| `/dashboard` | `DashboardPage` | Dashboard Gerencial — KPIs OEE, Pareto, tendencias, reportes | Gerente, Supervisor |
| `/supervisor` | `SupervisorPage` | Consola del Supervisor — paradas, asignaciones, planificación | Supervisor |
| `/configuracion` | `ConfiguracionPage` | ABM de Catálogos y carga de maestros/planes | Admin |

---

## 3. Modelo de Datos (Entidades)

### 3.1 Multi-Tenancy

```typescript
interface Tenant {
  id: string;         // "empresa_demo"
  name: string;       // "Acme Corp Industrial"
  logoUrl: string;    // URL del logo
}
```

> **Backend**: Toda tabla debe tener `tenant_id` como FK. El frontend obtiene el tenant del contexto de autenticación.

### 3.2 Catálogos Base (ABM en `/configuracion`)

#### Líneas de Producción
```typescript
interface LineaAbm {
  id: string;       // "L1"
  nombre: string;   // "Ensamblaje Principal"
}
```

#### Estaciones (pertenecen a una Línea)
```typescript
interface EstacionAbm {
  id: string;           // "E1"
  nombre: string;       // "Cerradora"
  lineaId: string;      // FK → Linea.id
  lineaNombre: string;  // denormalizado para display
}
```

#### Operarios
```typescript
interface OperarioAbm {
  id: string;       // "OP-101"
  legajo: string;   // "101"
  nombre: string;   // "Gabriel Gomez"
}
```

#### Turnos (pertenecen a una Línea)
```typescript
interface TurnoAbm {
  id: string;           // "T1"
  nombre: string;       // "Mañana"
  horaInicio: string;   // "06:00"
  horaFin: string;      // "14:00"
  lineaId: string;      // FK → Linea.id
  lineaNombre: string;  // denormalizado para display
}
```

#### Motivos de Parada
```typescript
interface MotivoAbm {
  id: string;          // "M1"
  descripcion: string; // "Falta de Material"
}
```

### 3.3 Relaciones entre Entidades

```
Tenant (1) ──→ (N) Línea
Línea  (1) ──→ (N) Estación
Línea  (1) ──→ (N) Turno
Tenant (1) ──→ (N) Operario
Tenant (1) ──→ (N) MotivoDeParada
```

> **Regla de negocio**: Los Turnos y Estaciones DEBEN estar vinculados a una Línea. Esta relación es obligatoria en el formulario de creación.

---

## 4. APIs Requeridas por Módulo

### 4.1 Tenant (`useTenant`)

| Método | Endpoint Sugerido | Query Key | Response |
|---|---|---|---|
| GET | `/api/tenant` | `["tenant", tenantId]` | `Tenant` |

**Configuración**: `staleTime: Infinity` — se cachea permanentemente durante la sesión.

---

### 4.2 Centro de Comando — HomePage (`useFactoryStatus`)

| Método | Endpoint Sugerido | Query Key | Polling | Response |
|---|---|---|---|---|
| GET | `/api/factory/status` | `["factoryStatus"]` | 30s | `FactoryData` |

```typescript
interface FactoryData {
  status: string;                    // "Operando"
  currentShift: string;              // "Mañana (06:00 - 14:00)"
  currentPlan: string;               // "Orden #4092 - Ensamblaje Motor V6"
  activeOperators: ActiveOperator[]; // Operarios activos ahora
  cameraUrl: string;                 // URL de cámara de fábrica
  lineLayout: Station[];             // Layout de estaciones con estado
}

interface Station {
  id: string;
  name: string;
  status: "active" | "idle" | "offline";
  operator: string | null;
}

interface ActiveOperator {
  id: number;
  name: string;
  station: string;
}
```

**Componentes que consumen**:
- `FactoryHeader` — muestra turno, plan, status, conteo de líneas/estaciones activas
- `LineMapVisualizer` — renderiza las estaciones como nodos visuales
- `FactoryCamera` — muestra la imagen de la cámara
- `ActiveOperators` — lista de operarios activos

---

### 4.3 Monitor de Telemetría — OperadorPage

#### 4.3.1 Dispositivos (`useDevices`)

| Método | Endpoint | Query Key | Polling | Response |
|---|---|---|---|---|
| GET | `/api/devices` | `["devices"]` | 5s | `Device[]` |

```typescript
interface Device {
  id: string;
  name: string;
  status: "online" | "offline";
  type: "scanner" | "pedal";
}
```

#### 4.3.2 Logs en Vivo (`useLiveDeviceLogs`)

| Método | Endpoint | Query Key | Polling | Response |
|---|---|---|---|---|
| GET | `/api/devices/logs` | `["liveLogs"]` | 2s | `DeviceLog[]` |

```typescript
interface DeviceLog {
  id: number;
  time: string;        // "14:05:22" (HH:mm:ss)
  device: string;      // Nombre del dispositivo
  payload: string;     // Dato crudo (ej. "OP-101", "PULSE", "SKU-8832-A")
  status: "success" | "warning" | "error";
  message: string;     // Descripción legible
}
```

> **Nota**: El frontend muestra hasta 50 logs. Considerar paginación o cursor-based para eficiencia.

---

### 4.4 Dashboard Gerencial — DashboardPage

Todos los hooks aceptarán filtros opcionales en el futuro (línea, estación, turno, fecha, rango de comparación). Actualmente el frontend tiene un componente `DashboardFilters` con estos selectores listos.

#### 4.4.1 KPIs OEE (`useOee`)

| Método | Endpoint | Query Key | Response |
|---|---|---|---|
| GET | `/api/dashboard/oee` | `["oee"]` | `OeeData` |

```typescript
interface OeeData {
  oee_general: number;        // 68.5 (%)
  disponibilidad: number;     // 75.0 (%)
  rendimiento: number;        // 88.0 (%)
  calidad: number;            // 92.5 (%)
  unidades_esperadas: number; // 1500
  unidades_producidas: number;// 1250
  minutos_perdidos: number;   // 120
}
```

#### 4.4.2 Tendencia OEE Diaria (`useTrend`)

| Método | Endpoint | Query Key | Response |
|---|---|---|---|
| GET | `/api/dashboard/trend` | `["oee-trend"]` | `TrendRow[]` |

```typescript
interface TrendRow {
  date: string;  // "Mar 01"
  oee: number;
  disp: number;  // disponibilidad
  rend: number;  // rendimiento
  cal: number;   // calidad
}
```

#### 4.4.3 Pareto de Paradas (`usePareto`)

| Método | Endpoint | Query Key | Response |
|---|---|---|---|
| GET | `/api/dashboard/pareto` | `["pareto"]` | `ParetoRow[]` |

```typescript
interface ParetoRow {
  motivo: string;          // "Falta de Material"
  tipo: string;            // "Logística", "Mantenimiento", "Setup", "Calidad"
  frecuencia: number;      // Cantidad de ocurrencias
  minutos_totales: number; // Tiempo total de parada
}
```

#### 4.4.4 Cuellos de Botella (`useBottlenecks`)

| Método | Endpoint | Query Key | Response |
|---|---|---|---|
| GET | `/api/dashboard/bottlenecks` | `["bottlenecks"]` | `BottleneckRow[]` |

```typescript
interface BottleneckRow {
  estacion: string;    // "Pintura"
  rendimiento: number; // 67 (%)
  esperado: number;    // Unidades esperadas
  real: number;        // Unidades reales
}
```

#### 4.4.5 Performance por Estación (`useSequentialLine`)

| Método | Endpoint | Query Key | Response |
|---|---|---|---|
| GET | `/api/dashboard/line-performance` | `["sequential-line"]` | `SequentialStationRow[]` |

```typescript
interface SequentialStationRow {
  station: string;     // "1. Corte"
  performance: number; // 95 (%)
}
```

#### 4.4.6 Reporte Operarios — Springwall (`useSpringwall`)

| Método | Endpoint | Query Key | Response |
|---|---|---|---|
| GET | `/api/dashboard/springwall` | `["springwall"]` | `SpringwallRow[]` |

```typescript
interface SpringwallRow {
  operario: string;       // "Juan Pérez"
  estacion: string;       // "Corte"
  esperada: number;       // 500
  real: number;           // 425
  diferencia_pct: number; // -15 (%)
}
```

#### 4.4.7 Reporte de Producción Detallado (`useExcelReport`)

| Método | Endpoint | Query Key | Response |
|---|---|---|---|
| GET | `/api/dashboard/excel-report` | `["excel-report"]` | `ExcelReportRow[]` |

```typescript
interface ExcelReportRow {
  categoria: string;   // "CERRADORES DE LINEA - COLCHON ENTERO"
  operario: string;    // "GOMEZ, GABRIEL"
  estacion: string;    // "Cerradora 1"
  esperada: number;    // 120
  real: number;        // 39
  diferencia: number;  // -68 (%)
}
```

> **Filtros futuros** para todos los endpoints del dashboard:
> ```
> ?lineaId=L1&estacionId=E1&turnoId=T1&fecha=2026-03-17&rangoComparacion=last7
> ```

---

### 4.5 Consola del Supervisor — SupervisorPage

#### 4.5.1 Paradas Pendientes (Tab: "Paradas en Vivo")

| Método | Endpoint | Query Key | Response |
|---|---|---|---|
| GET | `/api/supervisor/pending-stops` | `["pendingStops"]` | `ParadaPendiente[]` |

```typescript
interface ParadaPendiente {
  id: string;
  linea: string;            // "Ensamblaje Principal" — nombre de línea
  estacion: string;         // "Cerradora 1"
  turno: string;            // "Mañana"
  operario: string;         // "Gabriel Gomez"
  inicio: string;           // "14:05" (HH:mm)
  duracionSegundos: number; // 200
}
```

> **Regla visual**: Si `duracionSegundos > 150`, la fila se resalta en rojo (`bg-destructive/10`).

**Clasificar parada (mutación)**:

| Método | Endpoint | Query Key Invalidado | Request Body |
|---|---|---|---|
| POST | `/api/supervisor/stops/:stopId/classify` | `["pendingStops"]` | `{ reason: string }` |

`reason` es uno de los motivos de parada del catálogo:
```typescript
const motivosParada = [
  "Falta de Material", "Falla Eléctrica", "Atasco Mecánico",
  "Descanso Operario", "Cambio de Formato", "Mantenimiento Preventivo"
];
```

#### 4.5.2 Asignación Matricial de Operarios (Tab: "Asignación de Personal")

**Lectura de asignaciones**:

| Método | Endpoint | Query Key | Response |
|---|---|---|---|
| GET | `/api/supervisor/matrix-assignments` | `["matrixAssignments"]` | `AsignacionMatriz[]` |

```typescript
interface AsignacionMatriz {
  id: number;
  fecha: string;    // "2026-03-17" (YYYY-MM-DD)
  lineaId: string;  // "L1"
  turnoId: string;  // "T1"
  estacion: string; // "Cerradora 1"
  operario: string; // "Gabriel Gomez"
}
```

**Lógica de filtrado reactivo en frontend**: La tabla SOLO muestra las asignaciones donde `fecha`, `lineaId` y `turnoId` coinciden con los valores seleccionados en el formulario. Si falta alguno de los 3, muestra mensaje vacío.

**Columnas de la tabla**: Solo 2 — `Estación` y `Operario`.

**Crear asignación (mutación)**:

| Método | Endpoint | Query Key Invalidado | Request Body |
|---|---|---|---|
| POST | `/api/supervisor/matrix-assignments` | `["matrixAssignments"]` | `{ fecha, lineaId, turnoId, estacion, operario }` |

**Campos del formulario**:
1. **DatePicker** → `fecha` (YYYY-MM-DD)
2. **Select Línea** → `lineaId` (consume catálogo de Líneas)
3. **Select Turno** → `turnoId` (filtrado por línea seleccionada, consume `TurnoAbm`)
4. **Select Estación** → `estacion` (filtrada por línea seleccionada, consume `EstacionAbm`)
5. **Select Operario** → `operario` (consume catálogo de Operarios)

> **Regla de negocio sugerida**: Validar en backend que no exista duplicado de asignación (mismo día + línea + turno + estación).

#### 4.5.3 Planificación de Paradas (Tab: "Planificación")

**Lectura de paradas programadas**:

| Método | Endpoint | Query Key | Response |
|---|---|---|---|
| GET | `/api/supervisor/scheduled-stops` | `["scheduledStops"]` | `ScheduledStop[]` |

```typescript
interface ScheduledStop {
  id: number;
  fechas: string;   // "17 Mar 2026" o "17 Mar 2026 - 20 Mar 2026"
  turno: string;    // "Mañana"
  linea: string;    // "Ensamblaje Principal" o "Todas"
  motivo: string;   // "Descanso"
  horario: string;  // "10:00 - 10:15"
}
```

**Crear parada programada (mutación)**:

| Método | Endpoint | Query Key Invalidado | Request Body |
|---|---|---|---|
| POST | `/api/supervisor/scheduled-stops` | `["scheduledStops"]` | `{ motivo, fechas, linea, turno, horario }` |

**Campos del formulario**:
1. **Select Motivo** → catálogo: `["Descanso", "Almuerzo", "Cambio de Turno", "Mantenimiento Preventivo"]`
2. **DatePickerWithRange** → rango de fechas (un día o varios)
3. **Select Línea** → catálogo de Líneas
4. **Select Turno** → `["Mañana", "Tarde", "Noche"]`
5. **Input Horario** → texto libre, ej. `"14:00 - 14:30"`

---

### 4.6 Alertas en Vivo (`useLiveAlerts`)

| Método | Endpoint | Query Key | Polling | Response |
|---|---|---|---|---|
| GET | `/api/alerts/live` | `["live-alerts"]` | 10s | `LiveAlert[]` |

```typescript
type AlertType = "PARADA_PENDIENTE" | "RETRABAJO" | "LENTITUD_EXTREMA";

interface LiveAlert {
  id: number;
  hora: string;       // "10:45"
  estacion: string;   // "Corte"
  tipo: AlertType;
  mensaje: string;    // "Máquina detenida > 3 min sin clasificar"
}
```

> El frontend maneja el estado de "leído/no leído" localmente con `useState<Set<number>>`. Si se necesita persistir, agregar `readAt` nullable al modelo.

---

### 4.7 Configuración — ABM de Catálogos (`useConfigData`)

Todos los catálogos comparten la misma estructura CRUD:

| Operación | Método | Endpoint | Query Key | Body |
|---|---|---|---|---|
| Listar | GET | `/api/config/:entity` | `["config", entity]` | — |
| Crear | POST | `/api/config/:entity` | invalidate `["config", entity]` | `Record<string, string>` |

Donde `entity` ∈ `["lineas", "estaciones", "operarios", "turnos", "motivos"]`.

**Campos por formulario de creación**:

| Entidad | Campos | Campos Obligatorios |
|---|---|---|
| `lineas` | `nombre` | `nombre` |
| `estaciones` | `nombre`, `lineaId` (Select) | todos |
| `operarios` | `legajo`, `nombre` | todos |
| `turnos` | `nombre`, `horaInicio`, `horaFin`, `lineaId` (Select) | todos |
| `motivos` | `descripcion` | `descripcion` |

### 4.8 Carga Masiva de Archivos

| Operación | Método | Endpoint | Body |
|---|---|---|---|
| Importar SKUs | POST | `/api/config/upload/skus` | `multipart/form-data` (File) |
| Importar Plan | POST | `/api/config/upload/plan` | `multipart/form-data` (File) |

Response esperado: `{ success: boolean, rows: number }`.

---

## 5. Alertas y Notificaciones

### Tipos de Alerta

| Tipo | Icono | Color | Trigger Sugerido |
|---|---|---|---|
| `PARADA_PENDIENTE` | Hourglass | Rojo (destructive) | Estación detenida > 3 min sin clasificar |
| `RETRABAJO` | Wrench | Ámbar | Piezas devueltas consecutivas |
| `LENTITUD_EXTREMA` | TrendingDown | Amarillo | Ciclo +50% más lento de lo normal |

El panel de alertas se muestra como un `Sheet` lateral derecho, accesible desde el header.

---

## 6. Internacionalización (i18n)

El sistema soporta **Español (es)** y **Inglés (en)**. El idioma por defecto es español.

Todas las claves de traducción están en `src/i18n/config.ts`. El backend NO necesita traducir — solo enviar datos crudos. Las labels se resuelven en frontend.

---

## 7. Esquema de Base de Datos Sugerido

```sql
-- Multi-tenancy
CREATE TABLE tenants (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  logo_url TEXT
);

-- Catálogos
CREATE TABLE lineas (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenants(id) NOT NULL,
  nombre TEXT NOT NULL
);

CREATE TABLE estaciones (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenants(id) NOT NULL,
  linea_id UUID REFERENCES lineas(id) NOT NULL,
  nombre TEXT NOT NULL
);

CREATE TABLE operarios (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenants(id) NOT NULL,
  legajo TEXT NOT NULL,
  nombre TEXT NOT NULL
);

CREATE TABLE turnos (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenants(id) NOT NULL,
  linea_id UUID REFERENCES lineas(id) NOT NULL,
  nombre TEXT NOT NULL,
  hora_inicio TIME NOT NULL,
  hora_fin TIME NOT NULL
);

CREATE TABLE motivos_parada (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenants(id) NOT NULL,
  descripcion TEXT NOT NULL
);

-- Operaciones
CREATE TABLE paradas (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenants(id) NOT NULL,
  estacion_id UUID REFERENCES estaciones(id) NOT NULL,
  turno_id UUID REFERENCES turnos(id) NOT NULL,
  operario_id UUID REFERENCES operarios(id),
  inicio TIMESTAMPTZ NOT NULL,
  fin TIMESTAMPTZ,
  duracion_segundos INT GENERATED ALWAYS AS (
    EXTRACT(EPOCH FROM (COALESCE(fin, NOW()) - inicio))::INT
  ) STORED,
  motivo_id UUID REFERENCES motivos_parada(id), -- NULL = pendiente de clasificar
  clasificada_por UUID, -- FK al usuario supervisor que clasificó
  clasificada_at TIMESTAMPTZ
);

CREATE TABLE asignaciones_matriz (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenants(id) NOT NULL,
  fecha DATE NOT NULL,
  linea_id UUID REFERENCES lineas(id) NOT NULL,
  turno_id UUID REFERENCES turnos(id) NOT NULL,
  estacion_id UUID REFERENCES estaciones(id) NOT NULL,
  operario_id UUID REFERENCES operarios(id) NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(tenant_id, fecha, linea_id, turno_id, estacion_id) -- Una estación, un operario por combinación
);

CREATE TABLE paradas_programadas (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenants(id) NOT NULL,
  motivo TEXT NOT NULL,
  fecha_inicio DATE NOT NULL,
  fecha_fin DATE, -- NULL = un solo día
  linea_id UUID REFERENCES lineas(id), -- NULL = "Todas"
  turno TEXT NOT NULL,
  hora_inicio TIME NOT NULL,
  hora_fin TIME NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Telemetría IoT
CREATE TABLE dispositivos (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenants(id) NOT NULL,
  nombre TEXT NOT NULL,
  tipo TEXT NOT NULL CHECK (tipo IN ('scanner', 'pedal')),
  status TEXT NOT NULL DEFAULT 'offline' CHECK (status IN ('online', 'offline'))
);

CREATE TABLE dispositivos_logs (
  id BIGSERIAL PRIMARY KEY,
  tenant_id UUID REFERENCES tenants(id) NOT NULL,
  dispositivo_id UUID REFERENCES dispositivos(id) NOT NULL,
  timestamp TIMESTAMPTZ DEFAULT NOW(),
  payload TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('success', 'warning', 'error')),
  mensaje TEXT NOT NULL
);

-- Alertas
CREATE TABLE alertas (
  id BIGSERIAL PRIMARY KEY,
  tenant_id UUID REFERENCES tenants(id) NOT NULL,
  estacion_id UUID REFERENCES estaciones(id) NOT NULL,
  tipo TEXT NOT NULL CHECK (tipo IN ('PARADA_PENDIENTE', 'RETRABAJO', 'LENTITUD_EXTREMA')),
  mensaje TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  read_at TIMESTAMPTZ -- NULL = no leída
);
```

---

## 8. Reglas de Negocio Clave

| # | Regla | Módulo |
|---|---|---|
| 1 | Los Turnos DEBEN estar vinculados a una Línea | Configuración |
| 2 | Las Estaciones DEBEN estar vinculadas a una Línea | Configuración |
| 3 | Paradas con duración > 150s se resaltan visualmente | Supervisor |
| 4 | Una parada sin `motivo_id` está "pendiente de clasificar" | Supervisor |
| 5 | La asignación matricial es única por (fecha + línea + turno + estación) | Supervisor |
| 6 | Al seleccionar Línea, los Turnos y Estaciones se filtran | Supervisor |
| 7 | La tabla de asignaciones solo se muestra si Día + Línea + Turno están seleccionados | Supervisor |
| 8 | Las alertas se consultan cada 10s; el estado leído/no-leído es local (o persistible) | Global |
| 9 | Factory status se refresca cada 30s | Home |
| 10 | Device logs se refrescan cada 2s | Operador |

---

## 9. Polling y Frecuencias de Refresco

| Hook | Intervalo | Justificación |
|---|---|---|
| `useFactoryStatus` | 30s | Estado general de planta |
| `useLiveDeviceLogs` | 2s | Logs IoT en tiempo real |
| `useDevices` | 5s | Estado de dispositivos |
| `useLiveAlerts` | 10s | Alertas operacionales |
| Otros hooks | Sin polling | Datos bajo demanda |

> **Recomendación**: Para `useLiveDeviceLogs` y `useLiveAlerts`, considerar migrar a WebSockets o Server-Sent Events (SSE) en producción.

---

## 10. Resumen de Endpoints Requeridos

| # | Método | Endpoint | Descripción |
|---|---|---|---|
| 1 | GET | `/api/tenant` | Datos del tenant actual |
| 2 | GET | `/api/factory/status` | Estado general de fábrica |
| 3 | GET | `/api/devices` | Lista de dispositivos IoT |
| 4 | GET | `/api/devices/logs` | Logs en vivo de dispositivos |
| 5 | GET | `/api/alerts/live` | Alertas activas |
| 6 | GET | `/api/dashboard/oee` | KPIs OEE |
| 7 | GET | `/api/dashboard/trend` | Tendencia diaria OEE |
| 8 | GET | `/api/dashboard/pareto` | Pareto de paradas |
| 9 | GET | `/api/dashboard/bottlenecks` | Cuellos de botella |
| 10 | GET | `/api/dashboard/line-performance` | Performance por estación |
| 11 | GET | `/api/dashboard/springwall` | Reporte operarios |
| 12 | GET | `/api/dashboard/excel-report` | Reporte producción detallado |
| 13 | GET | `/api/supervisor/pending-stops` | Paradas sin clasificar |
| 14 | POST | `/api/supervisor/stops/:id/classify` | Clasificar una parada |
| 15 | GET | `/api/supervisor/matrix-assignments` | Asignaciones matriciales |
| 16 | POST | `/api/supervisor/matrix-assignments` | Crear asignación matricial |
| 17 | GET | `/api/supervisor/scheduled-stops` | Paradas programadas |
| 18 | POST | `/api/supervisor/scheduled-stops` | Programar parada |
| 19 | GET | `/api/config/:entity` | Listar catálogo |
| 20 | POST | `/api/config/:entity` | Crear registro en catálogo |
| 21 | POST | `/api/config/upload/skus` | Importar maestro SKUs |
| 22 | POST | `/api/config/upload/plan` | Importar plan producción |

---

## 11. Próximos Pasos Sugeridos

1. **Autenticación y autorización**: Implementar auth con roles (Admin, Supervisor, Operador, Gerente).
2. **RLS por tenant**: Aplicar Row Level Security en todas las tablas.
3. **Filtros del Dashboard**: Conectar los filtros de `DashboardFilters` a query params de los endpoints.
4. **WebSockets**: Migrar los endpoints de polling intensivo (logs, alertas) a conexiones en tiempo real.
5. **Paginación**: Implementar en tablas con potencial de muchos registros (logs, paradas, asignaciones).
6. **Edición/Eliminación en catálogos**: El frontend solo tiene creación (POST); agregar PUT y DELETE.
7. **Validaciones de negocio en backend**: Duplicados de asignación, horarios solapados en paradas programadas.
