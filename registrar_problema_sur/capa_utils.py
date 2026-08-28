"""
Acceso a capas del proyecto y escritura del feature de OS.
Sin dependencias de UI: reutilizable desde la consola o desde tests.
"""

from qgis.core import (
    QgsProject,
    QgsFeature,
    QgsGeometry,
    QgsCoordinateTransform,
)
from PyQt5.QtCore import QVariant, QDate

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────
# TODO: si esto varía entre las 6 PCs, migrar a QgsSettings en vez de constante.
RAIZ_IMAGENES = r"G:\Unidades compartidas\GRUPO TAU\INTENDENCIA DE MONTEVIDEO\SOMS\IMAGENES_OS"

CAPA_OS = "inspecciones_OS"
CAPA_PADRONES = "padrones"
CAMPO_PADRON = "padron"

CAPA_FOTOS_OS = "fotos_OS"
CAMPO_FOTOS_OS_N_OS = "N°_OS"
CAMPO_FOTOS_OS_RUTA = "ruta_relativa"
# CAMPO_FOTOS_OS_RUTA es relativa a esta carpeta.
CARPETA_ORIGEN_FOTOS = r"C:\Proyectos-QGisCloud\QField\cloud\inspecciones_os"

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
    capas = QgsProject.instance().mapLayersByName(nombre)
    return capas[0] if capas else None


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


def buscar_rutas_fotos_os(numero_os):
    """
    Devuelve la lista de rutas (campo CAMPO_FOTOS_OS_RUTA, relativa a
    CARPETA_ORIGEN_FOTOS) de las fotos asociadas a numero_os en la capa
    CAPA_FOTOS_OS.
    """
    capa = obtener_capa(CAPA_FOTOS_OS)
    if capa is None:
        raise ValueError(f"No se encontró la capa '{CAPA_FOTOS_OS}' en el proyecto.")

    idx = capa.fields().indexOf(CAMPO_FOTOS_OS_N_OS)
    if idx < 0:
        raise ValueError(f"La capa '{CAPA_FOTOS_OS}' no tiene el campo '{CAMPO_FOTOS_OS_N_OS}'.")

    numero_os = str(numero_os).strip()
    return [
        f[CAMPO_FOTOS_OS_RUTA]
        for f in capa.getFeatures()
        if str(f[CAMPO_FOTOS_OS_N_OS]).strip() == numero_os and f[CAMPO_FOTOS_OS_RUTA]
    ]


def agregar_feature_os(datos, punto_xy):
    capa = obtener_capa(CAPA_OS)
    if capa is None:
        raise ValueError(f"No se encontró la capa '{CAPA_OS}' en el proyecto.")

    if not capa.isEditable():
        capa.startEditing()

    feat = QgsFeature(capa.fields())
    feat.setGeometry(QgsGeometry.fromPointXY(punto_xy))

    indices_seteados = set()
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
