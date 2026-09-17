"""
Conteo de Problemas por categoria de "Tipo", cruzado con el campo Dentro_Zona.

Dos fuentes, un solo criterio:
    1. La capa problemas_sur cargada en QGIS (se recalcula sola en el panel).
    2. Una planilla CSV o XLSX bajada del sistema (se congela en "Comparacion").

Las dos pasan por clasificar(), asi que si los numeros no coinciden la
diferencia esta en los datos, nunca en el criterio de conteo.

Sin dependencias de UI. La lectura de planillas (CSV o XLSX) esta en
intercambio_im.tabla, el mismo lector que usa el intercambio de permisos con la
IM: antes cada uno tenia el suyo.

OJO: CATEGORIAS esta duplicada en sacar_numeros.py, en la raiz del repo, que es
la version de linea de comandos de este mismo conteo. Si se agrega o se cambia
una categoria hay que tocar los dos archivos o los numeros van a divergir.
"""

from collections import Counter

from .intercambio_im import tabla

CAMPO_TIPO = "Tipo"
CAMPO_ETAPA = "Etapa"
# "Problema" es la columna con el N° de problema en la planilla del sistema;
# "N_Problema" es el campo equivalente en la capa (ver capa_utils.CAMPOS_PASO1).
# Se duplica el nombre de campo en vez de importarlo de capa_utils para que este
# modulo siga sin depender de qgis a nivel de import.
CAMPO_PROBLEMA = "Problema"
CAMPO_N_PROBLEMA = "N_Problema"

# Los problemas finalizados no se cuentan: el panel muestra lo que queda por
# hacer, no el historico. Se compara contra el PREFIJO de la Etapa normalizada,
# asi que "finalizad" toma Finalizada / Finalizado / Finalizadas, pero no
# convierte un hipotetico "No finalizada" en un descarte.
ETAPAS_DESCARTADAS = ("finalizad",)


# Categorias en ORDEN DE PRIORIDAD: gana la primera que coincide y el problema
# se cuenta una sola vez, asi los subtotales suman exactamente el total.
#
# Los patrones se buscan como subcadena dentro del Tipo normalizado (minusculas,
# sin acentos y con los espacios colapsados), asi que sirven tanto los Tipos
# completos como un prefijo ("obstru" tomaria obstruido / obstruida / obstruidas).
CATEGORIAS = [
    ("Inspecciones", (
        "Varios",
        "Acera y/o Pavimento Hundido",
    )),
    ("Tapas", (
        "Boca de Tormenta sin Tapa",
        "Registro sin Tapa",
    )),
    ("Limpieza", (
        "Alcantarilla Obstruida",
        "Boca de Tormenta Obstruida",
        "Cañada Obstruida",
        "Colector Interno Obstruido",
        "Colector Obstruido",
        "Colector Sucio",
        "Conexion Obstruida",
        "Conexion Sucia",
        "Conexión de Boca de Tormenta Obstruida",
        "Registro Sucio",
    )),
    ("Obras", (
        "Alcantarilla Rota",
        "Boca de Tormenta Dañada",
        "Colector Interno Roto",
        "Colector Roto",
        "Conexion Nueva",
        "Conexion Rota",
        "Conexión de Boca de Tormenta Rota",
        "Registro Nuevo",
        "Registro Roto",
        "Reposición de Calzada",
        "Reposición de Vereda",
        "Reposición de Vereda y Calzada",
    )),
]
CATEGORIA_RESTO = "Otros"

# Los Tipo vacios no se mezclan con "Otros": si aparecen, salen en su propia
# fila. Una planilla y una capa que difieren suelen diferir justo aca.
CATEGORIA_SIN_DATO = "(sin dato)"


# ─────────────────────────────────────────────────────────────────────────────
# CLASIFICACION (la unica fuente de verdad, compartida por las dos fuentes)
# ─────────────────────────────────────────────────────────────────────────────
def normalizar(texto):
    """Minusculas, sin acentos y con los espacios colapsados: el mismo Tipo
    escrito 'Boca de tormenta OBSTRUIDA' o 'boca de tormenta obstruida' tiene
    que caer en la misma categoria."""
    return tabla.plegar(texto)


# Los patrones se normalizan una sola vez al importar, no en cada fila.
_CATEGORIAS_NORM = [
    (nombre, tuple(normalizar(p) for p in patrones))
    for nombre, patrones in CATEGORIAS
]


def clasificar(tipo):
    t = normalizar(tipo)
    if not t:
        return CATEGORIA_SIN_DATO
    for nombre, patrones in _CATEGORIAS_NORM:
        if any(p in t for p in patrones):
            return nombre
    return CATEGORIA_RESTO


