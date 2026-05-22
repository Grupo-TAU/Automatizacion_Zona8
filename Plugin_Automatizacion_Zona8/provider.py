from qgis.core import QgsProcessingProvider
from .algorithms.actualizar_reclamos_zona8 import ActualizarReclamos


class AutomatizacionZona8Provider(QgsProcessingProvider):

    def loadAlgorithms(self):
        self.addAlgorithm(ActualizarReclamos())

    def id(self):
        return "automatizacion_zona8"

    def name(self):
        return "Automatizacion Zona 8"

    def longName(self):
        return "Automatizacion de capas - Zona 8"
