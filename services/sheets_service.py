import os
import json
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

def _get_creds():
    raw = os.getenv("GOOGLE_SHEETS_CREDENTIALS_JSON")
    if not raw:
        return None, "Falta GOOGLE_SHEETS_CREDENTIALS_JSON"
    try:
        info = json.loads(raw)
    except Exception as e:
        return None, f"Credenciales inválidas: {e}"
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return creds, None

def save_conversation(agent_id, data, sheet_id=None):
    creds, err = _get_creds()
    if err:
        return {"status": "error", "message": err}

    sid = sheet_id or data.get("sheet_id")
    if not sid:
        return {"status": "error", "message": "Falta sheet_id (ponlo en el JSON del agente)"}

    try:
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(sid)
        ws = sh.sheet1  # primera hoja

        # Cabecera si está vacía
        if not ws.acell("A1").value:
            ws.append_row(["timestamp", "agent_id", "event", "transcription", "raw_json"])

        ts = data.get("timestamp") or datetime.utcnow().isoformat()
        event = data.get("evento") or data.get("event") or "post_call"
        transcription = data.get("transcription", "")

        # Limitar raw para no exceder tamaño de celda
        raw_str = json.dumps(data, ensure_ascii=False)[:48000]

        ws.append_row([ts, agent_id, event, transcription, raw_str])
        return {"status": "ok", "message": "Fila añadida a Sheets", "timestamp": ts}
    except Exception as e:
        return {"status": "error", "message": f"Error escribiendo en Sheets: {e}"}
