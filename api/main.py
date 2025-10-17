from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse
from workflows.processor import process_agent_event
import hmac, hashlib, os, json, base64
from dotenv import load_dotenv
from typing import Any, Dict, Optional, List
import traceback

# =========================
# Configuración del Directorio
# =========================
# SOLUCIÓN DE RUTA DEFINITIVA: 
# Usamos 'os.path.dirname' para encontrar el directorio donde está main.py (api/)
# Luego usamos 'os.path.join' y 'os.path.abspath' para retroceder y encontrar la ruta correcta.
# 
# Si main.py está en /project/api/main.py, la ruta de la config es /project/agents
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# ✅ CORRECCIÓN CLAVE: Subimos del directorio 'api' (..) y vamos directamente a 'agents'
BOT_CONFIG_DIR = os.path.join(SCRIPT_DIR, '..', 'agents')
BOT_CONFIG_DIR = os.path.abspath(BOT_CONFIG_DIR)

# =========================
# Cargar .env (Render/local)
# =========================
SECRET_ENV_PATH = "/etc/secrets/.env"
if os.path.exists(SECRET_ENV_PATH):
    load_dotenv(SECRET_ENV_PATH)
    print(f"✅ Archivo .env cargado desde {SECRET_ENV_PATH}")
else:
    load_dotenv()
    print("⚠️ Usando .env local")

# =========================
# App
# =========================
app = FastAPI()
print("✅ FastAPI cargado correctamente y esperando eventos de ElevenLabs…")

# =========================
# Config HMAC
# =========================
HMAC_SECRET = (os.getenv("ELEVENLABS_HMAC_SECRET") or "").strip()
SKIP_HMAC = (os.getenv("ELEVENLABS_SKIP_HMAC") or "false").strip().lower() == "true"

if not HMAC_SECRET and not SKIP_HMAC:
    raise RuntimeError("❌ Falta ELEVENLABS_HMAC_SECRET (o define ELEVENLABS_SKIP_HMAC=true para omitir).")


# =========================
# Lógica de Mapeo de Agentes
# =========================
AGENT_ID_TO_FILENAME_CACHE: Dict[str, str] = {}

def map_agent_id_to_filename(agent_id: str) -> Optional[str]:
    """
    Busca el nombre del archivo de configuración (ej. 'sundin.json') dado el ID
    largo de ElevenLabs (ej. 'agent_8301...').
    """
    
    # 1. Intentar encontrar en el caché
    if agent_id in AGENT_ID_TO_FILENAME_CACHE:
        return AGENT_ID_TO_FILENAME_CACHE[agent_id]

    # 2. Si no está en caché, recorrer los archivos del directorio
    try:
        if not os.path.isdir(BOT_CONFIG_DIR):
            print(f"❌ Directorio de agentes no encontrado. Ruta calculada: {BOT_CONFIG_DIR}")
            return None

        print(f"Buscando el ID de Agente {agent_id} en el directorio: {BOT_CONFIG_DIR}")
        
        for filename in os.listdir(BOT_CONFIG_DIR):
            if not filename.endswith(".json") or filename.startswith("_"):
                continue

            filepath = os.path.join(BOT_CONFIG_DIR, filename)
            
            with open(filepath, 'r', encoding='utf-8') as f:
                config: Dict[str, Any] = json.load(f)
                
                # 3. Comprobar si el ID del agente de ElevenLabs coincide
                if config.get("elevenlabs_agent_id") == agent_id:
                    # 4. Encontramos la coincidencia, guardamos en caché y devolvemos
                    AGENT_ID_TO_FILENAME_CACHE[agent_id] = filename
                    print(f"✅ Mapeo encontrado: {agent_id} -> {filename}")
                    return filename
        
        # Si el loop termina sin encontrarlo
        print(f"❌ No se encontró ningún archivo JSON con el ID de agente {agent_id} en {BOT_CONFIG_DIR}.")
        return None

    except Exception as e:
        print(f"💥 Error al intentar mapear el agente: {e}")
        return None

