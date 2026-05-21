from .provider import AutomatizacionProvider


def classFactory(iface):
    return AutomatizacionPlugin(iface)


class AutomatizacionPlugin:

    def __init__(self, iface):
        self.iface     = iface
        self.provider  = None

    def initProcessing(self):
        self.provider = AutomatizacionProvider()
        from qgis.core import QgsApplication
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()

    def unload(self):
        if self.provider:
            from qgis.core import QgsApplication
            QgsApplication.processingRegistry().removeProvider(self.provider)
