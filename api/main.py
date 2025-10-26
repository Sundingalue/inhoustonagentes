from fastapi import FastAPI, Request, Header, HTTPException, Depends, File, UploadFile, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware # <--- 1. AÑADIDO AQUÍ
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from jose import JWTError, jwt
from workflows.processor import process_agent_event
import hmac, hashlib, os, json, base64
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

# Importar las funciones del servicio que acabamos de añadir
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

# =========================
# Configuración de CORS
# =========================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"], 
)

print("✅ FastAPI cargado correctamente y esperando eventos de ElevenLabs…")

# =========================
# Config HMAC
# =========================
HMAC_SECRET = (os.getenv("ELEVENLABS_HMAC_SECRET") or "").strip()
SKIP_HMAC = (os.getenv("ELEVENLABS_SKIP_HMAC") or "false").strip().lower() == "true"
if not HMAC_SECRET and not SKIP_HMAC:
    raise RuntimeError("❌ Falta ELEVENLABS_HMAC_SECRET")

# =========================
# Config Twilio
# =========================
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')
twilio_client = None
twilio_configurado = False
if all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER]):
    try:
        twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        twilio_configurado = True
        print("✅ Cliente de Twilio configurado.")
    except Exception as e:
        print(f"⚠️ Error al configurar Twilio: {e}")
else:
    print("⚠️ Faltan variables de Twilio. SMS no funcionará.")

# =========================
# Lógica de Mapeo de Agentes y Helpers
# =========================
AGENT_ID_TO_FILENAME_CACHE: Dict[str, str] = {}
def map_agent_id_to_filename(agent_id: str) -> Optional[str]:
    # ... (código sin cambios) ...
    if agent_id in AGENT_ID_TO_FILENAME_CACHE: return AGENT_ID_TO_FILENAME_CACHE[agent_id]
    try:
        if not os.path.isdir(BOT_CONFIG_DIR): print(f"❌ Directorio agentes no encontrado: {BOT_CONFIG_DIR}"); return None
        print(f"Buscando Agent ID {agent_id} en: {BOT_CONFIG_DIR}")
        for filename in os.listdir(BOT_CONFIG_DIR):
            if not filename.endswith(".json") or filename.startswith("_"): continue
            filepath = os.path.join(BOT_CONFIG_DIR, filename)
            with open(filepath, 'r', encoding='utf-8') as f: config: Dict[str, Any] = json.load(f)
            if config.get("elevenlabs_agent_id") == agent_id:
                AGENT_ID_TO_FILENAME_CACHE[agent_id] = filename
                print(f"✅ Mapeo encontrado: {agent_id} -> {filename}"); return filename
        print(f"❌ No se encontró archivo JSON para {agent_id} en {BOT_CONFIG_DIR}."); return None
    except Exception as e: print(f"💥 Error mapeando agente: {e}"); return None

AGENT_USERNAME_TO_CONFIG_CACHE: Dict[str, Dict[str, Any]] = {}
def map_username_to_agent_data(username: str) -> Optional[Dict[str, Any]]:
    # ... (código sin cambios) ...
    if username in AGENT_USERNAME_TO_CONFIG_CACHE: return AGENT_USERNAME_TO_CONFIG_CACHE[username]
    try:
        if not os.path.isdir(BOT_CONFIG_DIR): print(f"❌ Directorio agentes no encontrado: {BOT_CONFIG_DIR}"); return None
        print(f"Buscando 'agent_user' {username} en: {BOT_CONFIG_DIR}")
        for filename in os.listdir(BOT_CONFIG_DIR):
            if not filename.endswith(".json") or filename.startswith("_"): continue
            filepath = os.path.join(BOT_CONFIG_DIR, filename)
            with open(filepath, 'r', encoding='utf-8') as f: config: Dict[str, Any] = json.load(f)
            if config.get("agent_user") == username:
                config["_bot_slug"] = filename.replace(".json", "")
                AGENT_USERNAME_TO_CONFIG_CACHE[username] = config
                print(f"✅ Mapeo usuario encontrado: {username} -> {filename}"); return config
        print(f"❌ No se encontró archivo JSON para 'agent_user' {username}."); return None
    except Exception as e: print(f"💥 Error mapeando usuario: {e}"); return None

