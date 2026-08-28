import os

from PyQt5.QtWidgets import QAction
from PyQt5.QtGui import QIcon


class RegistrarOSPlugin:
    """Punto de entrada del plugin: alta/baja de los botones en la GUI de QGIS."""

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dlg = None  # referencia persistente, evita que el GC destruya el diálogo no-modal

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        self.action = QAction(QIcon(icon_path), "Registrar OS", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&Grupo TAU", self.action)

    def unload(self):
        self.iface.removePluginMenu("&Grupo TAU", self.action)
        self.iface.removeToolBarIcon(self.action)

    def run(self):
        from .dialogo_registro_os import DialogoRegistroOS
        self.dlg = DialogoRegistroOS()
        self.dlg.show()  # no-modal: permite clic en el mapa con el diálogo abierto
