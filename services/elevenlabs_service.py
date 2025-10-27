# services/elevenlabs_service.py
import os
import time
from typing import Any, Dict, List, Optional, Tuple
import requests

# =========================
# Config básica
# =========================
XI_API_KEY = (os.getenv("XI_API_KEY") or os.getenv("ELEVENLABS_API_KEY") or "").strip()
BASE_URL   = "https://api.elevenlabs.io"
DEFAULT_TIMEOUT = 30
MAX_RETRIES     = 3
RETRY_BACKOFF   = 1.5

# Factor de créditos por segundo (obligatorio si conversations no trae créditos)
# Ejemplo (de tus números): 286 créditos / 35 s = 8.1714286
CREDITS_PER_SEC = float(os.getenv("ELEVENLABS_CREDITS_PER_SEC", "0") or 0)

# =========================
# Helpers HTTP
# =========================
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

# =========================
# Admin: Agentes / Números
# =========================
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

# ==========================================================
# MÉTRICAS: sólo conversations (analytics/agent da 404)
#   - Contamos llamadas por cantidad de conversaciones
#   - Sumamos duración desde 'call_duration_secs'
#   - Créditos:
#       * Si la API trae un campo de créditos (raro), lo usamos
#       * Si no, calculamos: créditos = duración_total_en_segundos * CREDITS_PER_SEC
# ==========================================================
def _conversations_page(
    agent_id: str,
    start_unix_ts: int,
    end_unix_ts: int,
    limit: int = 200,
    cursor: Optional[str] = None
) -> Dict[str, Any]:
    base = f"{BASE_URL}/v1/convai/conversations"
    params = (
        f"?agent_id={agent_id}"
        f"&call_start_after_unix={start_unix_ts}"
        f"&call_start_before_unix={end_unix_ts}"
        f"&start_unix={start_unix_ts}"
        f"&end_unix={end_unix_ts}"
        f"&limit={limit}"
    )
    if cursor:
        params += f"&cursor={cursor}"

    url = base + params
    status, data, err = _http("GET", url, None)
    print(f"[metrics-conv] GET {url} -> status={status} err={err}")
    if err:
        return {"ok": False, "error": f"HTTP error: {err}"}
    if not (200 <= status < 300):
        return {"ok": False, "error": f"ElevenLabs error {status}: {str(data)[:200]}"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "Respuesta inesperada en conversations."}
    return {"ok": True, "data": data}

def get_agent_consumption_data(agent_id: str, start_unix_ts: int, end_unix_ts: int) -> Dict[str, Any]:
    """
    Agrega métricas a partir de /v1/convai/conversations, con paginación.
    """
    total_calls = 0
    total_duration_secs = 0.0
    total_credits = 0.0

    cursor = None
    seen_keys_debugged = False  # log de una muestra de claves

    while True:
        page = _conversations_page(agent_id, start_unix_ts, end_unix_ts, limit=200, cursor=cursor)
        if not page["ok"]:
            return {"ok": False, "error": page["error"]}

        payload = page["data"]
        items = payload.get("conversations") or payload.get("items") or []
        if not isinstance(items, list):
            items = []

        # Mostrar una muestra de claves (debug)
        if not seen_keys_debugged and items:
            sample = items[0]
            keys = list(sample.keys())
            print(f"[metrics] sample conversation keys: {keys}")
            seen_keys_debugged = True

        for conv in items:
            try:
                # Contamos llamada si existe un id
                if conv.get("conversation_id") or conv.get("id"):
                    total_calls += 1

                # Duración
                dur = conv.get("call_duration_secs") or conv.get("duration_secs") or conv.get("seconds") or 0
                try:
                    total_duration_secs += float(dur or 0)
                except Exception:
                    pass

                # Créditos (si vinieran) — la mayoría de cuentas no lo traen
                credits = conv.get("credits") or conv.get("total_credits")
                if credits is not None:
                    try:
                        total_credits += float(credits)
                    except Exception:
                        pass
            except Exception:
                continue

        cursor = payload.get("cursor") or payload.get("next_cursor")
        if not cursor:
            break

    # Si la API no trajo créditos, calculamos por factor
    if total_credits == 0.0:
        if CREDITS_PER_SEC <= 0:
            # Si no hay factor configurado, devolvemos métricas sin créditos (para no inventar)
            print("[metrics] conversations no trae créditos y ELEVENLABS_CREDITS_PER_SEC no está definido (>0).")
            return {
                "ok": True,
                "data": {
                    "calls": total_calls,
                    "credits": 0.0,
                    "duration_secs": total_duration_secs
                }
            }
        # Calculamos créditos por duración
        total_credits = total_duration_secs * CREDITS_PER_SEC

    return {
        "ok": True,
        "data": {
            "calls": total_calls,
            "credits": total_credits,
            "duration_secs": total_duration_secs
        }
    }

# =========================
# Outbound (lotes)
# =========================
def _build_dynamic_variables(recipient: Dict[str, Any]) -> Dict[str, str]:
    dyn: Dict[str, str] = {}
    for k, v in recipient.items():
        if k == "phone_number":
            continue
        if v is None:
            continue
        sv = str(v).strip()
        if not sv:
            continue
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

        dyn = _build_dynamic_variables(r)  # conserva name, last_name, etc.
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
