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

# =========================
# Configuración de CORS
# =========================
# ¡ESTO RESUELVE EL ERROR 405 OPTIONS!
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Permitir todos los orígenes (puedes restringirlo a tu URL de WordPress)
    allow_credentials=True,
    allow_methods=["*"], # Permitir todos los métodos (GET, POST, OPTIONS, etc.)
    allow_headers=["*"], # Permitir todas las cabeceras
)

print("✅ FastAPI cargado correctamente y esperando eventos de ElevenLabs…")

# =========================
# Config HMAC
# =========================
HMAC_SECRET = (os.getenv("ELEVENLABS_HMAC_SECRET") or "").strip()
SKIP_HMAC = (os.getenv("ELEVENLABS_SKIP_HMAC") or "false").strip().lower() == "true"

if not HMAC_SECRET and not SKIP_HMAC:
    raise RuntimeError("❌ Falta ELEVENLABS_HMAC_SECRET (o define ELEVENLABS_SKIP_HMAC=true para omitir).")

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
        print("✅ Cliente de Twilio configurado exitosamente.")
    except Exception as e:
        print(f"⚠️  ADVERTENCIA: Error al configurar cliente de Twilio: {e}")
else:
    print("⚠️  ADVERTENCIA: Faltan variables de entorno de Twilio. El SMS no funcionará.")

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

# --- NUEVO HELPER DE LOGIN (AÑADIDO) ---
AGENT_USERNAME_TO_CONFIG_CACHE: Dict[str, Dict[str, Any]] = {}

def map_username_to_agent_data(username: str) -> Optional[Dict[str, Any]]:
    """
    Busca la configuración completa del agente (ej. 'sundin.json') dado el 'agent_user'
    definido en el archivo de configuración.
    """

    # 1. Intentar encontrar en el caché
    if username in AGENT_USERNAME_TO_CONFIG_CACHE:
        return AGENT_USERNAME_TO_CONFIG_CACHE[username]

    # 2. Si no está en caché, recorrer los archivos del directorio
    try:
        if not os.path.isdir(BOT_CONFIG_DIR):
            print(f"❌ Directorio de agentes no encontrado. Ruta calculada: {BOT_CONFIG_DIR}")
            return None

        print(f"Buscando el 'agent_user' {username} en el directorio: {BOT_CONFIG_DIR}")

        for filename in os.listdir(BOT_CONFIG_DIR):
            if not filename.endswith(".json") or filename.startswith("_"):
                continue

            filepath = os.path.join(BOT_CONFIG_DIR, filename)

            with open(filepath, 'r', encoding='utf-8') as f:
                config: Dict[str, Any] = json.load(f)

                # 3. Comprobar si el 'agent_user' coincide (que crearemos en WordPress)
                if config.get("agent_user") == username:
                    # 4. Encontramos la coincidencia, guardamos en caché y devolvemos
                    # ¡Guardamos el slug (nombre de archivo) DENTRO de la config para usarlo!
                    config["_bot_slug"] = filename.replace(".json", "")
                    AGENT_USERNAME_TO_CONFIG_CACHE[username] = config
                    print(f"✅ Mapeo de usuario encontrado: {username} -> {filename}")
                    return config

        # Si el loop termina sin encontrarlo
        print(f"❌ No se encontró ningún archivo JSON con el 'agent_user' {username}.")
        return None

    except Exception as e:
        print(f"💥 Error al intentar mapear el usuario del agente: {e}")
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
# Webhook (Ruta Principal de ElevenLabs)
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


# =========================
# LÓGICA DE AGENDAMIENTO (Ruta de Prueba/Integración Final)
# =========================
# Las funciones de servicio deben estar disponibles en el entorno de Render
from services.calendar_checker import check_availability
from services.calendar_service import book_appointment

