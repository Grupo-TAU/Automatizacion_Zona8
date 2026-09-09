"""Helpers compartidos por los dos algoritmos del intercambio con la IM."""

from qgis.core import QgsProcessingException

from ..intercambio_im import nucleo


def indice_campo(capa, nombre: str) -> int:
    """
    Indice del campo, o QgsProcessingException con la lista de campos reales.

    lookupField y no indexOf: es insensible a mayusculas y contempla alias. Al
    migrar a PostGIS los nombres llegan laundered (Permisos_UCCRIU pasa a
    permisos_uccriu) y con indexOf el algoritmo dejaria de encontrarlos.
    """
    indice = capa.fields().lookupField(nombre)
    if indice < 0:
        raise QgsProcessingException(
            f"La capa '{capa.name()}' no tiene el campo '{nombre}'.\n"
            f"Campos disponibles: {', '.join(capa.fields().names())}"
        )
    return indice


def advertir_si_fid(feedback, campo_clave: str) -> None:
    if campo_clave == nucleo.CLAVE_FID:
        feedback.pushWarning(f"ADVERTENCIA: {nucleo.ADVERTENCIA_FID}")


def describir_capa(capa) -> str:
    return f"{capa.name()} [{capa.dataProvider().name()}] {capa.source()}"
