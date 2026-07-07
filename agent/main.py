# agent/main.py — Servidor FastAPI + Webhook de WhatsApp
import os
import re
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, FileResponse
from dotenv import load_dotenv

from agent.brain import generar_respuesta
from agent.memory import (
    inicializar_db, guardar_mensaje, obtener_historial,
    guardar_nombre_cliente, obtener_nombre_cliente, crear_numero_orden,
)
from agent.providers import obtener_proveedor
from agent.exportar_excel import generar_excel_orden, _parsear_tabla
from agent.tools import calcular_planchas_necesarias, calcular_costo_pedido, piezas_desde_filas

load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger("agentkit")

proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))

# Número que recibe la orden confirmada (mismo que Yape/Plin de la ferretería)
PAYMENT_PHONE_NUMBER = os.getenv("PAYMENT_PHONE_NUMBER", "")

# Dominio público donde Railway sirve /files/{filename} (usado para el link del Excel)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
if PUBLIC_BASE_URL and not PUBLIC_BASE_URL.startswith(("http://", "https://")):
    PUBLIC_BASE_URL = f"https://{PUBLIC_BASE_URL}"

_ZONA_LIMA = ZoneInfo("America/Lima")

_PATRON_NOMBRE = re.compile(r'\[CLIENTE_NOMBRE:\s*(.+?)\]')
_PATRON_ORDEN = re.compile(r'\[ORDEN_CORTE\](.*?)\[/ORDEN_CORTE\]', re.DOTALL)
_PATRON_PREVIEW = re.compile(r'\[ORDEN_PREVIEW\](.*?)\[/ORDEN_PREVIEW\]', re.DOTALL)


def _limpiar_marcadores(texto: str) -> str:
    """Elimina marcadores internos antes de enviar al cliente."""
    texto = _PATRON_NOMBRE.sub('', texto)
    texto = _PATRON_ORDEN.sub(lambda m: m.group(1).strip(), texto)
    texto = _PATRON_PREVIEW.sub(lambda m: m.group(1).strip(), texto)
    return texto.strip()


def _extraer_cuerpo_orden(respuesta: str) -> str | None:
    match = _PATRON_ORDEN.search(respuesta)
    return match.group(1).strip() if match else None


def _extraer_cuerpo_preview(respuesta: str) -> str | None:
    match = _PATRON_PREVIEW.search(respuesta)
    return match.group(1).strip() if match else None


def _generar_link_excel(cuerpo: str, identificador: str) -> str | None:
    """Genera el Excel de la orden y devuelve su URL pública, o None si falla o falta PUBLIC_BASE_URL."""
    if not PUBLIC_BASE_URL:
        return None
    try:
        ruta_excel = generar_excel_orden(cuerpo, identificador)
        return f"{PUBLIC_BASE_URL}/files/{os.path.basename(ruta_excel)}"
    except Exception as e:
        logger.error(f"Error generando excel de la orden: {e}")
        return None


def _formatear_cotizacion(planchas: int, costo: dict) -> str:
    """Arma el mensaje de material + cotización + instrucciones de pago.

    El cálculo se hace en Python (nesting real + tarifas), nunca lo estima Claude,
    para evitar que el LLM invente cantidades de planchas o costos.
    """
    canteado = costo["costo_canto_delgado"] + costo["costo_canto_grueso"]

    mensaje = (
        f"📦 Material\n\n"
        f"Planchas requeridas: {planchas}\n\n"
        f"💰 Resumen económico\n\n"
        f"Melamina\n"
        f"Planchas requeridas: {planchas}\n"
        f"Precio por plancha: S/ {costo['precio_plancha']:.2f}\n"
        f"Costo melamina: S/ {costo['costo_melamina']:.2f}\n\n"
        f"Cortes: S/ {costo['costo_cortes']:.2f}\n"
        f"Canteado: S/ {canteado:.2f}\n"
        f"Ranurado: S/ {costo['costo_ranurado']:.2f}\n"
        f"────────────────\n"
        f"TOTAL: S/ {costo['total_estimado']:.2f}\n\n"
        f"💡 Este costo es referencial. El precio final puede variar según tipo de canto, espesor, color y servicio adicional.\n\n"
        f"Para confirmar tu pedido realizá el pago por *Yape* o *Plin* al número *{PAYMENT_PHONE_NUMBER}* y enviame la captura de pantalla del comprobante.\n\n"
        f"Una vez recibida la captura, generaré la orden de corte confirmada y la enviaré a la ferretería. ✅"
    )
    return mensaje