# Definición de la estructura de datos que esperamos para agendar
class CitaPayload(dict):
    """
    Clase simple para validar la estructura del JSON de entrada.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        required_keys = ["cliente_nombre", "fecha", "hora", "telefono"]
        if not all(k in self for k in required_keys):
            raise ValueError(f"Payload missing one of required keys: {required_keys}")

@app.post("/agendar_cita")
async def agendar_cita_endpoint(request: Request):
    """
    Endpoint que coordina la verificación de disponibilidad y la creación del evento.
    Simula la llamada final que haría la lógica de 'workflows/processor.py'.
    """
    try:
        # 1. Cargar y validar el JSON de la solicitud (Payload)
        try:
            payload = CitaPayload(await request.json())
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON format.")

        cliente_nombre = payload['cliente_nombre']
        fecha_str = payload['fecha']
        hora_str = payload['hora']

        # 2. Verificar Disponibilidad (Usando services/calendar_checker.py)
        print(f"🔄 Verificando disponibilidad para {cliente_nombre} en {fecha_str} a las {hora_str}...")

        is_available = check_availability(fecha_str, hora_str)

        if not is_available:
            return JSONResponse(
                status_code=409, # 409 Conflict - Recurso no disponible
                content={
                    "status": "failure",
                    "message": f"Horario no disponible: {fecha_str} a las {hora_str}.",
                }
            )

        # 3. Agendar Cita y Guardar Datos (Usando services/calendar_service.py - Webhook de Apps Script)
        print("✅ Disponible. Procediendo a agendar el evento mediante Apps Script Webhook...")

        # book_appointment requiere 6 campos, usamos placeholders para los no provistos en la prueba.
        book_result = book_appointment(
            nombre=cliente_nombre,
            apellido="N/A",         # Placeholder para el test
            telefono="N/A",         # Placeholder para el test
            email="test@webhook.com", # Placeholder para el test
            fechaCita=fecha_str,
            horaCita=hora_str
        )

        # 4. Analizar la Respuesta del Webhook de Apps Script
        if book_result.get('status') == 'success':
            success_message = f"Cita agendada con éxito para {cliente_nombre}. Mensaje de Apps Script: {book_result.get('message', 'Éxito.')}"
            print(f"🎉 Éxito: {success_message}")

            # --- INICIO: Enviar SMS de Confirmación con Twilio ---
            if twilio_configurado:
                try:
                    cliente_telefono = payload.get('telefono')
                    if cliente_telefono:
                        mensaje_sms = (
                            f"In Houston Texas: Hola {cliente_nombre}. "
                            f"Le confirmamos su cita para el {fecha_str} a las {hora_str}."
                        )

                        print(f"🔄 Enviando SMS de confirmación a {cliente_telefono}...")
                        message = twilio_client.messages.create(
                            body=mensaje_sms,
                            from_=TWILIO_PHONE_NUMBER,
                            to=cliente_telefono
                        )
                        print(f"✅ SMS enviado exitosamente. SID: {message.sid}")
                    else:
                        print("⚠️ No se encontró 'telefono' en el payload, no se puede enviar SMS.")

                except Exception as sms_error:
                    # Importante: Si falla el SMS, no detenemos todo. Solo lo registramos.
                    print(f"⚠️ Falló el envío de SMS, pero la cita FUE AGENDADA. Error: {sms_error}")
            # --- FIN: Enviar SMS de Confirmación con Twilio ---

            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "message": success_message,
                    "webhook_status": "Llamada a Apps Script exitosa",
                    "sheets_status": "Datos y cita guardados a través del Webhook de Apps Script."
                }
            )
        else:
             # Si el Apps Script falla o devuelve un error
            return JSONResponse(
                status_code=500,
                content={
                    "status": "failure",
                    "message": "Fallo al agendar la cita en Google Calendar/Sheets a través del Webhook.",
                    "details": book_result.get('message', 'Error desconocido del Webhook.')
                }
            )

    except HTTPException as http_err:
        return JSONResponse(status_code=http_err.status_code, content={"error": http_err.detail})
    except Exception as e:
        print(f"💥 Error grave en /agendar_cita: {e}")
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": "internal_error", "detail": str(e)})


# =================================================================
# === INICIO: LÓGICA DEL PANEL AGENTES (AÑADIDO) ==================
# =================================================================

# --- 1. Configuración de Autenticación (JWT para Agentes) ---
# Usamos el HMAC_SECRET como secreto del JWT, o uno nuevo si lo defines.
AGENT_JWT_SECRET = os.getenv("AGENT_JWT_SECRET", HMAC_SECRET)
JWT_ALGORITHM = "HS256"
# Este endpoint '/agent/login' lo crearemos más abajo
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/agent/login")

if not AGENT_JWT_SECRET:
    raise RuntimeError("❌ Falta AGENT_JWT_SECRET (o ELEVENLABS_HMAC_SECRET) para el login de agentes.")

# --- 2. Modelos de Datos (Pydantic) para FastAPI ---
class AgentDataRequest(BaseModel):
    start_date: str
    end_date: str

class Token(BaseModel):
    access_token: str
    token_type: str

class AgentData(BaseModel):
    bot_slug: str
    config: Dict[str, Any]


# --- 3. Dependencia de Autenticación (El "Guardia" de los Endpoints) ---
async def get_current_agent(token: str = Depends(oauth2_scheme)) -> AgentData:
    """
    Dependencia de FastAPI: Decodifica el token JWT, verifica que sea válido
    y devuelve la configuración del agente.
    """
    credentials_exception = HTTPException(
        status_code=401,
        detail="Credenciales inválidas",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, AGENT_JWT_SECRET, algorithms=[JWT_ALGORITHM])
        bot_slug: str = payload.get("sub") # "sub" (subject) es el bot_slug
        if bot_slug is None:
            raise credentials_exception

        # Volvemos a cargar la config del agente desde el slug (asegura datos frescos)
        # Usamos la ruta de tu variable global BOT_CONFIG_DIR
        bot_file_path = os.path.join(BOT_CONFIG_DIR, f"{bot_slug}.json")
        if not os.path.exists(bot_file_path):
            print(f"❌ Error de Token: No se encontró el archivo {bot_file_path} para el slug {bot_slug}")
            raise credentials_exception

        with open(bot_file_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)

        return AgentData(bot_slug=bot_slug, config=config_data)

    except JWTError:
        raise credentials_exception


# --- 4. Endpoints de Sincronización para Admin (WordPress) ---
# (Estos endpoints deben estar protegidos por tu autenticación de admin/bearer)
# (¡IMPORTANTE! Debes añadir tu propia seguridad a estos dos endpoints)

@app.get("/admin/sync-agents")
async def admin_sync_agents(
    # TODO: Añadir aquí tu dependencia de autenticación de admin
    # ej: admin_user: dict = Depends(get_current_admin_user)
):
    """
    Endpoint para que WordPress pida la lista de agentes de ElevenLabs.
    """
    result = get_eleven_agents()
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result["error"])

    # Extraemos solo lo que WordPress necesita: (name, agent_id)
    agents_list = []
    # Asegurarse que 'agents' existe y es una lista antes de iterar
    if isinstance(result.get("data"), dict) and isinstance(result["data"].get("agents"), list):
        agents_list = [
            {"agent_id": a.get("agent_id"), "name": a.get("name")}
            for a in result["data"].get("agents", []) if isinstance(a, dict)
        ]
    elif isinstance(result.get("data"), list): # Fallback por si la API cambia
         agents_list = [
            {"agent_id": a.get("agent_id"), "name": a.get("name")}
            for a in result["data"] if isinstance(a, dict)
        ]

    return JSONResponse(content={"ok": True, "data": agents_list})

@app.get("/admin/sync-numbers")
async def admin_sync_numbers(
    # TODO: Añadir aquí tu dependencia de autenticación de admin
):
    """
    Endpoint para que WordPress pida la lista de números de ElevenLabs.
    """
    result = get_eleven_phone_numbers()
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result["error"])

    # << CORRECCIÓN DEL ERROR AttributeError >>
    numbers_list = []
    # Verificar si 'data' es un diccionario y contiene 'phone_numbers' (estructura esperada)
    if isinstance(result.get("data"), dict) and isinstance(result["data"].get("phone_numbers"), list):
        phone_numbers_data = result["data"].get("phone_numbers", [])
    # Fallback: si 'data' es directamente la lista (menos probable según docs, pero más seguro)
    elif isinstance(result.get("data"), list):
        phone_numbers_data = result["data"]
    else:
        # Si la estructura no es la esperada, devolver lista vacía o error
        print(f"⚠️ Estructura inesperada en respuesta de /v1/convai/phone-numbers: {result.get('data')}")
        phone_numbers_data = [] # O podrías lanzar una HTTPException aquí

    # Iterar sobre la lista de números encontrada
    for n in phone_numbers_data:
        # Asegurarse que cada elemento 'n' es un diccionario antes de usar .get()
        if isinstance(n, dict):
            numbers_list.append({
                "phone_number_id": n.get("phone_number_id"),
                "phone_number": n.get("phone_number")
            })

    return JSONResponse(content={"ok": True, "data": numbers_list})


# --- 5. Endpoints del Panel Agentes (Cliente) ---

@app.post("/agent/login", response_model=Token)
async def agent_login(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Endpoint de login para el shortcode [panel_agentes].
    Usa el formato OAuth2 (username, password) que espera FastAPI.
    """
    username = form_data.username
    password = form_data.password

    # 1. Buscar al agente por su 'agent_user' usando nuestro nuevo helper
    agent_config = map_username_to_agent_data(username)

    if not agent_config:
        print(f"Login fallido: Usuario '{username}' no encontrado.")
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    # 2. Verificar la contraseña
    # (El 'agent_pass_hash' lo creará WordPress)
    stored_hash = agent_config.get('agent_pass_hash', '').encode('utf-8')

    try:
        # Usamos bcrypt para comparar el password con el hash
        if not bcrypt.checkpw(password.encode('utf-8'), stored_hash):
            print(f"Login fallido: Contraseña incorrecta para '{username}'.")
            raise HTTPException(status_code=401, detail="Credenciales inválidas")
    except ValueError:
        print(f"Login fallido: Hash de contraseña inválido o vacío para '{username}'.")
        raise HTTPException(status_code=500, detail="Error de configuración de cuenta")

    # 3. ¡Éxito! Crear y devolver un token
    bot_slug = agent_config["_bot_slug"] # El nombre de archivo (ej. 'sundin')

    # Creamos el token JWT
    payload = {
        "sub": bot_slug, # 'sub' (subject) es el estándar para el ID de usuario
        "iat": int(time.time()),
        "exp": int(time.time()) + (12 * 3600)  # Expira en 12 horas
    }
    access_token = jwt.encode(payload, AGENT_JWT_SECRET, algorithm=JWT_ALGORITHM)

    print(f"Login exitoso para: {username} (slug: {bot_slug})")
    return {"access_token": access_token, "token_type": "bearer"}


