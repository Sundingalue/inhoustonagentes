# IN Houston Agentes

Sistema escalable de agentes de voz integrados con ElevenLabs, Google Sheets y flujos personalizados.

## 🧱 Estructura del proyecto

- `agents/`: Archivos JSON con configuración por agente.
- `matrix/`: Flujo global de reglas de la empresa.
- `workflows/`: Procesadores y utilidades de ejecución.
- `services/`: Correos, agendas, ubicación, Sheets, análisis, facturación.
- `api/`: Punto de entrada para recibir eventos de ElevenLabs.
- `static/`: Archivos públicos si se requieren.
  
## 🚀 Ejecutar localmente

```bash
uvicorn api.main:app --reload
