def generate_invoice(agent_id, data):
    """
    Genera y envía una factura automática según reglas del agente.
    Ej: al finalizar llamada, cobrar por minuto, etc.
    """

    print("="*40)
    print(f"[Factura] Generando invoice para el agente: {agent_id}")
    print("Detalles de la operación:")
    print(data)
    print("="*40)

    # En el futuro: conectar con Frey, Stripe, Zoho Invoice o Google Docs
    return {
        "status": "ok",
        "message": "Factura simulada generada"
    }