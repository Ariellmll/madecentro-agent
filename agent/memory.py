# agent/memory.py — Memoria de conversaciones con SQLite
import os
from datetime import datetime, date
from zoneinfo import ZoneInfo
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Text, DateTime, select, Integer, func

_ZONA_LIMA = ZoneInfo("America/Lima")
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./agentkit.db")

if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Mensaje(Base):
    __tablename__ = "mensajes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Cliente(Base):
    __tablename__ = "clientes"

    telefono: Mapped[str] = mapped_column(String(50), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200))


class Orden(Base):
    __tablename__ = "ordenes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    numero_orden: Mapped[str] = mapped_column(String(30))
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


async def inicializar_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def guardar_mensaje(telefono: str, role: str, content: str):
    async with async_session() as session:
        mensaje = Mensaje(
            telefono=telefono,
            role=role,
            content=content,
            timestamp=datetime.utcnow()
        )
        session.add(mensaje)
        await session.commit()


async def obtener_historial(telefono: str, limite: int = 30) -> list[dict]:
    """Recupera los últimos N mensajes — límite mayor para acumular piezas del pedido."""
    async with async_session() as session:
        query = (
            select(Mensaje)
            .where(Mensaje.telefono == telefono)
            .order_by(Mensaje.timestamp.desc())
            .limit(limite)
        )
        result = await session.execute(query)
        mensajes = result.scalars().all()
        mensajes.reverse()
        return [
            {"role": msg.role, "content": msg.content}
            for msg in mensajes
        ]


async def limpiar_historial(telefono: str):
    async with async_session() as session:
        query = select(Mensaje).where(Mensaje.telefono == telefono)
        result = await session.execute(query)
        mensajes = result.scalars().all()
        for msg in mensajes:
            await session.delete(msg)
        await session.commit()


async def guardar_nombre_cliente(telefono: str, nombre: str):
    async with async_session() as session:
        cliente = await session.get(Cliente, telefono)
        if cliente:
            cliente.nombre = nombre
        else:
            cliente = Cliente(telefono=telefono, nombre=nombre)
            session.add(cliente)
        await session.commit()


async def obtener_nombre_cliente(telefono: str) -> str:
    async with async_session() as session:
        cliente = await session.get(Cliente, telefono)
        return cliente.nombre if cliente else "Sin nombre"


async def crear_numero_orden(telefono: str) -> str:
    # Usar hora Lima para que el contador diario se resetee a medianoche Lima, no UTC
    ahora_lima = datetime.now(_ZONA_LIMA)
    hoy = ahora_lima.date()
    # Guardar como naive datetime en hora Lima (SQLite no maneja tz)
    ahora_naive = ahora_lima.replace(tzinfo=None)
    async with async_session() as session:
        result = await session.execute(
            select(func.count(Orden.id)).where(
                func.date(Orden.fecha_creacion) == hoy
            )
        )
        count = (result.scalar() or 0) + 1
        numero = f"OC-{hoy.strftime('%Y%m%d')}-{count:03d}"
        orden = Orden(telefono=telefono, numero_orden=numero, fecha_creacion=ahora_naive)
        session.add(orden)
        await session.commit()
        return numero
