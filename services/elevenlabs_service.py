# services/elevenlabs_service.py
import os
import time
from typing import Any, Dict, List, Optional, Tuple
import requests

# ====== Config ======
XI_API_KEY = (os.getenv("XI_API_KEY") or os.getenv("ELEVENLABS_API_KEY") or "").strip()
BASE_URL   = "https://api.elevenlabs.io"

# Métricas configurables por ENV (para no volver a romper nada):
#   ELEVENLABS_METRICS_URL    -> path absoluto o relativo (ej: "/v1/convai/analytics/agent")
#   ELEVENLABS_METRICS_METHOD -> "POST" o "GET" (por defecto POST)
METRICS_URL_ENV    = (os.getenv("ELEVENLABS_METRICS_URL") or "/v1/convai/analytics/agent").strip()
METRICS_METHOD_ENV = (os.getenv("ELEVENLABS_METRICS_METHOD") or "POST").strip().upper()

DEFAULT_TIMEOUT = 30
MAX_RETRIES     = 3
RETRY_BACKOFF   = 1.5

# ====== Helpers HTTP ======
def _auth_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    if not XI_API_KEY:
        raise RuntimeError("XI_API_KEY / ELEVENLABS_API_KEY no está configurada.")
    headers = {
        "xi-api-key": XI_API_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if extra: headers.update(extra)
    return headers

def _http(method: str, url: str, json_body: Optional[Dict[str, Any]] = None,
          params: Optional[Dict[str, Any]] = None, timeout: int = DEFAULT_TIMEOUT) -> Tuple[int, Any, Optional[str], Dict[str, Any]]:
    try:
        resp = requests.request(method=method, url=url, json=json_body, params=params,
                                headers=_auth_headers(), timeout=timeout)
        ct = (resp.headers.get("Content-Type") or "").lower()
        data = resp.json() if "application/json" in ct else resp.text
        return resp.status_code, data, None, {"url": resp.url, "ct": ct}
    except requests.RequestException as e:
        return 0, None, str(e), {"url": url, "ct": ""}

def _retryable(status: int) -> bool:
    return status == 429 or 500 <= status < 600

# ====== Admin: Agentes y Números ======
def get_eleven_agents() -> Dict[str, Any]:
    url = f"{BASE_URL}/v1/convai/agents"
    status, data, err, _ = _http("GET", url)
    if err: return {"ok": False, "error": f"HTTP error: {err}"}
    if 200 <= status < 300: return {"ok": True, "data": data}
    return {"ok": False, "error": f"ElevenLabs error {status}: {data}"}

def get_eleven_phone_numbers() -> Dict[str, Any]:
    url = f"{BASE_URL}/v1/convai/twilio/phone-numbers"
    status, data, err, _ = _http("GET", url)
    if err: return {"ok": False, "error": f"HTTP error: {err}"}
    if 200 <= status < 300: return {"ok": True, "data": data}
    return {"ok": False, "error": f"ElevenLabs error {status}: {data}"}

# ====== Métricas (restaurado & configurable) ======
def _normalize_metrics(payload: Any) -> Optional[Dict[str, Any]]:
    """
    Normaliza a: { calls, credits, duration_secs } tolerando variantes.
    """
    if not isinstance(payload, dict):
        return None

    d = payload.get("data", payload)
    if isinstance(d, list):
        # Agregamos si viene en buckets
        calls = 0; credits = 0.0; dur_s = 0.0
        for row in d:
            if not isinstance(row, dict): continue
            calls   += int(row.get("calls") or row.get("total_calls") or 0)
            credits += float(row.get("credits") or row.get("total_credits") or 0.0)
            dur_s   += float(row.get("duration_secs") or row.get("total_duration_secs") or row.get("seconds") or 0.0)
        return {"calls": calls, "credits": credits, "duration_secs": dur_s}

    calls   = (d.get("calls") or d.get("total_calls") or d.get("outbound_calls") or 0)
    credits = (d.get("credits") or d.get("total_credits") or d.get("credits_consumed") or 0.0)
    dur_s   = (d.get("duration_secs") or d.get("total_duration_secs") or d.get("seconds") or 0.0)

    try:
        return {
            "calls": int(calls or 0),
            "credits": float(credits or 0.0),
            "duration_secs": float(dur_s or 0.0),
        }
    except Exception:
        return None

def _abs_metrics_url(path_or_url: str) -> str:
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    return f"{BASE_URL}{path_or_url}"

def get_agent_consumption_data(agent_id: str, start_unix_ts: int, end_unix_ts: int) -> Dict[str, Any]:
    """
    Usa la ruta definida por ENV para reproducir EXACTAMENTE tu setup anterior.
    Por defecto: POST /v1/convai/analytics/agent (lo más común).
    Si tu cuenta usa otra, fija ELEVENLABS_METRICS_URL y ELEVENLABS_METRICS_METHOD.
    """
    url = _abs_metrics_url(METRICS_URL_ENV)
    method = METRICS_METHOD_ENV if METRICS_METHOD_ENV in ("GET","POST") else "POST"
    payload = {"agent_id": agent_id, "start_unix_ts": start_unix_ts, "end_unix_ts": end_unix_ts}

    delay = 1.0
    for attempt in range(1, MAX_RETRIES + 1):
        if method == "POST":
            status, data, err, meta = _http("POST", url, json_body=payload)
        else:
            status, data, err, meta = _http("GET", url, params=payload)

        print(f"[metrics] {method} {meta['url']} -> status={status} err={err}")
        if err:
            if attempt >= MAX_RETRIES:
                return {"ok": False, "error": f"HTTP error: {err}"}
            time.sleep(delay); delay *= RETRY_BACKOFF; continue

        if 200 <= status < 300:
            norm = _normalize_metrics(data)
            if norm:
                return {"ok": True, "data": norm}
            else:
                return {"ok": False, "error": f"Formato desconocido: {str(data)[:200]}"}

        if _retryable(status):
            if attempt >= MAX_RETRIES:
                return {"ok": False, "error": f"ElevenLabs error {status}: {str(data)[:200]}"}
            time.sleep(delay); delay *= RETRY_BACKOFF; continue

        # Errores 4xx no retry
        return {"ok": False, "error": f"ElevenLabs error {status}: {str(data)[:200]}"}

    return {"ok": False, "error": "Unknown error"}

# ====== Outbound en lote (se mantiene tal cual) ======
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
        status, data, err, meta = _http("POST", url, json_body=payload)
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

        dyn = _build_dynamic_variables(r)
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
