import os
import requests
import json # Sigue siendo útil para depuración futura si es necesario

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

# --- Constante de URL Base ---
ELEVEN_API_BASE = "https://api.elevenlabs.io/v1"


# ===================================================================
# === CÓDIGO EXISTENTE (No se ha modificado) =========================
# ===================================================================

def start_conversation_with_agent(agent_id, input_text=None):
    """
    Inicia una conversación con un agente de ElevenLabs usando su agent_id.
    Opcionalmente puede enviar un mensaje inicial (input_text).
    """
    url = f"https://api.elevenlabs.io/v1/agents/{agent_id}/conversation"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {}
    if input_text:
        payload["message"] = input_text
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        print("[ElevenLabs] Conversación iniciada exitosamente.")
        return response.json()
    else:
        print(f"[ElevenLabs] Error {response.status_code}: {response.text}")
        return {"error": f"Error al iniciar conversación con el agente ElevenLabs {agent_id}"}


# ===================================================================
# === NUEVAS FUNCIONES (Añadidas para Panel Agentes) ================
# ===================================================================

def _eleven_request(method, endpoint, payload=None, params=None):
    """
    Helper genérico para peticiones a la API v1 de ElevenLabs.
    """
    if not ELEVENLABS_API_KEY:
        print("[ElevenLabs] Error: ELEVENLABS_API_KEY no configurada en el servidor")
        return {"ok": False, "error": "ELEVENLABS_API_KEY no configurada en el servidor"}

    url = f"{ELEVEN_API_BASE}{endpoint}"
    headers = {
        "Accept": "application/json",
        "xi-api-key": ELEVENLABS_API_KEY
    }
    
    try:
        # Quitamos los DEBUG prints generales
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, params=params)
        elif method.upper() == "POST":
            headers["Content-Type"] = "application/json"
            response = requests.post(url, headers=headers, json=payload)
        else:
            return {"ok": False, "error": f"Método HTTP no soportado: {method}"}

        response.raise_for_status()
        return {"ok": True, "data": response.json()}
    
    except requests.exceptions.HTTPError as http_err:
        try:
            error_details = http_err.response.json()
            error_msg = error_details.get('detail', {}).get('message', http_err.response.text)
            print(f"[ElevenLabs] Error de API (HTTP {http_err.response.status_code}): {error_msg}")
            return {"ok": False, "error": f"Error de API: {error_msg}"}
        except:
            print(f"[ElevenLabs] Error de API (HTTP {http_err.response.status_code}): {http_err}")
            return {"ok": False, "error": f"Error HTTP: {http_err}"}
    except requests.exceptions.RequestException as req_err:
        print(f"[ElevenLabs] Error de Conexión: {req_err}")
        return {"ok": False, "error": f"Error de conexión: {req_err}"}

# --- 1. Funciones para Sincronización de Admin (WordPress) ---

def get_eleven_agents():
    """
    Obtiene la lista de todos los agentes de la cuenta.
    (API: GET /v1/convai/agents)
    """
    print("[ElevenLabs] Obteniendo lista de agentes...")
    return _eleven_request("GET", "/convai/agents")

def get_eleven_phone_numbers():
    """
    Obtiene la lista de todos los números de teléfono de la cuenta.
    (API: GET /v1/convai/phone-numbers)
    """
    print("[ElevenLabs] Obteniendo lista de números de teléfono...")
    return _eleven_request("GET", "/convai/phone-numbers")


# --- 2. Funciones para el Panel de Agente (Cliente) ---

