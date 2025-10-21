# services/email_service.py
import os
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Any, List
from datetime import datetime

ZOHO_API_DOMAIN = os.getenv("ZOHO_API_DOMAIN", "https://www.zohoapis.com").rstrip("/")

# Rutas de plantillas
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'templates')
EMAIL_TEMPLATE_PATH = os.path.join(TEMPLATE_DIR, 'email_summary.html')


# =====================================================
# 🎨 Funciones de Renderizado (Dark Mode con Tablas)
# =====================================================
def _escape_html(s: str) -> str:
    """Escapa caracteres HTML para inyección segura."""
    return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def _render_transcript_blocks(transcript_list: List[Dict[str, Any]]) -> tuple[str, str]:
    """
    Genera la lista de texto plano y las burbujas HTML usando Tablas para compatibilidad.
    """
    lines_txt = []
    blocks_html = []
    
    # Colores CSS en línea para las burbujas
    # --- CAMBIO 1 ---
    AGENT_COLOR = "#333333"  # CAMBIADO de #F7BD02 a un gris oscuro
    USER_COLOR = "#4d4d4d"
    
    for item in (transcript_list or []):
        # Extracción segura de datos (corrección de 'role' is not defined)
        role = (item.get("role") or "user").lower()
        msg  = (item.get("message") or "").strip()
        
        ts = item.get("timestamp") or datetime.now().strftime("%H:%M")
        caller_number = item.get("caller_number") or "Desconocido"
        agent_display_name = item.get("agent_name") or "Agente"

        if not msg:
            continue
            
        # 1. Asignación de estilos y etiquetas
        if role == "agent":
            who_label = "Agente"
            bubble_color = AGENT_COLOR
            # --- CAMBIO 2 ---
            text_color = "#F7BD02" # CAMBIADO de #1a1a1a a dorado (para texto sobre fondo oscuro)
            meta_detail = "" # <-- CAMBIAR A VACÍO
            # Alineación a la derecha (Chat-Agente)
            align_dir = "right"
            bubble_radius = "18px 18px 0px 18px"
        else: # user o client
            who_label = "Cliente"
            bubble_color = USER_COLOR
            text_color = "#f0f0f0" # Texto claro sobre gris
            meta_detail = caller_number
            # Alineación a la izquierda (Chat-Cliente)
            align_dir = "left"
            bubble_radius = "18px 18px 18px 0px"

        lines_txt.append(f"[{ts}] {who_label} ({meta_detail}): {msg}")
        
        # 2. Generación del bloque HTML (Usando Tablas para alineación fiable)
        html_block = f"""
        <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-bottom: 15px;">
          <tr>
            <td align="{align_dir}">
              <table border="0" cellspacing="0" cellpadding="0">
                <tr>
                  <td style="
                    background-color: {bubble_color}; 
                    color: {text_color}; 
                    padding: 10px 15px; 
                    border-radius: {bubble_radius}; 
                    font-size: 14px; 
                    line-height: 1.4;
                    max-width: 80%;
                    word-wrap: break-word;"
                  >
                    {_escape_html(msg)}
                  </td>
                </tr>
                <tr>
                  <td align="{align_dir}" style="padding-top: 5px; font-size: 10px; color: #999999;">
                    {_escape_html(who_label)} · {_escape_html(meta_detail)} · {ts}
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
        """
        blocks_html.append(html_block)
        
    conv_blocks = "\n".join(blocks_html) or """<div style="color:#999999; text-align:center; padding: 20px 0;">No hay mensajes detallados.</div>"""
    return "\n".join(lines_txt), conv_blocks


def _render_email_template(agent_name: str, caller_number: str, transcript_list: List[Dict[str, Any]]) -> tuple[str, str]:
    """Lee la plantilla, genera el chat y lo inyecta junto con los datos clave."""
    call_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    if not os.path.exists(EMAIL_TEMPLATE_PATH):
        # Fallback a texto plano si la plantilla no existe
        return "(Plantilla no encontrada)", f"Error: Plantilla HTML no encontrada. Transcripción: {transcript_list}"

    try:
        with open(EMAIL_TEMPLATE_PATH, 'r', encoding='utf-8') as f:
            html_content = f.read()

        # 1. Generar contenido dinámico (Texto plano y Bloques HTML del chat)
        text_plain_transcript, conversation_blocks_html = _render_transcript_blocks(transcript_list)

        # 2. Reemplazar marcadores en el HTML
        html_content = (
            html_content
            .replace('{{ agent_name }}', _escape_html(agent_name or '—'))
            .replace('{{ caller_number }}', _escape_html(caller_number or '—'))
            .replace('{{ call_time }}', _escape_html(call_time))
            .replace('{{ conversation_blocks_html }}', conversation_blocks_html)
        )
        
        # 3. Construir el texto plano completo
        text_plain = (
            f"RESUMEN DE LLAMADA\n"
            f"--------------------------\n"
            f"Agente: {agent_name or '—'}\n"
            f"Número de Contacto: {caller_number or '—'}\n\n"
            f"TRANSCRIPCIÓN:\n{text_plain_transcript}\n"
            f"--------------------------"
        )
        
        return text_plain, html_content
    except Exception as e:
        print(f"❌ Error al renderizar la plantilla: {e}")
        return f"Error de renderizado: {e}", "Error de renderizado HTML"


