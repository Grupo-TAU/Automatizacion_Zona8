from qgis.core import QgsProcessingProvider
from .algorithms.actualizar_reclamos_zona8 import ActualizarReclamos
from .algorithms.exportar_permisos_im import ExportarPermisosIM
from .algorithms.mergear_permisos_im import MergearPermisosIM


class AutomatizacionZona8Provider(QgsProcessingProvider):

    def loadAlgorithms(self):
        self.addAlgorithm(ActualizarReclamos())
        self.addAlgorithm(ExportarPermisosIM())
        self.addAlgorithm(MergearPermisosIM())

    def id(self):
        return "automatizacion_zona8"

    def name(self):
        return "Automatizacion Zona 8"

    def longName(self):
        return "Automatizacion de capas - Zona 8"
