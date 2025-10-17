# service_calendar_checker.py: Lógica para verificar la disponibilidad en Google Calendar
# Usando la autenticación de Variables de Entorno (Service Account JSON)

import json
import os 
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import pytz

# *** CONFIGURACIÓN CRÍTICA ***
# ⚠️ CAMBIO CLAVE: Leer la clave JSON completa desde una variable de entorno de Render.
SERVICE_ACCOUNT_JSON_ENV_VAR = 'GOOGLE_CALENDAR_SERVICE_ACCOUNT_JSON'

# Ámbito de solo lectura de disponibilidad (Free/Busy)
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# ID del calendario que quieres verificar (usa el correo electrónico del calendario)
CALENDAR_ID = 'infoinhoustontexas@gmail.com' 
TIMEZONE = 'America/Chicago' 

# --- Lógica de Servicio de Google ---

def get_calendar_service():
    """
    Inicializa y retorna el objeto de servicio de Google Calendar leyendo la clave de ENV VAR.
    """
    # Esta línea confirma que el código es el nuevo.
    print(f"🔄 Intentando cargar credenciales desde la variable de entorno: {SERVICE_ACCOUNT_JSON_ENV_VAR}") 
    try:
        # 1. Obtener la cadena JSON completa de la variable de entorno
        json_key_string = os.getenv(SERVICE_ACCOUNT_JSON_ENV_VAR)
        
        if not json_key_string:
            # Esto debería ser imposible si la variable existe en Render
            raise ValueError(f"❌ Falta la variable de entorno: {SERVICE_ACCOUNT_JSON_ENV_VAR}")
        
        # 2. Cargar la cadena JSON en un diccionario
        info = json.loads(json_key_string)
        
        # 3. Crear las credenciales a partir del diccionario
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        
        print("✅ Credenciales de Cuenta de Servicio cargadas con éxito desde ENV VAR.")
        return build('calendar', 'v3', credentials=creds)

    except Exception as e:
        print(f"❌ ¡ERROR CRÍTICO de autenticación! Falla al procesar la clave de servicio.")
        print(f"Detalle: {e}")
        raise

def check_availability(date_str: str, time_str: str) -> bool:
    """
    Verifica si una hora específica está disponible.
    """
    try:
        service = get_calendar_service()
    except Exception:
        print("❌ AGENDAMIENTO FALLIDO: Fallo de autenticación del servicio.")
        return False

    # 1. Parsear la fecha y hora de la cita
    try:
        dt_start_naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        print(f"❌ Error en formato de fecha/hora: {date_str} {time_str}")
        return False
    
    # 2. Asignar Zona Horaria
    tz = pytz.timezone(TIMEZONE)
    dt_start_aware = tz.localize(dt_start_naive)
    
    # 3. Calcular la hora de fin (asumimos 30 minutos)
    dt_end_aware = dt_start_aware + timedelta(minutes=30)

    # 4. Formatear a RFC3339 para la API de Google
    time_min = dt_start_aware.isoformat()
    time_max = dt_end_aware.isoformat()

    # 5. Llamada a la API Free/Busy
    body = {
        "timeMin": time_min,
        "timeMax": time_max,
        "items": [{"id": CALENDAR_ID}]
    }

    print(f"🔄 Consultando disponibilidad entre {time_min} y {time_max}...")
    
    try:
        events_result = service.freebusy().query(body=body).execute()
        calendar_busy = events_result.get('calendars', {}).get(CALENDAR_ID, {}).get('busy', [])

        if calendar_busy:
            print("❌ Horario OCUPADO.")
            return False
        else:
            print("✅ Horario DISPONIBLE.")
            return True

    except Exception as e:
        print(f"❌ Error al consultar Free/Busy: {e}")
        return False
