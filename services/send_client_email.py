import os
import re
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ==========================================================
# ⚙️ CONFIGURACIÓN DEL CORREO (usa las mismas variables del .env)
# ==========================================================
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.zoho.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SENDER_NAME = os.getenv("SENDER_NAME", "In Houston Texas")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", SMTP_USER)

# ==========================================================
# 📁 RUTA LOCAL DE LOS AGENTES
# ==========================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS_DIR = os.path.join(BASE_DIR, "agents")

# ==========================================================
# 🔍 DETECTAR CORREO EN TEXTO
# ==========================================================
def extract_email_from_text(text: str) -> str | None:
    """Busca un correo electrónico dentro del texto."""
    if not text:
        return None
    match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
    if match:
        return match.group(0).lower()
    return None

# ==========================================================
# 📡 OBTENER DIRECCIÓN DESDE EL JSON LOCAL DEL AGENTE
# ==========================================================
def get_agent_address(agent_slug: str) -> str | None:
    """
    Carga el JSON local del agente (agents/<slug>.json)
    y devuelve el campo 'location.maps_url' o 'location.address' como respaldo.
    """
    try:
        agent_path = os.path.join(AGENTS_DIR, f"{agent_slug}.json")
        if not os.path.exists(agent_path):
            print(f"⚠️ No se encontró el archivo {agent_path}")
            return None

        with open(agent_path, "r", encoding="utf-8") as file:
            data = json.load(file)

            location = data.get("location", {})
            maps_url = location.get("maps_url")
            address = location.get("address")

            if maps_url:
                print(f"📍 maps_url encontrado en {agent_slug}.json: {maps_url}")
                return maps_url
            elif address:
                print(f"📍 address encontrado (sin maps_url) en {agent_slug}.json: {address}")
                return address

            print(f"⚠️ No se encontró ni maps_url ni address en {agent_slug}.json.")
    except Exception as e:
        print(f"❌ Error al leer el JSON de {agent_slug}: {e}")
    return None

# ==========================================================
# ✉️ ENVIAR CORREO AL CLIENTE
# ==========================================================
def send_email_to_client(conversation_text: str, agent_name: str = "sundin"):
    """
    Detecta el correo del cliente en la conversación y le envía un mensaje
    con el link de Google Maps obtenido desde el JSON local del agente.
    """

    # 1️⃣ Buscar correo dentro del texto
    client_email = extract_email_from_text(conversation_text)
    if not client_email:
        print("⚠️ No se encontró ningún correo en la conversación.")
        return False

    print(f"📧 Correo detectado en conversación: {client_email}")

    # 2️⃣ Obtener el link de Google Maps desde el JSON local del agente
    maps_link = get_agent_address(agent_name)
    if not maps_link:
        print("⚠️ No se encontró el link de Maps en el JSON. No se enviará correo.")
        return False

    # 3️⃣ Crear mensaje HTML
    subject = f"Ubicación de la oficina - {SENDER_NAME}"
    body_html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color:#333;">
        <p>Hola 👋,</p>
        <p>Gracias por comunicarte con <b>{SENDER_NAME}</b>.</p>
        <p>Aquí tienes el enlace con la ubicación de nuestra oficina en Google Maps:</p>
        <p><a href="{maps_link}" target="_blank">Ver ubicación en Google Maps</a></p>
        <br>
        <p>Atentamente,<br><b>{agent_name.title()}</b></p>
      </body>
    </html>
    """

    # 4️⃣ Enviar correo
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    message["To"] = client_email
    message.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SENDER_EMAIL, client_email, message.as_string())
        print(f"✅ Correo enviado correctamente a {client_email}")
        return True

    except Exception as e:
        print(f"❌ Error al enviar correo al cliente: {e}")
        return False
