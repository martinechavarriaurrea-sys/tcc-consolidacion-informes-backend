"""
Normaliza los estados raw de TCC a categorías internas estables.
Preserva siempre el valor raw original. La normalización es flexible:
si un estado raw no tiene mapeo conocido se clasifica como 'desconocido'
en vez de fallar.
"""

import re
from enum import StrEnum


class NormalizedStatus(StrEnum):
    REGISTRADO = "registrado"
    RECOGIDO = "recogido"
    EN_TRANSITO = "en_transito"
    EN_RUTA = "en_ruta_entrega"
    ENTREGADO = "entregado"
    NOVEDAD = "novedad"
    DEVUELTO = "devuelto"
    FALLIDO = "fallido"
    DESCONOCIDO = "desconocido"


# Estados que indican que la guía ya fue resuelta (no sigue activa)
TERMINAL_STATUSES = {
    NormalizedStatus.ENTREGADO,
    NormalizedStatus.DEVUELTO,
    NormalizedStatus.FALLIDO,
}

# Estados que representan problemas (para alertas y reportes)
ISSUE_STATUSES = {
    NormalizedStatus.NOVEDAD,
    NormalizedStatus.DEVUELTO,
    NormalizedStatus.FALLIDO,
}

# Mapeo de patrones (regex) a categorías normalizadas.
# El orden importa: se evalúan en secuencia y se usa el primer match.
#
# Reglas de ordenamiento:
# 1. Devuelto/Retornado — antes que Entregado para evitar falsos positivos
# 2. Novedad — antes que los patrones de Fallido que solapan ("dirección incorrecta")
#    porque "Novedad: dirección incorrecta" es NOVEDAD, no FALLIDO
# 3. Fallido con patrones específicos — antes de Entregado para evitar que
#    "intento fallido de entrega" o "fallido de entrega" matcheen ENTREGADO
# 4. Entregado — usa "entregad" (no "entreg") para no matchear "entrega" suelto
_NORMALIZATION_RULES: list[tuple[str, NormalizedStatus]] = [
    # Devuelto / devolución
    (r"devuel", NormalizedStatus.DEVUELTO),
    (r"retorn", NormalizedStatus.DEVUELTO),
    (r"proceso.*devoluci[oó]n", NormalizedStatus.DEVUELTO),
    (r"cumplido.*devoluci[oó]n", NormalizedStatus.DEVUELTO),
    # Novedad
    (r"novedad", NormalizedStatus.NOVEDAD),
    (r"cumplido.*novedad", NormalizedStatus.NOVEDAD),
    (r"incidencia", NormalizedStatus.NOVEDAD),
    (r"retenid", NormalizedStatus.NOVEDAD),
    (r"inspecci[oó]n.*aduaner", NormalizedStatus.NOVEDAD),
    (r"retenida.*dian", NormalizedStatus.NOVEDAD),
    (r"proceso.*indemnizaci[oó]n", NormalizedStatus.NOVEDAD),
    (r"demorad", NormalizedStatus.NOVEDAD),
    (r"da[ñn]o", NormalizedStatus.NOVEDAD),
    (r"aver[ií]", NormalizedStatus.NOVEDAD),
    # Fallido / indemnización / no despachado
    (r"indemnizaci[oó]n", NormalizedStatus.FALLIDO),
    (r"no.*despacha", NormalizedStatus.FALLIDO),
    (r"intento.*fallid", NormalizedStatus.FALLIDO),
    (r"fallid.*entrega", NormalizedStatus.FALLIDO),
    (r"no.*encontrado", NormalizedStatus.FALLIDO),
    (r"direcci[oó]n.*incorrecta", NormalizedStatus.FALLIDO),
    (r"destinatario.*ausente", NormalizedStatus.FALLIDO),
    (r"rechaz", NormalizedStatus.FALLIDO),
    # Entregado
    (r"entregad", NormalizedStatus.ENTREGADO),
    (r"recib.*destinatario", NormalizedStatus.ENTREGADO),
    (r"reemplaz.*remesa", NormalizedStatus.ENTREGADO),
    # Recogido
    (r"recogid", NormalizedStatus.RECOGIDO),
    (r"recolect", NormalizedStatus.RECOGIDO),
    (r"recib.*remitente", NormalizedStatus.RECOGIDO),
    # En ruta (último tramo)
    (r"en.*ruta", NormalizedStatus.EN_RUTA),
    (r"proceso.*entrega", NormalizedStatus.EN_RUTA),
    (r"mensajer", NormalizedStatus.EN_RUTA),
    (r"reparto", NormalizedStatus.EN_RUTA),
    (r"salida.*entrega", NormalizedStatus.EN_RUTA),
    (r"asignado.*domicilio", NormalizedStatus.EN_RUTA),
    (r"distribuci[oó]n", NormalizedStatus.EN_RUTA),
    # En tránsito
    (r"en.*tr[aá]nsito", NormalizedStatus.EN_TRANSITO),
    (r"tr[aá]nsito", NormalizedStatus.EN_TRANSITO),
    (r"en.*camino", NormalizedStatus.EN_TRANSITO),
    (r"despacho", NormalizedStatus.EN_TRANSITO),
    (r"proceso.*traslado", NormalizedStatus.EN_TRANSITO),
    (r"aerolinea", NormalizedStatus.EN_TRANSITO),
    (r"nacionalizada", NormalizedStatus.EN_TRANSITO),
    (r"contin[uú]a.*destino", NormalizedStatus.EN_TRANSITO),
    (r"carga.*planta", NormalizedStatus.EN_TRANSITO),
    (r"recepci[oó]n.*destino", NormalizedStatus.EN_TRANSITO),
    # Registrado
    (r"registrad", NormalizedStatus.REGISTRADO),
    (r"ingres", NormalizedStatus.REGISTRADO),
    (r"creaci[oó]n", NormalizedStatus.REGISTRADO),
    (r"generaci[oó]n.*gu[ií]a", NormalizedStatus.REGISTRADO),
]

_compiled_rules = [(re.compile(pattern, re.IGNORECASE), status) for pattern, status in _NORMALIZATION_RULES]


def normalize_status(raw_status: str) -> NormalizedStatus:
    """Mapea un estado raw a una categoría normalizada."""
    if not raw_status:
        return NormalizedStatus.DESCONOCIDO
    clean = raw_status.strip()
    for pattern, normalized in _compiled_rules:
        if pattern.search(clean):
            return normalized
    return NormalizedStatus.DESCONOCIDO


def is_terminal(status: str | NormalizedStatus) -> bool:
    return NormalizedStatus(status) in TERMINAL_STATUSES


def is_issue(status: str | NormalizedStatus) -> bool:
    return NormalizedStatus(status) in ISSUE_STATUSES
