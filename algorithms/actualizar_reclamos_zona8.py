from qgis.core import (
    QgsFeature,
    QgsFeatureRequest,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorLayer,
    QgsProject,
    QgsVectorLayer,
)
import processing


# ── Conexion WFS ───────────────────────────────────────────────────────────────
WFS_URL      = "https://geoserver-ssl.imm.gub.uy/geoserver/ows"
WFS_TYPENAME = "imm:V_RE_RECLAMOS_SANEA_PORTAL"

# Campo que identifica unicamente cada reclamo en la capa WFS.
CAMPO_ID_DEFAULT = "NUMERO_RECLAMO"


class ActualizarReclamos(QgsProcessingAlgorithm):
    """
    Actualiza la capa maestra 'Reclamos_limitado' incorporando los reclamos nuevos
    provenientes del servicio WFS (V_RE_RECLAMOS_SANEA_PORTAL).

    Flujo:
        1. Intersecta la capa WFS con 'Zona_delimitada'.
        2. Detecta reclamos nuevos comparando IDs con los ya presentes
           en 'Reclamos_limitado' (deduplicacion).
        3. Agrega solo los reclamos nuevos a 'Reclamos_limitado',
           mapeando unicamente las columnas existentes en esa capa.
        4. Elimina las capas auxiliares del proyecto.
    """

    ZONA_DELIMITADA = "ZONA_DELIMITADA"
    RECLAMOS_LIMITADO = "RECLAMOS_LIMITADO"
    CAMPO_ID          = "CAMPO_ID"
    OUTPUT_AGREGADOS  = "RECLAMOS_AGREGADOS"

    # ── Metadatos ──────────────────────────────────────────────────────────────

    def name(self):
        return "actualizar_reclamos"

    def displayName(self):
        return "Actualizar Obstrucciones"

    def group(self):
        return "Reclamos"

    def groupId(self):
        return "reclamos"

    def shortHelpString(self):
        return (
            "Actualiza la capa maestra 'Reclamos_limitado' con los reclamos nuevos\n"
            "provenientes del WFS (V_RE_RECLAMOS_SANEA_PORTAL).\n\n"
            "Pasos internos:\n"
            "  1. Conexion al WFS\n"
            "  2. Interseccion WFS x Zona_delimitada\n"
            "  3. Deduplicacion por campo ID\n"
            "  4. Incorporacion a Reclamos_limitado\n\n"
            f"Parametro 'Campo ID': nombre del campo unico de cada reclamo "
            f"(por defecto: '{CAMPO_ID_DEFAULT}')."
        )

    def createInstance(self):
        return ActualizarReclamos()

    # ── Parametros ─────────────────────────────────────────────────────────────

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.ZONA_DELIMITADA,
                "Capa Zona_delimitada",
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.RECLAMOS_LIMITADO,
                "Capa maestra Reclamos_limitado",
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.CAMPO_ID,
                "Campo ID unico del reclamo",
                defaultValue=CAMPO_ID_DEFAULT,
            )
        )
        self.addOutput(
            QgsProcessingOutputNumber(
                self.OUTPUT_AGREGADOS,
                "Reclamos nuevos agregados",
            )
        )

    # ── Logica principal ───────────────────────────────────────────────────────

    def processAlgorithm(self, parameters, context, feedback):

        zona_delimitada   = self.parameterAsVectorLayer(parameters, self.ZONA_DELIMITADA,   context)
        reclamos          = self.parameterAsVectorLayer(parameters, self.RECLAMOS_LIMITADO, context)
        campo_id          = self.parameterAsString    (parameters, self.CAMPO_ID,           context).strip()

        # ── Validaciones ───────────────────────────────────────────────────────

        if not zona_delimitada or not zona_delimitada.isValid():
            raise QgsProcessingException("La capa Zona_delimitada no es valida.")
        if not reclamos or not reclamos.isValid():
            raise QgsProcessingException("La capa Reclamos_limitado no es valida.")

        if reclamos.fields().lookupField(campo_id) == -1:
            raise QgsProcessingException(
                f"El campo ID '{campo_id}' no existe en Reclamos_limitado. "
                f"Campos disponibles: {[f.name() for f in reclamos.fields()]}"
            )
        capas_auxiliares = []
        cant_nuevos      = 0

        try:
            # ── PASO 0: Cargar capa WFS ───────────────────────────────────────
            feedback.pushInfo("[0/3] Conectando al servicio WFS ...")
            feedback.setProgress(2)

            uri_wfs  = (
                f"pagingEnabled='default' "
                f"typename='{WFS_TYPENAME}' "
                f"url='{WFS_URL}' "
                f"version='auto' "
                f"srsname='EPSG:32721'"
            )
            capa_wfs = QgsVectorLayer(uri_wfs, "WFS_Reclamos", "WFS")
            if not capa_wfs.isValid():
                raise QgsProcessingException(
                    f"No se pudo conectar al WFS: {WFS_URL}\n"
                    f"Verificar acceso a la red y disponibilidad del servicio."
                )
            if capa_wfs.fields().lookupField(campo_id) == -1:
                raise QgsProcessingException(
                    f"El campo ID '{campo_id}' no existe en la capa WFS. "
                    f"Campos disponibles: {[f.name() for f in capa_wfs.fields()]}"
                )
            feedback.pushInfo(f"  ✔ WFS conectado: {capa_wfs.featureCount()} reclamos disponibles.")

            # ── PASO 1: Interseccion WFS x Zona_delimitada ────────────────────
            feedback.pushInfo("[1/3] Intersectando WFS con Zona_delimitada ...")
            feedback.setProgress(10)

            res_interseccion = processing.run(
                "native:intersection",
                {
                    "INPUT":   capa_wfs,
                    "OVERLAY": zona_delimitada,
                    "OUTPUT":  "memory:Interseccion_temp",
                },
                context=context,
                feedback=feedback,
                is_child_algorithm=True,
            )
            capa_interseccion = context.getMapLayer(res_interseccion["OUTPUT"])
            if capa_interseccion is None:
                raise QgsProcessingException("No se pudo obtener la capa de interseccion del contexto.")
            capas_auxiliares.append(capa_interseccion)

            total_interseccion = capa_interseccion.featureCount()
            feedback.pushInfo(
                f"  ✔ Interseccion completada: {total_interseccion} features."
            )
            feedback.setProgress(40)

            if feedback.isCanceled():
                return {self.OUTPUT_AGREGADOS: 0}

            if total_interseccion == 0:
                feedback.pushInfo("  Sin reclamos en la zona. Fin del proceso.")
                return {self.OUTPUT_AGREGADOS: 0}

            capa_candidatas = capa_interseccion

            # ── PASO 2: Deduplicar ────────────────────────────────────────────
            feedback.pushInfo("[2/3] Detectando reclamos nuevos (deduplicacion) ...")

            ids_existentes = set()
            for feat in reclamos.getFeatures(
                QgsFeatureRequest().setFlags(QgsFeatureRequest.NoGeometry)
            ):
                val = feat[campo_id]
                if val is not None:
                    ids_existentes.add(str(val).strip())

            feedback.pushInfo(
                f"  IDs ya presentes en Reclamos: {len(ids_existentes)}"
            )

            campos_obs      = reclamos.fields()
            nombres_obs     = [f.name() for f in campos_obs]
            features_nuevas = []
            ids_nuevos      = []

            for feat in capa_candidatas.getFeatures():
                val_id = feat[campo_id]
                if val_id is None:
                    continue
                if str(val_id).strip() in ids_existentes:
                    continue

                nueva = QgsFeature(campos_obs)
                nueva.setGeometry(feat.geometry())
                for nombre in nombres_obs:
                    if nombre.lower() == "fid":
                        continue
                    idx = feat.fields().lookupField(nombre)
                    if idx != -1:
                        nueva[nombre] = feat[nombre]
                features_nuevas.append(nueva)
                ids_nuevos.append(str(val_id).strip())

            cant_nuevos = len(features_nuevas)
            feedback.pushInfo(f"  Reclamos nuevos a agregar: {cant_nuevos}")
            feedback.setProgress(75)

            if cant_nuevos == 0:
                feedback.pushInfo(" Reclamos ya estan actualizados. Sin cambios.")
                return {self.OUTPUT_AGREGADOS: 0}

            # ── PASO 3: Agregar a Obstrucciones ───────────────────────────────
            feedback.pushInfo("[3/3] Incorporando reclamos nuevos ...")

            ya_editando = reclamos.isEditable()
            if not ya_editando:
                reclamos.startEditing()

            exito = reclamos.addFeatures(features_nuevas)
            if not exito:
                reclamos.rollBack()
                raise QgsProcessingException(
                    "No se pudieron agregar los nuevos reclamos. "
                    "Verificar que la capa no este en modo solo lectura."
                )

            if not ya_editando:
                reclamos.commitChanges()

            reclamos.updateExtents()
            feedback.pushInfo(
                f"  ✔ {cant_nuevos} reclamos nuevos agregados."
            )
            feedback.setProgress(95)

        finally:
            # ── LIMPIEZA ──────────────────────────────────────────────────────
            feedback.pushInfo("Limpiando capas auxiliares ...")
            proyecto = QgsProject.instance()
            for capa in capas_auxiliares:
                try:
                    proyecto.removeMapLayer(capa.id())
                except Exception:
                    pass
            feedback.pushInfo("  ✔ Capas auxiliares eliminadas.")
            feedback.setProgress(100)

        ids_str = ", ".join(ids_nuevos) if ids_nuevos else "-"
        feedback.pushInfo(
            f"\n=== Actualizacion completada: {cant_nuevos} reclamos nuevos agregados. ==="
        )
        feedback.pushInfo(f"IDs incorporados: {ids_str}")
        return {self.OUTPUT_AGREGADOS: cant_nuevos}