def _extract_conversation_turns(event_data: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extrae la lista detallada de turnos del payload raw y añade metadatos por turno."""
    raw = event_data.get("raw") or {}
    root = raw.get("data", raw) if isinstance(raw, dict) else {}
    turns = root.get("transcript") or root.get("transcription") or []
    
    # Obtener metadatos una sola vez
    caller_num = event_data.get("caller") or "Desconocido"
    agent_id = event_data.get("agent_id") or "Agente"

    if not isinstance(turns, list):
        # Fallback si no es una lista
        txt = event_data.get("transcript_text") or event_data.get("transcription") or "(Conversación no disponible)"
        return [{
            'role': 'user', 
            'message': txt,
            'caller_number': caller_num,
            'agent_name': agent_id
        }]
        
    conversation = []
    for turn in turns:
        if isinstance(turn, dict) and turn.get("message"):
            # Normalizamos el rol a 'agent' o 'user'
            role = turn.get("role", "unknown").lower()
            if role == 'client': 
                role = 'user'
                
            conversation.append({
                'role': role,
                'message': turn['message'].strip(),
                'timestamp': turn.get('timestamp') or datetime.now().strftime("%H:%M"),
                # Pasamos los metadatos al turno para el renderizado
                'caller_number': caller_num,
                'agent_name': agent_id
            })
            
    return conversation


def _get_agent_name_from_config(agent_name_key: str) -> str:
    """Asumimos que el nombre legible (ej: "sundin") es el nombre del agente."""
    # Usamos capitalize() como tu código original
    return agent_name_key.capitalize()


# =====================================================
#  LÓGICA DE ENVÍO (SMTP/ZOHO API) - SIN CAMBIOS
# =====================================================

def _send_via_smtp(email_cfg: dict, email_content: Dict[str, str]) -> dict:
    smtp_server = os.getenv("MAIL_HOST", "smtp.zoho.com")
    smtp_port = int(os.getenv("MAIL_PORT", "587"))
    mail_from = os.getenv("MAIL_FROM") or email_cfg.get("from")
    username = os.getenv("MAIL_USERNAME", mail_from)
    password = os.getenv("MAIL_PASSWORD")

    if not mail_from or not username or not password:
        return {"status": "error", "message": "SMTP incompleto: MAIL_FROM/USERNAME/PASSWORD faltan"}

    to_addr = email_cfg.get("to")
    
    subject = f"📞 Conversación {email_content['agent_name']} | Contacto: {email_content['caller_number']}"

    msg = MIMEMultipart("alternative")
    msg["From"] = mail_from
    msg["To"] = to_addr
    msg["Subject"] = subject
    
    msg.attach(MIMEText(email_content["plain"], "plain", "utf-8"))
    msg.attach(MIMEText(email_content["html"], "html", "utf-8"))

    try:
        print(f"📡 Enviando SMTP (HTML) → {to_addr} ...")
        with smtplib.SMTP(smtp_server, smtp_port, timeout=20) as server:
            server.starttls()
            server.login(username, password)
            server.send_message(msg)
        print("✅ Correo SMTP (HTML) enviado correctamente.")
        return {"status": "ok", "provider": "smtp", "to": to_addr, "subject": subject}
    except Exception as e:
        print(f"❌ Error SMTP: {e}")
        return {"status": "error", "provider": "smtp", "message": str(e)}

def _zoho_headers(access_token: str) -> dict:
    return {"Authorization": f"Zoho-oauthtoken {access_token}"}

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

    to_addr = email_cfg.get("to")
    from_addr = email_cfg.get("from") or os.getenv("MAIL_FROM")
    
    subject = f"📞 Conversación {email_content['agent_name']} | Contacto: {email_content['caller_number']}"

    url = f"{ZOHO_API_DOMAIN}/mail/v2/accounts/{acc_id}/messages"
    data = {
        "fromAddress": from_addr,
        "toAddress": to_addr,
        "subject": subject,
        "content": email_content["html"],
        "mailFormat": "html",
    }
    
    try:
        r = requests.post(url, headers=_zoho_headers(token), data=data, timeout=15)
        if r.status_code == 401:
            token = _maybe_refresh_token()
            if token:
                r = requests.post(url, headers=_zoho_headers(token), data=data, timeout=15)

        if r.status_code >= 300:
            print(f"❌ Error Zoho API {r.status_code}: {r.text[:200]}")
            return {"status": "error", "provider": "zoho_api", "message": f"HTTP {r.status_code}"}

        print(f"✅ Correo enviado vía Zoho API (HTML) a {to_addr}")
        return {"status": "ok", "provider": "zoho_api", "to": to_addr, "subject": subject}
    except Exception as e:
        print(f"❌ Error general Zoho API: {e}")
        return {"status": "error", "provider": "zoho_api", "message": str(e)}

# =====================================================
#  FUNCIÓN PRINCIPAL USADA POR EL WORKFLOW
# =====================================================
def send_email(email_config: dict, agent_name: str, event_data: Dict[str, Any]) -> dict:
    
    # 1. Preparar datos
    agent_name_display = _get_agent_name_from_config(agent_name)
    caller_number = event_data.get("caller") or "Desconocido"
    
    # Extraer turnos detallados
    conversation_turns = _extract_conversation_turns(event_data)
    
    # 2. Construir el contenido (HTML y texto plano) usando la plantilla
    text_plain, html_body = _render_email_template(agent_name_display, caller_number, conversation_turns)
    
    email_content = {
        "html": html_body,
        "plain": text_plain,
        "agent_name": agent_name_display,
        "caller_number": caller_number
    }

    # 3. Prioridad: API Zoho
    token = (os.getenv("ZOHO_ACCESS_TOKEN") or "").strip()
    if token:
        res = _send_via_zoho_api(email_config, email_content)
        if res.get("status") == "ok":
            return res
        print("↩️ Falló Zoho API, intentando SMTP...")

    # 4. Fallback SMTP (soporta ambos cuerpos: HTML y Plain)
    return _send_via_smtp(email_config, email_content)