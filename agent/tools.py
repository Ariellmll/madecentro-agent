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
        "precio_plancha": 200.00,
        "plancha_ancho_mm": 2440,
        "plancha_largo_mm": 2140,
    }


def piezas_desde_filas(filas: list[list[str]]) -> list[dict]:
    """
    Convierte las filas parseadas de la tabla markdown de la orden (ver
    agent.exportar_excel._parsear_tabla) a piezas utilizables por el
    nesting y el cálculo de costos.

    Header esperado: Cantidad | Largo | Ancho | Largo 1 | Largo 2 | Ancho 1 | Ancho 2 | Ranura (Lado) | Ranura (Medida) | Observaciones
    """
    if not filas:
        return []

    _header, *datos = filas
    piezas = []
    for fila in datos:
        celdas = (fila + [""] * 9)[:9]
        cantidad_str, largo_str, ancho_str, l1, l2, a1, a2, ranura_lado, _ranura_medida = celdas
        try:
            cantidad = int(float(cantidad_str))
            largo = float(largo_str)
            ancho = float(ancho_str)
        except ValueError:
            continue

        lados_delgado = []
        lados_grueso = []
        for lado, medida in ((l1, largo), (l2, largo), (a1, ancho), (a2, ancho)):
            codigo = lado.strip().upper()
            if codigo == "D":
                lados_delgado.append(medida)
            elif codigo == "G":
                lados_grueso.append(medida)

        piezas.append({
            "cantidad": cantidad,
            "largo": largo,
            "ancho": ancho,
            "lados_delgado": lados_delgado,
            "lados_grueso": lados_grueso,
            "tiene_ranura": bool(ranura_lado.strip()),
        })

    return piezas


def calcular_planchas_necesarias(piezas: list[dict]) -> dict:
    """
    Calcula la cantidad mínima de planchas de melamina necesarias para
    cortar todas las piezas del pedido, usando un algoritmo de nesting
    (First-Fit Decreasing Height con rotación de piezas), no un simple
    cálculo por área.

    Cada pieza debe tener: cantidad, largo, ancho (en mm).
    """
    tarifas = obtener_tarifas()
    ancho_plancha = tarifas["plancha_ancho_mm"]
    largo_plancha = tarifas["plancha_largo_mm"]

    unidades = []
    for p in piezas:
        cantidad = p.get("cantidad", 1)
        largo = p.get("largo", 0)
        ancho = p.get("ancho", 0)
        unidades.extend([(largo, ancho)] * cantidad)

    # Ordenar de mayor a menor por el lado más largo (Decreasing Height)
    unidades.sort(key=lambda dims: max(dims), reverse=True)

    class _Estante:
        def __init__(self, ancho_disponible: float, alto: float):
            self.ancho_disponible = ancho_disponible
            self.alto = alto

    class _Plancha:
        def __init__(self):
            self.alto_restante = largo_plancha
            self.estantes: list[_Estante] = []

        def colocar(self, largo_pieza: float, ancho_pieza: float) -> bool:
            if largo_pieza > largo_plancha or ancho_pieza > ancho_plancha:
                return False
            # Buscar el estante existente que la contenga con menor sobrante (best-fit)
            mejor = None
            for estante in self.estantes:
                if estante.alto >= largo_pieza and estante.ancho_disponible >= ancho_pieza:
                    if mejor is None or estante.ancho_disponible < mejor.ancho_disponible:
                        mejor = estante
            if mejor:
                mejor.ancho_disponible -= ancho_pieza
                return True
            # Si no entra en ningún estante, abrir uno nuevo
            if self.alto_restante >= largo_pieza:
                self.estantes.append(_Estante(ancho_plancha - ancho_pieza, largo_pieza))
                self.alto_restante -= largo_pieza
                return True
            return False

    planchas: list[_Plancha] = []
    for largo_pieza, ancho_pieza in unidades:
        colocada = False
        for plancha in planchas:
            if plancha.colocar(largo_pieza, ancho_pieza) or plancha.colocar(ancho_pieza, largo_pieza):
                colocada = True
                break
        if not colocada:
            nueva = _Plancha()
            nueva.colocar(largo_pieza, ancho_pieza) or nueva.colocar(ancho_pieza, largo_pieza)
            planchas.append(nueva)

    return {"planchas": len(planchas)}


def calcular_costo_pedido(piezas: list[dict], numero_planchas: int = 0) -> dict:
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

    costo_melamina = numero_planchas * tarifas["precio_plancha"]

    total = costo_cortes + costo_canto_delgado + costo_canto_grueso + costo_ranurado + costo_melamina
    if total < tarifas["servicio_minimo"]:
        total = tarifas["servicio_minimo"]

    return {
        "total_piezas": total_piezas,
        "numero_planchas": numero_planchas,
        "precio_plancha": tarifas["precio_plancha"],
        "costo_melamina": round(costo_melamina, 2),
        "costo_cortes": round(costo_cortes, 2),
        "costo_canto_delgado": round(costo_canto_delgado, 2),
        "costo_canto_grueso": round(costo_canto_grueso, 2),
        "costo_ranurado": round(costo_ranurado, 2),
        "total_estimado": round(total, 2),
        "aplica_minimo": total == tarifas["servicio_minimo"],
    }