def _formatear_orden_ferreteria(cuerpo: str, telefono: str, nombre: str, numero_orden: str) -> str:
    ahora = datetime.now(_ZONA_LIMA)
    hora = ahora.strftime('%I:%M').lstrip('0') or '12'
    sufijo = 'a.m.' if ahora.hour < 12 else 'p.m.'
    fecha_str = f"{ahora.strftime('%d/%m/%Y')} {hora} {sufijo}"

    header = (
        f"📋 ORDEN DE CORTE CONFIRMADA\n\n"
        f"🆔 Orden: {numero_orden}\n"
        f"Cliente: {telefono}\n"
        f"Nombre: {nombre}\n"
        f"Proveedor: Madecentro Melamine\n\n"
        f"──────────────────────────\n\n"
    )
    footer = f"\n\nFecha de envío: {fecha_str}"
    return header + cuerpo + footer


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
    if not PUBLIC_BASE_URL:
        logger.warning("PUBLIC_BASE_URL no configurado — no se podrá enviar el Excel de la orden por WhatsApp")
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


@app.get("/files/{filename}")
async def descargar_archivo(filename: str):
    nombre_seguro = os.path.basename(filename)
    if nombre_seguro != filename:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    ruta = os.path.join("generated", nombre_seguro)
    if not os.path.isfile(ruta):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return FileResponse(
        ruta,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=nombre_seguro,
    )


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

            # Extraer nombre del cliente si viene en esta respuesta y guardarlo
            match_nombre = _PATRON_NOMBRE.search(respuesta)
            if match_nombre:
                nombre_capturado = match_nombre.group(1).strip()
                await guardar_nombre_cliente(msg.telefono, nombre_capturado)
                logger.info(f"Nombre registrado: {nombre_capturado} ({msg.telefono})")

            # Guardar en historial con marcadores (Claude los necesita para recordar contexto)
            await guardar_mensaje(msg.telefono, "user", texto_procesado)
            await guardar_mensaje(msg.telefono, "assistant", respuesta)

            # Enviar al carpintero sin marcadores internos
            respuesta_limpia = _limpiar_marcadores(respuesta)
            await proveedor.enviar_mensaje(msg.telefono, respuesta_limpia)

            # Al FINALIZAR (antes del pago), mandar el Excel de previsualización al carpintero
            cuerpo_preview = _extraer_cuerpo_preview(respuesta)
            if cuerpo_preview:
                excel_preview_url = _generar_link_excel(cuerpo_preview, f"preview-{msg.mensaje_id or int(datetime.now(_ZONA_LIMA).timestamp())}")
                if excel_preview_url:
                    await proveedor.enviar_mensaje(
                        msg.telefono,
                        "📎 Aquí tienes tu orden de corte en Excel para revisar antes de pagar.",
                        media_urls=[excel_preview_url],
                    )
                else:
                    logger.warning("No se pudo generar/enviar el excel de previsualización (PUBLIC_BASE_URL no configurado o fallo en generación)")

                # Nesting de planchas + cotización — se calculan en Python, nunca los estima Claude
                piezas = piezas_desde_filas(_parsear_tabla(cuerpo_preview))
                if piezas:
                    planchas = calcular_planchas_necesarias(piezas)["planchas"]
                    costo = calcular_costo_pedido(piezas, numero_planchas=planchas)
                    await proveedor.enviar_mensaje(msg.telefono, _formatear_cotizacion(planchas, costo))
                else:
                    logger.warning("No se pudieron parsear piezas de la tabla de preview para cotizar")

            # Si fue pago confirmado, reenviar comprobante + orden formateada a la ferretería
            if msg.tiene_media and PAYMENT_PHONE_NUMBER:
                if msg.media_urls:
                    aviso = f"💰 *COMPROBANTE DE PAGO*\nCliente: {msg.telefono}"
                    await proveedor.enviar_mensaje(PAYMENT_PHONE_NUMBER, aviso, media_urls=msg.media_urls)
                    logger.info(f"Comprobante reenviado a ferretería: {PAYMENT_PHONE_NUMBER}")

                nombre_cliente = await obtener_nombre_cliente(msg.telefono)
                numero_orden = await crear_numero_orden(msg.telefono)
                cuerpo = _extraer_cuerpo_orden(respuesta) or respuesta_limpia
                orden_formateada = _formatear_orden_ferreteria(cuerpo, msg.telefono, nombre_cliente, numero_orden)

                excel_url = _generar_link_excel(cuerpo, numero_orden)
                await proveedor.enviar_mensaje(
                    PAYMENT_PHONE_NUMBER, orden_formateada,
                    media_urls=[excel_url] if excel_url else None,
                )
                logger.info(f"Orden {numero_orden} enviada a ferretería")

            logger.info(f"Respuesta a {msg.telefono}: {respuesta_limpia}")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
