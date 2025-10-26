# services/elevenlabs_service.py
import os
import time
from typing import Any, Dict, List, Optional, Tuple
import requests

# === Config básica (como antes) ===
XI_API_KEY = (os.getenv("XI_API_KEY") or os.getenv("ELEVENLABS_API_KEY") or "").strip()
BASE_URL   = "https://api.elevenlabs.io"
DEFAULT_TIMEOUT = 30
MAX_RETRIES     = 3
RETRY_BACKOFF   = 1.5

# ---------- Helpers HTTP ----------
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

# ---------- Admin: Agentes / Números (SIN CAMBIOS) ----------
def get_eleven_agents() -> Dict[str, Any]:
    url = f"{BASE_URL}/v1/convai/agents"
    status, data, err = _http("GET", url, None)
    if err:
        return {"ok": False, "error": f"HTTP error: {err}"}
    if 200 <= status < 300:
        return {"ok": True, "data": data}
    return {"ok": False, "error": f"ElevenLabs error {status}: {data}"}

def get_eleven_phone_numbers() -> Dict[str, Any]:
    url = f"{BASE_URL}/v1/convai/twilio/phone-numbers"
    status, data, err = _http("GET", url, None)
    if err:
        return {"ok": False, "error": f"HTTP error: {err}"}
    if 200 <= status < 300:
        return {"ok": True, "data": data}
    return {"ok": False, "error": f"ElevenLabs error {status}: {data}"}

# ---------- MÉTRICAS (RESTABLECIDO A LA RUTA ORIGINAL) ----------
def _normalize_metrics(payload: Any) -> Dict[str, Any]:
    """
    Normaliza a {calls, credits, duration_secs} tolerando nombres típicos.
    """
    if not isinstance(payload, dict):
        return {"calls": 0, "credits": 0.0, "duration_secs": 0.0}
    d = payload.get("data", payload)
    # nombres frecuentes
    calls   = d.get("calls", d.get("total_calls", 0))
    credits = d.get("credits", d.get("total_credits", d.get("credits_consumed", 0.0)))
    dur_s   = d.get("duration_secs", d.get("total_duration_secs", d.get("seconds", 0.0)))
    try:
        return {
            "calls": int(calls or 0),
            "credits": float(credits or 0.0),
            "duration_secs": float(dur_s or 0.0),
        }
    except Exception:
        return {"calls": 0, "credits": 0.0, "duration_secs": 0.0}

def get_agent_consumption_data(agent_id: str, start_unix_ts: int, end_unix_ts: int) -> Dict[str, Any]:
    """
    Versión simple (como estaba): POST /v1/convai/analytics/agent
    SIN cambios de environment ni rutas alternativas.
    """
    url = f"{BASE_URL}/v1/convai/analytics/agent"
    payload = {"agent_id": agent_id, "start_unix_ts": start_unix_ts, "end_unix_ts": end_unix_ts}

    delay = 1.0
    for attempt in range(1, MAX_RETRIES + 1):
        status, data, err = _http("POST", url, payload)
        if err:
            if attempt >= MAX_RETRIES:
                return {"ok": False, "error": f"HTTP error: {err}"}
            time.sleep(delay); delay *= RETRY_BACKOFF; continue

        if 200 <= status < 300 and isinstance(data, (dict, list)):
            # Algunos tenants devuelven lista (buckets) -> agregamos
            if isinstance(data, list):
                calls = 0; credits = 0.0; dur_s = 0.0
                for row in data:
                    if not isinstance(row, dict): continue
                    calls   += int(row.get("calls") or row.get("total_calls") or 0)
                    credits += float(row.get("credits") or row.get("total_credits") or 0.0)
                    dur_s   += float(row.get("duration_secs") or row.get("total_duration_secs") or row.get("seconds") or 0.0)
                return {"ok": True, "data": {"calls": calls, "credits": credits, "duration_secs": dur_s}}
            # Estructura plana
            norm = _normalize_metrics(data)
            return {"ok": True, "data": norm}

        if _retryable(status):
            if attempt >= MAX_RETRIES:
                return {"ok": False, "error": f"ElevenLabs error {status}: {str(data)[:200]}"}
            time.sleep(delay); delay *= RETRY_BACKOFF; continue

        # 4xx (no reintenta)
        return {"ok": False, "error": f"ElevenLabs error {status}: {str(data)[:200]}"}

    return {"ok": False, "error": "Unknown error"}

# ---------- OUTBOUND (LOTE) — SE MANTIENE ----------
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

        dyn = _build_dynamic_variables(r)  # <-- mantiene name, last_name, etc.
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
