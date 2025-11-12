# services/email_service.py
import os
import re
import json
import smtplib
import requests
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# =========================
# Constantes y rutas
# =========================
ZOHO_API_DOMAIN = os.getenv("ZOHO_API_DOMAIN", "https://www.zohoapis.com").rstrip("/")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS_DIR = os.path.join(BASE_DIR, "agents")

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates")
EMAIL_TEMPLATE_PATH = os.path.join(TEMPLATE_DIR, "email_summary.html")

# =========================
# Config por compatibilidad (enviar ubicación por correo)
# =========================
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.zoho.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")  # compat
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")  # compat
SENDER_NAME = os.getenv("SENDER_NAME", "In Houston Texas")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", SMTP_USER)  # compat

# =========================
# Utils
# =========================
def extract_email_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
    return m.group(0).lower() if m else None

def get_agent_address(agent_slug: str) -> Optional[str]:
    try:
        agent_path = os.path.join(AGENTS_DIR, f"{agent_slug}.json")
        if not os.path.exists(agent_path):
            print(f"⚠️ No se encontró {agent_path}")
            return None
        with open(agent_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        loc = data.get("location", {}) or {}
        return loc.get("maps_url") or loc.get("address")
    except Exception as e:
        print(f"❌ Error leyendo JSON de {agent_slug}: {e}")
        return None

def send_email_to_client(conversation_text: str, agent_name: str = "sundin") -> bool:
    """Función de cortesía (envía link de ubicación). Usa SMTP_USER como From."""
    client_email = extract_email_from_text(conversation_text)
    if not client_email:
        print("⚠️ No se encontró ningún correo en la conversación.")
        return False

    maps_link = get_agent_address(agent_name)
    if not maps_link:
        print("⚠️ No se encontró el link de Maps en el JSON.")
        return False

    subject = f"Ubicación de la oficina - {SENDER_NAME}"
    body_html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333">
      <p>Hola 👋,</p>
      <p>Gracias por comunicarte con <b>{SENDER_NAME}</b>.</p>
      <p>Aquí tienes la ubicación:</p>
      <p><a href="{maps_link}" target="_blank">Ver en Google Maps</a></p>
      <br><p>Atentamente,<br><b>{agent_name.title()}</b></p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL or SMTP_USER}>"
    msg["To"] = client_email
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=20) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASSWORD)
            s.send_message(msg)
        print(f"✅ Correo enviado a {client_email}")
        return True
    except Exception as e:
        print(f"❌ Error al enviar correo al cliente: {e}")
        return False

# =========================
# Renderizado de la plantilla de resumen
# =========================
def _escape_html(s: str) -> str:
    return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def _render_transcript_blocks(transcript_list: List[Dict[str, Any]]) -> Tuple[str, str]:
    lines_txt, blocks_html = [], []
    AGENT_COLOR = "#333333"
    USER_COLOR = "#4d4d4d"

    for item in (transcript_list or []):
        role = (item.get("role") or "user").lower()
        msg = (item.get("message") or "").strip()
        if not msg:
            continue
        ts = item.get("timestamp") or datetime.now().strftime("%H:%M")
        caller_number = item.get("caller_number") or "Desconocido"
        who_label = "Agente" if role == "agent" else "Cliente"
        bubble_color = AGENT_COLOR if role == "agent" else USER_COLOR
        text_color = "#F7BD02" if role == "agent" else "#f0f0f0"
        align_dir = "right" if role == "agent" else "left"
        bubble_radius = "18px 18px 0px 18px" if role == "agent" else "18px 18px 18px 0px"
        meta_detail = "" if role == "agent" else caller_number

        lines_txt.append(f"[{ts}] {who_label} ({meta_detail}): {msg}")

        html_block = f"""
        <table width="100%" cellspacing="0" cellpadding="0" style="margin-bottom:15px">
          <tr><td align="{align_dir}">
            <table cellspacing="0" cellpadding="0"><tr>
              <td style="background:{bubble_color};color:{text_color};padding:10px 15px;border-radius:{bubble_radius};font-size:14px;line-height:1.4;max-width:80%;word-wrap:break-word;">
                {_escape_html(msg)}
              </td>
            </tr>
            <tr><td align="{align_dir}" style="padding-top:5px;font-size:10px;color:#999">
              {_escape_html(who_label)} · {_escape_html(meta_detail)} · {ts}
            </td></tr></table>
          </td></tr>
        </table>
        """
        blocks_html.append(html_block)

    conv_blocks = "\n".join(blocks_html) or '<div style="color:#999;text-align:center;padding:20px 0">No hay mensajes detallados.</div>'
    return "\n".join(lines_txt), conv_blocks

