"""
Acceso a capas del proyecto y escritura del feature de OS.
Sin dependencias de UI: reutilizable desde la consola o desde tests.
"""

from qgis.core import (
    QgsProject,
    QgsFeature,
    QgsGeometry,
    QgsCoordinateTransform,
    QgsSpatialIndex,
    QgsCsException,
)
from PyQt5.QtCore import QVariant, QDate

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────
# TODO: si esto varía entre las 6 PCs, migrar a QgsSettings en vez de constante.
RAIZ_IMAGENES = r"G:\Unidades compartidas\GRUPO TAU\INTENDENCIA DE MONTEVIDEO\SOMS\IMAGENES_OS"

CAPA_OS = "problemas_sur"
CAPA_PADRONES = "padrones"
CAMPO_PADRON = "padron"

CAPA_ZONA = "Zona_delimitada"
CAMPO_FUERA_ZONA = "fuera_zona"

CAMPOS_PASO1 = [
    ("N°_OS", QVariant.String),
    ("Ubicación", QVariant.String),
    ("Fecha_Ingreso", QVariant.Date),
    ("Descripción", QVariant.String),
    ("N_Problema", QVariant.String),
    ("Contrato", QVariant.String),
    ("N° Trabajo", QVariant.String),
    ("Tipo", QVariant.String),
    ("Etapa", QVariant.String),
    ("Restringir", QVariant.String),
]


def obtener_capa(nombre):
    """
    Busca la capa por nombre exacto y, si no aparece, reintenta ignorando
    mayusculas/minusculas: los nombres de capa del proyecto no siempre respetan
    el mismo casing (problemas_sur / Problemas_Sur) y mapLayersByName()
    distingue mayusculas.
    """
    capas = QgsProject.instance().mapLayersByName(nombre)
    if capas:
        return capas[0]

    objetivo = nombre.casefold()
    for capa in QgsProject.instance().mapLayers().values():
        if capa.name().casefold() == objetivo:
            return capa
    return None


def buscar_punto_padron(numero_padron):
    """
    Busca en CAPA_PADRONES el feature cuyo CAMPO_PADRON coincide con
    numero_padron y devuelve el centroide (QgsPointXY) reproyectado al CRS
    del proyecto. Devuelve None si no hay 0 o más de 1 coincidencia, o si
    capa/campo no existen (el usuario deberá hacer clic manualmente).
    """
    capa = obtener_capa(CAPA_PADRONES)
    if capa is None:
        return None

    idx = capa.fields().indexOf(CAMPO_PADRON)
    if idx < 0:
        return None

    numero_padron = str(numero_padron).strip()
    coincidencias = [
        f for f in capa.getFeatures()
        if str(f[CAMPO_PADRON]).strip() == numero_padron
    ]
    if len(coincidencias) != 1:
        return None

    geom = coincidencias[0].geometry()
    if geom is None or geom.isEmpty():
        return None

    punto = geom.centroid().asPoint()
    crs_proyecto = QgsProject.instance().crs()
    if capa.crs() != crs_proyecto:
        transformador = QgsCoordinateTransform(capa.crs(), crs_proyecto, QgsProject.instance())
        punto = transformador.transform(punto)

    return punto


# ─────────────────────────────────────────────────────────────────────────────
# CLASIFICACIÓN DENTRO / FUERA DE LA ZONA DELIMITADA
# ─────────────────────────────────────────────────────────────────────────────
def construir_clasificador(capa_zonas, crs_puntos):
    """
    Devuelve fn(geom_punto) -> bool|None. True = el punto cae fuera de la zona.
    None = no se pudo determinar (geometría vacía o error de reproyección).

    El índice espacial y las geometrías se cargan una sola vez, así que conviene
    construir el clasificador una vez y reutilizarlo para varios puntos
    (ej: al cargar un itinerario completo).
    """
    geoms = {f.id(): f.geometry() for f in capa_zonas.getFeatures()}
    idx = QgsSpatialIndex()
    for fid, g in geoms.items():
        idx.addFeature(fid, g.boundingBox())

    tr = QgsCoordinateTransform(crs_puntos, capa_zonas.crs(), QgsProject.instance())

    def fuera_zona(geom_punto):
        if geom_punto is None or geom_punto.isEmpty():
            return None
        g = QgsGeometry(geom_punto)
        try:
            g.transform(tr)
        except QgsCsException:
            return None
        for fid in idx.intersects(g.boundingBox()):
            if geoms[fid].intersects(g):
                return False
        return True

    return fuera_zona


def clasificar_fuera_zona(geom_punto):
    """
    Atajo para un único punto: True si cae fuera de CAPA_ZONA, False si cae
    dentro, None si la capa no está en el proyecto o no se pudo determinar.
    """
    capa = obtener_capa(CAPA_ZONA)
    if capa is None:
        return None
    return construir_clasificador(capa, QgsProject.instance().crs())(geom_punto)


def _valor_fuera_zona(capa, idx_campo, fuera):
    """Adapta el bool al tipo real del campo en la capa (bool / texto / entero)."""
    tipo = capa.fields().at(idx_campo).type()
    if tipo == QVariant.Bool:
        return fuera
    if tipo in (QVariant.Int, QVariant.LongLong, QVariant.Double):
        return int(fuera)
    return "Si" if fuera else "No"


def agregar_feature_os(datos, punto_xy, fuera_zona=None):
    """
    Da de alta la OS en CAPA_OS y devuelve el valor calculado de fuera_zona
    (True/False/None). `fuera_zona` permite pasar un clasificador ya construido
    con construir_clasificador() para no releer las zonas en cada alta.
    """
    capa = obtener_capa(CAPA_OS)
    if capa is None:
        raise ValueError(f"No se encontró la capa '{CAPA_OS}' en el proyecto.")

    if not capa.isEditable():
        capa.startEditing()

    feat = QgsFeature(capa.fields())
    geom = QgsGeometry.fromPointXY(punto_xy)
    feat.setGeometry(geom)

    indices_seteados = set()

    # "fuera_zona" no se pide en el formulario: se resuelve intersectando el
    # punto con CAPA_ZONA. Si la capa no está cargada, el campo queda NULL.
    fuera = clasificar_fuera_zona(geom) if fuera_zona is None else fuera_zona(geom)
    idx_fz = capa.fields().lookupField(CAMPO_FUERA_ZONA)
    if idx_fz >= 0 and fuera is not None:
        feat.setAttribute(idx_fz, _valor_fuera_zona(capa, idx_fz, fuera))
        indices_seteados.add(idx_fz)

    for nombre_campo, tipo in CAMPOS_PASO1:
        idx = capa.fields().indexOf(nombre_campo)
        if idx >= 0 and nombre_campo in datos:
            valor = datos[nombre_campo]
            if tipo == QVariant.Date and isinstance(valor, str) and valor:
                valor = QDate.fromString(valor, "dd/MM/yyyy")
            feat.setAttribute(idx, valor)
            indices_seteados.add(idx)

    # Evaluar expresiones por defecto de la capa para campos no seteados
    # (ej: "N° Trabajo" con expresión maximum("N° Trabajo") + 1)
    for idx in range(capa.fields().count()):
        if idx not in indices_seteados:
            defn = capa.defaultValueDefinition(idx)
            if defn.isValid():
                feat.setAttribute(idx, capa.defaultValue(idx))

    capa.addFeature(feat)
    capa.commitChanges()
    capa.triggerRepaint()

    return fuera
