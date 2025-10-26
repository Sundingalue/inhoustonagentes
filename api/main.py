from fastapi import FastAPI, Request, Header, HTTPException, Depends, File, UploadFile, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from jose import JWTError, jwt
from workflows.processor import process_agent_event
import hmac, hashlib, os, json, base64, re
from dotenv import load_dotenv
from typing import Any, Dict, Optional, List
import traceback
from twilio.rest import Client
import bcrypt
import glob
from datetime import datetime, timedelta
import time
import io
import csv
import pandas as pd # Importar Pandas

# Importar las funciones del servicio
from services.elevenlabs_service import (
    get_eleven_agents,
    get_eleven_phone_numbers,
    get_agent_consumption_data,
    start_batch_call
)

# =========================
# Configuración del Directorio
# =========================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_CONFIG_DIR = os.path.join(SCRIPT_DIR, '..', 'agents')
BOT_CONFIG_DIR = os.path.abspath(BOT_CONFIG_DIR)

# =========================
# Cargar .env
# =========================
SECRET_ENV_PATH = "/etc/secrets/.env"
if os.path.exists(SECRET_ENV_PATH): load_dotenv(SECRET_ENV_PATH); print(f"✅ .env cargado desde {SECRET_ENV_PATH}")
else: load_dotenv(); print("⚠️ Usando .env local")

# =========================
# App y CORS
# =========================
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
print("✅ FastAPI cargado.")

# =========================
# Config HMAC y Twilio
# =========================
HMAC_SECRET = (os.getenv("ELEVENLABS_HMAC_SECRET") or "").strip()
SKIP_HMAC = (os.getenv("ELEVENLABS_SKIP_HMAC") or "false").strip().lower() == "true"
if not HMAC_SECRET and not SKIP_HMAC: raise RuntimeError("❌ Falta ELEVENLABS_HMAC_SECRET")

TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID'); TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN'); TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')
twilio_client = None; twilio_configurado = False
if all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER]):
    try: twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN); twilio_configurado = True; print("✅ Twilio configurado.")
    except Exception as e: print(f"⚠️ Error Twilio: {e}")
else: print("⚠️ Faltan variables Twilio.")

# =========================
# Lógica de Mapeo y Helpers
# =========================
AGENT_ID_TO_FILENAME_CACHE: Dict[str, str] = {}
def map_agent_id_to_filename(agent_id: str) -> Optional[str]:
    if agent_id in AGENT_ID_TO_FILENAME_CACHE: return AGENT_ID_TO_FILENAME_CACHE[agent_id]
    try:
        if not os.path.isdir(BOT_CONFIG_DIR): print(f"❌ Dir agentes no encontrado: {BOT_CONFIG_DIR}"); return None
        for filename in os.listdir(BOT_CONFIG_DIR):
            if not filename.endswith(".json") or filename.startswith("_"): continue
            filepath = os.path.join(BOT_CONFIG_DIR, filename)
            with open(filepath, 'r', encoding='utf-8') as f: config: Dict[str, Any] = json.load(f)
            if config.get("elevenlabs_agent_id") == agent_id:
                AGENT_ID_TO_FILENAME_CACHE[agent_id] = filename
                return filename
        print(f"❌ No se encontró archivo JSON para {agent_id}."); return None
    except Exception as e: print(f"💥 Error mapeando agente: {e}"); return None

AGENT_USERNAME_TO_CONFIG_CACHE: Dict[str, Dict[str, Any]] = {}
def map_username_to_agent_data(username: str) -> Optional[Dict[str, Any]]:
    if username in AGENT_USERNAME_TO_CONFIG_CACHE: return AGENT_USERNAME_TO_CONFIG_CACHE[username]
    try:
        if not os.path.isdir(BOT_CONFIG_DIR): print(f"❌ Dir agentes no encontrado: {BOT_CONFIG_DIR}"); return None
        for filename in os.listdir(BOT_CONFIG_DIR):
            if not filename.endswith(".json") or filename.startswith("_"): continue
            filepath = os.path.join(BOT_CONFIG_DIR, filename)
            with open(filepath, 'r', encoding='utf-8') as f: config: Dict[str, Any] = json.load(f)
            if config.get("agent_user") == username:
                config["_bot_slug"] = filename.replace(".json", "")
                AGENT_USERNAME_TO_CONFIG_CACHE[username] = config
                return config
        print(f"❌ No se encontró archivo JSON para 'agent_user' {username}."); return None
    except Exception as e: print(f"💥 Error mapeando usuario: {e}"); return None

