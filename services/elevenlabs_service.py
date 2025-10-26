import os
import requests
import json # Keep for potential future debugging if needed
import time

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVEN_API_BASE = "https://api.elevenlabs.io/v1"

# --- Tasa Fallback de Créditos por Segundo ---
DEFAULT_CREDITS_PER_SEC_FALLBACK = 10.73
try:
    FALLBACK_CREDITS_PER_SEC = float(os.getenv("ELEVENLABS_CREDITS_PER_SEC_FALLBACK", DEFAULT_CREDITS_PER_SEC_FALLBACK))
except ValueError:
    FALLBACK_CREDITS_PER_SEC = DEFAULT_CREDITS_PER_SEC_FALLBACK
print(f"[ElevenLabs] Using fallback credits/sec rate: {FALLBACK_CREDITS_PER_SEC}")

# --- Helper Function to make API requests ---
def _eleven_request(method, endpoint, payload=None, params=None):
    """Generic helper for ElevenLabs v1 API requests."""
    if not ELEVENLABS_API_KEY:
        print("[ElevenLabs] Error: API Key not configured.")
        return {"ok": False, "error": "API Key not configured"}
    url = f"{ELEVEN_API_BASE}{endpoint}"
    headers = {"Accept": "application/json", "xi-api-key": ELEVENLABS_API_KEY}
    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, params=params)
        elif method.upper() == "POST":
            headers["Content-Type"] = "application/json"
            response = requests.post(url, headers=headers, json=payload)
        else:
            return {"ok": False, "error": f"Unsupported HTTP method: {method}"}
        response.raise_for_status() # Raise exception for bad status codes (4xx or 5xx)
        return {"ok": True, "data": response.json()}
    except requests.exceptions.HTTPError as http_err:
        error_msg = http_err.response.text # Default error
        try: # Try to parse specific ElevenLabs error
            error_details = http_err.response.json()
            error_msg = error_details.get('detail', {}).get('message', error_msg)
        except json.JSONDecodeError:
             pass # Keep the raw text if JSON parsing fails
        print(f"[ElevenLabs] API Error (HTTP {http_err.response.status_code}): {error_msg}")
        return {"ok": False, "error": f"API Error: {error_msg}"}
    except requests.exceptions.RequestException as req_err:
        print(f"[ElevenLabs] Connection Error: {req_err}")
        return {"ok": False, "error": f"Connection error: {req_err}"}

# --- Functions for Admin Sync (Unchanged) ---
def get_eleven_agents():
    """Gets the list of agents from the account."""
    print("[ElevenLabs] Getting agent list...")
    return _eleven_request("GET", "/convai/agents")

def get_eleven_phone_numbers():
    """Gets the list of phone numbers from the account."""
    print("[ElevenLabs] Getting phone number list...")
    return _eleven_request("GET", "/convai/phone-numbers")

# ===================================================================
# === FINAL FUNCTION (Local Start Date Filtering, Fallback Credits) =
# ===================================================================
def get_agent_consumption_data(agent_id, start_unix_ts, end_unix_ts):
    """
    Gets consumption data using /conversations with pagination (cursor),
    API filtering only by end date, then filtering by start date locally.
    Calculates credits using fallback rate based on duration.
    """
    print(f"[EL] Getting conversations BEFORE {end_unix_ts} for Agent ID: {agent_id}...")
    endpoint = "/convai/conversations"
    all_conversations = [] # List to store all conversations before filtering

    # --- Pagination Loop ---
    has_more = True
    next_cursor = None
    page_num = 1
    max_pages = 50 # Safety limit

    while has_more and page_num <= max_pages:
        params = {"agent_id": agent_id, "page_size": 30}
        # Only send end date on the first request, use cursor after
        if not next_cursor:
            params["call_start_before_unix"] = int(end_unix_ts)
        else:
            params["cursor"] = next_cursor

        result = _eleven_request("GET", endpoint, params=params)

        if not result["ok"]:
            print(f"[EL] Error fetching page {page_num}: {result.get('error')}")
            # It's better to return accumulated data than nothing if a later page fails
            break

        data = result.get("data", {})
        conversations_page = data.get("conversations", [])

        if not conversations_page:
            # print(f"[EL] Page {page_num} empty. Assuming end of results.") # Optional log
            break

        all_conversations.extend(conversations_page)

        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor", None)

        if not has_more or not next_cursor:
            # print("[EL] API indicated end of pagination.") # Optional log
            break

        page_num += 1
    # --- End Pagination Loop ---

    if page_num > max_pages: print(f"[EL] WARN: Reached max pages limit ({max_pages}). Data might be incomplete.")
    print(f"[EL] Received {len(all_conversations)} conversations BEFORE local filtering.")

    # --- Local Filtering by Start Date ---
    filtered_conversations = []
    start_filter_ts_int = int(start_unix_ts)

    for convo in all_conversations:
         if isinstance(convo, dict):
             convo_start_value = convo.get("start_time_unix_secs") # Correct field name
             convo_start_num = None
             if convo_start_value is not None:
                 try: convo_start_num = int(float(convo_start_value)) # Convert safely
                 except (ValueError, TypeError): pass

             # Compare only if timestamp is valid
             if convo_start_num is not None and convo_start_num >= start_filter_ts_int:
                 filtered_conversations.append(convo)

    print(f"[EL] {len(filtered_conversations)} conversations AFTER filtering by start date >= {start_filter_ts_int}.")

    # --- Calculate Totals on Filtered List ---
    total_calls = 0
    total_credits = 0.0
    total_seconds = 0.0

    for convo in filtered_conversations:
        # Count only successful calls
        if convo.get('call_successful') == 'success':
            total_calls += 1
            # Get duration (check multiple possible field names)
            secs = float(convo.get("call_duration_secs", convo.get("duration_secs", 0.0)))
            total_seconds += secs
            # Calculate credits using fallback rate if duration > 0
            if secs > 0:
                calculated_credits = secs * FALLBACK_CREDITS_PER_SEC
                total_credits += calculated_credits

    print(f"[EL] Final Calculation: {total_calls} calls, {total_credits:.4f} credits (estimated).")

    # Prepare final result structure
    normalized_data = {
        "agent_id": agent_id,
        "calls": total_calls,
        "duration_secs": total_seconds,
        "credits": total_credits
    }
    return {"ok": True, "data": normalized_data}
# ===================================================================
# === END FINAL FUNCTION ============================================
# ===================================================================

# --- Function start_batch_call (Unchanged) ---
def start_batch_call(call_name, agent_id, phone_number_id, recipients_json):
    """Initiates a batch call."""
    print(f"[EL] Initiating batch call: {call_name} (Agent: {agent_id})")
    endpoint = "/convai/batch-calling/submit"
    payload = {
        "call_name": call_name,
        "agent_id": agent_id,
        "agent_phone_number_id": phone_number_id,
        "recipients": recipients_json
    }
    return _eleven_request("POST", endpoint, payload=payload)