"""
Algoritmo B del intercambio con la Intendencia: mergea el XLSX devuelto.

Escribe unicamente la columna del permiso, fila por fila segun la clave, y solo
despues de que pasan todas las validaciones. Cualquier fila sospechosa se
reporta y no se escribe.
"""

from datetime import datetime

from qgis.core import (
    QgsFeatureRequest,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterVectorLayer,
)

from ..intercambio_im import nucleo, planilla
from ..intercambio_im.nucleo import ErrorIntercambio
from .comun_im import advertir_si_fid, indice_campo


class MergearPermisosIM(QgsProcessingAlgorithm):

    CAPA            = "CAPA"
    ARCHIVO         = "ARCHIVO"
    SIMULACION      = "SIMULACION"
    PERMITIR_VACIAR = "PERMITIR_VACIAR"
    REPORTE         = "REPORTE"
    ESCRITAS        = "FILAS_ESCRITAS"

    # ── Metadatos ──────────────────────────────────────────────────────────────

    def name(self):
        return "mergear_permisos_im"

    def displayName(self):
        return "2. Mergear permisos devueltos por la IM"

    def group(self):
        return "Intercambio IM"

    def groupId(self):
        return "intercambio_im"

    def shortHelpString(self):
        return (
            "Vuelca el XLSX devuelto por la Intendencia sobre la capa, escribiendo\n"
            f"UNICAMENTE la columna '{nucleo.CAMPO_PERMISO}'. Cualquier otra columna\n"
            "que la IM haya tocado se ignora.\n\n"
            "Corre en modo simulacion por defecto: produce el reporte completo sin\n"
            "tocar la capa. Conviene mirarlo antes de destildar la casilla.\n\n"
            "Acciones del reporte:\n"
            "  actualizado    - se escribio el permiso nuevo\n"
            "  vaciado        - se borro un permiso existente (solo con 'permitir vaciar')\n"
            "  sin_cambio     - el valor devuelto ya estaba en la capa\n"
            "  ignorado_vacio - la IM no completo la celda y no se pisa lo que hay\n"
            "  no_devuelta    - la fila se envio pero no volvio en el archivo\n"
            "  clave_huerfana - la clave del archivo no existe en la capa\n"
            "  desalineado    - el N_Problema del archivo no coincide con el de la\n"
            "                   capa para esa clave: senal de que las claves se\n"
            "                   corrompieron. Esas filas NUNCA se escriben."
        )

    def createInstance(self):
        return MergearPermisosIM()

    # ── Parametros ─────────────────────────────────────────────────────────────

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.CAPA,
                "Capa destino (inspecciones_os)",
            )
        )
        self.addParameter(
            QgsProcessingParameterFile(
                self.ARCHIVO,
                "Planilla devuelta por la IM",
                extension="xlsx",
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.SIMULACION,
                "Modo simulacion (no escribe nada, solo genera el reporte)",
                defaultValue=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.PERMITIR_VACIAR,
                "Permitir que una celda vacia borre el permiso ya cargado",
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.REPORTE,
                "Reporte del merge",
                fileFilter="Planilla de Excel (*.xlsx)",
            )
        )
        self.addOutput(
            QgsProcessingOutputNumber(self.ESCRITAS, "Filas escritas")
        )

    # ── Proceso ────────────────────────────────────────────────────────────────

    def processAlgorithm(self, parameters, context, feedback):
        capa = self.parameterAsVectorLayer(parameters, self.CAPA, context)
        if capa is None:
            raise QgsProcessingException("No se pudo cargar la capa destino.")

        archivo         = self.parameterAsFile(parameters, self.ARCHIVO, context)
        simulacion      = self.parameterAsBool(parameters, self.SIMULACION, context)
        permitir_vaciar = self.parameterAsBool(parameters, self.PERMITIR_VACIAR, context)
        reporte         = self.parameterAsFileOutput(parameters, self.REPORTE, context)

        # Si el usuario dejo la capa en edicion con cambios pendientes, un
        # commitChanges() de este algoritmo tambien confirmaria los suyos, y el
        # rollback ante error se los borraria.
        if capa.isEditable() and capa.isModified():
            raise QgsProcessingException(
                f"La capa '{capa.name()}' esta en modo edicion con cambios sin guardar.\n"
                "Guardalos o descartalos antes de mergear: si no, este algoritmo "
                "confirmaria o descartaria tus cambios junto con los suyos."
            )

        feedback.pushInfo("[1/5] Leyendo la planilla devuelta ...")
        try:
            filas_archivo, meta, claves_enviadas_crudas = planilla.leer_devolucion(archivo)
        except ErrorIntercambio as exc:
            raise QgsProcessingException(str(exc)) from exc
        feedback.pushInfo(f"  {len(filas_archivo)} filas con datos.")

        feedback.pushInfo("[2/5] Verificando los metadatos del envio ...")
        campo_clave = self._resolver_campo_clave(meta, filas_archivo, capa, feedback)
        self._verificar_metadatos(meta, capa, campo_clave, feedback)
        advertir_si_fid(feedback, campo_clave)

        feedback.pushInfo(f"[3/5] Validando las claves del archivo ('{campo_clave}') ...")
        try:
            claves = nucleo.validar_claves(
                [f[nucleo.COLUMNA_CLAVE] for f in filas_archivo], campo_clave, primera_fila=2
            )
            claves_enviadas = nucleo.validar_claves(claves_enviadas_crudas, campo_clave)
        except ErrorIntercambio as exc:
            raise QgsProcessingException(str(exc)) from exc
        for fila, clave in zip(filas_archivo, claves):
            fila[nucleo.COLUMNA_CLAVE] = clave
        self._verificar_hash(meta, claves_enviadas, feedback)
        feedback.setProgress(30)

        feedback.pushInfo("[4/5] Comparando contra la capa ...")
        idx_permiso = indice_campo(capa, nucleo.CAMPO_PERMISO)
        filas_capa = self._leer_capa(capa, campo_clave)
        evaluaciones, escrituras = nucleo.diferencias(
            filas_archivo, filas_capa, claves_enviadas, campo_clave, permitir_vaciar
        )
        conteos = nucleo.resumen(evaluaciones)
        feedback.setProgress(60)

        vaciados = conteos.get(nucleo.ACC_VACIADO, 0)
        if vaciados:
            feedback.pushWarning(
                f"ADVERTENCIA: {vaciados} filas van a quedar SIN permiso porque la "
                "IM devolvio la celda vacia y 'permitir vaciar' esta activo."
            )

        feedback.pushInfo("[5/5] Escribiendo ...")
        escritas = 0
        if simulacion:
            feedback.pushInfo(
                f"  Modo simulacion: no se toco la capa. {len(escrituras)} filas "
                "se escribirian."
            )
        elif not escrituras:
            feedback.pushInfo("  No hay nada que escribir.")
        else:
            escritas = self._escribir(capa, idx_permiso, escrituras, feedback)
            feedback.pushInfo(f"  {escritas} filas escritas y confirmadas.")
        feedback.setProgress(85)

        encabezado = {
            "fecha_merge": datetime.now().astimezone().isoformat(timespec="seconds"),
            "archivo": archivo,
            "capa": capa.name(),
            "proveedor": capa.dataProvider().name(),
            "fuente": capa.source(),
            "campo_clave": campo_clave,
            "lote_id": meta.get("lote_id", "(sin _meta)"),
            "modo": "simulacion" if simulacion else "escritura",
            "permitir_vaciar": "si" if permitir_vaciar else "no",
        }
        try:
            planilla.escribir_reporte(reporte, evaluaciones, conteos, encabezado)
        except ErrorIntercambio as exc:
            raise QgsProcessingException(str(exc)) from exc

        self._resumir(conteos, evaluaciones, reporte, simulacion, feedback)
        feedback.setProgress(100)
        return {self.REPORTE: reporte, self.ESCRITAS: escritas}

    # ── Metadatos ──────────────────────────────────────────────────────────────

    def _resolver_campo_clave(self, meta, filas_archivo, capa, feedback) -> str:
        declarado = nucleo.texto_limpio(meta.get("campo_clave"))
        if declarado:
            return declarado

        feedback.pushWarning(
            "ADVERTENCIA: el archivo no trae la hoja '_meta' (la IM la borro o la "
            "planilla se rearmo). Se pierden el control de proveedor/fuente y el "
            "reporte de filas no devueltas."
        )
        crudas = [f[nucleo.COLUMNA_CLAVE] for f in filas_archivo]
        try:
            nucleo.validar_claves(crudas, nucleo.CLAVE_FID)
        except ErrorIntercambio:
            pass
        else:
            feedback.pushInfo("  Todas las claves son enteras: se asume campo_clave='fid'.")
            return nucleo.CLAVE_FID

        inferido = self._buscar_campo_que_cubre(capa, crudas)
        if inferido:
            feedback.pushInfo(f"  Las claves coinciden con el campo '{inferido}'.")
            return inferido

        raise QgsProcessingException(
            "El archivo no tiene hoja '_meta' y no se pudo inferir el campo clave: "
            "las claves no son enteras y no coinciden con ningun campo de texto de "
            "la capa.\nVolve a exportar con el algoritmo 1 y pedile a la IM que no "
            "borre la hoja oculta."
        )

    def _buscar_campo_que_cubre(self, capa, claves_crudas):
        """Primer campo de texto cuyos valores unicos cubren todas las claves del archivo."""
        buscadas = {nucleo.normalizar(c) for c in claves_crudas}
        if not buscadas:
            return None
        for campo in capa.fields():
            indice = capa.fields().lookupField(campo.name())
            solicitud = (
                QgsFeatureRequest()
                .setSubsetOfAttributes([indice])
                .setFlags(QgsFeatureRequest.NoGeometry)
            )
            valores = [nucleo.normalizar(f[indice]) for f in capa.getFeatures(solicitud)]
            sin_vacios = [v for v in valores if v]
            if len(set(sin_vacios)) == len(valores) and buscadas <= set(sin_vacios):
                return campo.name()
        return None

    def _verificar_metadatos(self, meta, capa, campo_clave, feedback) -> None:
        """
        Con fid, un cambio de proveedor/tabla/archivo aborta: las filas ya no son
        las mismas y el merge escribiria en cualquier lado. Con clave UUID la
        identidad viaja en el dato, asi que alcanza con avisar.
        """
        if not meta:
            return
        errores, avisos = nucleo.comparar_metadatos(
            meta, capa.dataProvider().name(), capa.source()
        )
        for aviso in avisos:
            feedback.pushWarning(f"ADVERTENCIA: {aviso}")

        if not errores:
            feedback.pushInfo("  La capa destino coincide con la del envio.")
            return

        detalle = "\n  - ".join(errores)
        if campo_clave == nucleo.CLAVE_FID:
            raise QgsProcessingException(
                "La capa destino no es la que se exporto:\n  - " + detalle + "\n\n"
                + nucleo.ADVERTENCIA_FID
                + "\nMerge abortado. No se escribio nada."
            )
        feedback.pushWarning(
            "ADVERTENCIA: la capa destino cambio desde el envio:\n  - " + detalle
            + f"\nSe continua porque la clave es '{campo_clave}' y no depende del orden "
              "de las filas."
        )

    def _verificar_hash(self, meta, claves_enviadas, feedback) -> None:
        esperado = nucleo.texto_limpio(meta.get("hash_claves"))
        if not esperado or not claves_enviadas:
            return
        if nucleo.hash_claves(claves_enviadas) != esperado:
            feedback.pushWarning(
                "ADVERTENCIA: la lista de claves enviadas de la hoja '_meta' no "
                "coincide con su propio hash. Alguien edito esa hoja; el reporte de "
                "filas no devueltas puede estar incompleto."
            )

    # ── Capa ───────────────────────────────────────────────────────────────────

    def _leer_capa(self, capa, campo_clave) -> dict:
        """{clave_normalizada: {'fid':…, 'N_Problema':…, 'Permisos_UCCRIU':…}}."""
        es_fid = campo_clave == nucleo.CLAVE_FID
        idx_contexto = indice_campo(capa, nucleo.CAMPO_CONTEXTO)
        idx_permiso  = indice_campo(capa, nucleo.CAMPO_PERMISO)
        indices = [idx_contexto, idx_permiso]
        idx_clave = None
        if not es_fid:
            idx_clave = indice_campo(capa, campo_clave)
            indices.append(idx_clave)

        solicitud = (
            QgsFeatureRequest()
            .setSubsetOfAttributes(indices)
            .setFlags(QgsFeatureRequest.NoGeometry)
        )

        filas, duplicadas = {}, []
        for feature in capa.getFeatures(solicitud):
            crudo = feature.id() if es_fid else feature[idx_clave]
            try:
                clave = nucleo.normalizar_clave(crudo, es_fid)
            except ErrorIntercambio:
                # Una fila de la capa sin clave nunca puede ser destino de un
                # merge; no es un error del archivo devuelto.
                continue
            if clave in filas:
                duplicadas.append(clave)
                continue
            filas[clave] = {
                "fid": feature.id(),
                nucleo.CAMPO_CONTEXTO: feature[idx_contexto],
                nucleo.CAMPO_PERMISO: feature[idx_permiso],
            }

        if duplicadas:
            raise QgsProcessingException(
                f"El campo '{campo_clave}' esta repetido en la capa destino y no "
                f"sirve como clave 1:1. Valores repetidos: "
                f"{', '.join(str(d) for d in duplicadas[:20])}"
            )
        return filas

    def _escribir(self, capa, idx_permiso, escrituras, feedback) -> int:
        """Una sola sesion de edicion para todo el lote, con rollback ante cualquier error."""
        if not capa.startEditing():
            raise QgsProcessingException(
                f"No se pudo abrir la capa '{capa.name()}' en modo edicion."
            )
        try:
            for fid, valor in escrituras.items():
                if not capa.changeAttributeValues(fid, {idx_permiso: valor}):
                    raise QgsProcessingException(
                        f"No se pudo asignar el permiso en la fila fid={fid}."
                    )
            if not capa.commitChanges():
                raise QgsProcessingException(
                    "commitChanges() fallo:\n  " + "\n  ".join(capa.commitErrors())
                )
        except Exception:
            capa.rollBack()
            feedback.reportError(
                "Se revirtieron todos los cambios: la capa quedo como estaba.", False
            )
            raise
        return len(escrituras)

    # ── Salida ─────────────────────────────────────────────────────────────────

    def _resumir(self, conteos, evaluaciones, reporte, simulacion, feedback) -> None:
        lineas = [f"  {accion:<15}: {cantidad}" for accion, cantidad in conteos.items()]
        feedback.pushInfo(
            "\n=== Resumen del merge ===\n"
            + "\n".join(lineas)
            + f"\n  {'TOTAL':<15}: {len(evaluaciones)}"
            + f"\n  Reporte        : {reporte}"
            + ("\n  MODO SIMULACION: la capa no se toco." if simulacion else "")
        )
        a_revisar = sum(conteos.get(a, 0) for a in nucleo.ACCIONES_A_REVISAR)
        if a_revisar:
            feedback.pushWarning(
                f"ADVERTENCIA: {a_revisar} filas quedaron sin aplicar por "
                f"{' o '.join(nucleo.ACCIONES_A_REVISAR)}. Miralas en el reporte: "
                "son la senal de que las claves del intercambio no cerraron."
            )
