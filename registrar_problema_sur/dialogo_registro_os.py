import os

from qgis.gui import QgsMapToolEmitPoint
from qgis.utils import iface

from PyQt5.QtCore import Qt, QDate
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog,
    QMessageBox, QGroupBox, QApplication, QComboBox,
)
from PyQt5.QtGui import QFont, QCursor

from .capa_utils import RAIZ_IMAGENES, buscar_punto_padron, agregar_feature_os, CAPA_PADRONES
from .pdf_parser import (
    parsear_pdf_os, parsear_pdf_itinerario, pdfplumber_disponible, instalar_pdfplumber,
)


class _CapturadorPunto(QgsMapToolEmitPoint):
    """Herramienta temporal: captura un clic izquierdo en el canvas."""

    def __init__(self, canvas, callback):
        super().__init__(canvas)
        self.canvasClicked.connect(callback)


class DialogoRegistroOS(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Registrar OS")
        self.setMinimumWidth(540)
        self.punto_xy = None
        self._capturador = None
        self._herramienta_previa = None
        self._cola_itinerario = []  # datos de las OS pendientes (excluye la que está en pantalla)
        self._total_itinerario = 0  # 0 = no se está cargando un itinerario
        self._nombre_itinerario = ""
        self._build_ui()

    def _campo(self, placeholder=""):
        w = QLineEdit()
        w.setPlaceholderText(placeholder)
        return w

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        titulo = QLabel("Registro de OS — Datos del correo")
        titulo.setFont(QFont("Arial", 13, QFont.Bold))
        layout.addWidget(titulo)

        # ── Ubicación en el mapa ─────────────────────────────────────────
        grp_mapa = QGroupBox("Ubicación en el mapa *")
        h_mapa = QHBoxLayout(grp_mapa)
        btn_punto = QPushButton("Hacer clic en el mapa…")
        btn_punto.setMinimumHeight(30)
        btn_punto.setStyleSheet(
            "QPushButton{background:#0a3d62;color:white;font-weight:bold;"
            "border-radius:3px;padding:0 14px;}"
            "QPushButton:hover{background:#1a5276;}"
        )
        btn_punto.clicked.connect(self._activar_captura)
        self.lbl_punto = QLabel("Sin punto seleccionado")
        self.lbl_punto.setStyleSheet("color:#999; font-style:italic;")
        h_mapa.addWidget(btn_punto)
        h_mapa.addWidget(self.lbl_punto, 1)
        layout.addWidget(grp_mapa)

        # ── Carga desde PDF ──────────────────────────────────────────────
        grp_pdf = QGroupBox("Carga automática desde PDF")
        h_pdf = QHBoxLayout(grp_pdf)
        btn_pdf = QPushButton("Cargar desde PDF…")
        btn_pdf.setMinimumHeight(30)
        btn_pdf.setStyleSheet(
            "QPushButton{background:#325423;color:white;font-weight:bold;"
            "border-radius:3px;padding:0 14px;}"
            "QPushButton:hover{background:#3e6a2c;}"
        )
        btn_pdf.clicked.connect(self._cargar_pdf)

        btn_itinerario = QPushButton("Cargar Itinerario (varias OS)…")
        btn_itinerario.setMinimumHeight(30)
        btn_itinerario.setStyleSheet(
            "QPushButton{background:#5a3d8a;color:white;font-weight:bold;"
            "border-radius:3px;padding:0 14px;}"
            "QPushButton:hover{background:#6f4ba8;}"
        )
        btn_itinerario.clicked.connect(self._cargar_itinerario)

        self.lbl_pdf = QLabel("Sin PDF seleccionado")
        self.lbl_pdf.setStyleSheet("color:#666; font-style:italic;")
        h_pdf.addWidget(btn_pdf)
        h_pdf.addWidget(btn_itinerario)
        h_pdf.addWidget(self.lbl_pdf, 1)

        self.btn_instalar_dep = QPushButton("Instalar dependencia (pdfplumber)…")
        self.btn_instalar_dep.setVisible(not pdfplumber_disponible())
        self.btn_instalar_dep.clicked.connect(self._instalar_dependencia)
        h_pdf.addWidget(self.btn_instalar_dep)

        layout.addWidget(grp_pdf)

        self.lbl_progreso_itinerario = QLabel("")
        self.lbl_progreso_itinerario.setStyleSheet(
            "color:#5a3d8a; font-weight:bold; font-style:italic;"
        )
        self.lbl_progreso_itinerario.setVisible(False)
        layout.addWidget(self.lbl_progreso_itinerario)

        # ── Datos ────────────────────────────────────────────────────────
        grp = QGroupBox("Datos de la OS")
        form = QFormLayout(grp)
        form.setSpacing(6)

        self.f_orden_servicio = self._campo("ej: 5337775")
        self.f_fecha_ingreso = self._campo("dd/mm/aaaa")
        self.f_fecha_ingreso.setText(QDate.currentDate().toString("dd/MM/yyyy"))
        self.f_ubicacion = self._campo("ej: 25 DE MAYO Nº 259")
        self.f_descripcion = self._campo("ej: Inspección cámara televisada")
        self.f_n_problema = self._campo("ej: 123456")
        self.f_contrato = self._campo("ej: Baderery-Giberol")
        self.f_n_trabajo = self._campo("ej: 12345")
        self.f_tipo = self._campo("ej: Reclamo")
        self.f_etapa = QComboBox()
        self.f_etapa.addItems(["Pendiente", "Limpieza"])

        form.addRow("Orden de Servicio:", self.f_orden_servicio)
        form.addRow("Fecha ingreso:", self.f_fecha_ingreso)
        form.addRow("Ubicación:", self.f_ubicacion)
        form.addRow("Descripción:", self.f_descripcion)
        form.addRow("N° Problema:", self.f_n_problema)
        form.addRow("Contrato:", self.f_contrato)
        form.addRow("N° Trabajo:", self.f_n_trabajo)
        form.addRow("Tipo:", self.f_tipo)
        form.addRow("Etapa:", self.f_etapa)
        layout.addWidget(grp)

        # ── Botones ──────────────────────────────────────────────────────
        hbox = QHBoxLayout()
        btn_ok = QPushButton("✓ Registrar OS")
        btn_cancel = QPushButton("Cancelar")
        btn_ok.setDefault(True)
        btn_ok.setMinimumHeight(34)
        btn_ok.setStyleSheet(
            "QPushButton{background:#325423;color:white;font-weight:bold;"
            "border-radius:3px;padding:0 14px;}"
            "QPushButton:hover{background:#3e6a2c;}"
        )
        btn_ok.clicked.connect(self._aceptar)
        btn_cancel.clicked.connect(self.reject)
        hbox.addStretch()
        hbox.addWidget(btn_cancel)
        hbox.addWidget(btn_ok)
        layout.addLayout(hbox)

    # ── Captura de punto en el mapa ──────────────────────────────────────────
    def _activar_captura(self):
        canvas = iface.mapCanvas()
        self._herramienta_previa = canvas.mapTool()
        self._capturador = _CapturadorPunto(canvas, self._punto_capturado)
        canvas.setMapTool(self._capturador)
        self.lbl_punto.setText("Hacé clic en el mapa…")
        self.lbl_punto.setStyleSheet("color:#e67e00; font-weight:bold;")

    def _punto_capturado(self, punto, boton):
        if boton != Qt.LeftButton:
            return
        self.punto_xy = punto
        iface.mapCanvas().setMapTool(self._herramienta_previa)
        self._capturador = None
        self.lbl_punto.setText(f"X: {punto.x():.2f} | Y: {punto.y():.2f}")
        self.lbl_punto.setStyleSheet("color:green; font-weight:bold;")
        self.raise_()
        self.activateWindow()

    # ── Instalación de pdfplumber ─────────────────────────────────────────────
    def _instalar_dependencia(self):
        respuesta = QMessageBox.question(
            self, "Instalar pdfplumber",
            "Esto va a instalar la librería pdfplumber en el intérprete de QGIS "
            "usando pip (requiere conexión a internet). Puede tardar unos segundos.\n\n"
            "¿Confirmás la instalación?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if respuesta != QMessageBox.Yes:
            return

        QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
        try:
            ok, salida = instalar_pdfplumber()
        finally:
            QApplication.restoreOverrideCursor()

        if ok and pdfplumber_disponible():
            self.btn_instalar_dep.setVisible(False)
            QMessageBox.information(self, "Instalación completa", "pdfplumber se instaló correctamente.")
        else:
            QMessageBox.critical(
                self, "Error al instalar",
                "No se pudo instalar pdfplumber.\n\nSalida de pip:\n" + salida[-2000:],
            )

    # ── Carga desde PDF ──────────────────────────────────────────────────────
    def _limpiar_formulario(self):
        for widget in (
            self.f_orden_servicio, self.f_ubicacion, self.f_descripcion,
            self.f_n_problema, self.f_contrato, self.f_n_trabajo, self.f_tipo,
        ):
            widget.clear()
        self.f_fecha_ingreso.setText(QDate.currentDate().toString("dd/MM/yyyy"))
        self.f_etapa.setCurrentIndex(0)
        self.punto_xy = None
        self.lbl_punto.setText("Sin punto seleccionado")
        self.lbl_punto.setStyleSheet("color:#999; font-style:italic;")

    def _aplicar_datos_a_formulario(self, datos):
        """
        Completa los campos con datos parseados de un PDF y, si hay padrón,
        intenta ubicar el punto automáticamente. Devuelve True si el punto
        quedó ubicado.
        """
        setters = {
            'orden_servicio': self.f_orden_servicio,
            'fecha_ingreso': self.f_fecha_ingreso,
            'descripcion': self.f_descripcion,
            'ubicacion': self.f_ubicacion,
            'n_problema': self.f_n_problema,
            'sector': self.f_contrato,
            'tipo': self.f_tipo,
        }
        for clave, widget in setters.items():
            if clave in datos:
                widget.setText(datos[clave])

        if 'padron' not in datos:
            return False

        punto = buscar_punto_padron(datos['padron'])
        if punto is None:
            return False

        self.punto_xy = punto
        self.lbl_punto.setText(
            f"X: {punto.x():.2f} | Y: {punto.y():.2f} (Padrón {datos['padron']} — automático)"
        )
        self.lbl_punto.setStyleSheet("color:green; font-weight:bold;")
        return True

    def _cargar_pdf(self):
        ruta_pdf, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar PDF de la OS", RAIZ_IMAGENES, "PDF (*.pdf *.PDF)"
        )
        if not ruta_pdf:
            return

        try:
            datos = parsear_pdf_os(ruta_pdf)
        except ImportError as e:
            QMessageBox.warning(self, "Librería faltante", str(e))
            return
        except Exception as e:
            QMessageBox.warning(self, "Error al leer el PDF", str(e))
            return

        ubicado = self._aplicar_datos_a_formulario(datos)

        self.lbl_pdf.setText(f"✓ {os.path.basename(ruta_pdf)}")
        self.lbl_pdf.setStyleSheet("color:green; font-weight:bold;")

        if 'padron' in datos and not ubicado:
            QMessageBox.warning(
                self, "Padrón no ubicado",
                f"No se encontró (o hay más de una coincidencia para) el "
                f"padrón {datos['padron']} en la capa '{CAPA_PADRONES}'.\n\n"
                "Hacé clic manualmente en el mapa para ubicar la OS."
            )

    # ── Carga desde PDF de Itinerario (varias OS) ─────────────────────────────
    def _cargar_itinerario(self):
        ruta_pdf, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar PDF de Itinerario", RAIZ_IMAGENES, "PDF (*.pdf *.PDF)"
        )
        if not ruta_pdf:
            return

        try:
            datos_lista = parsear_pdf_itinerario(ruta_pdf)
        except ImportError as e:
            QMessageBox.warning(self, "Librería faltante", str(e))
            return
        except Exception as e:
            QMessageBox.warning(self, "Error al leer el PDF", str(e))
            return

        if not datos_lista:
            QMessageBox.warning(
                self, "Sin OS encontradas",
                "No se pudo identificar ninguna Orden de Servicio en este PDF."
            )
            return

        self._nombre_itinerario = os.path.basename(ruta_pdf)
        self._total_itinerario = len(datos_lista)
        self._cola_itinerario = datos_lista[1:]
        self._cargar_item_de_cola(datos_lista[0])

    def _cargar_item_de_cola(self, datos):
        self._limpiar_formulario()
        ubicado = self._aplicar_datos_a_formulario(datos)

        indice = self._total_itinerario - len(self._cola_itinerario)
        numero_os = datos.get('orden_servicio', '?')

        self.lbl_progreso_itinerario.setText(
            f"Itinerario: OS {indice} de {self._total_itinerario} (N° {numero_os}) "
            f"— {self._nombre_itinerario}"
        )
        self.lbl_progreso_itinerario.setVisible(True)

        self.lbl_pdf.setText(f"✓ {self._nombre_itinerario} — OS {indice}/{self._total_itinerario}")
        self.lbl_pdf.setStyleSheet("color:green; font-weight:bold;")

        if 'padron' in datos and not ubicado:
            QMessageBox.warning(
                self, "Padrón no ubicado",
                f"OS {numero_os}: no se encontró (o hay más de una coincidencia para) "
                f"el padrón {datos['padron']} en la capa '{CAPA_PADRONES}'.\n\n"
                "Hacé clic manualmente en el mapa para ubicarla."
            )

    # ── Validación ───────────────────────────────────────────────────────────
    def _validar(self):
        errores = []
        if self.punto_xy is None:
            errores.append("• Hacé clic en el mapa para ubicar la OS.")
        if not self.f_orden_servicio.text().strip():
            errores.append("• Orden de Servicio es obligatoria.")
        if errores:
            QMessageBox.warning(self, "Campos incompletos", "\n".join(errores))
            return False
        return True

    # ── Aceptar ──────────────────────────────────────────────────────────────
    def _aceptar(self):
        if not self._validar():
            return

        datos = {
            "N°_OS": self.f_orden_servicio.text().strip(),
            "Ubicación": self.f_ubicacion.text().strip(),
            "Fecha_Ingreso": self.f_fecha_ingreso.text().strip(),
            "Descripción": self.f_descripcion.text().strip(),
            "N_Problema": self.f_n_problema.text().strip(),
            "Contrato": self.f_contrato.text().strip(),
            "Tipo": self.f_tipo.text().strip(),
            "Etapa": self.f_etapa.currentText(),
            "Restringir": "Si",
        }

        # "N° Trabajo" se ingresa a mano; si se deja vacío, se respeta la
        # expresión por defecto de la capa (ej: maximum("N° Trabajo") + 1).
        n_trabajo = self.f_n_trabajo.text().strip()
        if n_trabajo:
            datos["N° Trabajo"] = n_trabajo

        try:
            agregar_feature_os(datos, self.punto_xy)
        except Exception as e:
            QMessageBox.critical(self, "Error al registrar en QGIS", str(e))
            return

        iface.mapCanvas().setCenter(self.punto_xy)
        iface.mapCanvas().zoomScale(2000)
        iface.mapCanvas().refresh()

        # ── Modo itinerario: si quedan OS en la cola, avanzar a la
        # siguiente en vez de cerrar el diálogo ────────────────────────
        if self._cola_itinerario:
            QMessageBox.information(
                self, "OS Registrada",
                f"✓ OS {datos['N°_OS']} registrada.\n\n"
                f"Quedan {len(self._cola_itinerario)} OS por cargar en este itinerario."
            )
            siguiente = self._cola_itinerario.pop(0)
            self._cargar_item_de_cola(siguiente)
            return

        extra = "\n\n✓ Itinerario completo." if self._total_itinerario else ""
        QMessageBox.information(
            self, "OS Registrada",
            f"✓ OS {datos['N°_OS']} registrada correctamente.\n\n"
            f"  Ubicación : {datos['Ubicación']}\n"
            f"  Etapa     : {datos['Etapa']}\n"
            f"  Restringir: Si"
            f"{extra}"
        )
        self.accept()
