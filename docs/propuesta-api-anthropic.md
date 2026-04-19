# Propuesta de Habilitación — API Anthropic (Claude AI)
### Dirección de Tecnología → Gerencia General

---

**Fecha:** 19 de abril de 2026
**Elaborado por:** Dirección de Tecnología
**Clasificación:** Interno — Uso restringido

---

## Resumen Ejecutivo

Se solicita autorización para la habilitación y consumo de la API de Anthropic (Claude AI) con el propósito de implementar un sistema automatizado de revisión de código fuente integrado con nuestro flujo de desarrollo de software.

La propuesta tiene un impacto directo en la velocidad de entrega, la calidad del producto y la reducción de deuda técnica, con un costo operativo estimado de **$12 a $24 USD/mes** para el volumen actual del equipo — inferior al costo de una hora de trabajo de un desarrollador senior.

---

## 1. Contexto y Problema

El equipo de desarrollo actualmente gestiona el ciclo de vida de tareas en **Plane** y el código en **GitHub**. El proceso de revisión de código presenta las siguientes fricciones:

| Problema | Impacto |
|---|---|
| Las revisiones de código dependen exclusivamente de disponibilidad humana | Tiempos de espera de 2 a 24 horas entre que un PR se abre y recibe feedback |
| Los revisores deben recordar el contexto de cada ticket de Plane | Revisiones desconectadas del objetivo del negocio |
| No existe un estándar automatizado de calidad mínima | Issues de seguridad y rendimiento llegan a producción |
| El estado de las tareas en Plane debe actualizarse manualmente | Fricción operativa, estados desactualizados, pérdida de trazabilidad |

Estos problemas generan retrasos en el ciclo de entrega y aumentan el riesgo de que defectos lleguen a producción.

---

## 2. Solución Propuesta

Se ha desarrollado internamente un sistema denominado **Review App** — una plataforma de revisión de código impulsada por IA que se integra directamente con GitHub y Plane.

### Flujo automatizado

```
Desarrollador abre un Pull Request
          │
          ▼
GitHub envía evento al sistema (webhook)
          │
          ▼
El sistema verifica la firma criptográfica del evento (HMAC-SHA256)
          │
          ▼
Se recupera automáticamente:
  · Diferencia de código (diff del PR)
  · Contexto del ticket vinculado en Plane
  · Código relacionado del repositorio (búsqueda semántica)
          │
          ▼
Claude Haiku clasifica la complejidad del cambio
  · Trivial (<50 líneas) → aprobado automáticamente
  · Moderado / Complejo  → revisión completa con Claude Sonnet
          │
          ▼
El sistema publica automáticamente:
  · Comentario de revisión en el PR de GitHub (issues, severidades, recomendación)
  · Resumen en la tarea de Plane
  · Mueve el estado de la tarea: Revisión → QA Testing / Rechazado
```

### Resultado operativo

- El desarrollador abre un PR y en **menos de 60 segundos** tiene feedback estructurado.
- Cada revisión incluye el contexto del ticket de negocio asociado — el AI sabe **qué** se debía implementar.
- El estado de Plane se mueve automáticamente. Cero actualizaciones manuales.
- El equipo humano revisa únicamente lo que la IA ya filtró: los cambios que requieren juicio humano.

---

## 3. Arquitectura Técnica

El sistema está construido sobre tecnología de código abierto, desplegable en infraestructura propia:

| Componente | Tecnología | Propósito |
|---|---|---|
| API de webhooks | FastAPI (Python) | Recibe eventos de GitHub, retorna HTTP 202 en < 1 segundo |
| Cola de tareas | Celery + Redis | Procesamiento asíncrono — la revisión no bloquea la entrega |
| Base de datos | PostgreSQL | Historial de revisiones, métricas por desarrollador |
| Indexador semántico | Tree-sitter + ChromaDB | Entiende el código fuente del repositorio completo |
| Búsqueda híbrida | BM25 + Embeddings vectoriales | Recupera el código más relevante al contexto del PR |
| IA de clasificación | Claude Haiku | Clasifica trivial / moderado / complejo ($0.003/PR) |
| IA de revisión | Claude Sonnet | Revisión profunda solo para PRs moderados y complejos ($0.07/PR) |
| Dashboard | Next.js 15 | Panel de métricas: aprobación por desarrollador, tendencias |

