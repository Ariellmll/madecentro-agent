# agent/tools.py — Herramientas del agente Madecentro Bot
import os
import yaml
import logging

logger = logging.getLogger("agentkit")


def cargar_info_negocio() -> dict:
    try:
        with open("config/business.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logger.error("config/business.yaml no encontrado")
        return {}


def obtener_tarifas() -> dict:
    """Retorna las tarifas de corte vigentes desde knowledge/costos_corte.md."""
    return {
        "corte_por_pieza": 1.50,
        "canto_delgado_por_ml": 1.20,
        "canto_grueso_por_ml": 2.50,
        "ranurado_por_lado": 3.00,
        "servicio_minimo": 10.00,
    }


def calcular_costo_pedido(piezas: list[dict]) -> dict:
    """
    Calcula el costo estimado de un pedido de corte.

    Cada pieza debe tener:
    - cantidad: int
    - lados_delgado: list[int]  (medidas en mm)
    - lados_grueso: list[int]   (medidas en mm)
    - tiene_ranura: bool
    """
    tarifas = obtener_tarifas()
    total_piezas = sum(p.get("cantidad", 1) for p in piezas)

    costo_cortes = total_piezas * tarifas["corte_por_pieza"]

    costo_canto_delgado = 0.0
    for p in piezas:
        cantidad = p.get("cantidad", 1)
        lados = p.get("lados_delgado", [])
        ml_por_pieza = sum(lados) / 1000
        costo_canto_delgado += ml_por_pieza * cantidad * tarifas["canto_delgado_por_ml"]

    costo_canto_grueso = 0.0
    for p in piezas:
        cantidad = p.get("cantidad", 1)
        lados = p.get("lados_grueso", [])
        ml_por_pieza = sum(lados) / 1000
        costo_canto_grueso += ml_por_pieza * cantidad * tarifas["canto_grueso_por_ml"]

    costo_ranurado = 0.0
    for p in piezas:
        cantidad = p.get("cantidad", 1)
        if p.get("tiene_ranura", False):
            costo_ranurado += cantidad * tarifas["ranurado_por_lado"]

    total = costo_cortes + costo_canto_delgado + costo_canto_grueso + costo_ranurado
    if total < tarifas["servicio_minimo"]:
        total = tarifas["servicio_minimo"]

    return {
        "total_piezas": total_piezas,
        "costo_cortes": round(costo_cortes, 2),
        "costo_canto_delgado": round(costo_canto_delgado, 2),
        "costo_canto_grueso": round(costo_canto_grueso, 2),
        "costo_ranurado": round(costo_ranurado, 2),
        "total_estimado": round(total, 2),
        "aplica_minimo": total == tarifas["servicio_minimo"],
    }
