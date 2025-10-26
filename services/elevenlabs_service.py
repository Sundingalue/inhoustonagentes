import os
import requests
import json # Importar JSON para imprimir bonito

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

    # (Tu código original usa una URL diferente, la respetamos)
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
        print(f"DEBUG: Enviando petición {method} a {url} con params: {params}") # DEBUG
        
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, params=params)
        elif method.upper() == "POST":
            headers["Content-Type"] = "application/json"
            response = requests.post(url, headers=headers, json=payload)
        else:
            return {"ok": False, "error": f"Método HTTP no soportado: {method}"}

        # Lanza un error si la petición falla (ej. 401, 404, 500)
        response.raise_for_status()
        
        print("DEBUG: Petición exitosa (200 OK)") # DEBUG
        return {"ok": True, "data": response.json()}
    
    except requests.exceptions.HTTPError as http_err:
        try:
            # Intentar decodificar el error específico de ElevenLabs
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
# === FUNCIÓN CON DETECTOR DE BUCLE =================================
# ===================================================================
def get_agent_consumption_data(agent_id, start_unix, end_unix):
    """
    Obtiene los datos de consumo (llamadas, créditos, etc.) para UN agente 
    específico en un rango de fechas.
    
    ¡CON DETECTOR DE BUCLE INFINITO!
    """
    print(f"[ElevenLabs] Obteniendo TODAS las conversaciones para Agente ID: {agent_id}...")
    
    endpoint = "/convai/conversations"
    
    # Totales
    total_calls = 0
    total_credits = 0
    total_seconds = 0
    
    # Control de paginación
    has_more = True
    last_convo_id_to_send = None # El cursor que ENVIAREMOS
    last_convo_id_received = None # El cursor que RECIBIMOS
    
    page_num = 1 # Para logging

    while has_more:
        print(f"DEBUG: Entrando al bucle, Página {page_num}")
        params = {
            "agent_id": agent_id,
            "start_unix": int(start_unix),
            "end_unix": int(end_unix),
            "page_size": 30 # (Lo ponemos explícito)
        }
        
        if last_convo_id_to_send:
            print(f"DEBUG: Pidiendo página CON 'after_conversation_id': {last_convo_id_to_send}")
            params["after_conversation_id"] = last_convo_id_to_send
        else:
            print("DEBUG: Pidiendo página SIN 'after_conversation_id' (es la primera)")
        
        # --- Hacer la llamada ---
        result = _eleven_request("GET", endpoint, params=params)
        
        if not result["ok"]:
            print(f"[ElevenLabs] Error al obtener la página {page_num}.")
            return result 

        # --- Procesar la página ---
        data = result.get("data", {})
        conversations_page = data.get("conversations", [])
        
        page_has_more = data.get("has_more", False)
        
        print(f"DEBUG: Página {page_num} recibida.")
        print(f"DEBUG: Conversaciones en esta página: {len(conversations_page)}")
        print(f"DEBUG: API dice 'has_more': {page_has_more}")
        
        
        # +++++ LÍNEA DE DEPURACIÓN CRÍTICA (AÚN LA NECESITAMOS) +++++
        if conversations_page and page_num == 1:
            try:
                print("DEBUG: ================== INICIO DE LA PRIMERA CONVERSACIÓN ==================")
                print(json.dumps(conversations_page[0], indent=2))
                print("DEBUG: =================== FIN DE LA PRIMERA CONVERSACIÓN ====================")
            except Exception as e:
                print(f"DEBUG: Error al imprimir el objeto de conversación: {e}")
        # +++++++++++++++++++++++++++++++++++++++
        
        
        if not conversations_page:
            if page_num == 1:
                print(f"[ElevenLabs] No se encontraron conversaciones para {agent_id} en ese rango.")
            else:
                print(f"[ElevenLabs] Página {page_num} vacía. Asumiendo fin de resultados.")
            break # Salir del bucle si no hay conversaciones (ya sea en la pág 1 o en una posterior)

        
        # Sumar los totales de esta página
        for convo in conversations_page:
            if isinstance(convo, dict):
                total_calls += 1
                credits = convo.get("credit_usage", convo.get("credits_used", convo.get("credit_cost", 0)))
                total_credits += credits
                total_seconds += convo.get("duration_secs", 0)

        # --- Preparar el siguiente bucle ---
        has_more = page_has_more # Actualizamos el 'has_more' del bucle
        
        # Obtener el ID de la última conversación de la lista actual
        last_item = conversations_page[-1]
        if isinstance(last_item, dict) and last_item.get("conversation_id"):
            last_convo_id_received = last_item.get("conversation_id")
        else:
            print("DEBUG: No se pudo encontrar 'conversation_id' en el último item. Saliendo.")
            break
            
        # ==========================================================
        # === ¡AQUÍ ESTÁ EL DETECTOR DE BUCLE INFINITO! ===========
        # ==========================================================
        if last_convo_id_received == last_convo_id_to_send:
            print(f"DEBUG: ¡BUCLE INFINITO DETECTADO! El ID del cursor recibido ({last_convo_id_received}) es igual al enviado. Saliendo.")
            break
        # ==========================================================
            
        last_convo_id_to_send = last_convo_id_received # Preparamos el cursor para la *siguiente* petición
        page_num += 1
        
        if not has_more:
            print("DEBUG: 'has_more' es False. Saliendo del bucle.")
            break
        
        print(f"[ElevenLabs] Página procesada. Total parcial: {total_calls} llamadas. Pidiendo siguiente página...")

    # Fin del bucle while
    print(f"[ElevenLabs] Consumo total (con paginación) calculado: {total_calls} llamadas, {total_credits} créditos.")
    
    normalized_data = {
        "agent_id": agent_id,
        "calls": total_calls,
        "duration_secs": total_seconds,
        "credits": total_credits
    }
    return {"ok": True, "data": normalized_data}
# ===================================================================
# === FIN DE LA FUNCIÓN CON DEPURACIÓN ==============================
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
        # "scheduled_time_unix": Opcional si quieres programarlo
    }
    return _eleven_request("POST", endpoint, payload=payload)