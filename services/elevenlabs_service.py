# services/elevenlabs_service.py
import os
import time
import json
import math
import traceback
import re
from typing import Any, Dict, List, Optional, Tuple
import requests

# =========================
# Configuración base
# =========================
XI_API_KEY = (os.getenv("XI_API_KEY") or os.getenv("ELEVENLABS_API_KEY") or "").strip()
BASE_URL   = "https://api.elevenlabs.io"

DEFAULT_TIMEOUT = 30  # segundos
MAX_RETRIES     = 3
RETRY_BACKOFF   = 1.5  # multiplicador


def _auth_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    if not XI_API_KEY:
        raise RuntimeError("XI_API_KEY no configurada en entorno.")
    headers = {
        "xi-api-key": XI_API_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def _http(method: str, url: str, json_body: Optional[Dict[str, Any]] = None, timeout: int = DEFAULT_TIMEOUT) -> Tuple[int, Any, Optional[str]]:
    """
    Envuelve requests.* con manejo de excepciones.
    Retorna: (status_code, json|texto, error_message|None)
    """
    try:
        resp = requests.request(method=method, url=url, json=json_body, headers=_auth_headers(), timeout=timeout)
        content_type = resp.headers.get("Content-Type", "")
        if "application/json" in content_type:
            try:
                data = resp.json()
            except Exception:
                data = resp.text
        else:
            data = resp.text
        return resp.status_code, data, None
    except requests.RequestException as e:
        return 0, None, str(e)


def _retryable(status: int) -> bool:
    # Reintentar en 429 y 5xx
    return status == 429 or 500 <= status < 600


# =========================
# Agentes y Números
# =========================
def get_eleven_agents() -> Dict[str, Any]:
    """
    Devuelve { ok: bool, data: Any, error?: str }
    Normalmente ElevenLabs expone un listado de agentes ConvAI.
    Endpoint utilizado (puede variar en docs): GET /v1/convai/agents
    """
    url = f"{BASE_URL}/v1/convai/agents"
    status, data, err = _http("GET", url, None)
    if err:
        return {"ok": False, "error": f"HTTP error: {err}"}
    if 200 <= status < 300:
        return {"ok": True, "data": data}
    return {"ok": False, "error": f"ElevenLabs error {status}: {data}"}


def get_eleven_phone_numbers() -> Dict[str, Any]:
    """
    Devuelve { ok: bool, data: Any, error?: str }
    Endpoint usual para números Twilio de ConvAI:
    GET /v1/convai/twilio/phone-numbers
    """
    url = f"{BASE_URL}/v1/convai/twilio/phone-numbers"
    status, data, err = _http("GET", url, None)
    if err:
        return {"ok": False, "error": f"HTTP error: {err}"}
    if 200 <= status < 300:
        return {"ok": True, "data": data}
    return {"ok": False, "error": f"ElevenLabs error {status}: {data}"}


# =========================
# Consumo / Métricas
# =========================
def get_agent_consumption_data(agent_id: str, start_unix_ts: int, end_unix_ts: int) -> Dict[str, Any]:
    """
    Devuelve { ok: bool, data: { calls, credits, duration_secs }, error?: str }

    Nota: La API de métricas puede cambiar de ruta/nombre según la versión pública.
    Aquí intentamos un endpoint típico; si falla, devolvemos ceros de forma segura
    para no romper el panel.
    """
    # Intento 1: endpoint plausible (ajústalo a tu doc real si ya lo tienes)
    url = f"{BASE_URL}/v1/convai/analytics/agent"
    payload = {
        "agent_id": agent_id,
        "start_unix_ts": start_unix_ts,
        "end_unix_ts": end_unix_ts,
    }
    status, data, err = _http("POST", url, payload)
    if err:
        # Fallback con ceros
        return {"ok": True, "data": {"calls": 0, "credits": 0.0, "duration_secs": 0.0}}

    if 200 <= status < 300 and isinstance(data, dict):
        # Intentamos leer campos comunes; normalizamos a lo que espera el panel
        calls = data.get("calls") or data.get("total_calls") or 0
        credits = data.get("credits") or data.get("total_credits") or 0.0
        duration_secs = data.get("duration_secs") or data.get("total_duration_secs") or 0.0
        try:
            calls = int(calls)
        except Exception:
            calls = 0
        try:
            credits = float(credits)
        except Exception:
            credits = 0.0
        try:
            duration_secs = float(duration_secs)
        except Exception:
            duration_secs = 0.0
        return {"ok": True, "data": {"calls": calls, "credits": credits, "duration_secs": duration_secs}}

    # Si no funcionó, devolvemos ceros en lugar de romper el flujo
    return {"ok": True, "data": {"calls": 0, "credits": 0.0, "duration_secs": 0.0}}


# =========================
# Batch Outbound Call
# =========================
def _build_dynamic_variables(recipient: Dict[str, Any]) -> Dict[str, Any]:
    """
    Construye dynamic_variables a partir de la fila:
    - Siempre ignora 'phone_number'.
    - Incluye 'name' y 'last_name' si existen.
    - Incluye cualquier otra columna adicional (ya normalizada a snake_case por el backend).
    """
    dyn: Dict[str, Any] = {}
    for k, v in recipient.items():
        if k == "phone_number":
            continue
        if v is None:
            continue
        val = str(v).strip()
        if val == "":
            continue
        dyn[k] = val
    return dyn


def _post_outbound_call(agent_id: str, phone_number_id: str, to_number: str, dynamic_variables: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str], int]:
    """
    Hace el POST /v1/convai/twilio/outbound-call con reintentos.
    Retorna: (ok, json_data, error_str, status_code)
    """
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
            # Error de red a nivel requests
            if attempt >= MAX_RETRIES:
                return False, None, f"HTTP error: {err}", 0
            time.sleep(delay)
            delay *= RETRY_BACKOFF
            continue

        if 200 <= status < 300:
            # Ok
            out = data if isinstance(data, dict) else {"raw": data}
            return True, out, None, status

        if _retryable(status):
            if attempt >= MAX_RETRIES:
                return False, data, f"ElevenLabs error {status}: {data}", status
            # Backoff
            time.sleep(delay)
            delay *= RETRY_BACKOFF
            continue

        # No retryable
        return False, data, f"ElevenLabs error {status}: {data}", status

    return False, None, "Unknown error (no retries left)", 0


