# services/elevenlabs_service.py
import os
import time
from typing import Any, Dict, List, Optional, Tuple
import requests
from urllib.parse import urlparse, parse_qs

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
          params: Optional[Dict[str, Any]] = None,
          timeout: int = DEFAULT_TIMEOUT) -> Tuple[int, Any, Optional[str], Dict[str, Any]]:
    try:
        resp = requests.request(method=method, url=url, json=json_body, params=params,
                                headers=_auth_headers(), timeout=timeout)
        ct = (resp.headers.get("Content-Type") or "").lower()
        data = resp.json() if "application/json" in ct else resp.text
        meta = {"url": resp.url, "ct": ct}
        return resp.status_code, data, None, meta
    except requests.RequestException as e:
        return 0, None, str(e), {"url": url, "ct": ""}

def _retryable(status: int) -> bool:
    return status == 429 or 500 <= status < 600

# ---------- Admin: Agentes / Números ----------
def get_eleven_agents() -> Dict[str, Any]:
    url = f"{BASE_URL}/v1/convai/agents"
    status, data, err, _ = _http("GET", url, None)
    if err:
        return {"ok": False, "error": f"HTTP error: {err}"}
    if 200 <= status < 300:
        return {"ok": True, "data": data}
    return {"ok": False, "error": f"ElevenLabs error {status}: {data}"}

def get_eleven_phone_numbers() -> Dict[str, Any]:
    url = f"{BASE_URL}/v1/convai/twilio/phone-numbers"
    status, data, err, _ = _http("GET", url, None)
    if err:
        return {"ok": False, "error": f"HTTP error: {err}"}
    if 200 <= status < 300:
        return {"ok": True, "data": data}
    return {"ok": False, "error": f"ElevenLabs error {status}: {data}"}

# ---------- Normalizadores ----------
def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default

def _normalize_metrics(payload: Any) -> Dict[str, Any]:
    """
    Normaliza a {calls, credits, duration_secs} tolerando dict/list y envolturas {data}, {analytics}.
    """
    if payload is None:
        return {"calls": 0, "credits": 0.0, "duration_secs": 0.0}

    # Lista de buckets
    if isinstance(payload, list):
        calls = 0; credits = 0.0; dur_s = 0.0
        for row in payload:
            if not isinstance(row, dict): continue
            calls   += _safe_int(row.get("calls") or row.get("total_calls") or 0)
            credits += _safe_float(row.get("credits") or row.get("total_credits") or row.get("credits_consumed") or 0.0)
            dur_s   += _safe_float(row.get("duration_secs") or row.get("total_duration_secs") or row.get("seconds") or 0.0)
        return {"calls": calls, "credits": credits, "duration_secs": dur_s}

    if not isinstance(payload, dict):
        return {"calls": 0, "credits": 0.0, "duration_secs": 0.0}

    d = payload
    if "data" in d and isinstance(d["data"], (dict, list)):
        return _normalize_metrics(d["data"])
    if "analytics" in d and isinstance(d["analytics"], (dict, list)):
        return _normalize_metrics(d["analytics"])

    calls   = d.get("calls", d.get("total_calls", d.get("outbound_calls", 0)))
    credits = d.get("credits", d.get("total_credits", d.get("credits_consumed", 0.0)))
    dur_s   = d.get("duration_secs", d.get("total_duration_secs", d.get("seconds", 0.0)))
    return {
        "calls": _safe_int(calls),
        "credits": _safe_float(credits),
        "duration_secs": _safe_float(dur_s),
    }

def _extract_conversation_items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], list):
        return payload["data"]
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("conversations"), list):
        return payload["conversations"]
    return []

def _normalize_conversations_list(payload: Any) -> Dict[str, Any]:
    """
    Suma conversaciones.
    Busca claves frecuentes: credits/credits_consumed/usage_credits/cost_credits,
    duration_secs/call_duration_seconds/duration_seconds o calcula por timestamps.
    """
    calls = 0; credits = 0.0; dur_s = 0.0
    items = _extract_conversation_items(payload)

    for c in items:
        if not isinstance(c, dict): continue
        calls += 1
        cr_candidates = (c.get("credits"), c.get("credits_consumed"), c.get("usage_credits"), c.get("cost_credits"))
        credits += next((_safe_float(v) for v in cr_candidates if v is not None), 0.0)

        dur_candidates = (c.get("duration_secs"), c.get("call_duration_seconds"), c.get("duration_seconds"))
        dsec = next((_safe_float(v) for v in dur_candidates if v is not None), None)
        if dsec is None:
            start_ts = c.get("call_start_unix") or c.get("start_unix")
            end_ts   = c.get("call_end_unix")   or c.get("end_unix")
            if start_ts and end_ts:
                dsec = max(0.0, _safe_float(end_ts) - _safe_float(start_ts))
            else:
                dsec = 0.0
        dur_s += dsec

    return {"calls": calls, "credits": credits, "duration_secs": dur_s}

