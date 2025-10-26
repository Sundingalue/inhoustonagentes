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

# --- Helper Function to make API requests ---
def _eleven_request(method, endpoint, payload=None, params=None):
    """Generic helper for ElevenLabs v1 API requests."""
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
            return {"ok": False, "error": f"Unsupported HTTP method: {method}"}
        response.raise_for_status()
        return {"ok": True, "data": response.json()}
    except requests.exceptions.HTTPError as http_err:
        error_msg = http_err.response.text
        try:
            error_details = http_err.response.json()
            error_msg = error_details.get('detail', {}).get('message', error_msg)
        except json.JSONDecodeError:
             pass
        print(f"[ElevenLabs] API Error (HTTP {http_err.response.status_code}): {error_msg}")
        return {"ok": False, "error": f"API Error: {error_msg}"}
    except requests.exceptions.RequestException as req_err:
        print(f"[ElevenLabs] Connection Error: {req_err}")
        return {"ok": False, "error": f"Connection error: {req_err}"}

# --- Funciones de sincronización y consumo (sin cambios) ---
def get_eleven_agents():
    print("[ElevenLabs] Getting agent list...")
    return _eleven_request("GET", "/convai/agents")

def get_eleven_phone_numbers():
    print("[ElevenLabs] Getting phone number list...")
    return _eleven_request("GET", "/convai/phone-numbers")

def get_agent_consumption_data(agent_id, start_unix_ts, end_unix_ts):
    """Obtiene datos de consumo. (Mantenemos la lógica de filtrado local)."""
    print(f"[EL] Getting conversations BEFORE {end_unix_ts} for Agent ID: {agent_id}...")
    endpoint = "/convai/conversations"
    all_conversations = []
    has_more, next_cursor, page_num, max_pages = True, None, 1, 50

    while has_more and page_num <= max_pages:
        params = {"agent_id": agent_id, "page_size": 30}
        if not next_cursor: params["call_start_before_unix"] = int(end_unix_ts)
        else: params["cursor"] = next_cursor
        result = _eleven_request("GET", endpoint, params=params)
        if not result["ok"]: print(f"[EL] Error fetching page {page_num}: {result.get('error')}"); break
        data = result.get("data", {}); conversations_page = data.get("conversations", [])
        if not conversations_page: break
        all_conversations.extend(conversations_page)
        has_more = data.get("has_more", False); next_cursor = data.get("next_cursor", None)
        if not has_more or not next_cursor: break; page_num += 1

    if page_num > max_pages: print(f"[EL] WARN: Reached max pages limit ({max_pages}).")
    print(f"[EL] Received {len(all_conversations)} conversations BEFORE local filtering.")

    filtered_conversations = []; start_filter_ts_int = int(start_unix_ts)
    for convo in all_conversations:
         if isinstance(convo, dict):
             convo_start_value = convo.get("start_time_unix_secs"); convo_start_num = None
             if convo_start_value is not None:
                 try: convo_start_num = int(float(convo_start_value))
                 except (ValueError, TypeError): pass
             if convo_start_num is not None and convo_start_num >= start_filter_ts_int:
                 filtered_conversations.append(convo)

    print(f"[EL] {len(filtered_conversations)} conversations AFTER filtering by start date >= {start_filter_ts_int}.")
    total_calls, total_credits, total_seconds = 0, 0.0, 0.0
    for convo in filtered_conversations:
        if convo.get('call_successful') == 'success':
            total_calls += 1; secs = float(convo.get("call_duration_secs", convo.get("duration_secs", 0.0))); total_seconds += secs
            if secs > 0: total_credits += secs * FALLBACK_CREDITS_PER_SEC

    print(f"[EL] Final Calculation: {total_calls} calls, {total_credits:.4f} credits (estimated).")
    normalized_data = {"agent_id": agent_id, "calls": total_calls, "duration_secs": total_seconds, "credits": total_credits}
    return {"ok": True, "data": normalized_data}


# ===================================================================
# === FUNCIÓN CORREGIDA: VUELVE A COMPORTAMIENTO ORIGINAL DE LOTES ==
# ===================================================================
def start_batch_call(call_name, agent_id, phone_number_id, recipients_json):
    """
    Inicia una llamada por lotes (a múltiples destinatarios) sin forzar el inicio
    inmediato (el comportamiento original de ElevenLabs).
    """
    
    print(f"[EL] Initiating DEFAULT BATCH SUBMISSION: {call_name} for Agent: {agent_id} with {len(recipients_json)} recipients.")
    
    endpoint = "/convai/batch-calling/submit"
    
    payload = {
        "call_name": call_name,
        "agent_id": agent_id,
        "agent_phone_number_id": phone_number_id,
        "recipients": recipients_json
        # !!! QUITAMOS 'scheduled_time_unix' !!!
    }
    
    # Hacemos la llamada POST al endpoint de LOTES
    result = _eleven_request("POST", endpoint, payload=payload)
    
    if result.get("ok"):
        print(f"[EL] Batch submission successful. Status: ElevenLabs Default Queue.")
        return {"ok": True, "data": {"status": "Lote enviado a cola por defecto", "id": result['data'].get('batch_id')}}
    
    return result
# ===================================================================
# === END FINAL FUNCTION ============================================
# ===================================================================