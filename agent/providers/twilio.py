# agent/providers/twilio.py — Adaptador para Twilio WhatsApp
import os
import logging
import base64
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")


class ProveedorTwilio(ProveedorWhatsApp):
    """Proveedor de WhatsApp usando Twilio."""

    def __init__(self):
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        self.phone_number = os.getenv("TWILIO_PHONE_NUMBER")

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Parsea el payload form-encoded de Twilio. Captura imágenes adjuntas."""
        form = await request.form()
        texto = form.get("Body", "")
        telefono = form.get("From", "").replace("whatsapp:", "")
        mensaje_id = form.get("MessageSid", "")
        num_media = int(form.get("NumMedia", 0))
        tiene_media = num_media > 0

        # Extraer todas las URLs de imágenes adjuntas
        media_urls = [form.get(f"MediaUrl{i}", "") for i in range(num_media) if form.get(f"MediaUrl{i}")]

        if not texto and not tiene_media:
            return []

        return [MensajeEntrante(
            telefono=telefono,
            texto=texto,
            mensaje_id=mensaje_id,
            es_propio=False,
            tiene_media=tiene_media,
            media_urls=media_urls,
        )]

    async def enviar_mensaje(self, telefono: str, mensaje: str, media_urls: list[str] | None = None) -> bool:
        """Envía mensaje de texto y opcionalmente imágenes via Twilio API."""
        if not all([self.account_sid, self.auth_token, self.phone_number]):
            logger.warning("Variables de Twilio no configuradas")
            return False

        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        auth = base64.b64encode(f"{self.account_sid}:{self.auth_token}".encode()).decode()
        headers = {"Authorization": f"Basic {auth}"}

        # Si hay imágenes, enviar cada una en un mensaje separado
        # Twilio solo acepta una MediaUrl por mensaje
        urls_a_enviar = media_urls or []
        exito = True

        if urls_a_enviar:
            for media_url in urls_a_enviar:
                data = {
                    "From": f"whatsapp:{self.phone_number}",
                    "To": f"whatsapp:{telefono}",
                    "Body": "",
                    "MediaUrl": media_url,
                }
                async with httpx.AsyncClient() as client:
                    r = await client.post(url, data=data, headers=headers)
                    if r.status_code != 201:
                        logger.error(f"Error Twilio (media): {r.status_code} — {r.text}")
                        exito = False

        # Enviar el mensaje de texto
        if mensaje:
            data = {
                "From": f"whatsapp:{self.phone_number}",
                "To": f"whatsapp:{telefono}",
                "Body": mensaje,
            }
            async with httpx.AsyncClient() as client:
                r = await client.post(url, data=data, headers=headers)
                if r.status_code != 201:
                    logger.error(f"Error Twilio: {r.status_code} — {r.text}")
                    exito = False

        return exito
