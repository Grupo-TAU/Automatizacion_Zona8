"""
Panel acoplable con el conteo de Problemas por categoria.

Tres columnas se recalculan solas cada vez que la capa cambia (alta, baja,
edicion de atributos, filtro del panel de capas). La cuarta, "Comparacion", es
estatica: se llena a pedido corriendo la extraccion de una planilla y queda
guardada en el proyecto junto con la fecha en que se corrio, para poder ver de
un vistazo cuanto se movio la capa desde el ultimo cruce contra el sistema.

Cada celda de la tabla es clickeable: muestra los N_Problema que arman ese
numero (capa_utils.CAMPOS_PASO1 / conteo.CAMPO_N_PROBLEMA).
"""

import json
import os

from qgis.core import QgsProject
from qgis.utils import iface

from PyQt5.QtCore import Qt, QTimer, QDateTime
from PyQt5.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QApplication,
    QDialog, QPlainTextEdit, QDialogButtonBox,
)
from PyQt5.QtGui import QBrush, QColor, QFont, QCursor

from .capa_utils import obtener_capa, CAPA_OS, CAMPO_DENTRO_ZONA
from . import conteo as cnt

# ─────────────────────────────────────────────────────────────────────────────
# PERSISTENCIA DE LA COMPARACION (en el .qgz, no en la config local)
# ─────────────────────────────────────────────────────────────────────────────
# Va en el proyecto para que las 6 PCs vean la misma comparacion y la misma
# fecha. El precio es que hay que guardar el proyecto: writeEntry lo marca como
# modificado, y el panel lo avisa en pantalla. Todo en una sola clave JSON: es
# mas facil de extender que una clave por campo.
_SCOPE = "RegistrarProblemaSur"
_CLAVE_DATOS = "comparacion/datos"

_DATOS_POR_DEFECTO = {
    "conteo": {},
    "fecha": "",
    "archivo": "",
    "ids_sin_cargar": [],
    "ids_sin_planilla": [],
    "ids_por_categoria": {},
}

# Coalescencia de los recalculos: una edicion masiva dispara decenas de señales
# seguidas y no tiene sentido recorrer la capa en cada una.
_MS_REBOTE = 250


def leer_comparacion():
    """Devuelve el dict de la ultima comparacion guardada en el proyecto (conteo,
    fecha, archivo, ids_sin_cargar, ids_sin_planilla, ids_por_categoria), con
    los valores por defecto (vacios) si todavia no se corrio ninguna."""
    proyecto = QgsProject.instance()
    crudo, _ = proyecto.readEntry(_SCOPE, _CLAVE_DATOS, "")
    try:
        guardado = json.loads(crudo) if crudo else {}
    except ValueError:
        guardado = {}
    return {**_DATOS_POR_DEFECTO, **guardado}


def guardar_comparacion(conteo, archivo, ids_sin_cargar=(), ids_sin_planilla=(), ids_por_categoria=None):
    """Congela el conteo de la planilla en el proyecto, con la fecha de ahora."""
    proyecto = QgsProject.instance()
    fecha = QDateTime.currentDateTime().toString("dd/MM/yyyy HH:mm")
    datos = {
        "conteo": dict(conteo),
        "fecha": fecha,
        "archivo": archivo,
        "ids_sin_cargar": list(ids_sin_cargar),
        "ids_sin_planilla": list(ids_sin_planilla),
        "ids_por_categoria": {k: list(v) for k, v in (ids_por_categoria or {}).items()},
    }
    proyecto.writeEntry(_SCOPE, _CLAVE_DATOS, json.dumps(datos, ensure_ascii=False))
    return fecha


# ─────────────────────────────────────────────────────────────────────────────
# PANEL
# ─────────────────────────────────────────────────────────────────────────────
_COLUMNAS = ["Fuera de Zona", "Dentro de Zona", "Total", "Comparación"]
_FILA_TOTAL = "TOTAL"

_GRIS = "color:#777; font-style:italic;"
_ROJO = "color:#a03030;"