def _verify_hmac(secret: str, body: bytes, sig_header: str) -> bool:
    # ... (código sin cambios) ...
    if not sig_header: print("🚨 No se recibió cabecera HMAC."); return False
    t, v0 = "", ""
    for part in [x.strip() for x in sig_header.split(",")]:
        if part.startswith("t="): t = part.split("=", 1)[1]
        if part.startswith("v0="): v0 = part.split("=", 1)[1]
    if not v0: print("🚨 HMAC sin v0."); return False
    body_txt = body.decode("utf-8", errors="ignore")
    expected_body = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    expected_t_body = hmac.new(secret.encode(), f"{t}.{body_txt}".encode(), hashlib.sha256).hexdigest()
    expected_tbody = hmac.new(secret.encode(), f"{t}{body_txt}".encode(), hashlib.sha256).hexdigest()
    if v0 in (expected_body, expected_t_body, expected_tbody): print("🔏 HMAC válido ✅"); return True
    print("🚨 HMAC inválido"); print(f"received: {sig_header}"); return False


def _normalize_event(data: Dict[str, Any]) -> Dict[str, Any]:
    # ... (código sin cambios) ...
    root = data.get("data", data) if isinstance(data, dict) else {}
    agent_id = (root.get("agent_id") or (root.get("agent") or {}).get("id") or data.get("agent_id") or None)
    transcript_list = root.get("transcript") or root.get("transcription") or []
    transcript_text = ""
    if isinstance(transcript_list, list):
        try: transcript_text = " ".join((t.get("message", "") or "").strip() for t in transcript_list if isinstance(t, dict) and t.get("role") == "user").strip()
        except Exception: transcript_text = ""
    elif isinstance(transcript_list, str): transcript_text = transcript_list.strip()
    caller = called = None
    try:
        client_data = root.get("conversation_initiation_client_data", {}) or {}
        dyn = client_data.get("dynamic_variables", {}) or {}
        caller = (dyn.get("system__caller_id") or "").strip() or None
        called = (dyn.get("system__called_number") or "").strip() or None
    except Exception: pass
    return {"agent_id": agent_id, "transcript_text": transcript_text, "caller": caller, "called": called, "timestamp": root.get("timestamp") or data.get("timestamp"), "raw": data}

# =========================
# Webhook y Endpoints de Agendamiento
# =========================
@app.post("/api/agent-event")
async def handle_agent_event(request: Request, elevenlabs_signature: str = Header(default=None, alias="elevenlabs-signature")):
    # ... (código sin cambios) ...
    try:
        body_bytes = await request.body()
        if not SKIP_HMAC:
            sig_header = (elevenlabs_signature or request.headers.get("elevenlabs-signature") or request.headers.get("ElevenLabs-Signature"))
            if not _verify_hmac(HMAC_SECRET, body_bytes, sig_header): raise HTTPException(status_code=401, detail="Invalid HMAC signature.")
        else: print("⚠️ HMAC BYPASS ACTIVADO")
        try: data = json.loads(body_bytes.decode("utf-8"))
        except Exception: data = await request.json()
        normalized = _normalize_event(data)
        agent_id = normalized.get("agent_id")
        if not agent_id: raise HTTPException(status_code=400, detail="Missing agent_id.")
        print(f"🚀 Procesando evento para ID: {agent_id}")
        config_filename = map_agent_id_to_filename(agent_id)
        if not config_filename: raise HTTPException(status_code=404, detail=f"Config file not found for ElevenLabs ID: {agent_id}")
        agent_name = config_filename.replace(".json", "")
        result = process_agent_event(agent_name, normalized)
        return JSONResponse(status_code=200, content={"status": "ok", "result": result})
    except HTTPException as http_err: return JSONResponse(status_code=http_err.status_code, content={"error": http_err.detail})
    except Exception as e: print(f"💥 Error webhook: {e}"); traceback.print_exc(); return JSONResponse(status_code=500, content={"error": "internal_error", "detail": str(e)})