def _render_email_template(agent_name: str, caller_number: str, transcript_list: List[Dict[str, Any]]) -> Tuple[str, str]:
    call_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not os.path.exists(EMAIL_TEMPLATE_PATH):
        return "(Plantilla no encontrada)", f"Error: Plantilla HTML no encontrada. Transcripción: {transcript_list}"
    try:
        with open(EMAIL_TEMPLATE_PATH, "r", encoding="utf-8") as f:
            html = f.read()
        text_plain_transcript, blocks_html = _render_transcript_blocks(transcript_list)
        html = (html
                .replace("{{ agent_name }}", _escape_html(agent_name or "—"))
                .replace("{{ caller_number }}", _escape_html(caller_number or "—"))
                .replace("{{ call_time }}", _escape_html(call_time))
                .replace("{{ conversation_blocks_html }}", blocks_html))
        text_plain = (
            "RESUMEN DE LLAMADA\n"
            "--------------------------\n"
            f"Agente: {agent_name or '—'}\n"
            f"Número de Contacto: {caller_number or '—'}\n\n"
            f"TRANSCRIPCIÓN:\n{text_plain_transcript}\n"
            "--------------------------"
        )
        return text_plain, html
    except Exception as e:
        print(f"❌ Error al renderizar plantilla: {e}")
        return f"Error de renderizado: {e}", "Error de renderizado HTML"

def _extract_conversation_turns(event_data: Dict[str, Any]) -> List[Dict[str, str]]:
    raw = event_data.get("raw") or {}
    root = raw.get("data", raw) if isinstance(raw, dict) else {}
    turns = root.get("transcript") or root.get("transcription") or []
    caller_num = event_data.get("caller") or "Desconocido"
    agent_id = event_data.get("agent_id") or "Agente"

    if not isinstance(turns, list):
        txt = event_data.get("transcript_text") or event_data.get("transcription") or "(Conversación no disponible)"
        return [{'role': 'user', 'message': txt, 'caller_number': caller_num, 'agent_name': agent_id}]

    out = []
    for t in turns:
        if isinstance(t, dict) and t.get("message"):
            role = (t.get("role") or "unknown").lower()
            if role == "client":
                role = "user"
            out.append({
                "role": role,
                "message": t["message"].strip(),
                "timestamp": t.get("timestamp") or datetime.now().strftime("%H:%M"),
                "caller_number": caller_num,
                "agent_name": agent_id
            })
    return out

def _get_agent_name_from_config(agent_name_key: str) -> str:
    return (agent_name_key or "").capitalize() or "Agente"

# =========================
# Envío: SMTP y Zoho API (corregidos y endurecidos)
# =========================
def _send_via_smtp(email_cfg: dict, email_content: Dict[str, str]) -> dict:
    smtp_host = os.getenv("MAIL_HOST", "smtp.zoho.com")
    smtp_port = int(os.getenv("MAIL_PORT", "587"))

    # Buzón con el que AUTENTICAS
    username = (os.getenv("MAIL_USERNAME") or os.getenv("SMTP_USER") or "").strip()
    password = (os.getenv("MAIL_PASSWORD") or os.getenv("SMTP_PASSWORD") or "").strip()

    # From efectivo (permite override explícito)
    forced_from = (os.getenv("MAIL_FROM_OVERRIDE") or username).strip()

    # El "from" del agente (JSON) se usa como Reply-To
    cfg_from = (email_cfg.get("from") or "").strip()
    reply_to = cfg_from if cfg_from and cfg_from.lower() != forced_from.lower() else None

    if not forced_from or not username or not password:
        return {"status": "error", "message": "SMTP incompleto: MAIL_USERNAME/MAIL_PASSWORD faltan"}

    to_addr = email_cfg.get("to")
    subject = f"📞 Conversación {email_content['agent_name']} | Contacto: {email_content['caller_number']}"

    msg = MIMEMultipart("alternative")
    msg["From"] = forced_from
    msg["To"] = to_addr
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to

    msg.attach(MIMEText(email_content["plain"], "plain", "utf-8"))
    msg.attach(MIMEText(email_content["html"], "html", "utf-8"))

    try:
        print(f"📡 SMTP → To={to_addr} | From={forced_from} | Reply-To={reply_to} | AuthUser={username}")
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.starttls()
            server.login(username, password)
            server.send_message(msg)
        print("✅ SMTP OK")
        return {"status": "ok", "provider": "smtp", "to": to_addr, "subject": subject}
    except Exception as e:
        print(f"❌ SMTP FAIL: {e}")
        return {"status": "error", "provider": "smtp", "message": str(e)}

