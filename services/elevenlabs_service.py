# services/elevenlabs_service.py
import os
import time
from typing import Any, Dict, List, Optional, Tuple
import requests

# ===============================
# Config
# ===============================
XI_API_KEY = (os.getenv("XI_API_KEY") or os.getenv("ELEVENLABS_API_KEY") or "").strip()
BASE_URL   = "https://api.elevenlabs.io"
DEFAULT_TIMEOUT = 30
MAX_RETRIES     = 3
RETRY_BACKOFF   = 1.5

# ===============================
# HTTP helpers
# ===============================
def _auth_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    if not XI_API_KEY:
        raise RuntimeError("XI_API_KEY / ELEVENLABS_API_KEY no está configurada.")
    headers = {
        "xi-api-key": XI_API_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers

def _http(method: str, url: str, json_body: Optional[Dict[str, Any]] = None,
          timeout: int = DEFAULT_TIMEOUT) -> Tuple[int, Any, Optional[str]]:
    try:
        resp = requests.request(method=method, url=url, json=json_body,
                                headers=_auth_headers(), timeout=timeout)
        ct = (resp.headers.get("Content-Type") or "").lower()
        data = resp.json() if "application/json" in ct else resp.text
        return resp.status_code, data, None
    except requests.RequestException as e:
        return 0, None, str(e)

def _retryable(status: int) -> bool:
    return status == 429 or 500 <= status < 600

def _to_float(x: Any, default: float = 0.0) -> float:
    try: return float(x) if x is not None else default
    except Exception: return default

def _to_int(x: Any, default: int = 0) -> int:
    try: return int(round(float(x))) if x is not None else default
    except Exception: return default

# ===============================
# Admin (sin cambios)
# ===============================
def get_eleven_agents() -> Dict[str, Any]:
    url = f"{BASE_URL}/v1/convai/agents"
    status, data, err = _http("GET", url, None)
    if err: return {"ok": False, "error": f"HTTP error: {err}"}
    if 200 <= status < 300: return {"ok": True, "data": data}
    return {"ok": False, "error": f"ElevenLabs error {status}: {data}"}

def get_eleven_phone_numbers() -> Dict[str, Any]:
    url = f"{BASE_URL}/v1/convai/twilio/phone-numbers"
    status, data, err = _http("GET", url, None)
    if err: return {"ok": False, "error": f"HTTP error: {err}"}
    if 200 <= status < 300: return {"ok": True, "data": data}
    return {"ok": False, "error": f"ElevenLabs error {status}: {data}"}

# ===============================
# MÉTRICAS — Solo conversations (robusto y con logs)
# ===============================
def _fallback_conversations(agent_id: str, start_unix_ts: int, end_unix_ts: int) -> Dict[str, Any]:
    """
    Agrega llamadas, créditos y duración leyendo todas las conversaciones del rango.
    Pagina con cursor hasta agotar resultados. LOGS incluidos para depurar.
    """
    base_query = (
        f"{BASE_URL}/v1/convai/conversations"
        f"?agent_id={agent_id}"
        f"&call_start_after_unix={start_unix_ts}"
        f"&call_start_before_unix={end_unix_ts}"
        f"&start_unix={start_unix_ts}"
        f"&end_unix={end_unix_ts}"
        f"&limit=200"
    )

    total_calls = 0
    total_credits = 0.0
    total_duration = 0.0

    next_url = base_query
    page = 0

    while next_url and page < 100:  # límite duro
        page += 1
        status, data, err = _http("GET", next_url, None)
        print(f"[metrics-fallback] GET {next_url} -> status={status} err={err}")
        if not (200 <= status < 300) or not isinstance(data, (dict, list)):
            break

        # Posibles estructuras
        conversations: List[Dict[str, Any]] = []
        if isinstance(data, dict):
            if isinstance(data.get("conversations"), list):
                conversations = data["conversations"]
            elif isinstance(data.get("data"), dict) and isinstance(data["data"].get("conversations"), list):
                conversations = data["data"]["conversations"]
        elif isinstance(data, list):
            conversations = data

        for conv in conversations:
            if not isinstance(conv, dict): continue
            total_calls += 1
            # nombres de crédito posibles
            cr = (conv.get("credits") or conv.get("credits_consumed")
                  or conv.get("total_credits") or conv.get("cost_credits") or 0.0)
            total_credits += _to_float(cr, 0.0)
            # nombres de duración posibles
            dur = (conv.get("duration_secs") or conv.get("duration_seconds")
                   or conv.get("total_duration_secs") or conv.get("seconds")
                   or conv.get("call_duration_seconds") or 0.0)
            total_duration += _to_float(dur, 0.0)

        cursor = None
        if isinstance(data, dict):
            cursor = data.get("next_cursor")
            if not cursor and isinstance(data.get("data"), dict):
                cursor = data["data"].get("next_cursor")

        if cursor:
            next_url = base_query + f"&cursor={requests.utils.quote(cursor)}"
        else:
            next_url = None

    return {"ok": True, "data": {"calls": total_calls, "credits": total_credits, "duration_secs": total_duration}}

def get_agent_consumption_data(agent_id: str, start_unix_ts: int, end_unix_ts: int) -> Dict[str, Any]:
    """
    Siempre usa el fallback /v1/convai/conversations para tu tenant (analytics devuelve 404).
    """
    try:
        return _fallback_conversations(agent_id, start_unix_ts, end_unix_ts)
    except Exception as e:
        return {"ok": False, "error": f"metrics-error: {e}"}

# ===============================
# Outbound (lote) — sin cambios funcionales
# ===============================
def _build_dynamic_variables(recipient: Dict[str, Any]) -> Dict[str, Any]:
    dyn: Dict[str, Any] = {}
    for k, v in recipient.items():
        if k == "phone_number": continue
        if v is None: continue
        sv = str(v).strip()
        if not sv: continue
        dyn[k] = sv
    return dyn

def _post_outbound_call(agent_id: str, phone_number_id: str, to_number: str,
                        dynamic_variables: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str], int]:
    url = f"{BASE_URL}/v1/convai/twilio/outbound-call"
    payload = {
        "agent_id": agent_id,
        "agent_phone_number_id": phone_number_id,
        "to_number": to_number,
        "conversation_initiation_client_data": {
            "type": "conversation_initiation_client_data",
            "dynamic_variables": dynamic_variables
        }
    }

    delay = 1.0
    for attempt in range(1, MAX_RETRIES + 1):
        status, data, err = _http("POST", url, payload)
        if err:
            if attempt >= MAX_RETRIES: return False, None, f"HTTP error: {err}", 0
            time.sleep(delay); delay *= RETRY_BACKOFF; continue
        if 200 <= status < 300:
            return True, (data if isinstance(data, dict) else {"raw": data}), None, status
        if _retryable(status):
            if attempt >= MAX_RETRIES: return False, data, f"ElevenLabs error {status}: {data}", status
            time.sleep(delay); delay *= RETRY_BACKOFF; continue
        return False, data, f"ElevenLabs error {status}: {data}", status

    return False, None, "Unknown error", 0