# =========================
# Funciones de Soporte
# =========================
def _verify_hmac(secret: str, body: bytes, sig_header: str) -> bool:
    """
    Verifica firma HMAC.
    """
    if not sig_header:
        print("🚨 No se recibió cabecera HMAC.")
        return False

    t, v0 = "", ""
    for part in [x.strip() for x in sig_header.split(",")]:
        if part.startswith("t="):  t = part.split("=", 1)[1]
        if part.startswith("v0="): v0 = part.split("=", 1)[1]
    if not v0:
        print("🚨 HMAC sin v0.")
        return False

    body_txt = body.decode("utf-8", errors="ignore")

    # Pruebas de compatibilidad con diferentes formatos de firma
    expected_body   = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    expected_t_body = hmac.new(secret.encode(), f"{t}.{body_txt}".encode(), hashlib.sha256).hexdigest()
    expected_tbody  = hmac.new(secret.encode(), f"{t}{body_txt}".encode(), hashlib.sha256).hexdigest()

    if v0 in (expected_body, expected_t_body, expected_tbody):
        print("🔏 HMAC válido ✅")
        return True

    # Debug útil
    print("🚨 HMAC inválido")
    print(f"received: {sig_header}")
    return False

def _normalize_event(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normaliza el payload del evento de ElevenLabs.
    """
    root = data.get("data", data) if isinstance(data, dict) else {}
    agent_id = (
        root.get("agent_id")
        or (root.get("agent") or {}).get("id")
        or data.get("agent_id")
        or None
    )

    transcript_list = root.get("transcript") or root.get("transcription") or []
    transcript_text = ""
    if isinstance(transcript_list, list):
        try:
            transcript_text = " ".join(
                (t.get("message", "") or "").strip()
                for t in transcript_list
                if isinstance(t, dict) and t.get("role") == "user"
            ).strip()
        except Exception:
            transcript_text = ""
    elif isinstance(transcript_list, str):
        transcript_text = transcript_list.strip()

    caller = called = None
    try:
        client_data = root.get("conversation_initiation_client_data", {}) or {}
        dyn = client_data.get("dynamic_variables", {}) or {}
        caller = (dyn.get("system__caller_id") or "").strip() or None
        called = (dyn.get("system__called_number") or "").strip() or None
    except Exception:
        pass

    return {
        "agent_id": agent_id,
        "transcript_text": transcript_text,
        "caller": caller,
        "called": called,
        "timestamp": root.get("timestamp") or data.get("timestamp"),
        "raw": data,
    }

# =========================
# Webhook
# =========================
@app.post("/api/agent-event")
async def handle_agent_event(
    request: Request,
    elevenlabs_signature: str = Header(default=None, alias="elevenlabs-signature"),
):
    try:
        body_bytes = await request.body()

        # 1. Verificación HMAC
        if not SKIP_HMAC:
            sig_header = (
                elevenlabs_signature
                or request.headers.get("elevenlabs-signature")
                or request.headers.get("ElevenLabs-Signature")
            )
            if not _verify_hmac(HMAC_SECRET, body_bytes, sig_header):
                raise HTTPException(status_code=401, detail="Invalid HMAC signature.")
        else:
            print("⚠️ HMAC BYPASS ACTIVADO (ELEVENLABS_SKIP_HMAC=true)")

        # 2. Carga y Normalización de datos
        try:
            data = json.loads(body_bytes.decode("utf-8"))
        except Exception:
            data = await request.json()

        normalized = _normalize_event(data)
        agent_id = normalized.get("agent_id")

        if not agent_id:
            raise HTTPException(status_code=400, detail="Missing agent_id in payload.")

        print(f"🚀 Procesando evento para ID de ElevenLabs: {agent_id}")

        # 3. 🔑 Mapeo del ID largo al nombre de archivo legible (¡La solución!)
        config_filename = map_agent_id_to_filename(agent_id)

        if not config_filename:
            # Si no encuentra el mapeo
            detail_msg = f"Config file not found for ElevenLabs ID: {agent_id}. Ensure an agent config file in '{BOT_CONFIG_DIR}/' contains the key 'elevenlabs_agent_id' with this ID."
            raise HTTPException(status_code=404, detail=detail_msg)
        
        # 4. Procesamiento
        # ✅ CORRECCIÓN CLAVE: Pasamos el nombre legible del agente (ej: "sundin") a processor.py
        agent_name = config_filename.replace(".json", "")
        result = process_agent_event(agent_name, normalized)

        return JSONResponse(status_code=200, content={"status": "ok", "result": result})

    except HTTPException as http_err:
        return JSONResponse(status_code=http_err.status_code, content={"error": http_err.detail})
    except Exception as e:
        print(f"💥 Error procesando webhook: {e}")
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": "internal_error", "detail": str(e)})

# =========================
# Env check
# =========================
@app.get("/_envcheck")
def envcheck():
    keys = [
        "MAIL_FROM","MAIL_USERNAME","MAIL_PASSWORD","MAIL_HOST","MAIL_PORT",
        "ELEVENLABS_HMAC_SECRET","ELEVENLABS_SKIP_HMAC"
    ]
    return {k: os.getenv(k) for k in keys}