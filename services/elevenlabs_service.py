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
    # ... (código sin cambios) ...
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
    # ... (código sin cambios) ...
    if not ELEVENLABS_API_KEY:
        print("[ElevenLabs] Error: API Key no configurada.")
        return {"ok": False, "error": "API Key no configurada"}
    url = f"{ELEVEN_API_BASE}{endpoint}"
    headers = {"Accept": "application/json", "xi-api-key": ELEVENLABS_API_KEY}
    try:
        # Quitamos logs DEBUG para limpieza
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
    # ... (código sin cambios) ...
    print("[ElevenLabs] Obteniendo agentes...")
    return _eleven_request("GET", "/convai/agents")

def get_eleven_phone_numbers():
    # ... (código sin cambios) ...
    print("[ElevenLabs] Obteniendo números...")
    return _eleven_request("GET", "/convai/phone-numbers")

# ===================================================================
# === FUNCIÓN FINAL (CON FILTRADO LOCAL DE FECHA 'DESDE') =========
# ===================================================================
def get_agent_consumption_data(agent_id, start_unix_ts, end_unix_ts):
    """
    Obtiene consumo usando /conversations con paginación (cursor),
    SOLO filtra por fecha FIN en la API, y luego filtra por fecha INICIO localmente.
    Calcula créditos usando fallback.
    """
    print(f"[ElevenLabs] Obteniendo conversaciones ANTES de {end_unix_ts} para Agente ID: {agent_id}...")
    
    endpoint = "/convai/conversations"
    all_conversations = [] # Lista para guardar TODAS las conversaciones antes de filtrar
    
    has_more = True
    next_cursor = None
    page_num = 1 
    max_pages = 50 # Límite de seguridad

    while has_more and page_num <= max_pages:
        # print(f"[ElevenLabs] Solicitando página {page_num}...") # Log opcional
        params = {
            "agent_id": agent_id,
            "page_size": 30 
        }
        
        # *** SOLO ENVIAR FECHA FIN (call_start_before_unix) la primera vez ***
        # *** Ya no enviamos la fecha de inicio a la API ***
        if not next_cursor:
            params["call_start_before_unix"] = int(end_unix_ts) 
        else:
            params["cursor"] = next_cursor 
        
        # print(f"DEBUG: Params enviados a la API: {params}") # Log opcional
        result = _eleven_request("GET", endpoint, params=params)
        
        if not result["ok"]:
            print(f"[ElevenLabs] Error al obtener la página {page_num}: {result.get('error')}")
            # Si falla una página, continuamos con lo que tenemos
            break 

        data = result.get("data", {})
        conversations_page = data.get("conversations", [])
        
        if not conversations_page:
            # print(f"[ElevenLabs] Página {page_num} vacía. Fin.") # Log opcional
            break # Salir si no hay conversaciones
            
        # --- Guardar conversaciones de esta página ---
        all_conversations.extend(conversations_page) 
        
        # --- Preparar el siguiente bucle ---
        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor", None) 
        
        # print(f"[ElevenLabs] Página {page_num} recibida. {len(all_conversations)} conversaciones acumuladas.") # Log opcional
        
        if not has_more or not next_cursor:
            # print("[ElevenLabs] Fin de la paginación según la API.") # Log opcional
            break 
            
        page_num += 1

    # --- FIN DEL BUCLE: Ya tenemos all_conversations ---
    if page_num > max_pages: print(f"[ElevenLabs] ADVERTENCIA: Límite de {max_pages} páginas alcanzado.")
    print(f"[ElevenLabs] Total conversaciones recibidas ANTES de filtrar por fecha inicio: {len(all_conversations)}")

    # --- AHORA FILTRAMOS LOCALMENTE POR FECHA DE INICIO ---
    filtered_conversations = []
    start_filter_ts = int(start_unix_ts) # Asegurar que es entero
    
    for convo in all_conversations:
         if isinstance(convo, dict):
             # Obtener el timestamp de inicio de la conversación
             # Probamos varios campos posibles donde podría estar
             convo_start_ts = convo.get("start_time_unix_secs", convo.get("start_unix_secs", convo.get("call_start_unix", 0)))
             
             # Comparar con la fecha "Desde" seleccionada
             if convo_start_ts >= start_filter_ts:
                 filtered_conversations.append(convo)

    print(f"[ElevenLabs] Total conversaciones DESPUÉS de filtrar por fecha inicio ({start_filter_ts}): {len(filtered_conversations)}")

    # --- Calcular totales SOBRE LA LISTA FILTRADA ---
    total_calls = 0
    total_credits = 0.0
    total_seconds = 0.0

    for convo in filtered_conversations:
        # Reutilizamos la lógica de cálculo que ya teníamos
        call_status = convo.get('call_successful', 'success') 
        if call_status != 'success': continue

        total_calls += 1
        secs = float(convo.get("call_duration_secs", convo.get("duration_secs", 0.0)))
        total_seconds += secs

        # Usar Fallback para créditos
        if secs > 0:
            calculated_credits = secs * FALLBACK_CREDITS_PER_SEC
            total_credits += calculated_credits

    # --- Fin del cálculo ---
    print(f"[ElevenLabs] Consumo total final calculado (filtrado): {total_calls} llamadas, {total_credits:.4f} créditos.")
    
    normalized_data = {
        "agent_id": agent_id,
        "calls": total_calls,
        "duration_secs": total_seconds,
        "credits": total_credits 
    }
    return {"ok": True, "data": normalized_data}
# ===================================================================
# === FIN DE LA FUNCIÓN CON FILTRO LOCAL ============================
# ===================================================================

# --- Función start_batch_call (sin cambios) ---
def start_batch_call(call_name, agent_id, phone_number_id, recipients_json):
    # ... (código sin cambios) ...
    print(f"[ElevenLabs] Iniciando lote: {call_name} (Agente: {agent_id})")
    endpoint = "/convai/batch-calling/submit"
    payload = {"call_name": call_name, "agent_id": agent_id, "agent_phone_number_id": phone_number_id, "recipients": recipients_json}
    return _eleven_request("POST", endpoint, payload=payload)