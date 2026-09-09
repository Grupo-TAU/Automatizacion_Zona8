"""
Algoritmo A del intercambio con la Intendencia: arma el XLSX que se le envia.

Exporta las filas abiertas y sin permiso cargado a una planilla de tres columnas
(clave, N_Problema, Permisos_UCCRIU), con la hoja _meta que el algoritmo B
necesita despues para verificar que la devolucion corresponde a esta capa.
"""

import uuid
from datetime import datetime

from qgis.core import (
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFeatureRequest,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingParameterExpression,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorLayer,
)

from ..intercambio_im import nucleo, planilla
from ..intercambio_im.nucleo import ErrorIntercambio
from .comun_im import advertir_si_fid, indice_campo


class ExportarPermisosIM(QgsProcessingAlgorithm):

    CAPA        = "CAPA"
    EXPRESION   = "EXPRESION"
    CAMPO_CLAVE = "CAMPO_CLAVE"
    SALIDA      = "SALIDA"
    FILAS       = "FILAS_EXPORTADAS"

    # ── Metadatos ──────────────────────────────────────────────────────────────

    def name(self):
        return "exportar_permisos_im"

    def displayName(self):
        return "1. Exportar permisos para la IM"

    def group(self):
        return "Intercambio IM"

    def groupId(self):
        return "intercambio_im"

    def shortHelpString(self):
        return (
            "Arma el XLSX que se le envia a la Intendencia para que complete la\n"
            f"columna '{nucleo.CAMPO_PERMISO}'.\n\n"
            "La planilla tiene una hoja 'Datos' con tres columnas (clave, "
            f"{nucleo.CAMPO_CONTEXTO}, {nucleo.CAMPO_PERMISO}) y una hoja oculta "
            "'_meta' con el lote, la capa de origen y las claves enviadas. Esa hoja\n"
            "es la que le permite al algoritmo 2 verificar que la devolucion\n"
            "corresponde a esta misma capa: si la IM la borra, el merge pierde\n"
            "controles.\n\n"
            "Filtro por defecto: etapas abiertas Y permiso todavia vacio. Al\n"
            "reexportar despues de un merge, lo que ya volvio cargado queda afuera\n"
            "solo, asi que la planilla funciona como cola de trabajo.\n\n"
            "Parametro 'Campo clave': 'fid' o el nombre de una columna UUID. Con\n"
            "'fid' el algoritmo avisa por que es fragil."
        )

    def createInstance(self):
        return ExportarPermisosIM()

    # ── Parametros ─────────────────────────────────────────────────────────────

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.CAPA,
                "Capa de problemas (inspecciones_os)",
            )
        )
        self.addParameter(
            QgsProcessingParameterExpression(
                self.EXPRESION,
                "Filtro de filas a exportar",
                defaultValue=nucleo.expresion_abiertas(),
                parentLayerParameterName=self.CAPA,
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.CAMPO_CLAVE,
                "Campo clave ('fid' o una columna UUID)",
                defaultValue=nucleo.CLAVE_FID,
            )
        )
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.SALIDA,
                "Planilla a enviar a la IM",
                fileFilter="Planilla de Excel (*.xlsx)",
            )
        )
        self.addOutput(
            QgsProcessingOutputNumber(self.FILAS, "Filas exportadas")
        )

    # ── Proceso ────────────────────────────────────────────────────────────────

    def processAlgorithm(self, parameters, context, feedback):
        capa = self.parameterAsVectorLayer(parameters, self.CAPA, context)
        if capa is None:
            raise QgsProcessingException("No se pudo cargar la capa de entrada.")

        campo_clave = (self.parameterAsString(parameters, self.CAMPO_CLAVE, context) or "").strip()
        if not campo_clave:
            raise QgsProcessingException("El campo clave no puede estar vacio.")
        salida = self.parameterAsFileOutput(parameters, self.SALIDA, context)

        idx_contexto = indice_campo(capa, nucleo.CAMPO_CONTEXTO)
        idx_permiso  = indice_campo(capa, nucleo.CAMPO_PERMISO)
        idx_etapa    = indice_campo(capa, nucleo.CAMPO_ETAPA)
        es_fid = campo_clave == nucleo.CLAVE_FID
        idx_clave = None if es_fid else indice_campo(capa, campo_clave)

        advertir_si_fid(feedback, campo_clave)

        feedback.pushInfo("[1/4] Filtrando la capa ...")

        claves_crudas, filas = [], []
        for feature in self._features_filtradas(parameters, context, capa):
            if feedback.isCanceled():
                return {}
            clave = feature.id() if es_fid else feature[idx_clave]
            claves_crudas.append(clave)
            filas.append({
                nucleo.COLUMNA_CLAVE: clave,
                nucleo.CAMPO_CONTEXTO: feature[idx_contexto],
                nucleo.CAMPO_PERMISO: feature[idx_permiso],
            })
        feedback.pushInfo(f"  {len(filas)} filas pasan el filtro.")
        feedback.setProgress(35)

        feedback.pushInfo(f"[2/4] Validando el campo clave '{campo_clave}' ...")
        try:
            claves = nucleo.validar_claves(claves_crudas, campo_clave)
        except ErrorIntercambio as exc:
            raise QgsProcessingException(str(exc)) from exc
        for fila, clave in zip(filas, claves):
            fila[nucleo.COLUMNA_CLAVE] = clave
        feedback.pushInfo("  Sin nulos ni duplicados.")
        feedback.setProgress(55)

        feedback.pushInfo("[3/4] Escribiendo la planilla ...")
        lote_id = str(uuid.uuid4())
        meta = {
            "lote_id": lote_id,
            "fecha_export": datetime.now().astimezone().isoformat(timespec="seconds"),
            "capa": capa.name(),
            "campo_clave": campo_clave,
            "proveedor": capa.dataProvider().name(),
            "fuente": capa.source(),
            "filas": len(filas),
            "hash_claves": nucleo.hash_claves(claves),
        }
        try:
            planilla.escribir_envio(salida, filas, meta)
        except ErrorIntercambio as exc:
            raise QgsProcessingException(str(exc)) from exc
        feedback.pushInfo(f"  {salida}")
        feedback.setProgress(75)

        feedback.pushInfo("[4/4] Revisando las etapas de la capa ...")
        self._reportar_etapas(capa, idx_etapa, feedback)
        self._avisar_si_cambio_la_expresion(parameters, context, feedback)
        feedback.setProgress(100)

        if not filas:
            feedback.pushWarning(
                "ADVERTENCIA: la planilla quedo vacia. Con el filtro por defecto "
                "esto significa que no hay filas abiertas con el permiso sin cargar."
            )

        feedback.pushInfo(
            "\n=== Envio armado ===\n"
            f"  Filas       : {len(filas)}\n"
            f"  Lote        : {lote_id}\n"
            f"  Campo clave : {campo_clave}\n"
            f"  Planilla    : {salida}"
        )
        return {self.SALIDA: salida, self.FILAS: len(filas)}

    # ── Auxiliares ─────────────────────────────────────────────────────────────

    def _features_filtradas(self, parameters, context, capa):
        """
        Features que pasan la expresion, evaluada SIEMPRE del lado de QGIS.

        No se usa setSubsetString, que dejaria la capa filtrada para todo el
        proyecto. Pero tampoco se le pasa la expresion al QgsFeatureRequest: ahi
        el proveedor la compila a SQL y la ejecuta el, con otra semantica. En esta
        capa el efecto es concreto y silencioso: SQLite tiene upper() solo ASCII,
        asi que upper('En_Ejecución') le da 'EN_EJECUCIóN' y no matchea el literal
        'EN_EJECUCIÓN' de la lista blanca. Las filas En_Ejecución quedaban afuera
        del envio sin ningun mensaje. Evaluar en Python cuesta nada con esta
        cantidad de filas y ademas deja el resultado igual cuando la capa migre a
        PostGIS, donde upper() vuelve a comportarse distinto.
        """
        texto = self.parameterAsExpression(parameters, self.EXPRESION, context)
        expresion = QgsExpression(texto)
        if expresion.hasParserError():
            raise QgsProcessingException(
                f"La expresion de filtro no compila: {expresion.parserErrorString()}\n{texto}"
            )

        contexto_expresion = QgsExpressionContext()
        contexto_expresion.appendScopes(
            QgsExpressionContextUtils.globalProjectLayerScopes(capa)
        )
        expresion.prepare(contexto_expresion)

        solicitud = QgsFeatureRequest()
        if not expresion.needsGeometry():
            solicitud.setFlags(QgsFeatureRequest.NoGeometry)

        for feature in capa.getFeatures(solicitud):
            contexto_expresion.setFeature(feature)
            if expresion.evaluate(contexto_expresion):
                yield feature
        if expresion.hasEvalError():
            raise QgsProcessingException(
                f"La expresion de filtro fallo al evaluarse: {expresion.evalErrorString()}"
            )

    def _reportar_etapas(self, capa, idx_etapa, feedback):
        """
        Etapas presentes en la capa que no estan en la lista blanca. No aborta:
        es para enterarse de una etapa nueva o mal escrita que esta quedando
        fuera del envio en silencio.
        """
        solicitud = (
            QgsFeatureRequest()
            .setSubsetOfAttributes([idx_etapa])
            .setFlags(QgsFeatureRequest.NoGeometry)
        )
        valores = (f[idx_etapa] for f in capa.getFeatures(solicitud))
        desconocidas = nucleo.etapas_desconocidas(valores)
        if not desconocidas:
            feedback.pushInfo("  Todas las etapas de la capa estan en la lista de abiertas.")
            return
        detalle = ", ".join(f"{etapa} ({cantidad})" for etapa, cantidad in desconocidas.items())
        feedback.pushInfo(
            f"  Etapas fuera de la lista de abiertas: {detalle}\n"
            "  Estas filas no se envian. Si alguna deberia enviarse, agregala a "
            "ETAPAS_ABIERTAS en intercambio_im/nucleo.py."
        )

    def _avisar_si_cambio_la_expresion(self, parameters, context, feedback):
        texto = self.parameterAsExpression(parameters, self.EXPRESION, context)
        if texto.strip() != nucleo.expresion_abiertas().strip():
            feedback.pushWarning(
                "ADVERTENCIA: la expresion de filtro fue editada, asi que ya no "
                "coincide con ETAPAS_ABIERTAS. El reporte de etapas de arriba se "
                "sigue calculando contra la lista del codigo, no contra lo que "
                "acabas de escribir."
            )
