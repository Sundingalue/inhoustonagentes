# main.py
from fastapi import FastAPI, Request, Header, HTTPException, Depends, File, UploadFile, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from jose import JWTError, jwt
import hmac, hashlib, os, json, base64, re, traceback, io, time
from typing import Any, Dict, Optional, List
from datetime import datetime, timedelta

import pandas as pd

# Servicios ElevenLabs (sin tocar envs ni rutas raras)
from services.elevenlabs_service import (
    get_eleven_agents,
    get_eleven_phone_numbers,
    get_agent_consumption_data,
    start_batch_call
)

# --------- Carga .env (local o /etc/secrets) ----------
from dotenv import load_dotenv
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SECRET_ENV_PATH = "/etc/secrets/.env"
if os.path.exists(SECRET_ENV_PATH):
    load_dotenv(SECRET_ENV_PATH); print(f"✅ .env cargado desde {SECRET_ENV_PATH}")
else:
    load_dotenv(); print("⚠️ Usando .env local")

# --------- App / CORS ----------
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"]
)
print("✅ FastAPI cargado.")

# --------- Seguridad Webhooks / HMAC ----------
HMAC_SECRET = (os.getenv("ELEVENLABS_HMAC_SECRET") or "").strip()
SKIP_HMAC   = (os.getenv("ELEVENLABS_SKIP_HMAC") or "false").strip().lower() == "true"
if not HMAC_SECRET and not SKIP_HMAC:
    raise RuntimeError("❌ Falta ELEVENLABS_HMAC_SECRET")

def _verify_hmac(secret: str, body: bytes, sig_header: str) -> bool:
    if not sig_header:
        print("🚨 Falta header HMAC."); return False
    t, v0 = "", ""
    for part in [x.strip() for x in sig_header.split(",")]:
        if part.startswith("t="):  t = part.split("=", 1)[1]
        if part.startswith("v0="): v0 = part.split("=", 1)[1]
    if not v0:
        print("🚨 HMAC sin v0."); return False

    body_txt = body.decode("utf-8", errors="ignore")
    expected_body   = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    expected_t_body = hmac.new(secret.encode(), f"{t}.{body_txt}".encode(), hashlib.sha256).hexdigest()
    expected_tbody  = hmac.new(secret.encode(), f"{t}{body_txt}".encode(), hashlib.sha256).hexdigest()
    return v0 in (expected_body, expected_t_body, expected_tbody)

# --------- Mapeos de archivos de agentes (configs JSON en /agents) ----------
BOT_CONFIG_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, '..', 'agents'))