# ---------- Métricas con fallback y paginación ----------
def _try_metrics_variant(method: str, url: str, json_body: Optional[Dict[str, Any]] = None,
                         params: Optional[Dict[str, Any]] = None) -> Tuple[bool, Dict[str, Any], str, int]:
    status, data, err, meta = _http(method, url, json_body=json_body, params=params)
    dbg = f"[metrics] {method} {meta.get('url')} -> status={status} err={err}"
    if err:
        return False, {"error": f"HTTP error: {err}"}, dbg, status
    if 200 <= status < 300:
        norm = _normalize_metrics(data)
        return True, {"ok": True, "data": norm}, dbg, status
    return False, {"error": f"ElevenLabs error {status}: {str(data)[:200]}"}, dbg, status

def _paginate_conversations(agent_id: str, start_unix_ts: int, end_unix_ts: int) -> Dict[str, Any]:
    """
    Recorre todas las páginas de /v1/convai/conversations sumando métricas.
    Intenta varios esquemas de paginación:
      - 'next' como URL completa
      - 'next_page_token' / 'nextToken' -> page_token/next_page_token
      - fallback por 'offset' cuando length == limit
    Limita a 50 páginas para evitar loops.
    """
    base_url = f"{BASE_URL}/v1/convai/conversations"
    limit = 200  # subir un poco el tope por página
    params: Dict[str, Any] = {
        "agent_id": agent_id,
        "call_start_after_unix":  start_unix_ts,
        "call_start_before_unix": end_unix_ts,
        "start_unix": start_unix_ts,
        "end_unix":   end_unix_ts,
        "limit": limit,
    }

    total_calls = 0; total_credits = 0.0; total_secs = 0.0
    url = base_url
    pages = 0

    while pages < 50:
        status, data, err, meta = _http("GET", url, params=params)
        dbg = f"[metrics-fallback] GET {meta.get('url')} -> status={status} err={err}"
        print(dbg)
        if err:
            break
        if not (200 <= status < 300):
            break

        # sumar esta página
        norm = _normalize_conversations_list(data)
        total_calls   += norm["calls"]
        total_credits += norm["credits"]
        total_secs    += norm["duration_secs"]
        pages += 1

        # detectar siguiente página
        next_url = None
        next_token = None

        if isinstance(data, dict):
            # 1) next como URL
            if isinstance(data.get("next"), str) and data["next"]:
                next_url = data["next"]

            # 2) next_page_token / nextToken
            next_token = data.get("next_page_token") or data.get("nextToken")

        # 3) si no hay next explícito, usar offset cuando llenamos la página
        page_items = _extract_conversation_items(data)
        filled_page = len(page_items) >= limit

        if next_url:
            # si es absoluta, úsala tal cual
            url = next_url
            params = None  # la URL ya trae sus query params
            continue
        elif next_token:
            url = base_url
            if params is None: params = {}
            # probar nombres de parámetro comunes
            params.update({
                "page_token": next_token,
                "next_page_token": next_token
            })
            continue
        elif filled_page:
            # fallback por offset
            url = base_url
            if params is None: params = {}
            params["offset"] = _safe_int(params.get("offset", 0)) + limit
            continue
        else:
            # no hay más páginas
            break

    return {"calls": total_calls, "credits": total_credits, "duration_secs": total_secs, "pages": pages}

def get_agent_consumption_data(agent_id: str, start_unix_ts: int, end_unix_ts: int) -> Dict[str, Any]:
    """
    1) Intenta endpoint legacy /v1/convai/analytics/agent (si existe).
    2) Si 404, usa Fallback: /v1/convai/conversations con paginación y filtros.
    """
    base = f"{BASE_URL}/v1/convai/analytics/agent"
    variants = [
        ("POST", base, {"agent_id": agent_id, "start_unix_ts": start_unix_ts, "end_unix_ts": end_unix_ts}, None),
        ("POST", base, {"agentId": agent_id, "startUnixTs": start_unix_ts, "endUnixTs": end_unix_ts}, None),
        ("GET",  base, None, {"agent_id": agent_id, "start_unix_ts": start_unix_ts, "end_unix_ts": end_unix_ts}),
        ("GET",  base, None, {"agentId": agent_id, "startUnixTs": start_unix_ts, "endUnixTs": end_unix_ts}),
    ]

    delay = 1.0
    last_status = None
    last_error = "Unknown error"

    for attempt in range(1, MAX_RETRIES + 1):
        for (method, url, body, params) in variants:
            ok, res, dbg, status = _try_metrics_variant(method, url, json_body=body, params=params)
            print(dbg)
            last_status = status
            if ok:
                return res
            last_error = res.get("error", last_error)

        if last_status == 404:
            break
        if attempt < MAX_RETRIES:
            time.sleep(delay); delay *= RETRY_BACKOFF

    # --- Fallback paginado ---
    tot = _paginate_conversations(agent_id, start_unix_ts, end_unix_ts)
    return {"ok": True, "data": {"calls": tot["calls"], "credits": tot["credits"], "duration_secs": tot["duration_secs"]}}

# ---------- OUTBOUND (LOTE) (sin cambios) ----------
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
        status, data, err, _ = _http("POST", url, json_body=payload)
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

        dyn = _build_dynamic_variables(r)  # deja name, last_name, etc.
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