def start_batch_call(call_name: str, agent_id: str, phone_number_id: str, recipients_json: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Lanza llamadas salientes una por una.
    recipients_json: lista de objetos con al menos 'phone_number' y opcionalmente 'name', 'last_name' y demás columnas.

    Devuelve:
    {
      "ok": True/False,
      "data": {
         "batch_name": str,
         "total": int,
         "sent": int,
         "failed": int,
         "failures": [ { "phone_number": str, "error": str, "status": int, "payload_sample": {...} } ],
         "responses_sample": [ ... hasta 3 ... ]
      },
      "error": str (opcional)
    }
    """
    if not isinstance(recipients_json, list):
        return {"ok": False, "error": "Parámetro recipients_json debe ser una lista."}
    if not agent_id or not phone_number_id:
        return {"ok": False, "error": "Faltan agent_id o phone_number_id."}

    total = len(recipients_json)
    sent = 0
    failed = 0
    failures: List[Dict[str, Any]] = []
    responses_sample: List[Any] = []

    # Throttling simple para no golpear demasiado la API (ajusta a tu gusto)
    per_call_sleep = float(os.getenv("ELEVENLABS_BATCH_SLEEP", "0.0"))
    sample_limit = 3

    for idx, recipient in enumerate(recipients_json, start=1):
        to_number = str(recipient.get("phone_number", "")).strip()
        if not to_number:
            failed += 1
            failures.append({
                "phone_number": "",
                "error": "Fila sin phone_number",
                "status": 0,
                "payload_sample": recipient
            })
            continue

        dynamic_variables = _build_dynamic_variables(recipient)

        ok, data, err, status = _post_outbound_call(agent_id, phone_number_id, to_number, dynamic_variables)
        if ok:
            sent += 1
            if len(responses_sample) < sample_limit:
                responses_sample.append({
                    "phone_number": to_number,
                    "result": data
                })
        else:
            failed += 1
            failures.append({
                "phone_number": to_number,
                "error": err or "error_desconocido",
                "status": status,
                "payload_sample": {"dynamic_variables": dynamic_variables}
            })

        if per_call_sleep > 0 and idx < total:
            time.sleep(per_call_sleep)

    summary = {
        "batch_name": call_name,
        "total": total,
        "sent": sent,
        "failed": failed,
        "failures": failures,
        "responses_sample": responses_sample
    }

    if failed == 0:
        return {"ok": True, "data": summary}
    # Si hubo fallos, igual devolvemos ok=True para que el backend pueda mostrar el detalle
    return {"ok": True, "data": summary}
