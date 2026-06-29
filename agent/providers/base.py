# agent/providers/base.py — Clase base para proveedores de WhatsApp
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from fastapi import Request


@dataclass
class MensajeEntrante:
    """Mensaje normalizado — mismo formato sin importar el proveedor."""
    telefono: str
    texto: str
    mensaje_id: str
    es_propio: bool
    tiene_media: bool = False
    media_urls: list[str] = field(default_factory=list)  # URLs de imágenes adjuntas


class ProveedorWhatsApp(ABC):
    """Interfaz que cada proveedor de WhatsApp debe implementar."""

    @abstractmethod
    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Extrae y normaliza mensajes del payload del webhook."""
        ...

    @abstractmethod
    async def enviar_mensaje(self, telefono: str, mensaje: str, media_urls: list[str] | None = None) -> bool:
        """Envía un mensaje de texto y opcionalmente imágenes. Retorna True si fue exitoso."""
        ...

    async def validar_webhook(self, request: Request) -> dict | int | None:
        """Verificación GET del webhook (solo Meta la requiere). Retorna respuesta o None."""
        return None
