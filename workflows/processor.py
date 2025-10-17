# workflows/processor.py: Lógica de flujo de trabajo que maneja los eventos del agente.

import json
import logging
from typing import Dict, Any, List

# Importar el nuevo servicio de análisis
from services.analysis_service import extract_customer_data 
from services.calendar_checker import check_availability
from services.email_service import send_email
from services.sheets_client import write_data_to_sheets

# Configuración del logger
logger = logging.getLogger(__name__)

def process_agent_event(event_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Procesa un evento del agente (Webhook de ElevenLabs).
    Detecta la intención y ejecuta el flujo de trabajo correspondiente.
    """
    transcript: List[Dict[str, str]] = event_data.get('transcript', [])
    agent_id: str = event_data.get('agent_id', 'unknown')

    # 1. Detección de la Intención (Frase Clave)
    # Buscamos la frase clave de confirmación en el último mensaje del usuario
    confirmation_message = ""
    for item in reversed(transcript):
        if item.get('role') == 'user' and "AGENDAR_CITA_CONFIRMADA" in item.get('message', ''):
            confirmation_message = item['message']
            break

    if confirmation_message:
        logger.info("DETECCIÓN: Intención de agendamiento detectada en la transcripción.")
        
        # --- NUEVA LÓGICA DE EXTRACCIÓN DE DATOS ---
        logger.info("INICIANDO WORKFLOW DE AGENDAMIENTO...")

        # A. EXTRAER DATOS REALES DE LA TRANSCRIPCIÓN usando Gemini
        logger.info("1. EXTRACCIÓN DE DATOS: Llamando al LLM para obtener entidades...")
        customer_data = extract_customer_data(transcript)
        
        if not customer_data or any(customer_data.get(key) in (None, '') for key in ["cliente_nombre_completo", "fecha_cita_iso", "hora_cita_24h"]):
            logger.error("❌ AGENDAMIENTO FALLIDO: El LLM no pudo extraer los datos esenciales (Nombre, Fecha, Hora).")
            return {"status": "error", "message": "Fallo en la extracción de datos esenciales del cliente."}

        # B. PREPARAR VARIABLES A PARTIR DE LOS DATOS EXTRAÍDOS
        cliente_nombre_completo = customer_data.get('cliente_nombre_completo', 'N/A')
        cliente_telefono = customer_data.get('cliente_telefono', 'N/A')
        cliente_email = customer_data.get('cliente_email', 'N/A')
        cliente_direccion = customer_data.get('cliente_direccion', 'N/A')
        fecha_str = customer_data.get('fecha_cita_iso')
        hora_str = customer_data.get('hora_cita_24h')

        # C. VERIFICACIÓN DE DISPONIBILIDAD
        logger.info(f"2. VERIFICACIÓN: Verificando disponibilidad para {fecha_str} a las {hora_str}...")
        is_available = check_availability(fecha_str, hora_str)

        if not is_available:
            logger.warning("❌ AGENDAMIENTO FALLIDO: Horario no disponible.")
            return {"status": "error", "agendamiento": {"status": "failed", "reason": "Horario no disponible"}}

        # D. AGENDAMIENTO Y GUARDADO DE DATOS (si está disponible)
        logger.info("3. AGENDAMIENTO & GUARDADO: Horario disponible. Procediendo a agendar.")

        # Construir el objeto de datos para el Apps Script
        data_to_save = {
            "Cliente": cliente_nombre_completo,
            "Telefono": cliente_telefono,
            "Email": cliente_email,
            "Direccion": cliente_direccion,
            "FechaCita": fecha_str,
            "HoraCita": hora_str,
            "AgenteID": agent_id,
            "TranscriptJSON": json.dumps(transcript) # Guardar la transcripción completa para referencia
        }
        
        # Llamar al servicio de Sheets (que también agenda la cita a través del Apps Script Webhook)
        sheets_result = write_data_to_sheets(data_to_save)

        if sheets_result.get('status') == 'success':
            logger.info("ÉXITO: Cita agendada y datos guardados correctamente por Apps Script.")
            
            # Opcional: Enviar email de confirmación al cliente
            # if cliente_email != 'N/A' and cliente_email != '':
            #     send_email(cliente_email, "Cita confirmada", f"Hola {cliente_nombre_completo}, tu cita ha sido agendada para el {fecha_str} a las {hora_str}.")

            return {"status": "ok", "result": {"agendamiento": {"status": "success", "data": data_to_save, "apps_script_response": sheets_result}}}
        else:
            logger.error(f"❌ AGENDAMIENTO FALLIDO: Apps Script falló. Respuesta: {sheets_result}")
            return {"status": "error", "agendamiento": {"status": "failed", "reason": "Fallo al guardar/agendar en Apps Script"}}


    logger.info("DETECCIÓN: Ninguna intención de flujo de trabajo detectada.")
    return {"status": "ok", "message": "No se detectó ninguna intención de flujo de trabajo para procesar."}
