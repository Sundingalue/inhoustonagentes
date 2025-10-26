import os
import requests
import json
import time

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVEN_API_BASE = "https://api.elevenlabs.io/v1"

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
    if response.status_code == 200: return response.json()
    else: print(f"[ElevenLabs] Error {response.status_code}: {response.text}"); return {"error": f"Error al iniciar"}

def _eleven_request(method, endpoint, payload=None, params=None):
    if not ELEVENLABS_API_KEY: return {"ok": False, "error": "API Key no configurada"}
    url = f"{ELEVEN_API_BASE}{endpoint}"
    headers = {"Accept": "application/json", "xi-api-key": ELEVENLABS_API_KEY}
    try:
        if method.upper() == "GET": response = requests.get(url, headers=headers, params=params)
        elif method.upper() == "POST": headers["Content-Type"] = "application/json"; response = requests.post(url, headers=headers, json=payload)
        else: return {"ok": False, "error": f"Método no soportado: {method}"}
        response.raise_for_status()
        return {"ok": True, "data": response.json()}
    except requests.exceptions.HTTPError as http_err:
        try: error_details = http_err.response.json(); error_msg = error_details.get('detail', {}).get('message', http_err.response.text)
        except: error_msg = http_err.response.text
        print(f"[ElevenLabs] Error API (HTTP {http_err.response.status_code}): {error_msg}"); return {"ok": False, "error": f"Error API: {error_msg}"}
    except requests.exceptions.RequestException as req_err: print(f"[ElevenLabs] Error Conexión: {req_err}"); return {"ok": False, "error": f"Error conexión: {req_err}"}

def get_eleven_agents(): return _eleven_request("GET", "/convai/agents")
def get_eleven_phone_numbers(): return _eleven_request("GET", "/convai/phone-numbers")

# ===================================================================
# === FUNCIÓN CON COMPARACIÓN SIMPLIFICADA Y LOGGING =============
# ===================================================================
def get_agent_consumption_data(agent_id, start_unix_ts, end_unix_ts):
    print(f"[ElevenLabs] Obteniendo conversaciones ANTES de {end_unix_ts} para Agente ID: {agent_id}...")
    endpoint = "/convai/conversations"
    all_conversations = []
    has_more, next_cursor, page_num, max_pages = True, None, 1, 50

    while has_more and page_num <= max_pages:
        params = {"agent_id": agent_id, "page_size": 30}
        if not next_cursor: params["call_start_before_unix"] = int(end_unix_ts)
        else: params["cursor"] = next_cursor
        result = _eleven_request("GET", endpoint, params=params)
        if not result["ok"]: print(f"[EL] Error P{page_num}: {result.get('error')}"); break
        data = result.get("data", {}); conversations_page = data.get("conversations", [])
        if not conversations_page: break
        all_conversations.extend(conversations_page)
        has_more = data.get("has_more", False); next_cursor = data.get("next_cursor", None)
        if not has_more or not next_cursor: break
        page_num += 1

    if page_num > max_pages: print(f"[EL] WARN: Límite {max_pages} pág.")
    print(f"[EL] Recibidas ANTES filtrar: {len(all_conversations)}")

    # --- FILTRADO LOCAL (SIMPLIFICADO Y CON LOGS) ---
    filtered_conversations = []
    start_filter_ts_int = int(start_unix_ts) # Tu fecha "Desde" como número

    for convo in all_conversations:
         if isinstance(convo, dict):
             # Leer directamente el campo que sabemos existe
             convo_start_value = convo.get("start_time_unix_secs")
             convo_id = convo.get("conversation_id", "ID_DESCONOCIDO") # Para logging

             # Convertir a int de forma segura
             convo_start_num = None
             if convo_start_value is not None:
                 try: convo_start_num = int(convo_start_value)
                 except (ValueError, TypeError): pass # Si no es número, se queda None

             # *** Loggear la comparación ***
             print(f"DEBUG: Comparando convo {convo_id}: timestamp={convo_start_num} >= filtro={start_filter_ts_int} ?")

             # Hacer la comparación solo si ambos son números válidos
             if convo_start_num is not None and convo_start_num >= start_filter_ts_int:
                 print(f"DEBUG: --> SÍ, incluir.")
                 filtered_conversations.append(convo)
             else:
                 print(f"DEBUG: --> NO, excluir.")


    print(f"[EL] DESPUÉS filtrar ({start_filter_ts_int}): {len(filtered_conversations)}")

    # --- Calcular totales ---
    total_calls, total_credits, total_seconds = 0, 0.0, 0.0
    for convo in filtered_conversations:
        if convo.get('call_successful') == 'success':
            total_calls += 1
            secs = float(convo.get("call_duration_secs", 0.0))
            total_seconds += secs
            if secs > 0: total_credits += secs * FALLBACK_CREDITS_PER_SEC

    print(f"[EL] Final (filtrado): {total_calls} llamadas, {total_credits:.4f} créditos.")
    normalized_data = {"agent_id": agent_id, "calls": total_calls, "duration_secs": total_seconds, "credits": total_credits}
    return {"ok": True, "data": normalized_data}
# ===================================================================
# === FIN FUNCIÓN ==================================================
# ===================================================================

def start_batch_call(call_name, agent_id, phone_number_id, recipients_json):
    print(f"[EL] Iniciando lote: {call_name} (Ag: {agent_id})")
    payload = {"call_name": call_name, "agent_id": agent_id, "agent_phone_number_id": phone_number_id, "recipients": recipients_json}
    return _eleven_request("POST", "/convai/batch-calling/submit", payload=payload)