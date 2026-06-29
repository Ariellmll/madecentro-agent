# agent/main.py — Servidor FastAPI + Webhook de WhatsApp
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv

from agent.brain import generar_respuesta
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial
from agent.providers import obtener_proveedor

load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger("agentkit")

proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))

# Número que recibe la orden confirmada (mismo que Yape/Plin de la ferretería)
PAYMENT_PHONE_NUMBER = os.getenv("PAYMENT_PHONE_NUMBER", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await inicializar_db()
    logger.info("Base de datos inicializada")
    logger.info(f"Servidor AgentKit corriendo en puerto {PORT}")
    logger.info(f"Proveedor de WhatsApp: {proveedor.__class__.__name__}")
    if PAYMENT_PHONE_NUMBER:
        logger.info(f"Número de ferretería para pedidos: {PAYMENT_PHONE_NUMBER}")
    else:
        logger.warning("PAYMENT_PHONE_NUMBER no configurado — los pedidos confirmados no se reenviarán")
    yield


app = FastAPI(
    title="Madecentro Bot — WhatsApp AI Agent",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/")
async def health_check():
    return {"status": "ok", "service": "madecentro-bot"}


@app.get("/webhook")
async def webhook_verificacion(request: Request):
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


@app.post("/webhook")
async def webhook_handler(request: Request):
    try:
        mensajes = await proveedor.parsear_webhook(request)

        for msg in mensajes:
            if msg.es_propio:
                continue

            # Ignorar mensajes vacíos sin media
            if not msg.texto and not msg.tiene_media:
                continue

            # Cuando llega una imagen, asumimos que es el comprobante de pago
            if msg.tiene_media:
                texto_procesado = "[PAGO_RECIBIDO] El carpintero envió una captura de pantalla del comprobante de pago."
                logger.info(f"Comprobante de pago recibido de {msg.telefono}")
            else:
                texto_procesado = msg.texto
                logger.info(f"Mensaje de {msg.telefono}: {msg.texto}")

            historial = await obtener_historial(msg.telefono)
            respuesta = await generar_respuesta(texto_procesado, historial)

            await guardar_mensaje(msg.telefono, "user", texto_procesado)
            await guardar_mensaje(msg.telefono, "assistant", respuesta)

            # Enviar respuesta al carpintero
            await proveedor.enviar_mensaje(msg.telefono, respuesta)

            # Si fue pago confirmado, reenviar comprobante + orden a la ferretería
            if msg.tiene_media and PAYMENT_PHONE_NUMBER:
                # 1. Reenviar el comprobante de pago (imagen) para que la ferretería verifique
                if msg.media_urls:
                    aviso = f"💰 *COMPROBANTE DE PAGO*\nCliente: {msg.telefono}"
                    await proveedor.enviar_mensaje(PAYMENT_PHONE_NUMBER, aviso, media_urls=msg.media_urls)
                    logger.info(f"Comprobante reenviado a ferretería: {PAYMENT_PHONE_NUMBER}")

                # 2. Reenviar la orden de corte confirmada
                encabezado = f"📋 *ORDEN DE CORTE CONFIRMADA*\nCliente: {msg.telefono}\n\n"
                await proveedor.enviar_mensaje(PAYMENT_PHONE_NUMBER, encabezado + respuesta)
                logger.info(f"Orden reenviada a ferretería: {PAYMENT_PHONE_NUMBER}")

            logger.info(f"Respuesta a {msg.telefono}: {respuesta}")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
