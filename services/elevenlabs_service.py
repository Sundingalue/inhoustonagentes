import os
import requests
import json 

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVEN_API_BASE = "https://api.elevenlabs.io/v1"

# --- Funciones existentes (start_conversation_with_agent, _eleven_request, etc.) ---
# ... (Sin cambios aquí, incluir todo el código anterior hasta get_agent_consumption_data) ...

def start_conversation_with_agent(agent_id, input_text=None):
    """
    Inicia una conversación con un agente de ElevenLabs usando su agent_id.
    Opcionalmente puede enviar un mensaje inicial (input_text).
    """
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
        # Quitamos los DEBUG prints generales aquí para claridad
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, params=params)
        elif method.upper() == "POST":
            headers["Content-Type"] = "application/json"
            response = requests.post(url, headers=headers, json=payload)
        else:
            return {"ok": False, "error": f"Método HTTP no soportado: {method}"}

        response.raise_for_status()
        return {"ok": True, "data": response.json()}
    
    except requests.exceptions.HTTPError as http_err:
        try:
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

def get_eleven_agents():
    print("[ElevenLabs] Obteniendo lista de agentes...")
    return _eleven_request("GET", "/convai/agents")

def get_eleven_phone_numbers():
    print("[ElevenLabs] Obteniendo lista de números de teléfono...")
    return _eleven_request("GET", "/convai/phone-numbers")

# ===================================================================
# === FUNCIÓN CON AJUSTE DE FECHAS EN PAGINACIÓN ====================
# ===================================================================
def get_agent_consumption_data(agent_id, start_unix, end_unix):
    """
    Obtiene datos de consumo usando /conversations con paginación (cursor)
    y enviando fechas SOLO en la primera petición.
    """
    print(f"[ElevenLabs] Obteniendo TODAS las conversaciones para Agente ID: {agent_id}...")
    
    endpoint = "/convai/conversations"
    
    total_calls = 0
    total_credits = 0.0
    total_seconds = 0.0
    credit_field_candidates = ['credits', 'credit_cost', 'credits_used', 'llm_credits', 'cost_credits', 'total_credits', 'credit_usage']
    
    has_more = True
    next_cursor = None
    page_num = 1 
    max_pages = 50 # Límite de seguridad

    while has_more and page_num <= max_pages:
        print(f"[ElevenLabs] Solicitando página {page_num}...")
        params = {
            "agent_id": agent_id,
            "page_size": 30
        }
        
        # *** AJUSTE CLAVE: Enviar fechas solo si NO hay cursor (primera página) ***
        if not next_cursor:
            params["start_unix"] = int(start_unix)
            params["end_unix"] = int(end_unix)
            print("DEBUG: Enviando fechas (primera página)")
        else:
            params["cursor"] = next_cursor 
            print(f"DEBUG: Enviando cursor: {next_cursor} (NO se envían fechas)")
        
        # --- Hacer la llamada ---
        # Imprimimos los params exactos que se envían
        print(f"DEBUG: Params enviados a la API: {params}") 
        result = _eleven_request("GET", endpoint, params=params)
        
        if not result["ok"]:
            print(f"[ElevenLabs] Error al obtener la página {page_num}: {result.get('error')}")
            break 

        # --- Procesar la página ---
        data = result.get("data", {})
        conversations_page = data.get("conversations", [])
        
        if not conversations_page:
            if page_num == 1: print(f"[ElevenLabs] No se encontraron conversaciones...")
            else: print(f"[ElevenLabs] Página {page_num} vacía. Fin.")
            break 

        # *** IMPRESIÓN DEL JSON (SIGUE SIENDO NECESARIA) ***
        if page_num == 1 and conversations_page:
             try:
                 print("DEBUG: ================== PRIMERA CONVERSACIÓN (PAG 1) ==================")
                 print(json.dumps(conversations_page[0], indent=2))
                 print("DEBUG: =================================================================")
             except Exception as e:
                 print(f"DEBUG: Error al imprimir el objeto de conversación: {e}")

        # Sumar los totales
        for convo in conversations_page:
            if isinstance(convo, dict):
                total_calls += 1
                credits_found = 0.0
                for field_name in credit_field_candidates:
                    # Check más robusto: existe, no es None, es numérico
                    if field_name in convo and convo[field_name] is not None:
                        try:
                            value = float(convo[field_name])
                            if value > 0: # Solo sumar si es positivo
                                credits_found = value
                                break 
                        except (ValueError, TypeError):
                            continue 
                total_credits += credits_found
                total_seconds += float(convo.get("duration_secs", convo.get("call_duration_secs", 0.0)))

        # --- Preparar el siguiente bucle ---
        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor", None) 
        
        print(f"[ElevenLabs] Página {page_num} procesada. Total parcial: {total_calls} llamadas, {total_credits:.2f} créditos. HasMore={has_more}, NextCursor={next_cursor}")
        
        if not has_more or not next_cursor:
            print("[ElevenLabs] Fin de la paginación según la API.")
            break 
            
        page_num += 1

    # Fin del bucle
    if page_num > max_pages: print(f"[ElevenLabs] ADVERTENCIA: Límite de {max_pages} páginas alcanzado.")

    print(f"[ElevenLabs] Consumo total final calculado: {total_calls} llamadas, {total_credits:.4f} créditos.")
    
    normalized_data = {"agent_id": agent_id, "calls": total_calls, "duration_secs": total_seconds, "credits": total_credits}
    return {"ok": True, "data": normalized_data}

# --- Función start_batch_call ---
# ... (Sin cambios aquí, incluir el código anterior) ...
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
    }
    return _eleven_request("POST", endpoint, payload=payload)