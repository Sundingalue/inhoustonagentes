import json
import os
from typing import Dict, Any

# Solo usamos email en este paso. Los demás servicios quedan listos para el futuro, pero sin ejecutar.
from services.email_service import send_email
from services.send_client_email import send_email_to_client
# from services.sheets_service import save_conversation
# from services.calendar_service import send_agenda_link
# from services.location_service import send_location
# from services.analysis_service import log_agent_activity
# from services.invoice_service import generate_invoice
# from services.elevenlabs_service import start_conversation_with_agent


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
    Obtiene el texto de la conversación desde el evento normalizado.
    Prioriza event['transcript_text'] y tiene fallbacks por compatibilidad.
    """
    # Normalizado por main.py
    txt = (event.get("transcript_text") or "").strip()
    if txt:
        return txt

    # Fallbacks por si llega con otras claves
    txt = (event.get("transcription") or "").strip()
    if txt:
        return txt

    # Intento de rescate desde el raw (si llegó lista de turnos)
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


# ✅ CORRECCIÓN CLAVE: La firma ahora espera el nombre legible del agente (ej: "sundin")
def process_agent_event(agent_name: str, event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Procesa el evento del webhook de ElevenLabs con foco en ENVIAR EMAIL.
    Espera:
      - agent_name (nombre legible del archivo, ej: "sundin")
      - event (payload normalizado)
    """
    results: Dict[str, Any] = {}

    try:
        if not agent_name:
            return {"error": "agent_name missing"}

        # Cargar configuración del agente (usa el nombre legible)
        config = _read_agent_config(agent_name)
        if not config:
            return {"error": f"agent '{agent_name}' not found or invalid config"}

        # Determinar workflow del agente (por defecto, solo email)
        workflow = config.get("workflow") or ["email"]
        if not isinstance(workflow, list):
            workflow = ["email"]

        # Texto de la conversación a enviar
        transcript_text = _extract_transcript_text(event)
        if not transcript_text:
            print("⚠️ No se obtuvo transcript_text. Se enviará cuerpo vacío (o mensaje por defecto).")

            # 💡 NUEVO: Enviar correo al cliente con su ubicación
        send_email_to_client(transcript_text, agent_name)


        # Ejecutar SOLO el paso de email (dejamos los demás como skipped)
        for step in workflow:
            step_norm = str(step or "").strip().lower()

            # === ENVIAR EMAIL === (admite 'email' o 'enviar_email')
            if step_norm in ("email", "enviar_email"):
                email_cfg = config.get("email") or {}
                if not isinstance(email_cfg, dict) or not email_cfg:
                    results["email"] = {
                        "status": "error",
                        "message": "Config 'email' ausente o inválida en el JSON del agente"
                    }
                    print("❌ Falta sección 'email' en la configuración del agente.")
                    # seguimos al siguiente step (si lo hubiera)
                    continue

                # Cuerpo por defecto si no hay transcripción
                body = transcript_text or "No se recibió transcripción de la llamada."
                try:
                    print("📧 Enviando correo (Zoho SMTP) con la conversación...")
                    # La firma de send_email debe aceptar (email_cfg, body).
                    # Si tu email_service requiere subject/attachments, puedes agregarlos a email_cfg en el JSON.
                    result_email = send_email(email_cfg, agent_name, event)
                    results["email"] = result_email if isinstance(result_email, dict) else {"status": "ok", "detail": str(result_email)}
                except Exception as e:
                    print(f"❌ Error enviando correo: {e}")
                    results["email"] = {"status": "error", "message": str(e)}

            else:
                # Por ahora no ejecutamos otros pasos. Los dejamos registrados como skipped.
                results[step_norm or "unknown"] = {"status": "skipped"}

        print("✅ Flujo de EMAIL ejecutado.")
        return results

    except Exception as e:
        print(f"🚨 Error general en process_agent_event: {e}")
        return {"error": str(e)}