### Datos que no salen de la organización

- El código fuente **nunca se almacena en servidores de terceros** de forma permanente. Se envía únicamente el fragmento de código relevante para la revisión (máximo 6,000 caracteres por llamada) a la API de Anthropic durante el procesamiento, y no se retiene.
- Toda la información de revisiones, métricas y repositorios se persiste en **PostgreSQL en infraestructura propia**.

---

## 4. Modelo de Consumo y Costos

La API de Anthropic se factura por tokens procesados (unidades de texto). El sistema está diseñado para minimizar el consumo mediante una arquitectura de dos modelos:

### Costo por revisión

| Tipo de PR | % del volumen | Modelos utilizados | Costo unitario |
|---|---|---|---|
| Trivial (< 50 líneas) | ~30 % | Solo Haiku | ~$0.003 |
| Moderado | ~40 % | Haiku + Sonnet | ~$0.073 |
| Complejo (> 300 líneas) | ~30 % | Haiku + Sonnet | ~$0.073 |

### Proyección mensual

| Volumen de PRs/mes | Costo mensual estimado |
|---|---|
| 100 PRs | ~$2.40 USD |
| 500 PRs | ~$12 USD |
| 1,000 PRs | ~$24 USD |
| 5,000 PRs | ~$120 USD |

**Los embeddings vectoriales no tienen costo adicional** — se ejecutan localmente con el modelo `all-MiniLM-L6-v2` (código abierto, sin llamadas externas).

### Comparativa de valor

| Métrica | Situación actual | Con Review App |
|---|---|---|
| Tiempo de primera revisión | 2–24 horas | < 60 segundos |
| Costo de revisión humana por PR | ~$15–$30 USD (tiempo dev senior) | $0.003–$0.073 USD |
| Actualización de estado en Plane | Manual, frecuentemente olvidada | Automática, 100% de trazabilidad |
| Cobertura de revisión | Parcial (depende de disponibilidad) | 100 % de PRs revisados |

---

## 5. Seguridad y Cumplimiento

### Verificación criptográfica

Cada webhook de GitHub es verificado mediante firma `HMAC-SHA256` antes de cualquier procesamiento. Requests sin firma válida son rechazados con `HTTP 401`.

### Política de datos de Anthropic

Anthropic dispone de una política empresarial en la que los datos enviados a través de la API **no se utilizan para entrenar modelos** y no se retienen más allá del procesamiento de la solicitud. Esta política está disponible en `anthropic.com/legal/api-data-usage`.

### Exposición mínima de datos

El sistema nunca envía el repositorio completo a la API. Solo se envía:
- El diff del PR (cambios específicos, no el código base completo).
- Fragmentos de código relevantes (máximo 6,000 caracteres seleccionados por búsqueda semántica).
- El título y descripción del ticket de Plane vinculado.

No se envían: credenciales, variables de entorno, datos de usuarios finales, ni información de negocio confidencial.

### Autenticación con GitHub

El sistema utiliza el protocolo oficial de GitHub Apps con autenticación `RS256 JWT` y tokens de instalación de 1 hora de vigencia. No se almacenan credenciales de GitHub a largo plazo.

---

## 6. Plan de Implementación

El sistema ya ha sido desarrollado y se encuentra en estado de integración. El plan de activación es el siguiente:

| Fase | Descripción | Duración estimada |
|---|---|---|
| **Habilitación** | Activación de la API key de Anthropic y configuración de entorno | 1 día |
| **Piloto** | Conexión con 1 repositorio de prueba, validación del flujo completo | 3 días |
| **Expansión** | Conexión de repositorios de producción, ajuste de umbrales y estados de Plane | 1 semana |
| **Dashboard** | Activación del panel de métricas para seguimiento por equipo | 1 semana |

**Prerequisitos ya completados:**
- Sistema desarrollado (FastAPI + Celery + ChromaDB + Next.js).
- Integración con Plane SDK implementada y probada.
- Integración con GitHub Apps implementada.
- Infraestructura Docker definida (docker-compose.yml).

**Único prerequisito pendiente:** API key de Anthropic habilitada.

