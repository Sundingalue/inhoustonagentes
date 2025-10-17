import requests
import json
import os 
# Nota: La librería 'requests' debe estar instalada (pip install requests)

# 1. *** CONFIGURACIÓN CRUCIAL: URL DE LA IMPLEMENTACIÓN 'VERSIÓN 11' (FINAL) ***
# Esta URL apunta a la última implementación con la corrección de fecha/hora.
WEBHOOK_URL = 'https://script.google.com/macros/s/AKfycbxbtKar4yyzWkD6DVnuf7bgG4SnuYsNZthOZFqVbOByIyF3P20iLI85wFfVns5zZVSDBA/exec'

# --- FUNCIÓN PRINCIPAL DE AGENDAMIENTO ---
def book_appointment(nombre, apellido, telefono, email, fechaCita, horaCita):
    """
    Función para enviar los datos del cliente al Webhook de Google.
    Esta función es la que llama tu lógica de ElevenLabs después de la confirmación.
    Retorna el resultado del Webhook de Google.
    """
    # 2. Construye el diccionario de datos.
    # Las claves son CRUCIALES y deben coincidir con Apps Script (nombre, apellido, etc.)
    datos_cliente = {
        "nombre": nombre,
        "apellido": apellido,
        "telefono": telefono,
        "email": email,
        "fechaCita": fechaCita, # Formato AAAA-MM-DD
        "horaCita": horaCita    # Formato HH:MM (24 horas)
    }

    print(f"--- [CALENDAR SERVICE] Intentando guardar datos de {nombre} {apellido} ---")
    
    # 3. Envía la solicitud POST al Webhook
    try:
        response = requests.post(
            WEBHOOK_URL,
            headers={'Content-Type': 'application/json'},
            data=json.dumps(datos_cliente)
        )

        response.raise_for_status() # Lanza error si hay un problema HTTP
        
        # Parseamos la respuesta de Google Sheets
        resultado = response.json()
        
        # El Apps Script corregido devuelve 'Datos guardados y cita creada correctamente.'
        if resultado.get('status') == 'success':
            print(f"✅ ÉXITO: {resultado.get('message')}")
        else:
            print(f"⚠️ ERROR de Apps Script: {resultado.get('message')}")
            
        return resultado
        
    except requests.exceptions.RequestException as e:
        print(f"❌ ERROR CRÍTICO de conexión o HTTP: {e}")
        return {"status": "error", "message": str(e)}

# --- FUNCIÓN DE PRUEBA (PARA EJECUTAR ESTE ARCHIVO DIRECTAMENTE) ---
if __name__ == '__main__':
    print("\n--- EJECUTANDO PRUEBA DIRECTA DE services/calendar_service.py ---")

    # Datos simulados que tu lógica de ElevenLabs ya capturó
    cliente_simulado = {
        "nombre": "Ricardo",
        "apellido": "Vazquez",
        "telefono": "555-4321",
        "email": "ricardo.vazquez@agente.com",
        "fechaCita": "2025-10-17", 
        "horaCita": "11:30"      
    }
    
    resultado_reserva = book_appointment(**cliente_simulado)
    
    print("---------------------------------------------------------")
    print(json.dumps(resultado_reserva, indent=4))
    print("---------------------------------------------------------")