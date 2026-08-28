from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog, QMessageBox,
)

from .copiar_imagenes import copiar_imagenes_os


class DialogoCopiarImagenes(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Copiar imágenes de OS")
        self.setMinimumWidth(480)
        self.carpeta_destino = ""
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form = QFormLayout()
        self.f_numero_os = QLineEdit()
        self.f_numero_os.setPlaceholderText("ej: 5337775")
        form.addRow("N° de OS:", self.f_numero_os)
        layout.addLayout(form)

        h_dest = QHBoxLayout()
        btn_destino = QPushButton("Elegir carpeta destino…")
        btn_destino.clicked.connect(self._elegir_destino)
        self.lbl_destino = QLabel("Sin carpeta seleccionada")
        self.lbl_destino.setStyleSheet("color:#999; font-style:italic;")
        h_dest.addWidget(btn_destino)
        h_dest.addWidget(self.lbl_destino, 1)
        layout.addLayout(h_dest)

        hbox = QHBoxLayout()
        btn_ok = QPushButton("Copiar imágenes")
        btn_cancel = QPushButton("Cancelar")
        btn_ok.setDefault(True)
        btn_ok.setMinimumHeight(30)
        btn_ok.setStyleSheet(
            "QPushButton{background:#325423;color:white;font-weight:bold;"
            "border-radius:3px;padding:0 14px;}"
            "QPushButton:hover{background:#3e6a2c;}"
        )
        btn_ok.clicked.connect(self._copiar)
        btn_cancel.clicked.connect(self.reject)
        hbox.addStretch()
        hbox.addWidget(btn_cancel)
        hbox.addWidget(btn_ok)
        layout.addLayout(hbox)

    def _elegir_destino(self):
        carpeta = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta destino")
        if not carpeta:
            return
        self.carpeta_destino = carpeta
        self.lbl_destino.setText(carpeta)
        self.lbl_destino.setStyleSheet("color:green; font-weight:bold;")

    def _copiar(self):
        numero_os = self.f_numero_os.text().strip()
        if not numero_os:
            QMessageBox.warning(self, "Falta el N° de OS", "Ingresá el N° de OS a copiar.")
            return
        if not self.carpeta_destino:
            QMessageBox.warning(self, "Falta el destino", "Elegí la carpeta destino.")
            return

        try:
            copiadas, faltantes = copiar_imagenes_os(numero_os, self.carpeta_destino)
        except Exception as e:
            QMessageBox.critical(self, "Error al copiar imágenes", str(e))
            return

        if not copiadas and not faltantes:
            QMessageBox.information(
                self, "Sin resultados",
                f"No se encontraron fotos para la OS {numero_os} en la capa 'fotos_os'."
            )
            return

        mensaje = f"✓ {len(copiadas)} imagen(es) copiada(s) a:\n{self.carpeta_destino}"
        if faltantes:
            mensaje += (
                f"\n\n⚠ {len(faltantes)} archivo(s) no encontrado(s) en disco:\n"
                + "\n".join(faltantes)
            )
        QMessageBox.information(self, "Copia de imágenes", mensaje)
        self.accept()