def es_descartable(etapa):
    """True si la Etapa indica un problema que no hay que contar."""
    t = normalizar(etapa)
    return t.startswith(ETAPAS_DESCARTADAS)


def patrones_categoria(nombre):
    """Los patrones de Tipo (tal cual, sin normalizar) que arman esta categoria,
    o None si la categoria no tiene una lista fija (Otros, sin dato)."""
    for cat_nombre, patrones in CATEGORIAS:
        if cat_nombre == nombre:
            return patrones
    return None


def categorias_visibles(*conteos):
    """Las cuatro categorias fijas mas "Otros", y "(sin dato)" solo si alguno de
    los conteos lo trae: una fila vacia permanente confunde mas de lo que informa."""
    nombres = [nombre for nombre, _ in CATEGORIAS] + [CATEGORIA_RESTO]
    if any(c.get(CATEGORIA_SIN_DATO) for c in conteos):
        nombres.append(CATEGORIA_SIN_DATO)
    return nombres


# ─────────────────────────────────────────────────────────────────────────────
# LECTURA DE LA PLANILLA (CSV o XLSX)
# ─────────────────────────────────────────────────────────────────────────────
def leer_filas(ruta, campo=CAMPO_TIPO, opcionales=(), hoja=None):
    """
    Lista de dicts con la columna obligatoria `campo` y las de `opcionales` que
    existan. Las opcionales que falten simplemente no aparecen en los dicts: la
    planilla del sistema no siempre trae las mismas columnas.
    """
    return tabla.leer_filas(ruta, campo, opcionales, hoja)


def leer_columna(ruta, campo=CAMPO_TIPO, hoja=None):
    """Los valores de una sola columna, en orden."""
    return tabla.leer_columna(ruta, campo, hoja)


def contar_planilla(ruta, campo=CAMPO_TIPO, hoja=None):
    """
    categoria -> cantidad, leyendo la columna Tipo de un CSV o XLSX.

    Si la planilla trae la columna Etapa, los finalizados se descartan igual que
    en la capa. Si no la trae (es lo que pasa hoy: la exportacion del sistema
    solo tiene Problema, Tipo, Ubicacion y Fecha), se cuenta todo, asumiendo que
    el sistema ya exporto unicamente los problemas abiertos.
    """
    conteo = Counter()
    descartados = 0
    for fila in leer_filas(ruta, campo, (CAMPO_ETAPA,), hoja):
        if es_descartable(fila.get(CAMPO_ETAPA)):
            descartados += 1
            continue
        conteo[clasificar(fila[campo])] += 1
    return conteo, descartados


# ─────────────────────────────────────────────────────────────────────────────
# COMPARACION DE IDs: PLANILLA vs CAPA
# ─────────────────────────────────────────────────────────────────────────────
def leer_ids_planilla(ruta, campo=CAMPO_PROBLEMA, hoja=None):
    """Los N° de problema de la planilla, como texto y sin vacios."""
    return [t for t in (str(v).strip() for v in leer_columna(ruta, campo, hoja)) if t]


def ids_capa(capa, campo=CAMPO_N_PROBLEMA, campo_etapa=None):
    """
    Los N° de problema de la capa, como texto y sin vacios.

    Sin campo_etapa recorre toda la capa: un problema finalizado que sigue en
    la capa ya esta cargado igual, asi que cuenta como "presente". Pasando
    campo_etapa (tipicamente CAMPO_ETAPA) descarta los finalizados: es el
    universo a usar cuando del otro lado hay una planilla que, como la del
    sistema, solo trae problemas abiertos.
    """
    from qgis.core import QgsFeatureRequest

    idx = capa.fields().lookupField(campo)
    if idx < 0:
        raise ValueError(
            f"La capa '{capa.name()}' no tiene el campo '{campo}'.\n"
            f"Campos: {', '.join(capa.fields().names())}"
        )
    idx_etapa = capa.fields().lookupField(campo_etapa) if campo_etapa else -1

    atributos = [idx] + ([idx_etapa] if idx_etapa >= 0 else [])
    solicitud = (
        QgsFeatureRequest()
        .setSubsetOfAttributes(atributos)
        .setFlags(QgsFeatureRequest.NoGeometry)
    )
    ids = []
    for feature in capa.getFeatures(solicitud):
        if idx_etapa >= 0 and es_descartable(feature[idx_etapa]):
            continue
        texto = str(feature[idx]).strip()
        if texto and texto.lower() != "null":
            ids.append(texto)
    return ids


def _clave_orden_id(id_):
    """Los N° de problema son numericos casi siempre: ordenarlos como numero
    evita que "10" quede antes que "2"."""
    return (0, int(id_)) if id_.isdigit() else (1, id_)