AGENT_ID_TO_FILENAME_CACHE: Dict[str, str] = {}
def map_agent_id_to_filename(agent_id: str) -> Optional[str]:
    if agent_id in AGENT_ID_TO_FILENAME_CACHE:
        return AGENT_ID_TO_FILENAME_CACHE[agent_id]
    try:
        if not os.path.isdir(BOT_CONFIG_DIR):
            print(f"❌ Dir agentes no encontrado: {BOT_CONFIG_DIR}"); return None
        for fn in os.listdir(BOT_CONFIG_DIR):
            if not fn.endswith(".json") or fn.startswith("_"):
                continue
            fp = os.path.join(BOT_CONFIG_DIR, fn)
            with open(fp, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("elevenlabs_agent_id") == agent_id:
                AGENT_ID_TO_FILENAME_CACHE[agent_id] = fn
                return fn
        print(f"❌ No se encontró config para agent_id={agent_id}")
        return None
    except Exception as e:
        print(f"💥 Error map_agent_id_to_filename: {e}"); return None

AGENT_USERNAME_TO_CONFIG_CACHE: Dict[str, Dict[str, Any]] = {}
def map_username_to_agent_data(username: str) -> Optional[Dict[str, Any]]:
    if username in AGENT_USERNAME_TO_CONFIG_CACHE:
        return AGENT_USERNAME_TO_CONFIG_CACHE[username]
    try:
        if not os.path.isdir(BOT_CONFIG_DIR):
            print(f"❌ Dir agentes no encontrado: {BOT_CONFIG_DIR}"); return None
        for fn in os.listdir(BOT_CONFIG_DIR):
            if not fn.endswith(".json") or fn.startswith("_"):
                continue
            fp = os.path.join(BOT_CONFIG_DIR, fn)
            with open(fp, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # El login del cliente en WP guarda agent_user
            if cfg.get("agent_user") == username:
                cfg["_bot_slug"] = fn.replace(".json", "")
                AGENT_USERNAME_TO_CONFIG_CACHE[username] = cfg
                return cfg
        print(f"❌ No se encontró config para agent_user={username}")
        return None
    except Exception as e:
        print(f"💥 Error map_username_to_agent_data: {e}"); return None

# --------- Normalización de eventos (webhook) ----------
def _normalize_event(data: Dict[str, Any]) -> Dict[str, Any]:
    root = data.get("data", data) if isinstance(data, dict) else {}
    agent_id = root.get("agent_id") or (root.get("agent") or {}).get("id") or data.get("agent_id")
    transcript_list = root.get("transcript") or root.get("transcription") or []
    transcript_text = ""
    if isinstance(transcript_list, list):
        try:
            transcript_text = " ".join(
                (t.get("message", "") or "").strip()
                for t in transcript_list if isinstance(t, dict) and t.get("role") == "user"
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
        "raw": data
    }

# --------- Webhook de eventos del agente (si lo usas) ----------
@app.post("/api/agent-event")
async def handle_agent_event(request: Request, elevenlabs_signature: str = Header(default=None, alias="elevenlabs-signature")):
    try:
        body_bytes = await request.body()
        if not SKIP_HMAC:
            sig_header = elevenlabs_signature or request.headers.get("elevenlabs-signature") or request.headers.get("ElevenLabs-Signature")
            if not _verify_hmac(HMAC_SECRET, body_bytes, sig_header):
                raise HTTPException(status_code=401, detail="Invalid HMAC.")
        else:
            print("⚠️ HMAC BYPASS")

        try:
            data = json.loads(body_bytes.decode("utf-8"))
        except Exception:
            data = await request.json()

        normalized = _normalize_event(data)
        agent_id = normalized.get("agent_id")
        if not agent_id:
            raise HTTPException(status_code=400, detail="Missing agent_id.")
        config_filename = map_agent_id_to_filename(agent_id)
        if not config_filename:
            raise HTTPException(status_code=404, detail=f"Config no encontrada para ID: {agent_id}")
        # Aquí podrías llamar a tu processor si lo usas.
        return JSONResponse(status_code=200, content={"status": "ok"})
    except HTTPException as http_err:
        return JSONResponse(status_code=http_err.status_code, content={"error": http_err.detail})
    except Exception as e:
        print(f"💥 Error webhook: {e}")
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": "internal_error", "detail": str(e)})

# ---------- Health/env ----------
@app.get("/_envcheck")
def envcheck():
    keys = ["ELEVENLABS_HMAC_SECRET","ELEVENLABS_SKIP_HMAC","XI_API_KEY","ELEVENLABS_API_KEY"]
    return {k: os.getenv(k) for k in keys}

# =================================================================
# === PANEL AGENTES: AUTH & MÉTRICAS ==============================
# =================================================================
AGENT_JWT_SECRET = (os.getenv("AGENT_JWT_SECRET") or HMAC_SECRET)
if not AGENT_JWT_SECRET:
    raise RuntimeError("❌ Falta AGENT_JWT_SECRET")
JWT_ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/agent/login")

class AgentDataRequest(BaseModel):
    start_date: str
    end_date: str

class Token(BaseModel):
    access_token: str
    token_type: str

class AgentData(BaseModel):
    bot_slug: str
    config: Dict[str, Any]

async def get_current_agent(token: str = Depends(oauth2_scheme)) -> AgentData:
    credentials_exception = HTTPException(status_code=401, detail="Credenciales inválidas", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, AGENT_JWT_SECRET, algorithms=[JWT_ALGORITHM])
        bot_slug: str = payload.get("sub")
        if bot_slug is None:
            print("❌ Token sin 'sub'"); raise credentials_exception
        bot_file_path = os.path.join(BOT_CONFIG_DIR, f"{bot_slug}.json")
        if not os.path.exists(bot_file_path):
            print(f"❌ No existe config: {bot_file_path}"); raise credentials_exception
        with open(bot_file_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
        return AgentData(bot_slug=bot_slug, config=config_data)
    except JWTError as e:
        print(f"❌ JWT inválido: {e}"); raise credentials_exception
    except Exception as e:
        print(f"💥 Error get_current_agent: {e}")
        traceback.print_exc()
        raise credentials_exception

# --------- Admin: Sync (para WP) ----------
@app.get("/admin/sync-agents")
async def admin_sync_agents():
    res = get_eleven_agents()
    if not res["ok"]:
        raise HTTPException(500, res["error"])
    l = []
    d = res.get("data")
    # Normalizamos a lista de {agent_id, name}
    if isinstance(d, dict) and isinstance(d.get("agents"), list):
        for a in d["agents"]:
            if isinstance(a, dict):
                l.append({"agent_id": a.get("agent_id"), "name": a.get("name")})
    elif isinstance(d, list):
        for a in d:
            if isinstance(a, dict):
                l.append({"agent_id": a.get("agent_id"), "name": a.get("name")})
    return JSONResponse({"ok": True, "data": l})

@app.get("/admin/sync-numbers")
async def admin_sync_numbers():
    res = get_eleven_phone_numbers()
    if not res["ok"]:
        raise HTTPException(500, res["error"])
    l = []
    d = res.get("data")
    pnd: List[Dict[str, Any]] = []
    if isinstance(d, dict) and isinstance(d.get("phone_numbers"), list):
        pnd = d["phone_numbers"]
    elif isinstance(d, list):
        pnd = d
    for n in pnd:
        if isinstance(n, dict):
            l.append({"phone_number_id": n.get("phone_number_id"), "phone_number": n.get("phone_number")})
    return JSONResponse({"ok": True, "data": l})

# --------- Login del agente (para shortcode WP) ----------
import bcrypt

@app.post("/agent/login", response_model=Token)
async def agent_login(form_data: OAuth2PasswordRequestForm = Depends()):
    un = form_data.username
    pw = form_data.password
    cfg = map_username_to_agent_data(un)
    if not cfg:
        print(f"Login fail user '{un}'"); raise HTTPException(401, "Credenciales inválidas")
    h = cfg.get("agent_pass_hash", "").encode("utf-8")
    try:
        if not bcrypt.checkpw(pw.encode("utf-8"), h):
            print(f"Login fail pass '{un}'"); raise HTTPException(401, "Credenciales inválidas")
    except ValueError:
        print(f"Hash inválido '{un}'"); raise HTTPException(500, "Error configuración de la cuenta")

    slug = cfg["_bot_slug"]
    pay = {"sub": slug, "iat": int(time.time()), "exp": int(time.time()) + (12 * 3600)}
    tok = jwt.encode(pay, AGENT_JWT_SECRET, algorithm=JWT_ALGORITHM)
    print(f"Login OK: {un} (slug={slug})")
    return {"access_token": tok, "token_type": "bearer"}

# --------- Datos de consumo (WP) ----------
@app.post("/agent/data")
async def get_agent_data(req: AgentDataRequest, agent: AgentData = Depends(get_current_agent)):
    cfg = agent.config
    aid = cfg.get("elevenlabs_agent_id")
    ph  = cfg.get("phone_number")
    name = cfg.get("name", agent.bot_slug)
    if not aid:
        raise HTTPException(400, "Falta agent_id en la config")

    # Fechas
    try:
        start_dt = datetime.strptime(req.start_date, "%Y-%m-%d")
        end_dt   = datetime.strptime(req.end_date, "%Y-%m-%d") + timedelta(days=1, seconds=-1)
        start_ts = int(time.mktime(start_dt.timetuple()))
        end_ts   = int(time.mktime(end_dt.timetuple()))
    except ValueError:
        raise HTTPException(400, "Fecha inválida YYYY-MM-DD")

    # Llamamos al servicio (ruta POST /v1/convai/analytics/agent)
    res = get_agent_consumption_data(agent_id=aid, start_unix_ts=start_ts, end_unix_ts=end_ts)
    if not res["ok"]:
        print(f"Error consumo: {res.get('error')}")
        calls, credits, minutes = 0, 0.0, 0.0
    else:
        d = res["data"]
        calls   = int(d.get("calls", 0))
        credits = float(d.get("credits", 0.0))
        minutes = float(d.get("duration_secs", 0.0)) / 60.0

    try:
        rate = float(os.getenv("ELEVENLABS_USD_PER_CREDIT", "0.0001"))
    except Exception:
        rate = 0.0001
    cost = credits * rate

    final = {
        "agent_name": name,
        "phone_number": ph,
        "calls": calls,
        "credits_consumed": credits,
        "total_cost_usd": cost
    }
    return JSONResponse({"ok": True, "data": final})

# --------- Llamada por lotes (WP) ----------
@app.post("/agent/start-batch-call")
async def handle_batch_call(
    agent: AgentData = Depends(get_current_agent),
    batch_name: str = Form(...),
    csv_file: UploadFile = File(...)
):
    bot_cfg = agent.config
    filename = (csv_file.filename or "").lower()
    if not filename.endswith((".csv", ".xls", ".xlsx")):
        raise HTTPException(400, "Formato no soportado. Usa .csv, .xls o .xlsx")

    # Claves correctas en tu config JSON:
    agent_id        = bot_cfg.get("elevenlabs_agent_id")
    phone_number_id = bot_cfg.get("elevenlabs_phone_number_id")  # <— CLAVE CORRECTA
    if not agent_id or not phone_number_id:
        raise HTTPException(400, "Faltan elevenlabs_agent_id o elevenlabs_phone_number_id en la config")

    recipients: List[Dict[str, Any]] = []
    try:
        content = await csv_file.read()
        buf = io.BytesIO(content)

        # Lee CSV/Excel
        if filename.endswith(".csv"):
            df = pd.read_csv(buf)
        else:
            df = pd.read_excel(buf)

        # Limpia encabezados: espacios/puntuación -> guiones bajos, minúsculas
        df.columns = [re.sub(r"\s+", "_", re.sub(r"[^\w\s]", "", str(c))).lower() for c in df.columns]

        # Detecta columna telefónica
        if "phone_number" not in df.columns:
            for cand in ["telefono", "teléfono", "numero", "número", "phone"]:
                if cand in df.columns:
                    df.rename(columns={cand: "phone_number"}, inplace=True)
                    break
        if "phone_number" not in df.columns:
            raise HTTPException(400, "El archivo debe tener columna 'phone_number' (o equivalente)")

        df = df.astype(str).fillna("")
        rows = df.to_dict(orient="records")

        for row in rows:
            phone = (row.get("phone_number") or "").strip()
            if not phone:
                continue

            # Flatten + normaliza claves para Eleven: name / last_name
            info = {"phone_number": phone}
            for k, v in row.items():
                if k == "phone_number":
                    continue
                val = str(v).strip()
                if not val:
                    continue
                clean_key = k.replace("_", "").lower()
                if clean_key == "name":
                    info["name"] = val
                elif clean_key == "lastname":
                    info["last_name"] = val
                else:
                    # otras variables personalizadas (en minúsculas sin underscores)
                    info[clean_key] = val
            recipients.append(info)

    except HTTPException:
        raise
    except Exception as e:
        print(f"💥 Error leyendo archivo: {e}")
        traceback.print_exc()
        raise HTTPException(400, f"Error al procesar el archivo: {e}")

    if not recipients:
        raise HTTPException(400, "Archivo sin destinatarios válidos")

    print(f"DEBUG batch '{batch_name}': sample={recipients[0] if recipients else None} total={len(recipients)}")
    result = start_batch_call(
        call_name=batch_name,
        agent_id=agent_id,
        phone_number_id=phone_number_id,
        recipients_json=recipients
    )
    if not result["ok"]:
        raise HTTPException(500, result["error"])
    return JSONResponse({"ok": True, "data": result["data"]})
