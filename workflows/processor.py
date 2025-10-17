import json
import os
from typing import Dict, Any, Optional

# Servicios de Agendamiento
from services.calendar_checker import check_availability
from services.calendar_service import book_appointment # Usa el webhook de Apps Script
# Servicios de Email (Actuales)
from services.email_service import send_email
from services.send_client_email import send_email_to_client

# Otros servicios (mantener inactivos por ahora)
# from services.sheets_service import save_conversation
# from services.location_service import send_location
# ...

# === Funciones de Soporte ===

def _read_agent_config(agent_name: str) -> Dict[str, Any]:
    """
    Lee agents/<agent_name>.json y retorna el dict o {} si no existe.
    """
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
    """
    Obtiene el texto de la conversación desde el evento normalizado.
    """
    txt = (event.get("transcript_text") or "").strip()
    if txt:
        return txt

    # Fallback/Rescate desde el raw
    raw = event.get("raw") or {}
    root = raw.get("data", raw) if isinstance(raw, dict) else {}
    tr = root.get("transcript") or root.get("transcription") or []
    if isinstance(tr, list):
        try:
            return " ".join(
                (t.get("message", "") or "").strip()
                for t in tr
                if isinstance(t, dict) and t.get("role") == "user"
            ).strip()
        except Exception:
            pass
    elif isinstance(tr, str):
        return tr.strip()

    return ""


def _simulate_data_extraction(transcript: str, event: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    SIMULACIÓN: En un entorno real, aquí usarías un LLM (como Gemini) 
    para extraer Nombre, Fecha y Hora del transcript.
    
    Para la prueba final, ASUMIMOS que la cita fue confirmada y los datos
    de la prueba están disponibles.
    
    Nota: La lógica real de ElevenLabs puede incluir una clave 'custom_data'
    con los datos estructurados, si se configuró un LLM para formatearlos.
    """
    # Usamos una CLAVE SECRETA en la transcripción para activar la simulación.
    # En producción, esto sería una detección de intención del LLM.
    if "AGENDAR_CITA_CONFIRMADA" in transcript:
        print("✅ DETECCIÓN: Intención de agendamiento detectada en la transcripción.")
        
        # Simulamos la extracción de los datos usados en la prueba curl anterior
        return {
            "cliente_nombre": "Sundin Galué",
            "fecha": "2025-10-30", # Usamos la fecha original para que se vea en el futuro
            "hora": "14:30",
            "apellido": "N/A", # Placeholder requerido por book_appointment
            "telefono": "N/A", # Placeholder requerido por book_appointment
            "email": "test-agendamiento@webhook.com" # Placeholder requerido por book_appointment
        }
    return None

# === Función Principal ===

def process_agent_event(agent_name: str, event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Procesa el evento del webhook de ElevenLabs y ejecuta el workflow.
    """
    results: Dict[str, Any] = {}
    transcript_text = _extract_transcript_text(event)

    try:
        # 1. Cargar configuración
        config = _read_agent_config(agent_name)
        if not config:
            return {"error": f"agent '{agent_name}' not found or invalid config"}

        # 2. Intentar Detección de Agendamiento
        cita_data = _simulate_data_extraction(transcript_text, event)
        
        if cita_data:
            print("🚀 INICIANDO WORKFLOW DE AGENDAMIENTO...")
            
            # --- Lógica de Agendamiento ---
            
            is_available = check_availability(cita_data['fecha'], cita_data['hora'])

            if not is_available:
                results["agendamiento"] = {
                    "status": "failure",
                    "message": f"Horario no disponible: {cita_data['fecha']} a las {cita_data['hora']}."
                }
                print(f"❌ AGENDAMIENTO FALLIDO: Horario no disponible.")
            else:
                # Si está disponible, crear evento y guardar datos vía Apps Script Webhook
                book_result = book_appointment(
                    nombre=cita_data['cliente_nombre'], 
                    apellido=cita_data['apellido'],
                    telefono=cita_data['telefono'],
                    email=cita_data['email'], 
                    fechaCita=cita_data['fecha'], 
                    horaCita=cita_data['hora']
                )
                results["agendamiento"] = book_result
                if book_result.get('status') == 'success':
                    print(f"🎉 ÉXITO: Cita agendada y datos guardados por Apps Script.")
                else:
                    print(f"⚠️ ERROR DE APPS SCRIPT: {book_result.get('message')}")

            # Finalizar workflow si hubo intento de agendamiento
            return results

        # 3. Si NO hay agendamiento, ejecutar el flujo de Email por defecto
        # ------------------------------------------------------------------
        print("➡️ Ejecutando flujo de EMAIL por defecto...")
        
        workflow = config.get("workflow") or ["email"]
        
        # Enviar correo al cliente con su ubicación (si aplica)
        send_email_to_client(transcript_text, agent_name)

        # Ejecutar pasos del workflow (solo 'email' por ahora)
        for step in workflow:
            step_norm = str(step or "").strip().lower()

            if step_norm in ("email", "enviar_email"):
                email_cfg = config.get("email") or {}
                # ... (Tu lógica original de envío de email aquí) ...
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
        return {"error": str(e)}