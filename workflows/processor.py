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
# from services.calendar_service import send_agenda_link
# from services.location_service import send_location
# from services.analysis_service import log_agent_activity
# from services.invoice_service import generate_invoice
# from services.elevenlabs_service import start_conversation_with_agent


# === Funciones de Soporte ===

def _read_agent_config(agent_name: str) -> Dict[str, Any]:
    """
    Lee agents/<agent_name>.json y retorna el dict o {} si no existe.
    Usa el nombre legible (ej. 'sundin'), no el ID largo de ElevenLabs.
    """
    # base_dir está en /workflows/
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Subir a la raíz (..) y buscar 'agents' (llegará a /src/agents)
    agents_dir = os.path.join(base_dir, "..", "agents")
    
    # Usa el nombre del agente para construir la ruta al archivo JSON
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
    VERSION CORREGIDA: Obtiene el texto de **toda** la conversación desde el evento normalizado.
    Esto asegura la captura de la palabra clave de agendamiento.
    """
    # 1. Intentar obtener el texto pre-normalizado
    txt = (event.get("transcript_text") or "").strip()
    if txt:
        return txt

    # 2. Rescatar desde el raw (lista de turnos)
    raw = event.get("raw") or {}
    root = raw.get("data", raw) if isinstance(raw, dict) else {}
    tr = root.get("transcript") or root.get("transcription") or []
    
    if isinstance(tr, list):
        try:
            # ✅ CORRECCIÓN CLAVE: Capturar TODOS los mensajes (user y agent)
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


def _simulate_data_extraction(transcript: str, event: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    SIMULACIÓN: Aquí se detecta la frase secreta para activar el agendamiento.
    """
    if "AGENDAR_CITA_CONFIRMADA" in transcript:
        print("✅ DETECCIÓN: Intención de agendamiento detectada en la transcripción.")
        
        # Simulamos la extracción de los datos usados en la prueba
        return {
            "cliente_nombre": "Sundin Galué",
            "fecha": "2025-10-30", 
            "hora": "14:30",
            "apellido": "N/A",
            "telefono": "N/A",
            "email": "test-agendamiento@webhook.com"
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
            
            # 2a. Verificar Disponibilidad
            is_available = check_availability(cita_data['fecha'], cita_data['hora'])

            if not is_available:
                results["agendamiento"] = {
                    "status": "failure",
                    "message": f"Horario no disponible: {cita_data['fecha']} a las {cita_data['hora']}."
                }
                print(f"❌ AGENDAMIENTO FALLIDO: Horario no disponible.")
            else:
                # 2b. Agendar Cita y Guardar Datos
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
        
        # Enviar correo al cliente
        send_email_to_client(transcript_text, agent_name)

        # Ejecutar pasos del workflow (solo 'email' por ahora)
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
        return {"error": str(e)}