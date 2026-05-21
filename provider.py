from qgis.core import QgsProcessingProvider
from .algorithms.actualizar_obstrucciones import ActualizarObstrucciones


class AutomatizacionProvider(QgsProcessingProvider):

    def loadAlgorithms(self):
        self.addAlgorithm(ActualizarObstrucciones())

    def id(self):
        return "automatizacion_zona8"

    def name(self):
        return "Automatizacion Zona 8"

    def longName(self):
        return "Automatizacion de capas - Zona 8"
