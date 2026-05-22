from qgis.core import QgsApplication
from .provider import AutomatizacionZona8Provider


class AutomatizacionZona8Plugin:
    """Clase principal del plugin. Registra y desregistra el provider de Processing."""

    def __init__(self, iface):
        self.iface    = iface
        self.provider = None

    def initGui(self):
        self.provider = AutomatizacionZona8Provider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def unload(self):
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
