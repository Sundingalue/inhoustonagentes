# service_account.py: Lógica para verificar la disponibilidad en Google Calendar
# Usando la autenticación de Cuenta de Servicio (Service Account)

import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import pytz
import os # <-- ¡NECESARIO AÑADIR ESTA LÍNEA!

# *** CONFIGURACIÓN CRÍTICA ***
# NOTA: Esta RUTA DE ARCHIVO debe coincidir EXACTAMENTE con el archivo que tienes en tu carpeta 'keys/'.
# ✅ CORRECCIÓN CLAVE: Usamos os.path.join para construir la ruta absoluta desde la raíz del proyecto.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) 
SERVICE_ACCOUNT_FILE = os.path.join(BASE_DIR, 'keys', 'service_key.json')


# Ámbito de solo lectura de disponibilidad (Free/Busy)
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# ID del calendario que quieres verificar (usa el correo electrónico del calendario)
CALENDAR_ID = 'infoinhoustontexas@gmail.com' 

# --- FUNCIÓN PRINCIPAL DE VERIFICACIÓN ---
def get_calendar_service():
    """Inicializa y retorna el servicio de Google Calendar."""
    print(f"🔄 Intentando cargar credenciales desde: {SERVICE_ACCOUNT_FILE}")
    try:
        # Carga las credenciales de la Cuenta de Servicio
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        
        # Construye el servicio de la API de Google Calendar
        service = build('calendar', 'v3', credentials=creds)
        return service
    except Exception as e:
        print(f"❌ ¡ERROR CRÍTICO de autenticación de Cuenta de Servicio! Verifica la ruta y los permisos del calendario.")
        # Mantenemos tu mensaje original y levantamos el error para que Render lo capture
        print(f"    Ruta esperada: {SERVICE_ACCOUNT_FILE}")
        print(f"    Detalle: {e}")
        # Es crucial levantar el error para detener el flujo si la clave falla
        raise


def check_availability(date_str, time_str):
    """
    Verifica si una hora específica en una fecha dada está ocupada.
    
    Args:
        date_str (str): Fecha en formato 'YYYY-MM-DD'.
        time_str (str): Hora en formato 'HH:MM' (24 horas).
        
    Returns:
        bool: True si está disponible, False si está ocupado o si hay un error.
    """
    try:
        service = get_calendar_service()
    except Exception:
        # Si get_calendar_service levanta un error (clave no encontrada, etc.)
        return False
    
    # 1. Definir la zona horaria (CRÍTICO para el calendario)
    timezone = pytz.timezone('America/Chicago') 

    # 2. Convertir la fecha y hora a objetos datetime con zona horaria
    try:
        # Crear el objeto datetime para el inicio del rango
        start_time_local = datetime.strptime(f'{date_str} {time_str}', '%Y-%m-%d %H:%M')
        start_time_tz = timezone.localize(start_time_local)
        
        # El rango de verificación es la hora de inicio + 30 minutos (tiempo típico de una cita)
        end_time_tz = start_time_tz + timedelta(minutes=30) 

        # Formato ISO 8601 requerido por la API de Google
        time_min = start_time_tz.isoformat()
        time_max = end_time_tz.isoformat()

    except ValueError as e:
        print(f"❌ ERROR: Formato de fecha/hora incorrecto: {e}")
        return False
        
    print(f"🔎 Buscando disponibilidad para: {date_str} a las {time_str}...")

    # 3. Llamada a la API Free/Busy
    try:
        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "items": [
                {"id": CALENDAR_ID}
            ]
        }

        # Ejecuta la consulta
        response = service.freebusy().query(body=body).execute()
        
        # 4. Analizar la respuesta
        # La clave 'busy' contiene una lista de franjas horarias ocupadas.
        busy_slots = response['calendars'][CALENDAR_ID].get('busy', [])
        
        if busy_slots:
            print("❌ Horario OCUPADO.")
            return False  # Ocupado
        else:
            print("✅ Horario DISPONIBLE.")
            return True   # Disponible (Libre)

    except Exception as e:
        print(f"❌ ERROR al consultar la API de Google Calendar: {e}")
        return False # Asumimos no disponible por seguridad

# --- FUNCIÓN DE PRUEBA (PARA EJECUTAR ESTE ARCHIVO DIRECTAMENTE) ---
if __name__ == '__main__':
    print("\n--- INICIANDO PRUEBA DE FUNCIÓN DE VERIFICACIÓN (CUENTA DE SERVICIO) ---")
    
    # Datos de prueba: Viernes, 18 de octubre de 2025, 10:00 AM
    FECHA_PRUEBA = "2025-10-18" 
    HORA_PRUEBA = "10:00"      

    # Ejecutar la verificación
    disponible = check_availability(FECHA_PRUEBA, HORA_PRUEBA)
    
    # Mostrar el resultado
    if disponible:
        print(f"\n✅ RESULTADO: El espacio del {FECHA_PRUEBA} a las {HORA_PRUEBA} está DISPONIBLE.")
    else:
        print(f"\n❌ RESULTADO: El espacio del {FECHA_PRUEBA} a las {HORA_PRUEBA} está OCUPADO o hubo un error.")
    
    print("---------------------------------------------------------")
