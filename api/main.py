# processor.py: Contiene la lógica central para decidir qué hacer con un evento de ElevenLabs.
# El webhook principal (api/main.py) llama a la función process_agent_event.

import json
from typing import Dict, Any
import requests
from datetime import datetime
import re

# 🚨 IMPORTANTE: Asumimos que ELEVENLABS_API_KEY está en tus variables de entorno de Render
# Usamos esta clave para hacer llamadas a ElevenLabs si fuera necesario (ej. para obtener variables dinámicas).
# Aquí no la necesitamos directamente, pero la mantengo como buena práctica.
# ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY") 

# La URL de tu PROPIO endpoint de agendamiento en Render.
# Cuando el Webhook recibe el evento final de ElevenLabs, se llama a sí mismo
# internamente para activar la lógica de Google Calendar/Sheets.
# NOTA: En un entorno de producción, puedes usar la URL de Render, o simplemente
# llamar a las funciones de servicio directamente sin un HTTP request, que es más rápido.
# Para esta arquitectura, llamaremos directamente a la función si está definida.

# Para evitar una llamada HTTP innecesaria, llamaremos a la función principal directamente.
# Es necesario importar la función desde el archivo principal.
# Esta es una práctica avanzada. Para que funcione, necesitas refactorizar la función
# 'agendar_cita' en api/main.py para que sea una función síncrona simple que tome los datos.

# Por ahora, vamos a simular la lógica de detección de intención.

# --- FUNCIÓN DE UTILIDAD ---
def extract_client_data(transcript: str) -> Dict[str, str]:
    """
    Simula la extracción de datos del cliente a partir de la transcripción.
    En un caso real, la API de ElevenLabs te enviaría esto en el payload
    si usaras un 'Function Call' para la herramienta de agendamiento.
    """
    
    # Este es un placeholder muy simple. Asumimos que los datos están al final.
    # En un caso real, ElevenLabs te pasaría un JSON estructurado con estos datos.

    # 🚨 P L A C E H O L D E R 🚨
    # Reemplaza esto con el JSON estructurado que realmente recibirías de ElevenLabs.
    
    # ----------------------------------------------------------------------------------
    # ⚠️ ASUMIMOS ESTE PATRÓN DE SALIDA SIMPLE DE LA HERRAMIENTA DE ELEVENLABS: ⚠️
    # transcript: "...la cita se confirma para el 2025-11-20 a las 15:00 con Juan Pérez,
    # email: juan@ejemplo.com, telefono: 555-1234."
    # ----------------------------------------------------------------------------------

    # --- SIMULACIÓN DE EXTRACCIÓN (¡AJUSTAR SEGÚN EL JSON REAL!) ---
    # Esto busca patrones simples de fecha, hora, nombre, email, etc.
    
    data = {
        "nombre": "Cliente",
        "apellido": "Agendado",
        "telefono": "555-0000",
        "email": "agendado@inhouston.com",
        "fechaCita": "2025-10-30", # Fecha de prueba
        "horaCita": "10:30"      # Hora de prueba
    }

    # Ejemplo de regex para extraer una fecha AAAA-MM-DD
    date_match = re.search(r'\d{4}-\d{2}-\d{2}', transcript)
    if date_match:
        data['fechaCita'] = date_match.group(0)

    # Ejemplo de regex para extraer una hora HH:MM
    time_match = re.search(r'a las\s+(\d{1,2}:\d{2})', transcript)
    if time_match:
        data['horaCita'] = time_match.group(1)

    # Nota: La extracción de nombre/email es compleja por Regex. Se recomienda depender
    # del JSON estructurado que ElevenLabs debe proporcionar al final de la llamada.
    
    print("⚠️ Usando datos de cliente extraídos/simulados. ¡Asegúrate de usar el JSON de ElevenLabs!")
    
    return data

# --- FUNCIÓN PRINCIPAL DEL PROCESADOR ---
def process_agent_event(agent_name: str, normalized_event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Procesa un evento normalizado de ElevenLabs.
    """
    event_type = normalized_event["raw"].get("type")
    transcript = normalized_event.get("transcript_text", "")

    # 1. FILTRAR POR EVENTO DE FIN DE LLAMADA
    # Solo nos importa el evento que marca el final de la conversación (summary/close)
    if event_type not in ["conversation_closed", "call_summary"]:
        return {"action": "ignored", "reason": f"Event type {event_type} not relevant for final processing."}

    # 2. DETECTAR INTENCIÓN DE AGENDAMIENTO
    # Aquí buscamos una señal en el transcript o en el JSON de que la cita se confirmó.
    # Si ElevenLabs te da una señal CLARA (e.g., una variable 'appointment_booked: true'), úsala.
    
    # --- SIMULACIÓN DE INTENCIÓN ---
    if "cita confirmada" in transcript.lower() or "evento creado" in transcript.lower():
        print(f"✅ Intención de agendamiento detectada para el agente: {agent_name}")
        
        # 3. EXTRAER DATOS DEL CLIENTE
        client_data = extract_client_data(transcript)
        
        # 4. LLAMAR AL ENDPOINT LOCAL DE AGENDAMIENTO (api/main.py -> /agendar_cita)
        # NOTA TÉCNICA AVANZADA: En lugar de hacer una solicitud HTTP a tu propio servidor,
        # es mucho más eficiente importar y llamar a la función 'agendar_cita' directamente.
        # Para hacer esto, necesitamos que api/main.py exporte la función como síncrona.
        
        # *** MÉTODO RECOMENDADO: Llamada directa a la función (requiere un pequeño cambio en api/main.py) ***
        # Puesto que ya definimos la lógica de agendamiento en el otro archivo, 
        # vamos a implementar la llamada directa (la forma más rápida en FastAPI).
        
        try:
            # 🚨 Importación Circular: Esto fallará si no manejamos la dependencia correctamente.
            # La forma más segura es hacer la llamada HTTP a tu propio endpoint 'agendar_cita'.
            
            # Dirección de tu propio Webhook de Render
            # Debes reemplazar ESTA URL con la URL de tu servicio de Render
            RENDER_WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL") or "http://localhost:8000"
            API_ENDPOINT = f"{RENDER_WEBHOOK_URL}/agendar_cita"

            print(f"📡 Llamando a la API local de Agendamiento: {API_ENDPOINT}")
            
            response = requests.post(
                API_ENDPOINT,
                headers={'Content-Type': 'application/json'},
                data=json.dumps(client_data)
            )
            response.raise_for_status() # Lanza error si hay un problema HTTP
            
            api_result = response.json()
            return {"action": "booked", "result": api_result}
            
        except requests.exceptions.RequestException as e:
            return {"action": "error_booking", "detail": f"Fallo al llamar al endpoint de agendamiento: {e}"}
        except Exception as e:
            return {"action": "error_processing", "detail": str(e)}

    return {"action": "closed_no_booking", "reason": "Conversation closed without detected booking intent."}