def _texto_filtro_categoria(categoria):
    """Que Tipo cae en esta fila: para poder chequear de un vistazo si el
    filtro esta agrupando bien, sin ir a buscar CATEGORIAS en conteo.py."""
    if categoria == _FILA_TOTAL:
        return "Suma de todas las filas."
    if categoria == cnt.CATEGORIA_SIN_DATO:
        return f"Problemas con el campo '{cnt.CAMPO_TIPO}' vacío."
    if categoria == cnt.CATEGORIA_RESTO:
        return f"'{cnt.CAMPO_TIPO}' que no coincide con ninguna otra categoría."
    patrones = cnt.patrones_categoria(categoria)
    if not patrones:
        return ""
    return f"'{cnt.CAMPO_TIPO}' contiene: " + ", ".join(patrones)


class PanelConteo(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Conteo de Problemas", parent)
        self.setObjectName("PanelConteoProblemasSur")
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)

        self._capa = None            # capa a la que estamos enganchados
        self._conexiones = []        # (senal, slot) para poder desconectar
        self._resultado_capa = None  # ultimo ConteoCapa, para las celdas clickeables
        self._categorias_tabla = []  # filas visibles actuales (sin el TOTAL)
        self._ids_por_categoria = {}  # categoria -> [N_Problema...] de la planilla
        self._ids_sin_cargar = []    # planilla -> falta en la capa
        self._ids_sin_planilla = []  # capa (abiertos) -> falta en la planilla
        self._rebote = QTimer(self)
        self._rebote.setSingleShot(True)
        self._rebote.setInterval(_MS_REBOTE)
        self._rebote.timeout.connect(self.refrescar)

        self._build_ui()
        self._conectar_proyecto()
        self._reenganchar_capa()

    # ── UI ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        contenedor = QWidget()
        layout = QVBoxLayout(contenedor)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.tabla = QTableWidget(0, len(_COLUMNAS))
        self.tabla.setHorizontalHeaderLabels(_COLUMNAS)
        self.tabla.setToolTip("Clic en una celda para ver los N° de problema que la componen.")
        self.tabla.verticalHeader().setDefaultSectionSize(24)
        self.tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tabla.setSelectionMode(QTableWidget.NoSelection)
        self.tabla.setAlternatingRowColors(True)
        self.tabla.setCursor(Qt.PointingHandCursor)
        self.tabla.cellClicked.connect(self._click_celda)
        cabecera = self.tabla.horizontalHeader()
        cabecera.setSectionResizeMode(QHeaderView.Stretch)
        self.tabla.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        layout.addWidget(self.tabla)

        self.lbl_estado = QLabel()
        self.lbl_estado.setWordWrap(True)
        self.lbl_estado.setStyleSheet(_GRIS)
        layout.addWidget(self.lbl_estado)

        self.lbl_comparacion = QLabel()
        self.lbl_comparacion.setWordWrap(True)
        self.lbl_comparacion.setStyleSheet(_GRIS)
        layout.addWidget(self.lbl_comparacion)

        fila_1 = QHBoxLayout()
        self.btn_planilla = QPushButton("Actualizar comparación…")
        self.btn_planilla.setToolTip(
            "Cuenta la columna Tipo de un CSV o XLSX bajado del sistema y congela\n"
            "el resultado en la columna Comparación, con la fecha de hoy.\n"
            "De paso compara los IDs de la planilla contra los de la capa."
        )
        self.btn_planilla.setStyleSheet(
            "QPushButton{background:#0a3d62;color:white;font-weight:bold;"
            "border-radius:3px;padding:5px 12px;}"
            "QPushButton:hover{background:#1a5276;}"
        )
        self.btn_planilla.clicked.connect(self._pedir_planilla)

        btn_recalcular = QPushButton("Recalcular")
        btn_recalcular.setToolTip("Vuelve a recorrer la capa. El panel ya lo hace solo en cada cambio.")
        btn_recalcular.clicked.connect(self.refrescar)

        btn_categorias = QPushButton("Qué cuenta cada fila…")
        btn_categorias.setToolTip("Muestra qué valores de Tipo arman cada categoría de la tabla.")
        btn_categorias.clicked.connect(self._mostrar_categorias)

        fila_1.addWidget(self.btn_planilla)
        fila_1.addWidget(btn_recalcular)
        fila_1.addWidget(btn_categorias)
        fila_1.addStretch(1)
        layout.addLayout(fila_1)

        fila_2 = QHBoxLayout()
        self.btn_sin_cargar = QPushButton("Faltan cargar")
        self.btn_sin_cargar.setToolTip(
            f"Números de '{cnt.CAMPO_PROBLEMA}' de la planilla que no están en el "
            f"campo '{cnt.CAMPO_N_PROBLEMA}' de la capa: problemas reportados que "
            "todavía no se cargaron."
        )
        self.btn_sin_cargar.clicked.connect(self._mostrar_ids_sin_cargar)
        self.btn_sin_cargar.setEnabled(False)

        self.btn_sin_planilla = QPushButton("No están en la planilla")
        self.btn_sin_planilla.setToolTip(
            f"Números de '{cnt.CAMPO_N_PROBLEMA}' abiertos en la capa (Etapa "
            f"distinta de finalizada/no corresponde) que no aparecen en "
            f"'{cnt.CAMPO_PROBLEMA}' de la planilla."
        )
        self.btn_sin_planilla.clicked.connect(self._mostrar_ids_sin_planilla)
        self.btn_sin_planilla.setEnabled(False)

        fila_2.addWidget(self.btn_sin_cargar)
        fila_2.addWidget(self.btn_sin_planilla)
        fila_2.addStretch(1)
        layout.addLayout(fila_2)

        self.setWidget(contenedor)

    # ── Enganche con el proyecto y la capa ───────────────────────────────
    def _conectar_proyecto(self):
        """La capa puede no estar cargada todavia, o cargarse/descargarse
        mientras el panel esta abierto, asi que el enganche se rehace en cada
        cambio del arbol de capas."""
        proyecto = QgsProject.instance()
        proyecto.layersAdded.connect(self._reenganchar_capa)
        proyecto.layersRemoved.connect(self._reenganchar_capa)
        proyecto.readProject.connect(self._reenganchar_capa)

    def _desconectar_capa(self):
        for senal, slot in self._conexiones:
            try:
                senal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass  # la capa ya fue destruida por QGIS
        self._conexiones = []
        self._capa = None

    def _reenganchar_capa(self, *args):
        self._desconectar_capa()
        capa = obtener_capa(CAPA_OS)
        if capa is not None:
            self._capa = capa
            # layerModified cubre las ediciones en el buffer; las otras cubren
            # el commit, los cambios que vienen del proveedor y el filtro.
            for nombre in (
                "layerModified", "editingStopped", "dataChanged",
                "subsetStringChanged", "featureAdded", "featuresDeleted",
                "attributeValueChanged", "geometryChanged",
            ):
                senal = getattr(capa, nombre, None)
                if senal is not None:
                    senal.connect(self._programar_refresco)
                    self._conexiones.append((senal, self._programar_refresco))
        self.refrescar()

    def _programar_refresco(self, *args):
        # Con el panel cerrado no hay para que recorrer la capa en cada edicion;
        # showEvent recalcula al volver a abrirlo.
        if self.isVisible():
            self._rebote.start()

    # ── Calculo y pintado ────────────────────────────────────────────────
    def refrescar(self):
        datos = leer_comparacion()
        comparacion = datos["conteo"]

        if self._capa is None:
            self._vaciar_tabla(f"La capa '{CAPA_OS}' no está cargada en el proyecto.")
            self._pintar_comparacion(datos)
            return

        try:
            resultado = cnt.contar_capa(self._capa, cnt.CAMPO_TIPO, CAMPO_DENTRO_ZONA)
        except ValueError as exc:
            self._vaciar_tabla(str(exc))
            self._pintar_comparacion(datos)
            return

        self._pintar_tabla(resultado, comparacion)
        self._pintar_estado(resultado)
        self._pintar_comparacion(datos)

    def _vaciar_tabla(self, mensaje):
        self.tabla.setRowCount(0)
        self._resultado_capa = None
        self._categorias_tabla = []
        self.lbl_estado.setText(mensaje)
        self.lbl_estado.setStyleSheet(_ROJO)

    def _pintar_tabla(self, resultado, comparacion):
        categorias = cnt.categorias_visibles(resultado.total, comparacion)
        self._resultado_capa = resultado
        self._categorias_tabla = categorias
        filas = categorias + [_FILA_TOTAL]
        self.tabla.setRowCount(len(filas))
        self.tabla.setVerticalHeaderLabels(filas)

        negrita = QFont()
        negrita.setBold(True)

        for n, categoria in enumerate(filas):
            cabecera = self.tabla.verticalHeaderItem(n)
            if cabecera is not None:
                cabecera.setToolTip(_texto_filtro_categoria(categoria))

            if categoria == _FILA_TOTAL:
                valores = [
                    sum(resultado.fuera[c] for c in categorias),
                    sum(resultado.dentro[c] for c in categorias),
                    sum(resultado.total[c] for c in categorias),
                    # Sin comparacion corrida, el total tambien va vacio: si no,
                    # queda un 0 en rojo que parece un desvio real.
                    sum(int(comparacion.get(c, 0)) for c in categorias) if comparacion else None,
                ]
            else:
                valores = [
                    resultado.fuera[categoria],
                    resultado.dentro[categoria],
                    resultado.total[categoria],
                    comparacion.get(categoria),
                ]

            for col, valor in enumerate(valores):
                # La celda de Comparación queda vacía, no en cero, mientras no
                # se haya corrido nunca: un cero real y "todavía no lo corrí"
                # son cosas distintas.
                texto = "" if valor is None else str(int(valor))
                item = QTableWidgetItem(texto)
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if categoria == _FILA_TOTAL:
                    item.setFont(negrita)
                if col == 3 and valor is not None and int(valor) != int(valores[2]):
                    item.setForeground(QBrush(QColor("#a03030")))
                    item.setFont(negrita)
                    item.setToolTip(
                        f"La capa tiene {int(valores[2])} y la última comparación "
                        f"dio {int(valor)}: {int(valores[2]) - int(valor):+d}."
                    )
                self.tabla.setItem(n, col, item)

    def _pintar_estado(self, resultado):
        partes = [f"Capa '{self._capa.name()}': {sum(resultado.total.values())} problemas abiertos."]
        estilo = _GRIS
        if resultado.descartados:
            partes.append(f"Se descartaron {resultado.descartados} finalizados/no corresponde.")
        if resultado.excluidos:
            partes.append(f"Se excluyeron {resultado.excluidos} de Limpieza (no se cuentan en este panel).")
        if resultado.n_sin_clasificar:
            # Fuera + Dentro no cierra contra Total cuando pasa esto, así que
            # conviene decirlo en vez de dejar que el usuario haga la resta.
            partes.append(
                f"{resultado.n_sin_clasificar} sin '{CAMPO_DENTRO_ZONA}': "
                "no suman ni a Fuera ni a Dentro."
            )
            estilo = _ROJO
        if self._capa.subsetString():
            partes.append("La capa tiene un filtro activo; los números son los del filtro.")
            estilo = _ROJO
        self.lbl_estado.setText(" ".join(partes))
        self.lbl_estado.setStyleSheet(estilo)

    def _pintar_comparacion(self, datos):
        fecha = datos["fecha"]
        archivo = datos["archivo"]
        ids_sin_cargar = datos["ids_sin_cargar"]
        ids_sin_planilla = datos["ids_sin_planilla"]

        if not fecha:
            self.lbl_comparacion.setText(
                "Comparación: sin datos. Corré la extracción desde un CSV o XLSX."
            )
        else:
            nombre = os.path.basename(archivo) if archivo else "planilla"
            texto = f"Comparación: {nombre} — actualizada el {fecha}."
            partes = []
            if ids_sin_cargar:
                partes.append(f"{len(ids_sin_cargar)} sin cargar")
            if ids_sin_planilla:
                partes.append(f"{len(ids_sin_planilla)} sin planilla")
            if partes:
                texto += " " + ", ".join(partes) + "."
            self.lbl_comparacion.setText(texto)

        self._ids_por_categoria = datos["ids_por_categoria"]
        self._ids_sin_cargar = list(ids_sin_cargar)
        self._ids_sin_planilla = list(ids_sin_planilla)
        self.btn_sin_cargar.setText(f"Faltan cargar ({len(self._ids_sin_cargar)})")
        self.btn_sin_cargar.setEnabled(bool(self._ids_sin_cargar))
        self.btn_sin_planilla.setText(f"No están en la planilla ({len(self._ids_sin_planilla)})")
        self.btn_sin_planilla.setEnabled(bool(self._ids_sin_planilla))

    # ── Celdas clickeables ────────────────────────────────────────────────
    def _click_celda(self, fila, columna):
        """Cada celda muestra los N_Problema que arman su numero. Silencioso
        si la celda esta vacia: no tiene sentido abrir un dialogo sin nada."""
        filas = self._categorias_tabla + [_FILA_TOTAL]
        if fila >= len(filas):
            return
        categoria = filas[fila]
        categorias = self._categorias_tabla if categoria == _FILA_TOTAL else [categoria]

        if columna == 3:
            ids = [i for c in categorias for i in self._ids_por_categoria.get(c, [])]
        elif self._resultado_capa is None:
            return
        else:
            fuente = (
                self._resultado_capa.ids_fuera,
                self._resultado_capa.ids_dentro,
                self._resultado_capa.ids_total,
            )[columna]
            ids = [i for c in categorias for i in fuente.get(c, [])]

        if not ids:
            return

        ids = cnt.ordenar_ids(ids)
        self._dialogo_texto(
            f"{categoria} — {_COLUMNAS[columna]}",
            f"{len(ids)} N° de problema:",
            "\n".join(ids),
        )

    # ── Extraccion desde la planilla ─────────────────────────────────────
    def _pedir_planilla(self):
        ruta, _ = QFileDialog.getOpenFileName(
            self, "Planilla bajada del sistema", "",
            "Planillas (*.csv *.xlsx *.xlsm);;Todos los archivos (*)",
        )
        if not ruta:
            return

        QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
        try:
            resultado_planilla, error = cnt.contar_planilla(ruta), None
            ids_sin_cargar, ids_sin_planilla = self._calcular_comparacion_ids(ruta)
        except (OSError, ValueError, KeyError) as exc:
            resultado_planilla, ids_sin_cargar, ids_sin_planilla, error = None, [], [], str(exc)
        finally:
            QApplication.restoreOverrideCursor()

        # El mensaje va despues de restaurar el cursor: si no, el cartel de
        # error queda con el reloj de arena encima.
        if error:
            QMessageBox.warning(self, "No se pudo leer la planilla", error)
            return

        fecha = guardar_comparacion(
            resultado_planilla.conteo, ruta, ids_sin_cargar, ids_sin_planilla,
            resultado_planilla.ids_por_categoria,
        )
        self.refrescar()
        detalle = f"Se contaron {sum(resultado_planilla.conteo.values())} problemas de la planilla ({fecha})."
        if resultado_planilla.descartados:
            detalle += f"\nSe descartaron {resultado_planilla.descartados} finalizados/no corresponde."
        if resultado_planilla.excluidos:
            detalle += f"\nSe excluyeron {resultado_planilla.excluidos} de Limpieza."
        if self._capa is None:
            detalle += f"\nLa capa '{CAPA_OS}' no está cargada: no se compararon los IDs."
        else:
            detalle += (
                f"\n{len(ids_sin_cargar)} problema(s) de la planilla no están en la capa."
                f"\n{len(ids_sin_planilla)} problema(s) abiertos de la capa no están en la planilla."
            )
        QMessageBox.information(
            self, "Comparación actualizada",
            f"{detalle}\n\nQueda guardado en el proyecto: acordate de guardarlo "
            "para que lo vean las demás PCs.",
        )

    def _calcular_comparacion_ids(self, ruta):
        """(ids_sin_cargar, ids_sin_planilla). Si la capa no está cargada no
        hay con qué comparar, asi que conserva lo que ya estaba guardado en
        vez de vaciarlo."""
        if self._capa is None:
            datos = leer_comparacion()
            return datos["ids_sin_cargar"], datos["ids_sin_planilla"]

        ids_planilla = cnt.leer_ids_planilla(ruta)
        ids_sin_cargar = cnt.ids_faltantes(ids_planilla, cnt.ids_capa(self._capa))

        # Solo los abiertos de la capa: la planilla del sistema tambien es
        # solo de problemas abiertos, asi que un finalizado/no corresponde
        # nuestro no tiene por que estar ahi.
        ids_capa_abiertos = cnt.ids_capa(self._capa, campo_etapa=cnt.CAMPO_ETAPA)
        ids_sin_planilla = cnt.ids_faltantes(ids_capa_abiertos, ids_planilla)

        return ids_sin_cargar, ids_sin_planilla

    def _dialogo_texto(self, titulo, descripcion, cuerpo):
        dialogo = QDialog(self)
        dialogo.setWindowTitle(titulo)
        layout = QVBoxLayout(dialogo)

        etiqueta = QLabel(descripcion)
        etiqueta.setWordWrap(True)
        layout.addWidget(etiqueta)

        texto = QPlainTextEdit(cuerpo)
        texto.setReadOnly(True)
        layout.addWidget(texto)

        botones = QDialogButtonBox()
        btn_copiar = botones.addButton("Copiar", QDialogButtonBox.ActionRole)
        btn_copiar.clicked.connect(lambda: QApplication.clipboard().setText(cuerpo))
        botones.addButton(QDialogButtonBox.Close).clicked.connect(dialogo.accept)
        layout.addWidget(botones)

        dialogo.resize(320, 420)
        dialogo.exec_()

    def _mostrar_ids_sin_cargar(self):
        self._dialogo_texto(
            "Problemas sin cargar",
            f"{len(self._ids_sin_cargar)} número(s) de '{cnt.CAMPO_PROBLEMA}' de la "
            f"planilla que no aparecen en '{cnt.CAMPO_N_PROBLEMA}' de la capa:",
            "\n".join(self._ids_sin_cargar),
        )

    def _mostrar_ids_sin_planilla(self):
        self._dialogo_texto(
            "Problemas sin planilla",
            f"{len(self._ids_sin_planilla)} número(s) de '{cnt.CAMPO_N_PROBLEMA}' "
            f"abiertos en la capa (Etapa distinta de finalizada/no corresponde) que "
            f"no aparecen en '{cnt.CAMPO_PROBLEMA}' de la planilla:",
            "\n".join(self._ids_sin_planilla),
        )

    def _mostrar_categorias(self):
        filas = [nombre for nombre, _ in cnt.CATEGORIAS] + [cnt.CATEGORIA_RESTO, cnt.CATEGORIA_SIN_DATO]
        cuerpo = "\n\n".join(f"{fila}\n{_texto_filtro_categoria(fila)}" for fila in filas)
        self._dialogo_texto(
            "Qué cuenta cada fila",
            "Mismo criterio para la capa y para la planilla: si algo está mal "
            "clasificado, es acá donde hay que corregirlo. Los Tipo de Limpieza "
            "(Alcantarilla/Colector/Registro/Boca de Tormenta obstruidos o "
            "sucios, Conexión obstruida o sucia) no entran en ninguna fila: se "
            "excluyen del todo, ni siquiera van a 'Otros'.",
            cuerpo,
        )

    # ── Ciclo de vida ────────────────────────────────────────────────────
    def showEvent(self, evento):
        # Mientras estuvo cerrado no se recalculo nada, asi que los numeros
        # pueden estar viejos.
        super().showEvent(evento)
        self.refrescar()

    def limpiar(self):
        """Suelta todas las conexiones. La llama el plugin al descargarse: si
        quedan vivas, la proxima edicion de la capa entra a un widget ya
        destruido y QGIS tira un RuntimeError por consola."""
        self._rebote.stop()
        self._desconectar_capa()
        proyecto = QgsProject.instance()
        for senal in (proyecto.layersAdded, proyecto.layersRemoved, proyecto.readProject):
            try:
                senal.disconnect(self._reenganchar_capa)
            except (TypeError, RuntimeError):
                pass


def abrir_panel(plugin):
    """Crea el dock la primera vez y lo trae al frente las siguientes."""
    if plugin.panel is None:
        plugin.panel = PanelConteo(iface.mainWindow())
        iface.addDockWidget(Qt.RightDockWidgetArea, plugin.panel)
    plugin.panel.show()
    plugin.panel.raise_()
