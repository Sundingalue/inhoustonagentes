# services/elevenlabs_service.py
import os
import time
import json
from typing import Any, Dict, List, Optional, Tuple
import requests

XI_API_KEY = (os.getenv("XI_API_KEY") or os.getenv("ELEVENLABS_API_KEY") or "").strip()
BASE_URL   = "https://api.elevenlabs.io"

DEFAULT_TIMEOUT = 30
MAX_RETRIES     = 3
RETRY_BACKOFF   = 1.5

def _auth_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    if not XI_API_KEY:
        raise RuntimeError("XI_API_KEY no configurada en entorno.")
    headers = {
        "xi-api-key": XI_API_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if extra: headers.update(extra)
    return headers

def _http(method: str, url: str, json_body: Optional[Dict[str, Any]] = None, timeout: int = DEFAULT_TIMEOUT) -> Tuple[int, Any, Optional[str]]:
    try:
        resp = requests.request(method=method, url=url, json=json_body, headers=_auth_headers(), timeout=timeout)
        ct = resp.headers.get("Content-Type","")
        data = resp.json() if "application/json" in ct else resp.text
        return resp.status_code, data, None
    except requests.RequestException as e:
        return 0, None, str(e)

def _retryable(status: int) -> bool:
    return status == 429 or 500 <= status < 600

# ---------- Listados ----------
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

# ---------- MÉTRICAS (restaurado) ----------
def get_agent_consumption_data(agent_id: str, start_unix_ts: int, end_unix_ts: int) -> Dict[str, Any]:
    """
    Intenta 2 rutas conocidas. Si una responde OK, normaliza a:
    { calls, credits, duration_secs }
    """
    # Ruta A (POST): analytics de agente
    url_a = f"{BASE_URL}/v1/convai/analytics/agent"
    payload = {"agent_id": agent_id, "start_unix_ts": start_unix_ts, "end_unix_ts": end_unix_ts}
    status, data, err = _http("POST", url_a, payload)
    if not err and 200 <= status < 300 and isinstance(data, dict):
        calls  = int(data.get("calls") or data.get("total_calls") or 0)
        credits = float(data.get("credits") or data.get("total_credits") or 0.0)
        dur_s   = float(data.get("duration_secs") or data.get("total_duration_secs") or 0.0)
        return {"ok": True, "data": {"calls": calls, "credits": credits, "duration_secs": dur_s}}

    # Ruta B (GET): usage summary (nombre/ruta puede variar; ajusta si tu doc usa otro)
    url_b = f"{BASE_URL}/v1/convai/usage/summary"
    params_b = {"agent_id": agent_id, "start_unix_ts": start_unix_ts, "end_unix_ts": end_unix_ts}
    try:
        resp = requests.get(url_b, headers=_auth_headers(), params=params_b, timeout=DEFAULT_TIMEOUT)
        if 200 <= resp.status_code < 300:
            data_b = resp.json()
            if isinstance(data_b, dict):
                calls  = int(data_b.get("calls") or data_b.get("total_calls") or 0)
                credits = float(data_b.get("credits") or data_b.get("total_credits") or 0.0)
                dur_s   = float(data_b.get("duration_secs") or data_b.get("total_duration_secs") or 0.0)
                return {"ok": True, "data": {"calls": calls, "credits": credits, "duration_secs": dur_s}}
    except requests.RequestException as e:
        return {"ok": False, "error": f"HTTP error: {e}"}

    # Si ninguna ruta respondió correctamente, devolvemos error (no forzamos ceros)
    return {"ok": False, "error": f"No se pudo obtener métricas. A:{status} {data}"}

# ---------- Outbound con variables ----------
def _build_dynamic_variables(recipient: Dict[str, Any]) -> Dict[str, Any]:
    dyn: Dict[str, Any] = {}
    for k, v in recipient.items():
        if k == "phone_number": continue
        if v is None: continue
        sv = str(v).strip()
        if not sv: continue
        dyn[k] = sv
    return dyn

def _post_outbound_call(agent_id: str, phone_number_id: str, to_number: str, dynamic_variables: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str], int]:
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
        if status == 429 or 500 <= status < 600:
            if attempt >= MAX_RETRIES: return False, data, f"ElevenLabs error {status}: {data}", status
            time.sleep(delay); delay *= RETRY_BACKOFF; continue
        return False, data, f"ElevenLabs error {status}: {data}", status

    return False, None, "Unknown error", 0

def start_batch_call(call_name: str, agent_id: str, phone_number_id: str, recipients_json: List[Dict[str, Any]]) -> Dict[str, Any]:
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
            failed += 1; failures.append({"phone_number":"","error":"Fila sin phone_number","status":0,"payload_sample":r}); continue
        dyn = _build_dynamic_variables(r)
        ok, data, err, status = _post_outbound_call(agent_id, phone_number_id, to, dyn)
        if ok:
            sent += 1
            if len(responses_sample) < 3:
                responses_sample.append({"phone_number": to, "result": data})
        else:
            failed += 1
            failures.append({"phone_number": to, "error": err or "error_desconocido", "status": status, "payload_sample": {"dynamic_variables": dyn}})
        if per_call_sleep and idx < total: time.sleep(per_call_sleep)

    return {"ok": True, "data": {"batch_name": call_name, "total": total, "sent": sent, "failed": failed, "failures": failures, "responses_sample": responses_sample}}
