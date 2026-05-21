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


# ── Tipos de problema que se consideran Obstrucciones ─────────────────────────
TIPOS_OBSTRUCCION = {"Colector Obstruido", "Colector Interno Obstruido"}

# Campo que identifica unicamente cada reclamo en la capa WFS.
# Ajustar si tiene otro nombre.
CAMPO_ID_DEFAULT = "ID_RECLAMO"


class ActualizarObstrucciones(QgsProcessingAlgorithm):
    """
    Actualiza la capa maestra 'Obstrucciones' incorporando los reclamos nuevos
    provenientes del servicio WFS (V_RE_RECLAMOS_SANEA_PORTAL).

    Flujo:
        1. Intersecta la capa WFS con 'Zona_delimitada'.
        2. Filtra por DESC_TIPO_PROBLEMA IN ('Colector Obstruido',
                                              'Colector Interno Obstruido').
        3. Detecta reclamos nuevos comparando IDs con los ya presentes
           en 'Obstrucciones' (deduplicacion).
        4. Agrega solo los reclamos nuevos a 'Obstrucciones',
           mapeando unicamente las columnas existentes en esa capa.
        5. Elimina las capas auxiliares del proyecto.
    """

    CAPA_WFS        = "CAPA_WFS"
    ZONA_DELIMITADA = "ZONA_DELIMITADA"
    OBSTRUCCIONES   = "OBSTRUCCIONES"
    CAMPO_ID        = "CAMPO_ID"
    OUTPUT_AGREGADOS = "RECLAMOS_AGREGADOS"

    # ── Metadatos ──────────────────────────────────────────────────────────────

    def name(self):
        return "actualizar_obstrucciones"

    def displayName(self):
        return "Actualizar Obstrucciones"

    def group(self):
        return "Reclamos"

    def groupId(self):
        return "reclamos"

    def shortHelpString(self):
        return (
            "Actualiza la capa maestra 'Obstrucciones' con los reclamos nuevos\n"
            "provenientes del WFS (V_RE_RECLAMOS_SANEA_PORTAL).\n\n"
            "Pasos internos:\n"
            "  1. Interseccion WFS x Zona_delimitada\n"
            "  2. Filtrado por tipo de problema\n"
            "  3. Deduplicacion por campo ID\n"
            "  4. Incorporacion a Obstrucciones\n"
            "  5. Limpieza de capas auxiliares\n\n"
            f"Parametro 'Campo ID': nombre del campo unico de cada reclamo "
            f"(por defecto: '{CAMPO_ID_DEFAULT}')."
        )

    def createInstance(self):
        return ActualizarObstrucciones()

    # ── Parametros ─────────────────────────────────────────────────────────────

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.CAPA_WFS,
                "Capa WFS (V_RE_RECLAMOS_SANEA_PORTAL)",
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.ZONA_DELIMITADA,
                "Capa Zona_delimitada",
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.OBSTRUCCIONES,
                "Capa maestra Obstrucciones",
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

        capa_wfs        = self.parameterAsVectorLayer(parameters, self.CAPA_WFS,        context)
        zona_delimitada = self.parameterAsVectorLayer(parameters, self.ZONA_DELIMITADA,  context)
        obstrucciones   = self.parameterAsVectorLayer(parameters, self.OBSTRUCCIONES,    context)
        campo_id        = self.parameterAsString    (parameters, self.CAMPO_ID,          context).strip()

        # ── Validaciones ───────────────────────────────────────────────────────

        if not capa_wfs or not capa_wfs.isValid():
            raise QgsProcessingException("La capa WFS no es valida o no se pudo cargar.")
        if not zona_delimitada or not zona_delimitada.isValid():
            raise QgsProcessingException("La capa Zona_delimitada no es valida.")
        if not obstrucciones or not obstrucciones.isValid():
            raise QgsProcessingException("La capa Obstrucciones no es valida.")

        if obstrucciones.fields().lookupField(campo_id) == -1:
            raise QgsProcessingException(
                f"El campo ID '{campo_id}' no existe en Obstrucciones. "
                f"Campos disponibles: {[f.name() for f in obstrucciones.fields()]}"
            )
        if capa_wfs.fields().lookupField(campo_id) == -1:
            raise QgsProcessingException(
                f"El campo ID '{campo_id}' no existe en la capa WFS. "
                f"Campos disponibles: {[f.name() for f in capa_wfs.fields()]}"
            )

        capas_auxiliares = []
        cant_nuevos      = 0

        try:
            # ── PASO 1: Interseccion WFS x Zona_delimitada ────────────────────
            feedback.pushInfo("[1/4] Intersectando WFS con Zona_delimitada ...")
            feedback.setProgress(5)

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
            capa_interseccion = res_interseccion["OUTPUT"]
            if isinstance(capa_interseccion, QgsVectorLayer):
                capas_auxiliares.append(capa_interseccion)

            feedback.pushInfo(
                f"  ✔ Interseccion completada: "
                f"{capa_interseccion.featureCount()} features."
            )
            feedback.setProgress(30)

            if feedback.isCanceled():
                return {self.OUTPUT_AGREGADOS: 0}

            # ── PASO 2: Filtrar por tipo de problema ──────────────────────────
            feedback.pushInfo("[2/4] Filtrando por tipo de problema ...")

            tipos_sql   = ", ".join(f"'{t}'" for t in TIPOS_OBSTRUCCION)
            expr_filtro = f'"DESC_TIPO_PROBLEMA" IN ({tipos_sql})'

            res_filtrado = processing.run(
                "native:extractbyexpression",
                {
                    "INPUT":      capa_interseccion,
                    "EXPRESSION": expr_filtro,
                    "OUTPUT":     "memory:Candidatas_temp",
                },
                context=context,
                feedback=feedback,
                is_child_algorithm=True,
            )
            capa_candidatas = res_filtrado["OUTPUT"]
            if isinstance(capa_candidatas, QgsVectorLayer):
                capas_auxiliares.append(capa_candidatas)

            total_candidatas = capa_candidatas.featureCount()
            feedback.pushInfo(
                f"  ✔ Filtrado completado: {total_candidatas} reclamos de obstruccion."
            )
            feedback.setProgress(55)

            if feedback.isCanceled():
                return {self.OUTPUT_AGREGADOS: 0}

            if total_candidatas == 0:
                feedback.pushInfo("  Sin nuevos reclamos para agregar. Fin del proceso.")
                return {self.OUTPUT_AGREGADOS: 0}

            # ── PASO 3: Deduplicar ────────────────────────────────────────────
            feedback.pushInfo("[3/4] Detectando reclamos nuevos (deduplicacion) ...")

            ids_existentes = set()
            for feat in obstrucciones.getFeatures(
                QgsFeatureRequest().setFlags(QgsFeatureRequest.NoGeometry)
            ):
                val = feat[campo_id]
                if val is not None:
                    ids_existentes.add(str(val).strip())

            feedback.pushInfo(
                f"  IDs ya presentes en Obstrucciones: {len(ids_existentes)}"
            )

            campos_obs      = obstrucciones.fields()
            nombres_obs     = [f.name() for f in campos_obs]
            features_nuevas = []

            for feat in capa_candidatas.getFeatures():
                val_id = feat[campo_id]
                if val_id is None:
                    continue
                if str(val_id).strip() in ids_existentes:
                    continue

                nueva = QgsFeature(campos_obs)
                nueva.setGeometry(feat.geometry())
                for nombre in nombres_obs:
                    idx = feat.fields().lookupField(nombre)
                    if idx != -1:
                        nueva[nombre] = feat[nombre]
                features_nuevas.append(nueva)

            cant_nuevos = len(features_nuevas)
            feedback.pushInfo(f"  Reclamos nuevos a agregar: {cant_nuevos}")
            feedback.setProgress(75)

            if cant_nuevos == 0:
                feedback.pushInfo("  Obstrucciones ya esta al dia. Sin cambios.")
                return {self.OUTPUT_AGREGADOS: 0}

            # ── PASO 4: Agregar a Obstrucciones ───────────────────────────────
            feedback.pushInfo("[4/4] Incorporando reclamos nuevos a Obstrucciones ...")

            exito, _ = obstrucciones.dataProvider().addFeatures(features_nuevas)
            if not exito:
                raise QgsProcessingException(
                    "No se pudieron agregar los nuevos reclamos a Obstrucciones. "
                    "Verificar que la capa no este en modo solo lectura."
                )

            obstrucciones.updateExtents()
            feedback.pushInfo(
                f"  ✔ {cant_nuevos} reclamos nuevos agregados a Obstrucciones."
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

        feedback.pushInfo(
            f"\n=== Actualizacion completada: {cant_nuevos} reclamos nuevos agregados. ==="
        )
        return {self.OUTPUT_AGREGADOS: cant_nuevos}