def start_batch_call(call_name: str, agent_id: str, phone_number_id: str,
                     recipients_json: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(recipients_json, list):
        return {"ok": False, "error": "Parámetro recipients_json debe ser lista."}
    if not agent_id or not phone_number_id:
        return {"ok": False, "error": "Faltan agent_id o phone_number_id."}

    total = len(recipients_json); sent = 0; failed = 0
    failures: List[Dict[str, Any]] = []; responses_sample: List[Any] = []
    per_call_sleep = float(os.getenv("ELEVENLABS_BATCH_SLEEP", "0.0"))

    for idx, r in enumerate(recipients_json, start=1):
        to = str(r.get("phone_number","")).strip()
        if not to:
            failed += 1
            failures.append({"phone_number":"","error":"Fila sin phone_number","status":0,"payload_sample":r})
            continue

        dyn = _build_dynamic_variables(r)  # mantiene name, last_name, etc.
        ok, data, err, status = _post_outbound_call(agent_id, phone_number_id, to, dyn)
        if ok:
            sent += 1
            if len(responses_sample) < 3:
                responses_sample.append({"phone_number": to, "result": data})
        else:
            failed += 1
            failures.append({
                "phone_number": to,
                "error": err or "error_desconocido",
                "status": status,
                "payload_sample": {"dynamic_variables": dyn}
            })

        if per_call_sleep and idx < total:
            time.sleep(per_call_sleep)

    return {
        "ok": True,
        "data": {
            "batch_name": call_name,
            "total": total,
            "sent": sent,
            "failed": failed,
            "failures": failures,
            "responses_sample": responses_sample
        }
    }
