from qgis.core import (
    QgsFeature,
    QgsFeatureRequest,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorLayer,
    QgsProject,
    QgsVectorLayer,
)
import processing


# ── Conexion WFS ───────────────────────────────────────────────────────────────
WFS_URL      = "https://geoserver-ssl.imm.gub.uy/geoserver/ows"
WFS_TYPENAME = "imm:V_RE_PROBLEMAS_ABIERTOS_SANEA"

# Campo que identifica unicamente cada problema en la capa WFS.
CAMPO_ID_DEFAULT = "NUMERO_PROBLEMA"


class ActualizarReclamos(QgsProcessingAlgorithm):
    """
    Actualiza la capa maestra 'problemas_maestra' incorporando los problemas nuevos
    provenientes del servicio WFS (V_RE_PROBLEMAS_ABIERTOS_SANEA).

    Flujo:
        1. Intersecta la capa WFS con 'Zona_delimitada'.
        2. Detecta problemas nuevos comparando IDs con los ya presentes
           en 'Problemas_limitado' (deduplicacion).
        3. Detecta problemas finalizados: los que estan en 'Problemas_limitado'
           pero ya no figuran en el WFS de problemas abiertos.
        4. Agrega los nuevos y elimina los finalizados en 'Problemas_limitado',
           mapeando unicamente las columnas existentes en esa capa.
        5. Elimina las capas auxiliares del proyecto.
    """

    ZONA_DELIMITADA = "ZONA_DELIMITADA"
    PROBLEMAS_LIMITADO = "PROBLEMAS_LIMITADO"
    CAMPO_ID          = "CAMPO_ID"
    LIMPIAR_FINALIZADOS = "LIMPIAR_FINALIZADOS"
    OUTPUT_AGREGADOS  = "PROBLEMAS_AGREGADOS"
    OUTPUT_ELIMINADOS = "PROBLEMAS_ELIMINADOS"

    # ── Metadatos ──────────────────────────────────────────────────────────────

    def name(self):
        return "actualizar_problemas"

    def displayName(self):
        return "Actualizar Obstrucciones"

    def group(self):
        return "Problemas"

    def groupId(self):
        return "problemas"

    def shortHelpString(self):
        return (
            "Sincroniza la capa maestra 'Problemas_limitado' con el WFS de problemas\n"
            "abiertos (V_RE_PROBLEMAS_ABIERTOS_SANEA).\n\n"
            "Pasos internos:\n"
            "  1. Conexion al WFS\n"
            "  2. Interseccion WFS x Zona_delimitada\n"
            "  3. Deduplicacion por campo ID\n"
            "  4. Deteccion de problemas finalizados\n"
            "  5. Alta de nuevos y baja de finalizados en Problemas_limitado\n\n"
            f"Parametro 'Campo ID': nombre del campo unico de cada problema "
            f"(por defecto: '{CAMPO_ID_DEFAULT}').\n\n"
            "Parametro 'Eliminar problemas finalizados': el WFS solo publica\n"
            "problemas abiertos, por lo que un problema que desaparece del servicio\n"
            "se considera finalizado y se borra de la capa maestra. Los registros\n"
            "sin valor en el campo ID (cargados a mano) nunca se eliminan."
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
                self.PROBLEMAS_LIMITADO,
                "Capa maestra Problemas_limitado",
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.CAMPO_ID,
                "Campo ID unico del problema",
                defaultValue=CAMPO_ID_DEFAULT,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.LIMPIAR_FINALIZADOS,
                "Eliminar problemas finalizados (los que ya no estan en el WFS)",
                defaultValue=True,
            )
        )
        self.addOutput(
            QgsProcessingOutputNumber(
                self.OUTPUT_AGREGADOS,
                "Problemas nuevos agregados",
            )
        )
        self.addOutput(
            QgsProcessingOutputNumber(
                self.OUTPUT_ELIMINADOS,
                "Problemas finalizados eliminados",
            )
        )

    # ── Logica principal ───────────────────────────────────────────────────────

    def processAlgorithm(self, parameters, context, feedback):

        zona_delimitada   = self.parameterAsVectorLayer(parameters, self.ZONA_DELIMITADA,   context)
        problemas          = self.parameterAsVectorLayer(parameters, self.PROBLEMAS_LIMITADO, context)
        campo_id          = self.parameterAsString    (parameters, self.CAMPO_ID,           context).strip()
        limpiar           = self.parameterAsBool      (parameters, self.LIMPIAR_FINALIZADOS, context)

        # ── Validaciones ───────────────────────────────────────────────────────

        if not zona_delimitada or not zona_delimitada.isValid():
            raise QgsProcessingException("La capa Zona_delimitada no es valida.")
        if not problemas or not problemas.isValid():
            raise QgsProcessingException("La capa Problemas_limitado no es valida.")

        if problemas.fields().lookupField(campo_id) == -1:
            raise QgsProcessingException(
                f"El campo ID '{campo_id}' no existe en Problemas_limitado. "
                f"Campos disponibles: {[f.name() for f in problemas.fields()]}"
            )
        capas_auxiliares = []
        cant_nuevos      = 0
        cant_eliminados  = 0
        ids_nuevos       = []
        ids_eliminados   = []

        try:
            # ── PASO 0: Cargar capa WFS ───────────────────────────────────────
            feedback.pushInfo("[0/4] Conectando al servicio WFS ...")
            feedback.setProgress(2)

            uri_wfs  = (
                f"pagingEnabled='default' "
                f"typename='{WFS_TYPENAME}' "
                f"url='{WFS_URL}' "
                f"version='auto' "
                f"srsname='EPSG:32721'"
            )
            capa_wfs = QgsVectorLayer(uri_wfs, "WFS_Problemas", "WFS")
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
            feedback.pushInfo(f"  ✔ WFS conectado: {capa_wfs.featureCount()} problemas disponibles.")

            # IDs actualmente abiertos segun el servicio. Se toman de la capa WFS
            # completa y no de la interseccion: un problema sigue abierto aunque
            # su geometria quede fuera de la zona, y no corresponde borrarlo.
            ids_abiertos = set()
            if limpiar:
                req_wfs = QgsFeatureRequest().setFlags(QgsFeatureRequest.NoGeometry)
                req_wfs.setSubsetOfAttributes([campo_id], capa_wfs.fields())
                for feat in capa_wfs.getFeatures(req_wfs):
                    val = feat[campo_id]
                    if val is not None:
                        ids_abiertos.add(str(val).strip())
                feedback.pushInfo(f"  IDs abiertos en el WFS: {len(ids_abiertos)}")

            # ── PASO 1: Interseccion WFS x Zona_delimitada ────────────────────
            feedback.pushInfo("[1/4] Intersectando WFS con Zona_delimitada ...")
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
                return {self.OUTPUT_AGREGADOS: 0, self.OUTPUT_ELIMINADOS: 0}

            if total_interseccion == 0:
                feedback.pushInfo("  Sin problemas abiertos en la zona.")

            capa_candidatas = capa_interseccion

            # ── PASO 2: Deduplicar ────────────────────────────────────────────
            feedback.pushInfo("[2/4] Detectando problemas nuevos (deduplicacion) ...")

            ids_existentes = set()
            for feat in problemas.getFeatures(
                QgsFeatureRequest().setFlags(QgsFeatureRequest.NoGeometry)
            ):
                val = feat[campo_id]
                if val is not None:
                    ids_existentes.add(str(val).strip())

            feedback.pushInfo(
                f"  IDs ya presentes en Problemas: {len(ids_existentes)}"
            )

            campos_obs      = problemas.fields()
            nombres_obs     = [f.name() for f in campos_obs]
            features_nuevas = []
            ids_en_lote     = set()   # evita duplicados dentro de la misma corrida
            duplicados_lote = 0

            for feat in capa_candidatas.getFeatures():
                val_id = feat[campo_id]
                if val_id is None:
                    continue
                id_norm = str(val_id).strip()
                if id_norm in ids_existentes:
                    continue
                if id_norm in ids_en_lote:
                    duplicados_lote += 1
                    continue
                ids_en_lote.add(id_norm)

                nueva = QgsFeature(campos_obs)
                nueva.setGeometry(feat.geometry())
                for nombre in nombres_obs:
                    if nombre.lower() == "fid":
                        continue
                    idx = feat.fields().lookupField(nombre)
                    if idx != -1:
                        nueva[nombre] = feat[nombre]
                features_nuevas.append(nueva)
                ids_nuevos.append(id_norm)

            cant_nuevos = len(features_nuevas)
            if duplicados_lote:
                feedback.pushInfo(
                    f"  Duplicados descartados en esta corrida: {duplicados_lote}"
                )
            feedback.pushInfo(f"  Problemas nuevos a agregar: {cant_nuevos}")
            feedback.setProgress(70)

            # ── PASO 3: Detectar problemas finalizados ────────────────────────
            feedback.pushInfo("[3/4] Detectando problemas finalizados ...")

            fids_a_borrar  = []
            sin_id         = 0

            if not limpiar:
                feedback.pushInfo("  Limpieza desactivada por parametro. Se omite.")
            elif not ids_abiertos:
                # Si el servicio no devolvio ningun ID no se puede distinguir
                # "todo finalizado" de "respuesta vacia por error"; no se borra nada.
                feedback.pushInfo(
                    "  El WFS no devolvio ningun ID. Se omite la limpieza por seguridad."
                )
            else:
                for feat in problemas.getFeatures(
                    QgsFeatureRequest().setFlags(QgsFeatureRequest.NoGeometry)
                ):
                    val = feat[campo_id]
                    if val is None or str(val).strip() == "":
                        sin_id += 1
                        continue
                    id_norm = str(val).strip()
                    if id_norm not in ids_abiertos:
                        fids_a_borrar.append(feat.id())
                        ids_eliminados.append(id_norm)

                if sin_id:
                    feedback.pushInfo(
                        f"  Registros sin ID conservados (carga manual): {sin_id}"
                    )

            cant_eliminados = len(fids_a_borrar)
            feedback.pushInfo(f"  Problemas finalizados a eliminar: {cant_eliminados}")
            feedback.setProgress(80)

            if cant_nuevos == 0 and cant_eliminados == 0:
                feedback.pushInfo("  Problemas ya estan actualizados. Sin cambios.")
                return {self.OUTPUT_AGREGADOS: 0, self.OUTPUT_ELIMINADOS: 0}

            # ── PASO 4: Aplicar altas y bajas ─────────────────────────────────
            feedback.pushInfo("[4/4] Aplicando cambios en Problemas_limitado ...")

            ya_editando = problemas.isEditable()
            if not ya_editando:
                problemas.startEditing()

            if features_nuevas:
                if not problemas.addFeatures(features_nuevas):
                    problemas.rollBack()
                    raise QgsProcessingException(
                        "No se pudieron agregar los nuevos problemas. "
                        "Verificar que la capa no este en modo solo lectura."
                    )

            if fids_a_borrar:
                if not problemas.deleteFeatures(fids_a_borrar):
                    problemas.rollBack()
                    raise QgsProcessingException(
                        "No se pudieron eliminar los problemas finalizados. "
                        "Verificar que la capa no este en modo solo lectura."
                    )

            if not ya_editando:
                problemas.commitChanges()

            problemas.updateExtents()
            feedback.pushInfo(
                f"  ✔ {cant_nuevos} agregados / {cant_eliminados} eliminados."
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
            f"\n=== Actualizacion completada: {cant_nuevos} agregados, "
            f"{cant_eliminados} eliminados. ==="
        )
        feedback.pushInfo(
            f"IDs incorporados: {', '.join(ids_nuevos) if ids_nuevos else '-'}"
        )
        feedback.pushInfo(
            f"IDs eliminados (finalizados): "
            f"{', '.join(ids_eliminados) if ids_eliminados else '-'}"
        )
        return {
            self.OUTPUT_AGREGADOS: cant_nuevos,
            self.OUTPUT_ELIMINADOS: cant_eliminados,
        }
