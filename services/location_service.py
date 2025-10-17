# services/location_service.py
from typing import Dict, Any
# Importamos la función de envío de correo simple desde el servicio de correo
from .email_service import send_address_email_wrapper 

def handle_address_request(event_data: Dict[str, Any], agent_cfg: Dict[str, Any]) -> dict:
    """
    Gestiona el evento de solicitud de dirección: extrae la información 
    y llama al servicio de correo para enviar la dirección.
    
    Asume que el agente de IA insertó 'address_to_send' y opcionalmente 'email_to_send_to'
    en el payload del evento.
    """
    
    print("="*40)
    print("[LOCATION SERVICE] Procesando solicitud de envío de dirección...")

    # 1. Extracción de Datos
    address = event_data.get("address_to_send")
    client_email = event_data.get("email_to_send_to")
    caller_number = event_data.get("caller") or "Desconocido"
    agent_id = event_data.get("agent_id") or "Agente"
    agent_name = agent_id.capitalize()

    if not address:
        print("❌ LOCATION_SERVICE: Evento sin 'address_to_send'.")
        print("="*40)
        return {"status": "error", "message": "Falta la dirección en el evento."}

    # 2. Configuración de Correo
    # La configuración de correo puede estar en la llave 'email' o 'email_service' del JSON del agente
    email_cfg = agent_cfg.get("email") or agent_cfg.get("email_service") or {}
    
    # PRIORIDAD: Usar el correo proporcionado por el agente de IA
    if client_email:
        email_cfg['to'] = client_email
        print(f"📧 Usando correo proporcionado por el agente: {client_email}")
    
    if not email_cfg.get("to"):
        print("❌ LOCATION_SERVICE: No se pudo determinar la dirección de correo 'to'.")
        print("="*40)
        return {"status": "error", "message": "Falta la dirección de correo 'to'."}

    # 3. Llamar al servicio de Correo
    print(f"📧 LOCATION_SERVICE: Enviando dirección '{address}' a {email_cfg['to']}...")
    try:
        result = send_address_email_wrapper(
            email_cfg=email_cfg,
            agent_name=agent_name,
            caller_number=caller_number,
            address=address
        )
        print("✅ LOCATION_SERVICE: Correo de dirección enviado.")
        print("="*40)
        return result
    except Exception as e:
        print(f"❌ LOCATION_SERVICE: Error al enviar el correo de dirección: {e}")
        print("="*40)
        return {"status": "error", "message": f"Fallo en el envío del correo: {e}"}