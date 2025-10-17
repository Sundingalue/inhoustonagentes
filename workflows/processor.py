import json
import logging
import os
from typing import Dict, Any, List, Optional
import requests
import traceback

# Importar servicios
from services.analysis_service import extract_customer_data 
from services.calendar_checker import check_availability
from services.email_service import send_email
from services.send_client_email import send_email_to_client
from services.calendar_service import book_appointment 

# Configuración del logger
logger = logging.getLogger(__name__)

# --- Funciones Auxiliares ---

def _read_agent_config(agent_name: str) -> Dict[str, Any]:
    """Lee agents/<agent_name>.json y retorna el dict o {} si no existe."""
    # ... (Tu código para leer la configuración del agente)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    agents_dir = os.path.join(base_dir, "..", "agents")
    json_path = os.path.normpath(os.path.join(agents_dir, f"{agent_name}.json"))
    if not os.path.exists(json_path):
        print(f"❌ No se encontró la configuración del agente: {json_path}")
        return {}
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception as e:
        print(f"❌ Error leyendo JSON del agente {agent_name}: {e}")
        return {}

def _extract_transcript_text(event: Dict[str, Any]) -> str:
    """Obtiene el texto de **toda** la conversación desde el evento normalizado."""
    # ... (Tu código para extraer el transcript)
    txt = (event.get("transcript_text") or "").strip()
    if txt: return txt
    raw = event.get("raw") or {}
    root = raw.get("data", raw) if isinstance(raw, dict) else {}
    tr = root.get("transcript") or root.get("transcription") or []
    if isinstance(tr, list):
        try:
            return " ".join(
                (t.get("message", "") or "").strip()
                for t in tr
                if isinstance(t, dict) and t.get("message")
            ).strip()
        except Exception:
            pass
    elif isinstance(tr, str):
        return tr.strip()
    return ""

def _map_extracted_data(extracted: Dict[str, Any]) -> Dict[str, str]:
    """Mapea los datos de Gemini a los campos esperados por Apps Script."""
    # ... (Tu código para mapear los datos)
    full_name = extracted.get('cliente_nombre_completo', 'N/A').split(' ', 1)
    return {
        "nombre": full_name[0] if full_name else 'N/A',
        "apellido": full_name[1] if len(full_name) > 1 else 'N/A',
        "telefono": extracted.get('cliente_telefono', 'N/A'),
        "email": extracted.get('cliente_email', 'N/A'),
        "fechaCita": extracted.get('fecha_cita_iso', ''),
        "horaCita": extracted.get('hora_cita_24h', ''),
        "direccion": extracted.get('cliente_direccion', 'N/A'),
    }


def process_agent_event(agent_name: str, event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Procesa el evento del webhook de ElevenLabs y ejecuta el workflow de agendamiento o email.
    """
    results: Dict[str, Any] = {}
    
    transcript_text = _extract_transcript_text(event)
    
    # Obtenemos la lista cruda de turnos (necesaria para el LLM)
    raw_transcript_list = event.get('raw', {}).get('data', {}).get('transcript', [])
    if not raw_transcript_list:
        raw_transcript_list = [{"role": "user", "message": transcript_text}]


    try:
        # 1. Cargar configuración del agente
        config = _read_agent_config(agent_name)
        if not config:
            return {"error": f"agent '{agent_name}' not found or invalid config"}

        # 2. Detección de Agendamiento
        transcript_lower = transcript_text.lower()
        
        # ✅ CORRECCIÓN CLAVE: Detección Robusta (busca la palabra 'agendar' Y 'cita' en el transcript)
        is_agendar_cita = "agendar" in transcript_lower and "cita" in transcript_lower
        
        if is_agendar_cita:
            print("🚀 INICIANDO WORKFLOW DE AGENDAMIENTO...")
            
            # --- Lógica de Extracción y Agendamiento ---
            
            # A. EXTRAER DATOS REALES DE LA TRANSCRIPCIÓN usando Gemini
            print("1. EXTRACCIÓN DE DATOS: Llamando al LLM para obtener entidades...")
            customer_data_raw = extract_customer_data(raw_transcript_list)
            
            if not customer_data_raw:
                results["agendamiento"] = {"status": "failure", "message": "Fallo en la extracción de datos de Gemini."}
                print(f"❌ AGENDAMIENTO FALLIDO: LLM no devolvió datos estructurados.")
                return results

            cita_data = _map_extracted_data(customer_data_raw)
            fecha_str = cita_data['fechaCita']
            hora_str = cita_data['horaCita']
            
            if not fecha_str or not hora_str:
                results["agendamiento"] = {"status": "failure", "message": "Datos de cita incompletos (fecha/hora no encontradas)."}
                print(f"❌ AGENDAMIENTO FALLIDO: Fecha u hora ausente en la extracción.")
                return results


            # B. VERIFICACIÓN DE DISPONIBILIDAD
            print(f"2. VERIFICACIÓN: Verificando disponibilidad para {fecha_str} a las {hora_str}...")
            is_available = check_availability(fecha_str, hora_str)

            if not is_available:
                results["agendamiento"] = {"status": "failure", "message": f"Horario no disponible: {fecha_str} a las {hora_str}."}
                print(f"❌ AGENDAMIENTO FALLIDO: Horario no disponible.")
                return results

            # C. AGENDAMIENTO Y GUARDADO DE DATOS
            print("3. AGENDAMIENTO: Horario disponible. Llamando a Apps Script...")
            
            book_result = book_appointment(
                nombre=cita_data['nombre'], 
                apellido=cita_data['apellido'],
                telefono=cita_data['telefono'],
                email=cita_data['email'], 
                fechaCita=fecha_str, 
                horaCita=hora_str
            )
            
            results["agendamiento"] = book_result
            if book_result.get('status') == 'success':
                print(f"🎉 ÉXITO: Cita agendada y datos guardados por Apps Script.")
            else:
                print(f"⚠️ ERROR DE APPS SCRIPT: {book_result.get('message')}")

            return results

        # 4. Si NO hay agendamiento, ejecutar el flujo de Email por defecto
        # ------------------------------------------------------------------
        print("➡️ Ejecutando flujo de EMAIL por defecto...")
        
        send_email_to_client(transcript_text, agent_name)

        workflow = config.get("workflow") or ["email"]
        
        for step in workflow:
            step_norm = str(step or "").strip().lower()

            if step_norm in ("email", "enviar_email"):
                email_cfg = config.get("email") or {}
                body = transcript_text or "No se recibió transcripción de la llamada."
                
                try:
                    print("📧 Enviando correo (Zoho SMTP) con la conversación...")
                    result_email = send_email(email_cfg, agent_name, event)
                    results["email"] = result_email if isinstance(result_email, dict) else {"status": "ok", "detail": str(result_email)}
                except Exception as e:
                    results["email"] = {"status": "error", "message": str(e)}

            else:
                results[step_norm or "unknown"] = {"status": "skipped"}

        return results

    except Exception as e:
        print(f"🚨 Error general en process_agent_event: {e}")
        traceback.print_exc()
        return {"error": str(e)}