---

## 7. Riesgos y Mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Costo mayor al proyectado por volumen de PRs elevado | Baja | Bajo | Umbral `max_diff_lines: 3000` — PRs excesivamente grandes se omiten. Límites de gasto configurables en la consola de Anthropic. |
| Falsos negativos — IA aprueba código con bugs | Media | Medio | El sistema es una capa adicional, no reemplaza la revisión humana. Los desarrolladores siguen siendo responsables. |
| Dependencia de disponibilidad del servicio externo | Baja | Bajo | Las tareas Celery tienen `max_retries=3` con backoff. Si la API no está disponible, el PR simplemente no recibe comentario automático — el flujo humano sigue intacto. |
| Privacidad del código fuente | Baja | Alto | Política contractual de Anthropic: datos API no se retienen ni se usan para entrenamiento. Solo se envían fragmentos, no repositorios completos. |

---

## 8. Métricas de Éxito

Se propone evaluar el impacto del sistema a 60 días de operación con las siguientes métricas:

| Métrica | Baseline actual | Objetivo a 60 días |
|---|---|---|
| Tiempo promedio de primera revisión | > 4 horas | < 5 minutos |
| % de PRs con revisión antes de merge | < 70 % | 100 % |
| % de estados de Plane actualizados correctamente | < 60 % | > 95 % |
| Issues de severidad alta/crítica detectados pre-merge | No medido | Línea base establecida |
| Costo mensual de API | $0 | < $30 USD |

---

## 9. Recomendación y Solicitud

La Dirección de Tecnología recomienda aprobar la habilitación de la API de Anthropic bajo las siguientes condiciones:

1. **Presupuesto mensual:** hasta $50 USD/mes en API de Anthropic (holgura sobre el proyectado de $24 USD para absorber picos de actividad).
2. **Modalidad de contratación:** pago por uso (pay-as-you-go), sin compromiso mínimo mensual. Se puede desactivar en cualquier momento.
3. **Responsable técnico:** Dirección de Tecnología, con revisión de costos mensual.
4. **Periodo de evaluación:** 60 días, con informe de métricas al término.

La inversión estimada representa **menos del 0.1 % del costo mensual del equipo de desarrollo**, con un retorno directo en velocidad de entrega y trazabilidad del proceso.

---

## Anexo A — Comparativa de Herramientas del Mercado

| Herramienta | Modelo | Costo estimado (500 PRs/mes) | Observaciones |
|---|---|---|---|
| **Review App (interno)** | Claude Haiku + Sonnet | ~$12 USD | Control total, integración Plane nativa, sin lock-in |
| CodeRabbit | Propio | ~$240 USD ($12/dev × 5 devs × mes) | SaaS, sin integración Plane, datos en terceros |
| pr-agent (Codium) | GPT-4 / Claude | ~$100 USD + licencia | AGPL-3.0 — requiere abrir código fuente si se hace SaaS |
| GitHub Copilot for PRs | GPT-4 | ~$190 USD ($19/dev × 10 devs) | Sin integración Plane, datos en Microsoft |
| Revisión manual únicamente | N/A | ~$1,500–$3,000 USD (tiempo dev) | Estimado en base a 30 PRs/dev/mes × 1h/revisión |

La solución interna ofrece la mayor relación costo-control-integración del mercado para nuestro caso de uso específico.

---

## Anexo B — Información Técnica de la API

| Parámetro | Detalle |
|---|---|
| Proveedor | Anthropic, PBC (San Francisco, CA) |
| Endpoint | `https://api.anthropic.com/v1/messages` |
| Autenticación | API Key en header `x-api-key` |
| Modelos utilizados | `claude-haiku-4-5-20251001`, `claude-sonnet-4-6` |
| Tipo de facturación | Por tokens (input + output), pago por uso |
| SLA del servicio | 99.9 % uptime (disponible en anthropic.com/status) |
| Política de datos | Los datos de API no se usan para entrenar modelos |
| Documentación | `docs.anthropic.com` |
| Cumplimiento | SOC 2 Type II, CSA STAR Level 1 |

---

*Documento elaborado por la Dirección de Tecnología.*
*Para consultas técnicas adicionales, contactar al Director de Tecnología.*