@app.post("/agent/data")
async def get_agent_data(
    request: AgentDataRequest,
    agent: AgentData = Depends(get_current_agent) # El "Guardia"
):
    """
    Endpoint seguro para obtener los datos de consumo del agente.
    El agente se identifica por el token JWT.
    """
    bot_config = agent.config

    # De tu JSON: "elevenlabs_agent_id"
    agent_id = bot_config.get('elevenlabs_agent_id')
    # 'phone_number' lo guardaremos en el JSON desde WordPress
    phone_number = bot_config.get('phone_number')
    # 'name' lo guardaremos en el JSON desde WordPress
    agent_name = bot_config.get('name', agent.bot_slug)
    
    # ==========================================================
    # === ¡CORRECCIÓN! Ya no buscamos api_key aquí =============
    # ==========================================================
    # api_key = bot_config.get('api_key') <-- ELIMINADO

    if not agent_id: # <-- CORREGIDO (solo revisa agent_id)
        raise HTTPException(status_code=400, detail="Agente no configurado para ElevenLabs (falta agent_id en el JSON)")

    # 2. Convertir fechas a Unix
    try:
        start_unix = int(time.mktime(datetime.strptime(request.start_date, '%Y-%m-%d').timetuple()))
        # Aseguramos que la fecha final sea al final del día (23:59:59)
        end_dt = datetime.strptime(request.end_date, '%Y-%m-%d') + timedelta(days=1, seconds=-1)
        end_unix = int(time.mktime(end_dt.timetuple()))
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de fecha inválido, usar YYYY-MM-DD")

    # 3. Consultar la API de consumo (¡Pasando la API Key del cliente!)
    # ==========================================================
    # === ¡CORRECCIÓN! Ya no pasamos client_api_key ===========
    # ==========================================================
    result = get_agent_consumption_data(
        agent_id=agent_id,
        start_unix=start_unix, 
        end_unix=end_unix
        # client_api_key=api_key <-- ELIMINADO
    )

    if not result["ok"]:
        # Si no hay datos (ej. agente no encontrado en reporte), devolvemos ceros
        # O si la API de ElevenLabs falla (como el 404 que vimos)
        print(f"Error al obtener datos de consumo: {result.get('error', 'Error desconocido')}")
        consumption_data = {"calls": 0, "credits": 0, "minutes": 0}
    else:
        # Usamos los campos normalizados de nuestro helper
        d = result["data"]
        consumption_data = {
            "calls": d.get("calls", 0),
            "credits": d.get("credits", 0),
            "minutes": d.get("duration_secs", 0) / 60
        }

    # 4. Devolver el JSON final
    # (¡IMPORTANTE! Debes añadir tu lógica de 'costo por crédito' aquí)
    # Por ahora usamos un valor fijo, pero deberías cargarlo desde .env
    try:
        usd_per_credit = float(os.getenv("ELEVENLABS_USD_PER_CREDIT", "0.0001"))
    except:
        usd_per_credit = 0.0001

    total_cost_usd = consumption_data["credits"] * usd_per_credit

    final_data = {
        "agent_name": agent_name,
        "phone_number": phone_number,
        "calls": consumption_data["calls"],
        "credits_consumed": consumption_data["credits"],
        "total_cost_usd": total_cost_usd
    }
    return JSONResponse(content={"ok": True, "data": final_data})


