# auth.py
import jwt
import os
import time
from functools import wraps
from flask import request, jsonify

# Esta debe ser una clave secreta FUERTE. 
# ¡Guárdala en tus variables de entorno en Render!
JWT_SECRET_KEY = os.getenv("AGENT_JWT_SECRET", "una-clave-secreta-muy-fuerte-cambiame")
JWT_ALGORITHM = "HS256"

def create_agent_token(bot_slug):
    """Crea un nuevo token JWT para un agente."""
    payload = {
        "bot_slug": bot_slug,
        "iat": int(time.time()),
        "exp": int(time.time()) + (12 * 3600)  # Expira en 12 horas
    }
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return token

def token_required(f):
    """
    Un decorador para proteger endpoints que requieren un token de agente válido.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if "Authorization" in request.headers:
            try:
                # Espera un token "Bearer <token>"
                token = request.headers["Authorization"].split(" ")[1]
            except IndexError:
                return jsonify({"ok": False, "error": "Token malformado"}), 401

        if not token:
            return jsonify({"ok": False, "error": "Token de autorización faltante"}), 401

        try:
            # Decodificar el token
            data = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            # Inyectamos los datos del agente en el contexto de la petición
            request.agent_data = data
        except jwt.ExpiredSignatureError:
            return jsonify({"ok": False, "error": "Token expirado"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"ok": False, "error": "Token inválido"}), 401

        return f(*args, **kwargs)

    return decorated