# ===================================================================
# === FUNCIÓN FINAL (CON PAGINACIÓN CORRECTA Y BÚSQUEDA AMPLIA) =====
# ===================================================================
def get_agent_consumption_data(agent_id, start_unix, end_unix):
    """
    Obtiene los datos de consumo (llamadas, créditos, etc.) para UN agente 
    específico en un rango de fechas usando el endpoint /conversations
    con paginación correcta (cursor).
    """
    print(f"[ElevenLabs] Obteniendo TODAS las conversaciones para Agente ID: {agent_id}...")
    
    endpoint = "/convai/conversations"
    
    # Totales
    total_calls = 0
    total_credits = 0.0 # Usar float para créditos
    total_seconds = 0.0 # Usar float para segundos

    # Campos posibles para los créditos (basado en el plugin INH Billing)
    credit_field_candidates = [
        'credits', 'credit_cost', 'credits_used', 
        'llm_credits', 'cost_credits', 'total_credits',
        'credit_usage' # Mantenemos el que teníamos por si acaso
    ]
    
    # Control de paginación
    has_more = True
    next_cursor = None # Usaremos 'next_cursor' como en el PHP
    
    page_num = 1 
    max_pages = 50 # Límite de seguridad como en el PHP

    while has_more and page_num <= max_pages:
        print(f"[ElevenLabs] Solicitando página {page_num}...")
        params = {
            "agent_id": agent_id,
            "start_unix": int(start_unix),
            "end_unix": int(end_unix),
            "page_size": 30 # Tamaño de página común
        }
        
        # Añadir el cursor si existe (para páginas 2 en adelante)
        if next_cursor:
            params["cursor"] = next_cursor 
        
        # --- Hacer la llamada ---
        result = _eleven_request("GET", endpoint, params=params)
        
        if not result["ok"]:
            print(f"[ElevenLabs] Error al obtener la página {page_num}: {result.get('error')}")
            # Devolver el error pero con los totales acumulados hasta ahora
            # Esto es mejor que devolver un error completo si solo falló una página tardía
            break 

        # --- Procesar la página ---
        data = result.get("data", {})
        conversations_page = data.get("conversations", [])
        
        if not conversations_page:
            if page_num == 1:
                print(f"[ElevenLabs] No se encontraron conversaciones para {agent_id} en ese rango.")
            else:
                print(f"[ElevenLabs] Página {page_num} vacía. Fin de resultados.")
            break # Salir si no hay conversaciones

        # Imprimir la primera conversación de la primera página (útil si aún fallan los créditos)
        if page_num == 1 and conversations_page:
             try:
                 print("DEBUG: ================== PRIMERA CONVERSACIÓN (PAG 1) ==================")
                 print(json.dumps(conversations_page[0], indent=2))
                 print("DEBUG: =================================================================")
             except Exception as e:
                 print(f"DEBUG: Error al imprimir el objeto de conversación: {e}")

        # Sumar los totales de esta página
        found_credits_in_page = False # Para saber si necesitamos el fallback (aún no implementado)
        for convo in conversations_page:
            if isinstance(convo, dict):
                total_calls += 1
                
                # Buscar créditos en la lista ampliada de campos
                credits_found = 0.0
                for field_name in credit_field_candidates:
                    if field_name in convo and convo[field_name] is not None:
                        try:
                            credits_found = float(convo[field_name])
                            if credits_found > 0:
                                found_credits_in_page = True # Marcamos que encontramos al menos uno
                                break # Salir del bucle de campos si encontramos uno válido
                        except (ValueError, TypeError):
                            continue # Ignorar si el campo no es numérico
                
                total_credits += credits_found
                total_seconds += float(convo.get("duration_secs", convo.get("call_duration_secs", 0.0))) # También buscar en 'call_duration_secs'

        # --- Preparar el siguiente bucle ---
        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor", None) # Usar 'next_cursor'
        
        print(f"[ElevenLabs] Página {page_num} procesada. Total parcial: {total_calls} llamadas, {total_credits:.2f} créditos.")
        
        if not has_more or not next_cursor:
            print("[ElevenLabs] Fin de la paginación según la API.")
            break 
            
        page_num += 1

    # Fin del bucle while
    if page_num > max_pages:
        print(f"[ElevenLabs] ADVERTENCIA: Se alcanzó el límite de {max_pages} páginas. El total puede ser incompleto.")

    print(f"[ElevenLabs] Consumo total final calculado: {total_calls} llamadas, {total_credits:.4f} créditos.")
    
    normalized_data = {
        "agent_id": agent_id,
        "calls": total_calls,
        "duration_secs": total_seconds,
        "credits": total_credits # Devolver el valor float
    }
    return {"ok": True, "data": normalized_data}
# ===================================================================
# === FIN DE LA FUNCIÓN FINAL =======================================
# ===================================================================


def start_batch_call(call_name, agent_id, phone_number_id, recipients_json):
    """
    Inicia una nueva llamada por lotes.
    'recipients_json' debe ser una lista de dicts, ej: [{"phone_number": "+1..."}, ...]
    (API: POST /v1/convai/batch-calling/submit)
    """
    print(f"[ElevenLabs] Iniciando lote de llamadas: {call_name} (Agente: {agent_id})")
    
    endpoint = "/convai/batch-calling/submit"
    payload = {
        "call_name": call_name,
        "agent_id": agent_id,
        "agent_phone_number_id": phone_number_id,
        "recipients": recipients_json
    }
    return _eleven_request("POST", endpoint, payload=payload)