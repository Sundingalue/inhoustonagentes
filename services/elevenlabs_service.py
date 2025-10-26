import os
import requests
import json
import time

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVEN_API_BASE = "https://api.elevenlabs.io/v1"

# --- Tasa Fallback de Créditos por Segundo ---
DEFAULT_CREDITS_PER_SEC_FALLBACK = 10.73
try:
    FALLBACK_CREDITS_PER_SEC = float(os.getenv("ELEVENLABS_CREDITS_PER_SEC_FALLBACK", DEFAULT_CREDITS_PER_SEC_FALLBACK))
except ValueError:
    FALLBACK_CREDITS_PER_SEC = DEFAULT_CREDITS_PER_SEC_FALLBACK
print(f"[ElevenLabs] Usando tasa fallback de créditos/seg: {FALLBACK_CREDITS_PER_SEC}")

# --- Funciones existentes (sin cambios) ---
def start_conversation_with_agent(agent_id, input_text=None):
    url = f"https://api.elevenlabs.io/v1/agents/{agent_id}/conversation"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {"message": input_text} if input_text else {}
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        print("[ElevenLabs] Conversación iniciada.")
        return response.json()
    else:
        print(f"[ElevenLabs] Error {response.status_code}: {response.text}")
        return {"error": f"Error al iniciar conversación"}

def _eleven_request(method, endpoint, payload=None, params=None):
    if not ELEVENLABS_API_KEY:
        print("[ElevenLabs] Error: API Key no configurada.")
        return {"ok": False, "error": "API Key no configurada"}
    url = f"{ELEVEN_API_BASE}{endpoint}"
    headers = {"Accept": "application/json", "xi-api-key": ELEVENLABS_API_KEY}
    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, params=params)
        elif method.upper() == "POST":
            headers["Content-Type"] = "application/json"
            response = requests.post(url, headers=headers, json=payload)
        else:
            return {"ok": False, "error": f"Método no soportado: {method}"}
        response.raise_for_status()
        return {"ok": True, "data": response.json()}
    except requests.exceptions.HTTPError as http_err:
        try:
            error_details = http_err.response.json()
            error_msg = error_details.get('detail', {}).get('message', http_err.response.text)
        except:
             error_msg = http_err.response.text
        print(f"[ElevenLabs] Error API (HTTP {http_err.response.status_code}): {error_msg}")
        return {"ok": False, "error": f"Error API: {error_msg}"}
    except requests.exceptions.RequestException as req_err:
        print(f"[ElevenLabs] Error Conexión: {req_err}")
        return {"ok": False, "error": f"Error conexión: {req_err}"}

def get_eleven_agents():
    print("[ElevenLabs] Obteniendo agentes...")
    return _eleven_request("GET", "/convai/agents")

def get_eleven_phone_numbers():
    print("[ElevenLabs] Obteniendo números...")
    return _eleven_request("GET", "/convai/phone-numbers")

# ===================================================================
# === FUNCIÓN FINAL (CON FILTRADO LOCAL CORREGIDO) ================
# ===================================================================
def get_agent_consumption_data(agent_id, start_unix_ts, end_unix_ts):
    """
    Obtiene consumo usando /conversations con paginación (cursor),
    SOLO filtra por fecha FIN en la API, y luego filtra por fecha INICIO localmente (CORREGIDO).
    Calcula créditos usando fallback.
    """
    print(f"[ElevenLabs] Obteniendo conversaciones ANTES de {end_unix_ts} para Agente ID: {agent_id}...")
    
    endpoint = "/convai/conversations"
    all_conversations = [] 
    
    has_more = True
    next_cursor = None
    page_num = 1 
    max_pages = 50 

    while has_more and page_num <= max_pages:
        params = { "agent_id": agent_id, "page_size": 30 }
        if not next_cursor:
            params["call_start_before_unix"] = int(end_unix_ts) 
        else:
            params["cursor"] = next_cursor 
        
        result = _eleven_request("GET", endpoint, params=params)
        
        if not result["ok"]:
            print(f"[ElevenLabs] Error al obtener la página {page_num}: {result.get('error')}")
            break 

        data = result.get("data", {})
        conversations_page = data.get("conversations", [])
        if not conversations_page: break 
        all_conversations.extend(conversations_page) 
        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor", None) 
        if not has_more or not next_cursor: break 
        page_num += 1

    if page_num > max_pages: print(f"[ElevenLabs] ADVERTENCIA: Límite de {max_pages} páginas.")
    print(f"[ElevenLabs] Total conversaciones recibidas ANTES de filtrar: {len(all_conversations)}")

    # --- FILTRADO LOCAL POR FECHA DE INICIO (CORREGIDO) ---
    filtered_conversations = []
    start_filter_ts = int(start_unix_ts) 
    
    for convo in all_conversations:
         if isinstance(convo, dict):
             # *** CORRECCIÓN: Leer el campo correcto y convertir a int ***
             convo_start_value = convo.get("start_time_unix_secs") # Leer el campo correcto

             # Intentar convertir a entero, si falla o no existe, saltar esta llamada
             try:
                 convo_start_num = int(convo_start_value)
             except (ValueError, TypeError, TypeError): # Atrapar si es None o no es número
                 print(f"DEBUG WARNING: Timestamp inválido o ausente ('{convo_start_value}') para convo {convo.get('conversation_id')}. Saltando.")
                 continue # Ir a la siguiente conversación

             # *** CORRECCIÓN: Comparar números enteros ***
             if convo_start_num >= start_filter_ts:
                 filtered_conversations.append(convo)

    print(f"[ElevenLabs] Total conversaciones DESPUÉS de filtrar por fecha inicio ({start_filter_ts}): {len(filtered_conversations)}")

    # --- Calcular totales SOBRE LA LISTA FILTRADA ---
    total_calls = 0
    total_credits = 0.0
    total_seconds = 0.0

    for convo in filtered_conversations:
        call_status = convo.get('call_successful', 'success') 
        if call_status != 'success': continue

        total_calls += 1
        secs = float(convo.get("call_duration_secs", convo.get("duration_secs", 0.0)))
        total_seconds += secs
        if secs > 0:
            calculated_credits = secs * FALLBACK_CREDITS_PER_SEC
            total_credits += calculated_credits

    print(f"[ElevenLabs] Consumo total final calculado (filtrado): {total_calls} llamadas, {total_credits:.4f} créditos.")
    
    normalized_data = {
        "agent_id": agent_id,
        "calls": total_calls,
        "duration_secs": total_seconds,
        "credits": total_credits 
    }
    return {"ok": True, "data": normalized_data}
# ===================================================================
# === FIN DE LA FUNCIÓN CON FILTRO LOCAL CORREGIDO ==================
# ===================================================================

# --- Función start_batch_call (sin cambios) ---
def start_batch_call(call_name, agent_id, phone_number_id, recipients_json):
    print(f"[ElevenLabs] Iniciando lote: {call_name} (Agente: {agent_id})")
    endpoint = "/convai/batch-calling/submit"
    payload = {"call_name": call_name, "agent_id": agent_id, "agent_phone_number_id": phone_number_id, "recipients": recipients_json}
    return _eleven_request("POST", endpoint, payload=payload)