@app.get("/_envcheck")
def envcheck():
    # ... (código sin cambios) ...
    keys = ["MAIL_FROM","MAIL_USERNAME","MAIL_PASSWORD","MAIL_HOST","MAIL_PORT","ELEVENLABS_HMAC_SECRET","ELEVENLABS_SKIP_HMAC"]
    return {k: os.getenv(k) for k in keys}


from services.calendar_checker import check_availability
from services.calendar_service import book_appointment
class CitaPayload(dict):
    # ... (código sin cambios) ...
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        required_keys = ["cliente_nombre", "fecha", "hora", "telefono"]
        if not all(k in self for k in required_keys): raise ValueError(f"Payload missing keys: {required_keys}")

@app.post("/agendar_cita")
async def agendar_cita_endpoint(request: Request):
    # ... (código sin cambios) ...
    try:
        try: payload = CitaPayload(await request.json())
        except ValueError as ve: raise HTTPException(status_code=400, detail=str(ve))
        except Exception: raise HTTPException(status_code=400, detail="Invalid JSON.")
        cliente_nombre = payload['cliente_nombre']; fecha_str = payload['fecha']; hora_str = payload['hora']
        print(f"🔄 Verificando disponibilidad para {cliente_nombre} en {fecha_str} a las {hora_str}...")
        is_available = check_availability(fecha_str, hora_str)
        if not is_available: return JSONResponse(status_code=409, content={"status": "failure", "message": f"Horario no disponible: {fecha_str} a las {hora_str}."})
        print("✅ Disponible. Agendando...")
        book_result = book_appointment(nombre=cliente_nombre, apellido="N/A", telefono="N/A", email="test@webhook.com", fechaCita=fecha_str, horaCita=hora_str)
        if book_result.get('status') == 'success':
            success_message = f"Cita agendada para {cliente_nombre}. Mensaje Apps Script: {book_result.get('message', 'Éxito.')}"
            print(f"🎉 Éxito: {success_message}")
            if twilio_configurado:
                try:
                    cliente_telefono = payload.get('telefono')
                    if cliente_telefono:
                        mensaje_sms = f"In Houston Texas: Hola {cliente_nombre}. Confirmamos su cita para el {fecha_str} a las {hora_str}."
                        print(f"🔄 Enviando SMS a {cliente_telefono}...")
                        message = twilio_client.messages.create(body=mensaje_sms, from_=TWILIO_PHONE_NUMBER, to=cliente_telefono)
                        print(f"✅ SMS enviado. SID: {message.sid}")
                    else: print("⚠️ No se encontró 'telefono', no se puede enviar SMS.")
                except Exception as sms_error: print(f"⚠️ Falló envío SMS (pero cita AGENDADA). Error: {sms_error}")
            return JSONResponse(status_code=200, content={"status": "success", "message": success_message})
        else: return JSONResponse(status_code=500, content={"status": "failure", "message": "Fallo al agendar.", "details": book_result.get('message', 'Error desconocido Webhook.')})
    except HTTPException as http_err: return JSONResponse(status_code=http_err.status_code, content={"error": http_err.detail})
    except Exception as e: print(f"💥 Error /agendar_cita: {e}"); traceback.print_exc(); return JSONResponse(status_code=500, content={"error": "internal_error", "detail": str(e)})


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
    # ... (código sin cambios) ...
    credentials_exception = HTTPException(status_code=401, detail="Credenciales inválidas", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, AGENT_JWT_SECRET, algorithms=[JWT_ALGORITHM])
        bot_slug: str = payload.get("sub") 
        if bot_slug is None: raise credentials_exception
        bot_file_path = os.path.join(BOT_CONFIG_DIR, f"{bot_slug}.json")
        if not os.path.exists(bot_file_path): print(f"❌ Error Token: No se encontró archivo {bot_file_path}"); raise credentials_exception
        with open(bot_file_path, 'r', encoding='utf-8') as f: config_data = json.load(f)
        return AgentData(bot_slug=bot_slug, config=config_data)
    except JWTError: raise credentials_exception


