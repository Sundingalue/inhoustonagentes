import os
import requests

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

# --- Constante de URL Base ---
ELEVEN_API_BASE = "https://api.elevenlabs.io/v1"


# ===================================================================
# === CÓDIGO EXISTENTE (No se ha modificado) =========================
# ===================================================================

def start_conversation_with_agent(agent_id, input_text=None):
    """
    Inicia una conversación con un agente de ElevenLabs usando su agent_id.
    Opcionalmente puede enviar un mensaje inicial (input_text).
    """

    # (Tu código original usa una URL diferente, la respetamos)
    url = f"https://api.elevenlabs.io/v1/agents/{agent_id}/conversation"

    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }

    payload = {}
    if input_text:
        payload["message"] = input_text

    response = requests.post(url, headers=headers, json=payload)

    if response.status_code == 200:
        print("[ElevenLabs] Conversación iniciada exitosamente.")
        return response.json()
    else:
        print(f"[ElevenLabs] Error {response.status_code}: {response.text}")
        return {"error": f"Error al iniciar conversación con el agente ElevenLabs {agent_id}"}


# ===================================================================
# === NUEVAS FUNCIONES (Añadidas para Panel Agentes) ================
# ===================================================================

def _eleven_request(method, endpoint, payload=None, params=None):
    """
    Helper genérico para peticiones a la API v1 de ElevenLabs.
    """
    if not ELEVENLABS_API_KEY:
        print("[ElevenLabs] Error: ELEVENLABS_API_KEY no configurada en el servidor")
        return {"ok": False, "error": "ELEVENLABS_API_KEY no configurada en el servidor"}

    url = f"{ELEVEN_API_BASE}{endpoint}"
    headers = {
        "Accept": "application/json",
        "xi-api-key": ELEVENLABS_API_KEY
    }
    
    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, params=params)
        elif method.upper() == "POST":
            headers["Content-Type"] = "application/json"
            response = requests.post(url, headers=headers, json=payload)
        else:
            return {"ok": False, "error": f"Método HTTP no soportado: {method}"}

        # Lanza un error si la petición falla (ej. 401, 404, 500)
        response.raise_for_status() 
        return {"ok": True, "data": response.json()}
    
    except requests.exceptions.HTTPError as http_err:
        try:
            # Intentar decodificar el error específico de ElevenLabs
            error_details = http_err.response.json()
            error_msg = error_details.get('detail', {}).get('message', http_err.response.text)
            print(f"[ElevenLabs] Error de API (HTTP {http_err.response.status_code}): {error_msg}")
            return {"ok": False, "error": f"Error de API: {error_msg}"}
        except:
            print(f"[ElevenLabs] Error de API (HTTP {http_err.response.status_code}): {http_err}")
            return {"ok": False, "error": f"Error HTTP: {http_err}"}
    except requests.exceptions.RequestException as req_err:
        print(f"[ElevenLabs] Error de Conexión: {req_err}")
        return {"ok": False, "error": f"Error de conexión: {req_err}"}

# --- 1. Funciones para Sincronización de Admin (WordPress) ---

def get_eleven_agents():
    """
    Obtiene la lista de todos los agentes de la cuenta.
    (API: GET /v1/convai/agents)
    """
    print("[ElevenLabs] Obteniendo lista de agentes...")
    return _eleven_request("GET", "/convai/agents")

def get_eleven_phone_numbers():
    """
    Obtiene la lista de todos los números de teléfono de la cuenta.
    (API: GET /v1/convai/phone-numbers)
    """
    print("[ElevenLabs] Obteniendo lista de números de teléfono...")
    return _eleven_request("GET", "/convai/phone-numbers")


# --- 2. Funciones para el Panel de Agente (Cliente) ---

def get_agent_consumption_data(agent_id, start_unix, end_unix):
    """
    Obtiene los datos de consumo (llamadas, créditos, etc.) para UN agente 
    específico en un rango de fechas.
    (API: GET /v1/convai/analytics)
    """
    print(f"[ElevenLabs] Obteniendo consumo para Agente ID: {agent_id}")
    
    endpoint = "/convai/analytics"
    params = {
        "start_unix": int(start_unix),
        "end_unix": int(end_unix)
    }
    result = _eleven_request("GET", endpoint, params=params)
    
    if not result["ok"]:
        return result # Devuelve el error

    # La API devuelve datos de *todos* los agentes. 
    # Debemos filtrar solo el que nos interesa.
    all_agents_data = []
    if "by_agent" in result["data"]:
        all_agents_data = result["data"].get("by_agent", [])
    elif "agents" in result["data"]:
         all_agents_data = result["data"].get("agents", [])

    for agent_data in all_agents_data:
        if agent_data.get("agent_id") == agent_id:
            # ¡Encontrado! Devuelve solo los datos de este agente
            print(f"[ElevenLabs] Consumo encontrado para {agent_id}")
            
            # Normalizamos los datos que devuelve la API
            d = agent_data
            secs = d.get("seconds", d.get("duration_secs", 0))
            creds = d.get("credits", d.get("credits_used", d.get("credit_cost", 0)))
            calls = d.get("calls", d.get("call_count", 0))
            
            normalized_data = {
                "agent_id": agent_id,
                "name": d.get("agent_name", d.get("name", "N/A")),
                "calls": calls,
                "duration_secs": secs,
                "credits": creds
            }
            return {"ok": True, "data": normalized_data}
            
    # Si el bucle termina, no se encontró el agente en el reporte
    print(f"[ElevenLabs] Agente {agent_id} no encontrado en el reporte de consumo.")
    return {"ok": False, "error": "Agente no encontrado en el reporte de consumo para ese rango."}


def start_batch_call(call_name, agent_id, phone_number_id, recipients_json):
    """
    Inicia una nueva llamada por lotes.
    'recipients_json' debe ser una lista de dicts, ej: [{"phone_number": "+1..."}, ...]
    (API: POST /v1/convai/batch-calling/submit)
    """
    print(f"[ElevenLabs] Iniciando lote de llamadas: {call_name} (Agente: {agent_id})")
    
    endpoint = "/convai/batch-calling/submit"
    payload = {
        "call_name": call_name,
        "agent_id": agent_id,
        "agent_phone_number_id": phone_number_id,
        "recipients": recipients_json
        # "scheduled_time_unix": Opcional si quieres programarlo
    }
    return _eleven_request("POST", endpoint, payload=payload)