def _verify_hmac(secret: str, body: bytes, sig_header: str) -> bool:
    if not sig_header: print("🚨 No HMAC header."); return False
    t, v0 = "", ""
    for part in [x.strip() for x in sig_header.split(",")]:
        if part.startswith("t="): t = part.split("=", 1)[1]
        if part.startswith("v0="): v0 = part.split("=", 1)[1]
    if not v0: print("🚨 HMAC no v0."); return False
    body_txt = body.decode("utf-8", errors="ignore")
    expected_body   = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    expected_t_body = hmac.new(secret.encode(), f"{t}.{body_txt}".encode(), hashlib.sha256).hexdigest()
    expected_tbody  = hmac.new(secret.encode(), f"{t}{body_txt}".encode(), hashlib.sha256).hexdigest()
    if v0 in (expected_body, expected_t_body, expected_tbody): return True
    print("🚨 HMAC inválido"); return False

def _normalize_event(data: Dict[str, Any]) -> Dict[str, Any]:
    root = data.get("data", data) if isinstance(data, dict) else {}
    agent_id = (root.get("agent_id") or (root.get("agent") or {}).get("id") or data.get("agent_id") or None)
    transcript_list = root.get("transcript") or root.get("transcription") or []
    transcript_text = ""
    if isinstance(transcript_list, list):
        try: transcript_text = " ".join((t.get("message", "") or "").strip() for t in transcript_list if isinstance(t, dict) and t.get("role") == "user").strip()
        except Exception: transcript_text = ""
    elif isinstance(transcript_list, str): transcript_text = transcript_list.strip()
    caller = called = None
    try: client_data = root.get("conversation_initiation_client_data", {}) or {}; dyn = client_data.get("dynamic_variables", {}) or {}; caller = (dyn.get("system__caller_id") or "").strip() or None; called = (dyn.get("system__called_number") or "").strip() or None
    except Exception: pass
    return {"agent_id": agent_id, "transcript_text": transcript_text, "caller": caller, "called": called, "timestamp": root.get("timestamp") or data.get("timestamp"), "raw": data}

# =========================
# Webhook y Endpoints Agendamiento
# =========================
@app.post("/api/agent-event")
async def handle_agent_event(request: Request, elevenlabs_signature: str = Header(default=None, alias="elevenlabs-signature")):
    sig_header = None 
    try:
        body_bytes = await request.body()
        if not SKIP_HMAC: 
            sig_header = (elevenlabs_signature or request.headers.get("elevenlabs-signature") or request.headers.get("ElevenLabs-Signature"));
            if not _verify_hmac(HMAC_SECRET, body_bytes, sig_header): 
                raise HTTPException(status_code=401, detail="Invalid HMAC.")
        else: print("⚠️ HMAC BYPASS")
        
        try: data = json.loads(body_bytes.decode("utf-8"))
        except Exception: data = await request.json()
        
        normalized = _normalize_event(data); agent_id = normalized.get("agent_id")
        if not agent_id: raise HTTPException(status_code=400, detail="Missing agent_id.")
        config_filename = map_agent_id_to_filename(agent_id)
        if not config_filename: raise HTTPException(status_code=404, detail=f"Config no encontrada para ID: {agent_id}")
        agent_name = config_filename.replace(".json", "")
        result = process_agent_event(agent_name, normalized)
        return JSONResponse(status_code=200, content={"status": "ok", "result": result})
    except HTTPException as http_err: return JSONResponse(status_code=http_err.status_code, content={"error": http_err.detail})
    except Exception as e: 
        print(f"💥 Error webhook: {e}"); 
        traceback.print_exc(); 
        return JSONResponse(status_code=500, content={"error": "internal_error", "detail": str(e)})