@app.get("/admin/sync-agents")
async def admin_sync_agents():
    # ... (código sin cambios) ...
    result = get_eleven_agents()
    if not result["ok"]: raise HTTPException(status_code=500, detail=result["error"])
    agents_list = []
    if isinstance(result.get("data"), dict) and isinstance(result["data"].get("agents"), list):
        agents_list = [{"agent_id": a.get("agent_id"), "name": a.get("name")} for a in result["data"].get("agents", []) if isinstance(a, dict)]
    elif isinstance(result.get("data"), list): agents_list = [{"agent_id": a.get("agent_id"), "name": a.get("name")} for a in result["data"] if isinstance(a, dict)]
    return JSONResponse(content={"ok": True, "data": agents_list})


@app.get("/admin/sync-numbers")
async def admin_sync_numbers():
    # ... (código sin cambios) ...
    result = get_eleven_phone_numbers()
    if not result["ok"]: raise HTTPException(status_code=500, detail=result["error"])
    numbers_list = []
    phone_numbers_data = []
    if isinstance(result.get("data"), dict) and isinstance(result["data"].get("phone_numbers"), list): phone_numbers_data = result["data"].get("phone_numbers", [])
    elif isinstance(result.get("data"), list): phone_numbers_data = result["data"]
    else: print(f"⚠️ Estructura inesperada en /phone-numbers: {result.get('data')}")
    for n in phone_numbers_data:
        if isinstance(n, dict): numbers_list.append({"phone_number_id": n.get("phone_number_id"), "phone_number": n.get("phone_number")})
    return JSONResponse(content={"ok": True, "data": numbers_list})


@app.post("/agent/login", response_model=Token)
async def agent_login(form_data: OAuth2PasswordRequestForm = Depends()):
    # ... (código sin cambios) ...
    username = form_data.username; password = form_data.password
    agent_config = map_username_to_agent_data(username)
    if not agent_config: print(f"Login fallido: Usuario '{username}' no encontrado."); raise HTTPException(status_code=401, detail="Credenciales inválidas")
    stored_hash = agent_config.get('agent_pass_hash', '').encode('utf-8')
    try:
        if not bcrypt.checkpw(password.encode('utf-8'), stored_hash): print(f"Login fallido: Contraseña incorrecta para '{username}'."); raise HTTPException(status_code=401, detail="Credenciales inválidas")
    except ValueError: print(f"Login fallido: Hash inválido para '{username}'."); raise HTTPException(status_code=500, detail="Error configuración cuenta")
    bot_slug = agent_config["_bot_slug"]
    payload = {"sub": bot_slug, "iat": int(time.time()), "exp": int(time.time()) + (12 * 3600)}
    access_token = jwt.encode(payload, AGENT_JWT_SECRET, algorithm=JWT_ALGORITHM)
    print(f"Login exitoso para: {username} (slug: {bot_slug})")
    return {"access_token": access_token, "token_type": "bearer"}