def _zoho_headers(token: str) -> dict:
    return {"Authorization": f"Zoho-oauthtoken {token}"}

def _maybe_refresh_token() -> str:
    refresh = os.getenv("ZOHO_REFRESH_TOKEN", "").strip()
    client_id = os.getenv("ZOHO_CLIENT_ID", "").strip()
    client_secret = os.getenv("ZOHO_CLIENT_SECRET", "").strip()
    if not (refresh and client_id and client_secret):
        return ""
    try:
        resp = requests.post(
            f"{ZOHO_API_DOMAIN}/oauth/v2/token",
            data={
                "refresh_token": refresh,
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
            },
            timeout=10,
        )
        data = resp.json()
        token = data.get("access_token", "")
        if token:
            os.environ["ZOHO_ACCESS_TOKEN"] = token
            print("♻️ Token Zoho renovado correctamente.")
            return token
    except Exception as e:
        print(f"⚠️ Error al refrescar token Zoho: {e}")
    return ""

def _get_zoho_account_id(access_token: str) -> str:
    try:
        r = requests.get(f"{ZOHO_API_DOMAIN}/mail/v2/accounts", headers=_zoho_headers(access_token), timeout=10)
        accounts = (r.json().get("data") or [])
        if not accounts:
            return ""
        for acc in accounts:
            if acc.get("isPrimary"):
                return str(acc.get("accountId"))
        return str(accounts[0].get("accountId", ""))
    except Exception as e:
        print(f"⚠️ Error obteniendo cuentas Zoho: {e}")
        return ""

def _send_via_zoho_api(email_cfg: dict, email_content: Dict[str, str]) -> dict:
    token = (os.getenv("ZOHO_ACCESS_TOKEN") or "").strip()
    if not token:
        return {"status": "error", "message": "ZOHO_ACCESS_TOKEN no configurado"}

    acc_id = _get_zoho_account_id(token)
    if not acc_id:
        token = _maybe_refresh_token()
        if not token:
            return {"status": "error", "message": "No se pudo obtener account_id de Zoho"}
        acc_id = _get_zoho_account_id(token)

    forced_from = (os.getenv("MAIL_USERNAME") or os.getenv("MAIL_FROM") or "").strip()
    cfg_from = (email_cfg.get("from") or "").strip()
    reply_to = cfg_from if cfg_from and cfg_from.lower() != forced_from.lower() else None

    to_addr = email_cfg.get("to")
    subject = f"📞 Conversación {email_content['agent_name']} | Contacto: {email_content['caller_number']}"

    url = f"{ZOHO_API_DOMAIN}/mail/v2/accounts/{acc_id}/messages"
    data = {
        "fromAddress": forced_from,
        "toAddress": to_addr,
        "subject": subject,
        "content": email_content["html"],
        "mailFormat": "html",
    }
    if reply_to:
        data["replyToAddress"] = reply_to

    try:
        r = requests.post(url, headers=_zoho_headers(token), data=data, timeout=15)
        if r.status_code == 401:
            token = _maybe_refresh_token()
            if token:
                r = requests.post(url, headers=_zoho_headers(token), data=data, timeout=15)

        if r.status_code >= 300:
            print(f"❌ Error Zoho API {r.status_code}: {r.text[:200]}")
            return {"status": "error", "provider": "zoho_api", "message": f"HTTP {r.status_code}"}

        print(f"✅ Zoho API OK → To={to_addr} | From={forced_from} | Reply-To={reply_to}")
        return {"status": "ok", "provider": "zoho_api", "to": to_addr, "subject": subject}
    except Exception as e:
        print(f"❌ Error general Zoho API: {e}")
        return {"status": "error", "provider": "zoho_api", "message": str(e)}

# =========================
# Punto de entrada usado por el workflow
# =========================
def send_email(email_config: dict, agent_name: str, event_data: Dict[str, Any]) -> dict:
    agent_name_display = _get_agent_name_from_config(agent_name)
    caller_number = event_data.get("caller") or "Desconocido"
    turns = _extract_conversation_turns(event_data)

    text_plain, html_body = _render_email_template(agent_name_display, caller_number, turns)
    email_content = {
        "html": html_body,
        "plain": text_plain,
        "agent_name": agent_name_display,
        "caller_number": caller_number
    }

    # 1) Intentar Zoho API
    token = (os.getenv("ZOHO_ACCESS_TOKEN") or "").strip()
    if token:
        res = _send_via_zoho_api(email_config, email_content)
        if res.get("status") == "ok":
            return res
        print("↩️ Falló Zoho API, intentando SMTP...")

    # 2) Fallback SMTP
    return _send_via_smtp(email_config, email_content)