def ids_faltantes(ids, ids_referencia):
    """
    Los elementos de `ids` que no aparecen en `ids_referencia`, sin duplicados
    y ordenados. Generico y simetrico: sirve tanto para "que hay en la
    planilla que no esta en la capa" como al reves, segun que se le pase.
    """
    presentes = set(ids_referencia)
    return sorted({i for i in ids if i not in presentes}, key=_clave_orden_id)


# ─────────────────────────────────────────────────────────────────────────────
# CONTEO DE LA CAPA, CRUZADO CON FUERA / DENTRO DE ZONA
# ─────────────────────────────────────────────────────────────────────────────
# El campo se llama Dentro_Zona y guarda "Si" cuando el problema cae DENTRO.
# Se escribe con el tipo que tenga la capa: texto, bool o entero (ver
# _valor_dentro_zona en capa_utils), asi que hay que aceptar los tres. Un NULL
# no es un "No": un problema sin clasificar no es un problema que este fuera.
CAMPO_DENTRO_ZONA = "Dentro_Zona"

_SI = {"si", "s", "true", "t", "1", "x", "dentro"}
_NO = {"no", "n", "false", "f", "0", "fuera"}


def interpretar_dentro_zona(valor):
    """True = dentro de la zona, False = fuera, None = sin clasificar."""
    if valor is None:
        return None
    # Los NULL de QGIS llegan como QVariant nulo, no como None.
    if hasattr(valor, "isNull") and valor.isNull():
        return None
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        return bool(valor)
    texto = normalizar(valor)
    if not texto or texto == "null":
        return None
    if texto in _SI:
        return True
    if texto in _NO:
        return False
    return None


class ConteoCapa:
    """
    Resultado de recorrer la capa una vez.

    fuera / dentro / sin_clasificar son Counter de categoria -> cantidad.
    total suma los tres, asi que si hay features sin Dentro_Zona la fila no
    cierra entre las dos primeras columnas: es a proposito, el panel lo avisa.

    descartados cuenta los finalizados, que no entran en ningun Counter.
    """

    def __init__(self):
        self.fuera = Counter()
        self.dentro = Counter()
        self.sin_clasificar = Counter()
        self.total = Counter()
        self.descartados = 0   # finalizados que quedaron fuera del conteo

    def agregar(self, tipo, dentro):
        categoria = clasificar(tipo)
        self.total[categoria] += 1
        if dentro is True:
            self.dentro[categoria] += 1
        elif dentro is False:
            self.fuera[categoria] += 1
        else:
            self.sin_clasificar[categoria] += 1

    @property
    def n_sin_clasificar(self):
        return sum(self.sin_clasificar.values())


def contar_capa(capa, campo_tipo=CAMPO_TIPO, campo_dentro_zona=CAMPO_DENTRO_ZONA,
                campo_etapa=CAMPO_ETAPA):
    """
    Recorre los features de la capa y los cuenta por categoria x zona.

    Respeta el filtro de la capa (subset string) porque usa getFeatures(): si la
    capa esta filtrada en el panel de capas, los numeros son los del filtro.
    Los problemas con Etapa finalizada se descartan y se cuentan aparte en
    ConteoCapa.descartados. Pide solo los atributos que usa y ninguna
    geometria, que es lo que hace viable recalcular en cada edicion.
    """
    from qgis.core import QgsFeatureRequest

    idx_tipo = capa.fields().lookupField(campo_tipo)
    if idx_tipo < 0:
        raise ValueError(
            f"La capa '{capa.name()}' no tiene el campo '{campo_tipo}'.\n"
            f"Campos: {', '.join(capa.fields().names())}"
        )
    idx_dz = capa.fields().lookupField(campo_dentro_zona)
    idx_etapa = capa.fields().lookupField(campo_etapa)

    atributos = [idx_tipo] + [i for i in (idx_dz, idx_etapa) if i >= 0]
    solicitud = (
        QgsFeatureRequest()
        .setSubsetOfAttributes(atributos)
        .setFlags(QgsFeatureRequest.NoGeometry)
    )

    conteo = ConteoCapa()
    for feature in capa.getFeatures(solicitud):
        # Los finalizados no entran en ninguna columna: el panel muestra lo que
        # queda por hacer. Si la capa no tiene el campo Etapa no se descarta nada.
        if idx_etapa >= 0 and es_descartable(feature[idx_etapa]):
            conteo.descartados += 1
            continue
        dentro = interpretar_dentro_zona(feature[idx_dz]) if idx_dz >= 0 else None
        conteo.agregar(feature[idx_tipo], dentro)
    return conteo
