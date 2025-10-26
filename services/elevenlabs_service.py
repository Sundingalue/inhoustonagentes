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

# ===================================================================
# === FUNCIÓN CORREGIDA =============================================
# ===================================================================
def get_agent_consumption_data(agent_id, start_unix, end_unix):
    """
    Obtiene los datos de consumo (llamadas, créditos, etc.) para UN agente 
    específico en un rango de fechas.
    
    ¡CORREGIDO! Usa el endpoint /conversations y suma los totales.
    (API: GET /v1/convai/conversations)
    """
    print(f"[ElevenLabs] Obteniendo conversaciones para Agente ID: {agent_id}...")
    
    endpoint = "/convai/conversations"
    params = {
        "agent_id": agent_id,
        "start_unix": int(start_unix),
        "end_unix": int(end_unix)
        # Nota: La API parece tener un límite por defecto, 
        # pero asumiremos que no hay más de 1000 llamadas en el rango
    }
    
    result = _eleven_request("GET", endpoint, params=params)
    
    if not result["ok"]:
        return result # Devuelve el error (ej. 401, 500)

    # La API devuelve un objeto con una clave "conversations" que es una lista
    conversations = result.get("data", {}).get("conversations", [])
    
    if not conversations:
        print(f"[ElevenLabs] No se encontraron conversaciones para {agent_id} en ese rango.")
        # No es un error, solo no hay datos. Devolvemos 0.
        return {"ok": True, "data": {
            "agent_id": agent_id,
            "calls": 0,
            "duration_secs": 0,
            "credits": 0
        }}

    # Ahora, sumamos el consumo de todas las conversaciones encontradas
    total_calls = 0
    total_credits = 0
    total_seconds = 0
    
    for convo in conversations:
        if isinstance(convo, dict):
            total_calls += 1
            # Sumamos los campos de consumo. Usamos .get(campo, 0) por seguridad.
            total_credits += convo.get("credit_usage", 0) 
            total_seconds += convo.get("duration_secs", 0)

    print(f"[ElevenLabs] Consumo total calculado: {total_calls} llamadas, {total_credits} créditos.")
    
    # Devolvemos el mismo formato que la función original esperaba
    normalized_data = {
        "agent_id": agent_id,
        "calls": total_calls,
        "duration_secs": total_seconds,
        "credits": total_credits
    }
    return {"ok": True, "data": normalized_data}
# ===================================================================
# === FIN DE LA FUNCIÓN CORREGIDA ===================================
# ===================================================================


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