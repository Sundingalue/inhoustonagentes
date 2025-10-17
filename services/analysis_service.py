# services/analysis_service.py: Servicio para extraer entidades (datos del cliente) de la transcripción
import os
import json
import requests
from typing import List, Dict, Any

# --- CONFIGURACIÓN DE LA API DE GEMINI ---
API_KEY = os.getenv('GEMINI_API_KEY', '')
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent"

# Esquema de respuesta JSON que esperamos de Gemini
# Usaremos esto para forzar a que la salida sea un objeto estructurado.
CUSTOMER_DATA_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "cliente_nombre_completo": {"type": "STRING", "description": "Nombre y Apellido completo del cliente."},
        "cliente_telefono": {"type": "STRING", "description": "Número de teléfono del cliente."},
        "cliente_email": {"type": "STRING", "description": "Dirección de correo electrónico del cliente."},
        "fecha_cita_iso": {"type": "STRING", "description": "Fecha de la cita en formato YYYY-MM-DD (Ej: 2025-10-30)."},
        "hora_cita_24h": {"type": "STRING", "description": "Hora de la cita en formato HH:MM (24 horas, Ej: 17:00)."},
        "cliente_direccion": {"type": "STRING", "description": "Dirección física (calle, número, ciudad) proporcionada por el cliente."}
    },
    "required": ["cliente_nombre_completo", "cliente_telefono", "cliente_email", "fecha_cita_iso", "hora_cita_24h"]
}

def build_gemini_payload(transcript: List[Dict[str, str]]) -> Dict[str, Any]:
    """Construye el payload para la llamada a la API de Gemini."""

    # 1. Convertir el transcript a un formato de texto legible para el modelo
    conversation_text = "\n".join([f"<{item['role'].upper()}>: {item['message']}" for item in transcript])

    # 2. Definir el prompt de la tarea para el modelo
    system_prompt = (
        "Eres un extractor de datos de alta precisión. Tu tarea es analizar la siguiente "
        "transcripción de una conversación telefónica entre un agente y un cliente. "
        "Extrae todos los datos de contacto y la información de la cita. "
        "Si la fecha u hora no están explícitas, infiere la fecha u hora mencionada en el último turno de la conversación. "
        "Debes responder **EXCLUSIVAMENTE** con un objeto JSON que siga el esquema proporcionado. "
        "Si un campo no se menciona, usa una cadena vacía ('')."
    )

    user_query = (
        f"Analiza la siguiente transcripción y extrae los datos de contacto y de la cita:\n\n"
        f"--- TRANSCRIPCIÓN ---\n{conversation_text}\n"
        f"---------------------\n\n"
        f"Genera el objeto JSON con las claves solicitadas."
    )

    payload = {
        "contents": [{"parts": [{"text": user_query}]}],
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": CUSTOMER_DATA_SCHEMA
        }
    }
    return payload

def extract_customer_data(transcript: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Llama a la API de Gemini para extraer los datos estructurados del cliente.
    Retorna un diccionario de datos o un diccionario vacío en caso de error.
    """
    if not API_KEY:
        print("❌ ERROR: La clave GEMINI_API_KEY no está configurada.")
        return {}

    payload = build_gemini_payload(transcript)
    headers = {'Content-Type': 'application/json'}

    try:
        print("🔄 Llamando a Gemini para la extracción de datos...")
        response = requests.post(f"{API_URL}?key={API_KEY}", headers=headers, data=json.dumps(payload))
        response.raise_for_status()  # Lanza una excepción para errores 4xx/5xx

        # La respuesta de Gemini es una cadena JSON dentro de un campo de texto
        result = response.json()
        
        # Extracción segura del JSON generado por el modelo
        json_string = result['candidates'][0]['content']['parts'][0]['text']
        
        # Parseo del JSON
        customer_data = json.loads(json_string)
        print("✅ Extracción de datos de cliente completada por Gemini.")
        return customer_data

    except requests.exceptions.RequestException as e:
        print(f"❌ Error en la solicitud a la API de Gemini: {e}")
    except KeyError:
        print(f"❌ Error: La estructura de respuesta de Gemini fue inesperada.")
    except json.JSONDecodeError:
        print(f"❌ Error: La respuesta no pudo ser parseada como JSON: {json_string}")
    except Exception as e:
        print(f"❌ Error desconocido durante la extracción de datos: {e}")
        
    return {}

# --- Función de prueba para debugging ---
if __name__ == '__main__':
    # Ejemplo de transcripción de llamada real
    test_transcript = [
        {"role": "agent", "message": "Hola, gracias por llamar a In Houston. ¿Con quién tengo el gusto?"},
        {"role": "user", "message": "Soy Juan Pérez y me gustaría agendar la cita."},
        {"role": "agent", "message": "¿Podría confirmarme su email y teléfono?"},
        {"role": "user", "message": "Claro, mi correo es juan.perez@ejemplo.com y mi teléfono es 555-123-4567. Y vivo en calle Falsa 123, Springfield."},
        {"role": "agent", "message": "Perfecto, ¿y la cita sería mañana a las 10 de la mañana?"},
        {"role": "user", "message": "Sí, mañana a las 10:00 AM. 2025-10-18."},
        {"role": "user", "message": "AGENDAR_CITA_CONFIRMADA"} 
    ]
    
    # NOTA: Debes configurar la GEMINI_API_KEY en tu entorno local para que esta prueba funcione.
    # Por ejemplo: export GEMINI_API_KEY='TU_CLAVE_AQUI'
    
    extracted = extract_customer_data(test_transcript)
    print("\n--- DATOS EXTRAÍDOS ---")
    print(json.dumps(extracted, indent=2))
