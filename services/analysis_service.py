def log_agent_activity(agent_id, data):
    """
    Registra actividad del agente para futuras métricas:
    - Número de llamadas
    - Duración
    - Temas tratados
    - Sentimientos detectados
    - Feedback del usuario (en el futuro)
    """

    print("="*40)
    print(f"[Análisis] Registrando actividad del agente: {agent_id}")
    print("Datos de la sesión:")
    print(data)
    print("="*40)

    # En futuro: guardar en MongoDB, Firebase, BigQuery, Sheets, etc.
    return {
        "status": "ok",
        "message": "Simulado: actividad registrada"
    }