@app.get("/_envcheck")
def envcheck():
    keys = ["MAIL_FROM","MAIL_USERNAME","MAIL_PASSWORD","MAIL_HOST","MAIL_PORT","ELEVENLABS_HMAC_SECRET","ELEVENLABS_SKIP_HMAC"]
    return {k: os.getenv(k) for k in keys}

from services.calendar_checker import check_availability
from services.calendar_service import book_appointment

class CitaPayload(dict):
    """Clase simple para validar la estructura del JSON de entrada."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        required = ["cliente_nombre", "fecha", "hora", "telefono"]
        if not all(k in self for k in required):
             raise ValueError(f"Faltan keys: {required}")

@app.post("/agendar_cita")
async def agendar_cita_endpoint(request: Request):
    """Endpoint que coordina la verificación de disponibilidad y la creación del evento."""
    try:
        try: payload = CitaPayload(await request.json())
        except ValueError as ve: raise HTTPException(status_code=400, detail=str(ve))
        except Exception: raise HTTPException(status_code=400, detail="Invalid JSON.")
        cn=payload['cliente_nombre']; fs=payload['fecha']; hs=payload['hora']
        print(f"🔄 Verificando disponibilidad {cn} {fs} {hs}...")
        if not check_availability(fs, hs): return JSONResponse(status_code=409, content={"status":"failure","message":f"No disponible: {fs} {hs}."})
        print("✅ Disponible. Agendando..."); book_result = book_appointment(nombre=cn, apellido="N/A", telefono="N/A", email="test@web.com", fechaCita=fs, horaCita=hs)
        if book_result.get('status') == 'success':
            msg = f"Cita agendada {cn}. {book_result.get('message','Éxito.')}" ; print(f"🎉 {msg}")
            if twilio_configurado:
                try: 
                    tel = payload.get('telefono')
                    if tel: sms = f"In Houston: Hola {cn}. Confirmamos cita {fs} {hs}."; print(f"🔄 SMS a {tel}..."); m = twilio_client.messages.create(body=sms, from_=TWILIO_PHONE_NUMBER, to=tel); print(f"✅ SMS SID: {m.sid}")
                    else: print("⚠️ No tel para SMS.")
                except Exception as sms_error: print(f"⚠️ Falló envío SMS (CITA AGENDADA). Error: {sms_error}")
            return JSONResponse(status_code=200, content={"status":"success", "message":msg})
        else: return JSONResponse(status_code=500, content={"status":"failure", "message":"Fallo al agendar.", "details": book_result.get('message','?')})
    except HTTPException as h: return JSONResponse(status_code=h.status_code, content={"error":h.detail})
    except Exception as e: print(f"💥 Error /agendar_cita: {e}"); traceback.print_exc(); return JSONResponse(status_code=500, content={"error":"internal_error", "detail":str(e)})

# =================================================================
# === INICIO: LÓGICA DEL PANEL AGENTES ============================
# =================================================================
AGENT_JWT_SECRET = os.getenv("AGENT_JWT_SECRET", HMAC_SECRET)
JWT_ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/agent/login")
if not AGENT_JWT_SECRET: raise RuntimeError("❌ Falta AGENT_JWT_SECRET")

class AgentDataRequest(BaseModel): start_date: str; end_date: str
class Token(BaseModel): access_token: str; token_type: str
class AgentData(BaseModel): bot_slug: str; config: Dict[str, Any]

async def get_current_agent(token: str = Depends(oauth2_scheme)) -> AgentData:
    credentials_exception = HTTPException(status_code=401, detail="Credenciales inválidas", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, AGENT_JWT_SECRET, algorithms=[JWT_ALGORITHM])
        bot_slug: str = payload.get("sub")
        if bot_slug is None: print("❌ Error Token: No 'sub'"); raise credentials_exception
        bot_file_path = os.path.join(BOT_CONFIG_DIR, f"{bot_slug}.json")
        if not os.path.exists(bot_file_path): print(f"❌ Error Token: No archivo {bot_file_path}"); raise credentials_exception
        with open(bot_file_path, 'r', encoding='utf-8') as f: config_data = json.load(f)
        return AgentData(bot_slug=bot_slug, config=config_data)
    except JWTError as e: print(f"❌ Error Token: JWT inválido. {e}"); raise credentials_exception
    except Exception as e: print(f"💥 Error get_current_agent: {e}"); traceback.print_exc(); raise credentials_exception

@app.get("/admin/sync-agents")
async def admin_sync_agents():
    res = get_eleven_agents();
    if not res["ok"]: raise HTTPException(500, res["error"])
    l = []; d = res.get("data")
    if isinstance(d,dict) and isinstance(d.get("agents"),list): l=[{"id":a.get("agent_id"),"n":a.get("name")} for a in d.get("agents",[]) if isinstance(a,dict)]
    elif isinstance(d,list): l=[{"id":a.get("agent_id"),"n":a.get("name")} for a in d if isinstance(a,dict)]
    return JSONResponse({"ok":True, "data":l})

@app.get("/admin/sync-numbers")
async def admin_sync_numbers():
    res = get_eleven_phone_numbers();
    if not res["ok"]: raise HTTPException(500, res["error"])
    l = []; d = res.get("data"); pnd = []
    if isinstance(d,dict) and isinstance(d.get("phone_numbers"),list): pnd=d.get("phone_numbers",[])
    elif isinstance(d,list): pnd=d
    else: print(f"⚠️ Estructura /numbers: {d}")
    for n in pnd:
        if isinstance(n,dict): l.append({"id":n.get("phone_number_id"), "n":n.get("phone_number")})
    return JSONResponse({"ok":True, "data":l})

@app.post("/agent/login", response_model=Token)
async def agent_login(form_data: OAuth2PasswordRequestForm = Depends()):
    un=form_data.username; pw=form_data.password; cfg=map_username_to_agent_data(un)
    if not cfg: print(f"Login fail: user '{un}'?"); raise HTTPException(401,"Credenciales inválidas")
    h = cfg.get('agent_pass_hash','').encode('utf-8')
    try:
        if not bcrypt.checkpw(pw.encode('utf-8'),h): print(f"Login fail: pass '{un}'?"); raise HTTPException(401,"Credenciales inválidas")
    except ValueError: print(f"Login fail: hash '{un}'?"); raise HTTPException(500,"Error config cuenta")
    slug = cfg["_bot_slug"]; pay={"sub":slug,"iat":int(time.time()),"exp":int(time.time())+(12*3600)}
    tok = jwt.encode(pay,AGENT_JWT_SECRET,algorithm=JWT_ALGORITHM)
    print(f"Login OK: {un} (slug: {slug})")
    return {"access_token":tok, "token_type":"bearer"}

@app.post("/agent/data")
async def get_agent_data(request: AgentDataRequest, agent: AgentData = Depends(get_current_agent)):
    cfg = agent.config; aid = cfg.get('elevenlabs_agent_id'); ph=cfg.get('phone_number'); name=cfg.get('name',agent.bot_slug)
    if not aid: raise HTTPException(400, "Falta agent_id en JSON")
    try: start_ts = int(time.mktime(datetime.strptime(request.start_date,'%Y-%m-%d').timetuple())); end_dt=datetime.strptime(request.end_date,'%Y-%m-%d')+timedelta(days=1,seconds=-1); end_ts=int(time.mktime(end_dt.timetuple()))
    except ValueError: raise HTTPException(400, "Fecha inválida YYYY-MM-DD")
    res = get_agent_consumption_data(agent_id=aid, start_unix_ts=start_ts, end_unix_ts=end_ts)
    if not res["ok"]: print(f"Error consumo: {res.get('error','?')}"); cd={"calls":0,"credits":0,"minutes":0}
    else: d=res["data"]; cd={"calls":d.get("calls",0),"credits":d.get("credits",0.0),"minutes":d.get("duration_secs",0.0)/60.0}
    try: rate = float(os.getenv("ELEVENLABS_USD_PER_CREDIT","0.0001"))
    except: rate = 0.0001
    cost = float(cd["credits"]) * rate
    final = {"agent_name":name,"phone_number":ph,"calls":cd["calls"],"credits_consumed":cd["credits"],"total_cost_usd":cost}
    return JSONResponse({"ok":True, "data":final})

# ==========================================================
# === MODIFICACIÓN PARA VARIABLES DINÁMICAS (CORREGIDO) ===
# ==========================================================
@app.post("/agent/start-batch-call")
async def handle_batch_call(
    agent: AgentData = Depends(get_current_agent),
    batch_name: str = Form(...),
    csv_file: UploadFile = File(...)
):
    bot_config = agent.config; filename = csv_file.filename.lower()
    allowed_extensions = ('.csv', '.xls', '.xlsx')
    if not filename.endswith(allowed_extensions): raise HTTPException(400, f"Formato no soportado. Usar {', '.join(allowed_extensions)}")
    agent_id = bot_config.get('elevenlabs_agent_id'); phone_number_id = bot_config.get('eleven_phone_number_id')
    if not agent_id or not phone_number_id: raise HTTPException(400, "Falta agent_id o phone_number_id en JSON")
    recipients = []
    try:
        content = await csv_file.read(); file_like_object = io.BytesIO(content); df = None
        if filename.endswith('.csv'): df = pd.read_csv(file_like_object)
        elif filename.endswith(('.xls', '.xlsx')): df = pd.read_excel(file_like_object)
        if df is None: raise ValueError("No se pudo leer archivo con pandas.")
        
        df.columns = [re.sub(r'\s+', '_', re.sub(r'[^\w\s]', '', col)).lower() for col in df.columns]

        if 'phone_number' not in df.columns:
            phone_col_candidates = ['telefono', 'teléfono', 'numero', 'número', 'phone']
            for col in df.columns:
                if col in phone_col_candidates:
                    df.rename(columns={col: 'phone_number'}, inplace=True)
                    break
            if 'phone_number' not in df.columns:
                 raise HTTPException(400, "El archivo debe contener una columna 'phone_number' (o similar)")

        df = df.astype(str)
        df.fillna('', inplace=True)
        recipients_data = df.to_dict(orient='records')

        for row_dict in recipients_data:
            phone = str(row_dict.get('phone_number', '')).strip()
            if not phone: continue

            # --- CORRECCIÓN CLAVE: DES-ANIDAR VARIABLES ---
            # El objeto del destinatario es la fila completa, incluyendo nombre y apellido
            recipient_info = {
                'phone_number': phone
            }
            # Añadir todas las demás columnas a nivel superior
            for key, value in row_dict.items():
                if key != 'phone_number':
                    recipient_info[str(key)] = str(value)
            
            recipients.append(recipient_info)
            # --- FIN CORRECCIÓN CLAVE ---


    except HTTPException as http_ex: raise http_ex
    except Exception as e: print(f"💥 Error procesando archivo: {e}"); traceback.print_exc(); raise HTTPException(400, f"Error al procesar el archivo: {e}")
    if not recipients: raise HTTPException(400, "Archivo no contiene destinatarios válidos")
    
    # *** Log para verificar la estructura que se envía ***
    print(f"DEBUG: Enviando muestra de destinatario a la API: {recipients[0] if recipients else 'None'}")
    
    print(f"Iniciando lote '{batch_name}' para {agent.bot_slug} ({len(recipients)} dest.)")
    result = start_batch_call(
        call_name=batch_name,
        agent_id=agent_id,
        phone_number_id=phone_number_id,
        recipients_json=recipients # Ahora recipients es la lista de objetos des-anidados
    )
    if not result["ok"]: raise HTTPException(500, result["error"])
    return JSONResponse({"ok": True, "data": result["data"]})

# =================================================================
# === FIN: LÓGICA DEL PANEL AGENTES ===============================
# =================================================================