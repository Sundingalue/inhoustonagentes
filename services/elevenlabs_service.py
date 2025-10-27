# services/elevenlabs_service.py
import os
import time
from typing import Any, Dict, List, Optional, Tuple
import requests

# === Config básica ===
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

# ---------- Admin: Agentes / Números ----------
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

# ---------- MÉTRICAS (solo conversations, con paginación) ----------
def _sum_float(x: Any) -> float:
    try:
        return float(x or 0.0)
    except Exception:
        return 0.0

def _sum_int(x: Any) -> int:
    try:
        return int(x or 0)
    except Exception:
        try:
            return int(float(x or 0))
        except Exception:
            return 0

def _extract_credits(row: Dict[str, Any]) -> float:
    # nombres comunes donde ElevenLabs entrega créditos
    for k in ("credits", "credits_consumed", "total_credits", "usage_credits"):
        if k in row:
            return _sum_float(row.get(k))
    # a veces vienen anidados
    usage = row.get("usage") or {}
    if isinstance(usage, dict):
        for k in ("credits", "credits_consumed"):
            if k in usage:
                return _sum_float(usage.get(k))
    return 0.0

def _extract_duration_secs(row: Dict[str, Any]) -> float:
    for k in ("duration_secs", "duration_seconds", "total_duration_secs", "seconds"):
        if k in row:
            return _sum_float(row.get(k))
    # a veces duration viene en milisegundos
    for k in ("duration_ms", "durationMilliseconds"):
        if k in row:
            return _sum_float(row.get(k)) / 1000.0
    return 0.0

def _conversations_url(agent_id: str, start_unix_ts: int, end_unix_ts: int, limit: int = 200, cursor: Optional[str] = None) -> str:
    base = (
        f"{BASE_URL}/v1/convai/conversations"
        f"?agent_id={agent_id}"
        f"&call_start_after_unix={start_unix_ts}"
        f"&call_start_before_unix={end_unix_ts}"
        f"&start_unix={start_unix_ts}"
        f"&end_unix={end_unix_ts}"
        f"&limit={limit}"
    )
    if cursor:
        from urllib.parse import quote
        base += f"&cursor={quote(cursor, safe='')}"
    return base

def get_agent_consumption_data(agent_id: str, start_unix_ts: int, end_unix_ts: int) -> Dict[str, Any]:
    """
    Suma llamadas, créditos y duración consultando /v1/convai/conversations
    (endpoint estable para tu tenant). Pagina con `cursor` hasta terminar.
    """
    total_calls = 0
    total_credits = 0.0
    total_duration_secs = 0.0

    cursor: Optional[str] = None
    page = 0

    while True:
        url = _conversations_url(agent_id, start_unix_ts, end_unix_ts, limit=200, cursor=cursor)
        status, data, err = _http("GET", url, None)
        print(f"[metrics] GET {url} -> status={status} err={err}")

        if err:
            return {"ok": False, "error": f"HTTP error: {err}"}

        if status == 429:
            time.sleep(1.5)
            continue

        if not (200 <= status < 300):
            return {"ok": False, "error": f"ElevenLabs error {status}: {str(data)[:200]}"}

        # Estructura esperada: { conversations: [...], next_cursor: "..." }
        conversations = []
        next_cursor = None

        if isinstance(data, dict):
            conversations = data.get("conversations") or data.get("items") or []
            next_cursor = data.get("next_cursor") or data.get("cursor") or None
        elif isinstance(data, list):
            conversations = data

        if not isinstance(conversations, list):
            conversations = []

        # Agregar
        for row in conversations:
            if not isinstance(row, dict):
                continue
            total_calls += 1
            total_credits += _extract_credits(row)
            total_duration_secs += _extract_duration_secs(row)

        # Log de muestra: solo en la primera página
        if page == 0 and conversations:
            try:
                sample = conversations[0]
                print("[metrics] sample conversation:", {
                    "has_credits": any(k in sample for k in ("credits", "credits_consumed")),
                    "has_duration": any(k in sample for k in ("duration_secs", "duration_seconds", "duration_ms")),
                    "keys": list(sample.keys())[:15]
                })
            except Exception:
                pass

        page += 1
        if next_cursor:
            cursor = next_cursor
            # pequeño respiro por si hay rate limiting
            time.sleep(0.05)
            continue
        break

    return {
        "ok": True,
        "data": {
            "calls": total_calls,
            "credits": round(total_credits, 6),
            "duration_secs": round(total_duration_secs, 3),
        }
    }

# ---------- OUTBOUND (LOTE) ----------
def _build_dynamic_variables(recipient: Dict[str, Any]) -> Dict[str, Any]:
    dyn: Dict[str, Any] = {}
    for k, v in recipient.items():
        if k == "phone_number":
            continue
        if v is None:
            continue
        sv = str(v).strip()
        if not sv:
            continue
        # mantener tal cual (name, last_name, etc.)
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
            if attempt >= MAX_RETRIES:
                return False, None, f"HTTP error: {err}", 0
            time.sleep(delay); delay *= RETRY_BACKOFF; continue
        if 200 <= status < 300:
            return True, (data if isinstance(data, dict) else {"raw": data}), None, status
        if _retryable(status):
            if attempt >= MAX_RETRIES:
                return False, data, f"ElevenLabs error {status}: {data}", status
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
