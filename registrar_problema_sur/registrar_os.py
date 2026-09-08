import os

from PyQt5.QtWidgets import QAction
from PyQt5.QtGui import QIcon


class RegistrarOSPlugin:
    """Punto de entrada del plugin: alta/baja de los botones en la GUI de QGIS."""

    def __init__(self, iface):
        self.iface = iface
        self.acciones = []
        self.dlg = None    # referencia persistente, evita que el GC destruya el diálogo no-modal
        self.panel = None  # dock del conteo; se crea la primera vez que se abre

    def _accion(self, texto, callback, icono=None):
        accion = QAction(QIcon(icono) if icono else QIcon(), texto, self.iface.mainWindow())
        accion.triggered.connect(callback)
        self.iface.addToolBarIcon(accion)
        self.iface.addPluginToMenu("&Grupo TAU", accion)
        self.acciones.append(accion)
        return accion

    def initGui(self):
        icono = os.path.join(os.path.dirname(__file__), "icon.png")
        self._accion("Registrar OS", self.run, icono)
        self._accion("Conteo de Problemas", self.abrir_conteo, icono)

    def unload(self):
        for accion in self.acciones:
            self.iface.removePluginMenu("&Grupo TAU", accion)
            self.iface.removeToolBarIcon(accion)
        self.acciones = []

        if self.panel is not None:
            self.panel.limpiar()
            self.iface.removeDockWidget(self.panel)
            self.panel.deleteLater()
            self.panel = None

    def run(self):
        from .dialogo_registro_os import DialogoRegistroOS
        self.dlg = DialogoRegistroOS()
        self.dlg.show()  # no-modal: permite clic en el mapa con el diálogo abierto

    def abrir_conteo(self):
        from .panel_conteo import abrir_panel
        abrir_panel(self)