@app.post("/agent/data")
async def get_agent_data(request: AgentDataRequest, agent: AgentData = Depends(get_current_agent)):
    bot_config = agent.config
    agent_id = bot_config.get('elevenlabs_agent_id')
    phone_number = bot_config.get('phone_number')
    agent_name = bot_config.get('name', agent.bot_slug)
    
    if not agent_id: raise HTTPException(status_code=400, detail="Falta agent_id en JSON")

    try:
        # Calcular los timestamps Unix a partir de las fechas YYYY-MM-DD
        start_unix_timestamp = int(time.mktime(datetime.strptime(request.start_date, '%Y-%m-%d').timetuple()))
        end_dt = datetime.strptime(request.end_date, '%Y-%m-%d') + timedelta(days=1, seconds=-1)
        end_unix_timestamp = int(time.mktime(end_dt.timetuple()))
    except ValueError: raise HTTPException(status_code=400, detail="Formato fecha inválido YYYY-MM-DD")

    # ==========================================================
    # === ¡AQUÍ ESTÁ LA CORRECCIÓN DEL TypeError! ==============
    # ==========================================================
    # Llamar a la función del servicio con los nombres correctos que espera
    result = get_agent_consumption_data(
        agent_id=agent_id,
        start_unix_ts=start_unix_timestamp, # <-- CORREGIDO (usa _ts)
        end_unix_ts=end_unix_timestamp      # <-- CORREGIDO (usa _ts)
    )

    if not result["ok"]:
        print(f"Error al obtener datos de consumo: {result.get('error', 'Desconocido')}")
        consumption_data = {"calls": 0, "credits": 0, "minutes": 0}
    else:
        d = result["data"]
        # Asegurar división flotante para minutos
        consumption_data = {"calls": d.get("calls", 0), "credits": d.get("credits", 0.0), "minutes": d.get("duration_secs", 0.0) / 60.0} 

    try: usd_per_credit = float(os.getenv("ELEVENLABS_USD_PER_CREDIT", "0.0001"))
    except: usd_per_credit = 0.0001
    
    # Asegurar que 'credits' sea float antes de multiplicar
    total_cost_usd = float(consumption_data["credits"]) * usd_per_credit

    final_data = {
        "agent_name": agent_name,
        "phone_number": phone_number,
        "calls": consumption_data["calls"],
        "credits_consumed": consumption_data["credits"], 
        "total_cost_usd": total_cost_usd
    }
    return JSONResponse(content={"ok": True, "data": final_data})

@app.post("/agent/start-batch-call")
async def handle_batch_call(agent: AgentData = Depends(get_current_agent), batch_name: str = Form(...), csv_file: UploadFile = File(...)):
    # ... (código sin cambios, incluyendo la lógica pendiente de Excel) ...
    bot_config = agent.config
    if not csv_file.filename.lower().endswith(('.csv', '.xls', '.xlsx')): 
         raise HTTPException(status_code=400, detail="Se requiere un archivo .csv, .xls o .xlsx")
    agent_id = bot_config.get('elevenlabs_agent_id')
    phone_number_id = bot_config.get('eleven_phone_number_id')
    if not agent_id or not phone_number_id: raise HTTPException(status_code=400, detail="Falta agent_id o phone_number_id en JSON")
    recipients = []
    try:
        content = await csv_file.read()
        if csv_file.filename.lower().endswith('.csv'):
             csv_data = content.decode("utf-8")
             csv_reader = csv.DictReader(io.StringIO(csv_data))
             for row in csv_reader:
                 if 'phone_number' not in row: raise HTTPException(status_code=400, detail="CSV debe tener columna 'phone_number'")
                 recipients.append({"phone_number": row['phone_number']})
        else: raise HTTPException(status_code=400, detail="Lectura de Excel (.xls/.xlsx) aún no implementada.")
    except HTTPException as http_ex: raise http_ex 
    except Exception as e: raise HTTPException(status_code=400, detail=f"Error procesando archivo: {e}")
    if not recipients: raise HTTPException(status_code=400, detail="Archivo no contiene destinatarios")
    print(f"Iniciando lote para {agent.bot_slug} (Agente ID: {agent_id})")
    result = start_batch_call(batch_name=batch_name, agent_id=agent_id, phone_number_id=phone_number_id, recipients=recipients)
    if not result["ok"]: raise HTTPException(status_code=500, detail=result["error"])
    return JSONResponse(content={"ok": True, "data": result["data"]})

# =================================================================
# === FIN: LÓGICA DEL PANEL AGENTES ===============================
# =================================================================