@app.post("/agent/start-batch-call")
async def handle_batch_call(
    agent: AgentData = Depends(get_current_agent), # El "Guardia"
    batch_name: str = Form(...),
    csv_file: UploadFile = File(...)
):
    """
    Endpoint seguro para iniciar un lote de llamadas.
    Recibe un formulario 'multipart/form-data'.
    """
    bot_config = agent.config

    # ==========================================================
    # === ¡CORRECCIÓN! Ya no buscamos api_key aquí =============
    # ==========================================================

    if not csv_file.filename.endswith('.csv'):
        # CORRECCIÓN: También aceptar .xls y .xlsx (Paso futuro, por ahora solo .csv)
        raise HTTPException(status_code=400, detail="Se requiere un archivo .csv válido")

    # 1. Leer la configuración del bot (para IDs y API Key)
    agent_id = bot_config.get('elevenlabs_agent_id')
    # 'eleven_phone_number_id' lo guardaremos en el JSON desde WordPress
    phone_number_id = bot_config.get('eleven_phone_number_id')
    # api_key = bot_config.get('api_key') # <-- ELIMINADO

    if not agent_id or not phone_number_id: # <-- CORREGIDO
        raise HTTPException(status_code=400, detail="Agente o número de teléfono no configurado en el JSON")

    # 2. Procesar el CSV y convertirlo a JSON para la API
    recipients = []
    try:
        # Leer el archivo CSV en memoria
        csv_data = (await csv_file.read()).decode("utf-8")
        csv_reader = csv.DictReader(io.StringIO(csv_data))

        for row in csv_reader:
            if 'phone_number' not in row:
                raise HTTPException(status_code=400, detail="El CSV debe contener una columna 'phone_number'")

            # (Aquí puedes añadir más variables dinámicas si las necesitas)
            recipients.append({"phone_number": row['phone_number']})

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error procesando el CSV: {e}")

    if not recipients:
        raise HTTPException(status_code=400, detail="El CSV no contiene destinatarios")

    # 3. Enviar la petición a ElevenLabs (¡Pasando la API Key del cliente!)
    # ==========================================================
    # === ¡CORRECCIÓN! Ya no pasamos client_api_key ===========
    # ==========================================================
    print(f"Iniciando lote para {agent.bot_slug} (Agente ID: {agent_id})")
    result = start_batch_call(
        batch_name=batch_name,
        agent_id=agent_id,
        phone_number_id=phone_number_id,
        recipients=recipients
        # client_api_key=api_key # <-- ELIMINADO
    )

    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result["error"])

    # ¡Éxito!
    return JSONResponse(content={"ok": True, "data": result["data"]})

# =================================================================
# === FIN: LÓGICA DEL PANEL AGENTES ===============================
# =================================================================