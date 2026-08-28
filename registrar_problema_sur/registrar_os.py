import os

from PyQt5.QtWidgets import QAction
from PyQt5.QtGui import QIcon


class RegistrarOSPlugin:
    """Punto de entrada del plugin: alta/baja de los botones en la GUI de QGIS."""

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.action_copiar_imagenes = None
        self.dlg = None  # referencia persistente, evita que el GC destruya el diálogo no-modal
        self.dlg_copiar_imagenes = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        self.action = QAction(QIcon(icon_path), "Registrar OS", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&Grupo TAU", self.action)

        self.action_copiar_imagenes = QAction(
            QIcon(icon_path), "Copiar imágenes de OS", self.iface.mainWindow()
        )
        self.action_copiar_imagenes.triggered.connect(self.run_copiar_imagenes)
        self.iface.addPluginToMenu("&Grupo TAU", self.action_copiar_imagenes)

    def unload(self):
        self.iface.removePluginMenu("&Grupo TAU", self.action)
        self.iface.removeToolBarIcon(self.action)
        self.iface.removePluginMenu("&Grupo TAU", self.action_copiar_imagenes)

    def run(self):
        from .dialogo_registro_os import DialogoRegistroOS
        self.dlg = DialogoRegistroOS()
        self.dlg.show()  # no-modal: permite clic en el mapa con el diálogo abierto

    def run_copiar_imagenes(self):
        from .dialogo_copiar_imagenes import DialogoCopiarImagenes
        self.dlg_copiar_imagenes = DialogoCopiarImagenes()
        self.dlg_copiar_imagenes.show()
