import os
import requests
import json 
import time # Para obtener fallback rate

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVEN_API_BASE = "https://api.elevenlabs.io/v1"

# --- Tasa Fallback de Créditos por Segundo (Leer de Env) ---
# Asegúrate de tener esta variable en Render, ej: 10.73
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
# === FUNCIÓN FINAL (Alineada con INH Billing) ======================
# ===================================================================
def get_agent_consumption_data(agent_id, start_unix, end_unix):
    """
    Obtiene consumo usando /conversations con paginación (cursor),
    parámetros de fecha correctos y fallback de créditos por duración.
    """
    print(f"[ElevenLabs] Obteniendo TODAS las conversaciones para Agente ID: {agent_id}...")
    endpoint = "/convai/conversations"
    
    total_calls = 0
    total_credits = 0.0
    total_seconds = 0.0
    
    # Lista ampliada de campos de créditos (como en INH Billing)
    credit_field_candidates = [
        'credits_used', 'credit_cost', 'credits', 
        'llm_credits', 'cost_credits', 'total_credits', 
        'credit_usage' 
    ]
    
    has_more = True
    next_cursor = None
    page_num = 1 
    max_pages = 50 

    while has_more and page_num <= max_pages:
        print(f"[ElevenLabs] Solicitando página {page_num}...")
        params = {
            "agent_id": agent_id,
            "page_size": 30 
        }
        
        # *** CORRECCIÓN: Usar nombres de parámetro de fecha correctos (como PHP) ***
        # *** CORRECCIÓN: Enviar fechas solo si NO hay cursor (primera página) ***
        if not next_cursor:
            params["call_start_after_unix"] = int(start_unix)  # <--- CORREGIDO
            params["call_start_before_unix"] = int(end_unix) # <--- CORREGIDO
            print("DEBUG: Enviando fechas (primera página)")
        else:
            params["cursor"] = next_cursor 
            print(f"DEBUG: Enviando cursor: {next_cursor} (NO se envían fechas)")
        
        # --- Hacer la llamada ---
        print(f"DEBUG: Params enviados a la API: {params}") 
        result = _eleven_request("GET", endpoint, params=params)
        
        if not result["ok"]:
            print(f"[ElevenLabs] Error al obtener la página {page_num}: {result.get('error')}")
            # Si falla una página, es mejor devolver lo acumulado que nada
            break 

        # --- Procesar la página ---
        data = result.get("data", {})
        conversations_page = data.get("conversations", [])
        
        if not conversations_page:
            if page_num == 1: print(f"[ElevenLabs] No se encontraron conversaciones...")
            else: print(f"[ElevenLabs] Página {page_num} vacía. Fin.")
            break 

        # Imprimir la primera conversación (útil para depurar si aún falla)
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
                # Ignorar llamadas no exitosas (como en PHP)
                call_status = convo.get('call_successful', 'success') # Asumir éxito si no existe
                if call_status != 'success':
                    continue

                total_calls += 1
                
                # Obtener duración (priorizar call_duration_secs como en PHP)
                secs = float(convo.get("call_duration_secs", convo.get("duration_secs", 0.0)))
                total_seconds += secs

                # Buscar créditos en la lista ampliada
                credits_found = 0.0
                found_explicit_credits = False
                for field_name in credit_field_candidates:
                    if field_name in convo and convo[field_name] is not None:
                        try:
                            value = float(convo[field_name])
                            # Considerar 0 como válido si el campo existe
                            if value >= 0: 
                                credits_found = value
                                found_explicit_credits = True 
                                break # Tomar el primer campo válido encontrado
                        except (ValueError, TypeError):
                            continue 
                
                # *** CORRECCIÓN: Implementar Fallback si no se encontró campo explícito ***
                if not found_explicit_credits and secs > 0:
                    calculated_credits = secs * FALLBACK_CREDITS_PER_SEC
                    print(f"DEBUG: No se encontró campo de créditos para conv {convo.get('conversation_id')}. Usando fallback: {secs:.2f}s * {FALLBACK_CREDITS_PER_SEC:.2f} = {calculated_credits:.4f} créditos.")
                    total_credits += calculated_credits
                else:
                    total_credits += credits_found # Sumar el crédito encontrado (o 0 si no se encontró y duración es 0)

        # --- Preparar el siguiente bucle ---
        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor", None) 
        
        print(f"[ElevenLabs] Página {page_num} procesada. Total parcial: {total_calls} llamadas, {total_credits:.4f} créditos. HasMore={has_more}, NextCursor={next_cursor}")
        
        if not has_more or not next_cursor:
            print("[ElevenLabs] Fin de la paginación según la API.")
            break 
            
        page_num += 1

    # Fin del bucle
    if page_num > max_pages: print(f"[ElevenLabs] ADVERTENCIA: Límite de {max_pages} páginas alcanzado.")

    print(f"[ElevenLabs] Consumo total final calculado: {total_calls} llamadas, {total_credits:.4f} créditos.")
    
    normalized_data = {
        "agent_id": agent_id,
        "calls": total_calls,
        "duration_secs": total_seconds,
        "credits": total_credits 
    }
    return {"ok": True, "data": normalized_data}
# ===================================================================
# === FIN DE LA FUNCIÓN FINAL =======================================
# ===================================================================

# --- Función start_batch_call (sin cambios) ---
def start_batch_call(call_name, agent_id, phone_number_id, recipients_json):
    print(f"[ElevenLabs] Iniciando lote: {call_name} (Agente: {agent_id})")
    endpoint = "/convai/batch-calling/submit"
    payload = {
        "call_name": call_name,
        "agent_id": agent_id,
        "agent_phone_number_id": phone_number_id,
        "recipients": recipients_json
    }
    return _eleven_request("POST", endpoint, payload=payload)