"""
Normalizacion, validaciones y diff del intercambio de permisos con la IM.

Sin dependencias: ni QGIS ni openpyxl. La lectura/escritura del XLSX vive en
planilla.py, que es el unico modulo que necesita openpyxl.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import NamedTuple


class ErrorIntercambio(Exception):
    """Falla esperable del intercambio. Se reporta como mensaje, sin traceback."""


def exigir_openpyxl() -> None:
    """
    Falla con un mensaje util si falta openpyxl.

    Vive aca, y no en los modulos que lo usan, porque lo necesitan tanto tabla
    como planilla. No rompe la regla de que nucleo no dependa de terceros: el
    import es local y solo sirve para comprobar que exista.
    """
    try:
        import openpyxl  # noqa: F401
    except ImportError as exc:
        raise ErrorIntercambio(
            f"Se necesita openpyxl para leer o escribir planillas ({exc}).\n"
            "QGIS 3.44 lo trae de fabrica y los plugins declaran esa version como "
            "minima, asi que esto no deberia pasar: o la instalacion de QGIS quedo "
            "incompleta, o el plugin se copio a mano en una version anterior.\n"
            "Se instala en el Python de QGIS con:\n"
            '    "C:/Program Files/QGIS 3.44/apps/Python312/python.exe" -m pip install openpyxl'
        ) from exc


# -- Campos de la capa inspecciones_os ----------------------------------------
CAMPO_PERMISO  = "Permisos_UCCRIU"      # en plural: asi se llama en la capa
CAMPO_CONTEXTO = "N_Problema"           # solo lectura para la IM, y NO es unico
CAMPO_ETAPA    = "Etapa"
CLAVE_FID      = "fid"

# Etapas que se consideran abiertas para pedir permiso. Se carga a mano, asi que
# la comparacion es con trim() y sin distinguir mayusculas.
ETAPAS_ABIERTAS = (
    "Enviado_Obra",
    "Permiso_UCCRIU",
    "En_Ejecución",
    "Fin_Hidraulica",
    "SOMS",
    "No_Corresponde",
    "Obra_Para_Enviar",
)

HOJA_DATOS = "Datos"
HOJA_META = "_meta"
COLUMNA_CLAVE = "clave"
COLUMNAS_DATOS = (COLUMNA_CLAVE, CAMPO_CONTEXTO, CAMPO_PERMISO)

ADVERTENCIA_FID = (
    "fid es el rowid del proveedor y no es estable ante re-exportacion de la "
    "capa ni ante migracion a PostGIS. Si la capa cambia de soporte entre el "
    "envio y la devolucion, el merge escribira en filas incorrectas."
)


# -- Normalizacion ------------------------------------------------------------
# Espacios que Excel y los copy/paste desde el navegador meten sin que se vean.
# El NBSP es el que mas aparece: una celda que "esta vacia" trae " ".
# Van escapados a proposito: escritos literales son invisibles al revisar.
_ESPACIOS_INVISIBLES = "\u00a0\u2007\u202f\u200b\ufeff"

_RE_ENTERO = re.compile(r"^-?\d+$")


def _limpiar(valor) -> str:
    """str() -> trim incluyendo NBSP -> quita el '.0' que openpyxl agrega a los enteros."""
    if valor is None:
        return ""
    # QGIS no entrega los NULL como None sino como QVariant nulo, y str() de eso
    # devuelve la cadena "NULL". Sin este control, una celda vacia de la capa
    # viaja a la IM con el texto NULL adentro y despues vuelve como si fuera un
    # permiso cargado. Se detecta por duck typing para no importar QGIS aca.
    es_nulo = getattr(valor, "isNull", None)
    if callable(es_nulo) and es_nulo():
        return ""
    texto = str(valor)
    for caracter in _ESPACIOS_INVISIBLES:
        texto = texto.replace(caracter, " ")
    texto = texto.strip()
    # Una celda numerica entera vuelve de openpyxl como 123.0; sin esto la clave
    # 123 del archivo no encontraria a la 123 de la capa.
    if texto.endswith(".0") and _RE_ENTERO.match(texto[:-2]):
        texto = texto[:-2]
    return texto


def texto_limpio(valor) -> str:
    """Lo que se escribe en la capa y en el reporte: limpio pero con sus mayusculas."""
    return _limpiar(valor)


def normalizar(valor) -> str:
    """Lo que se compara. Misma funcion de los dos lados del intercambio."""
    return _limpiar(valor).casefold()


def esta_vacio(valor) -> bool:
    return _limpiar(valor) == ""


def normalizar_clave(valor, es_fid: bool):
    """Clave comparable: int cuando es fid, texto plegado cuando es UUID."""
    texto = _limpiar(valor)
    if not texto:
        raise ErrorIntercambio("clave vacia")
    if not es_fid:
        return texto.casefold()
    if not _RE_ENTERO.match(texto):
        raise ErrorIntercambio(f"{texto!r} no es un entero")
    return int(texto)


# -- Filtro de la capa --------------------------------------------------------
def expresion_abiertas(campo_etapa: str = CAMPO_ETAPA,
                       campo_permiso: str = CAMPO_PERMISO) -> str:
    """
    Expresion por defecto del algoritmo A. Se genera desde ETAPAS_ABIERTAS para
    que la lista blanca sea una sola: si se edita la expresion en el dialogo, el
    reporte de etapas desconocidas avisa que las dos dejaron de coincidir.
    """
    valores = ", ".join("'" + e.upper().replace("'", "''") + "'" for e in ETAPAS_ABIERTAS)
    vacio = "''"
    return (
        'trim(upper("{etapa}")) IN ({valores})\n'
        'AND ("{permiso}" IS NULL OR trim("{permiso}") = {vacio})'
    ).format(etapa=campo_etapa, valores=valores, permiso=campo_permiso, vacio=vacio)


def etapas_desconocidas(valores) -> dict:
    """
    Valores de Etapa presentes en la capa que no estan en la lista blanca, con
    su frecuencia. Sirve para detectar etapas nuevas o mal escritas que quedarian
    fuera del envio sin que nadie se entere. No aborta nada.
    """
    abiertas = {normalizar(e) for e in ETAPAS_ABIERTAS}
    cuenta: Counter = Counter()
    for valor in valores:
        if normalizar(valor) in abiertas:
            continue
        cuenta[texto_limpio(valor) or "(sin dato)"] += 1
    return dict(cuenta.most_common())


def hash_claves(claves) -> str:
    """Checksum del set de claves exportadas, independiente del orden."""
    texto = "\n".join(sorted(str(c) for c in claves))
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


# -- Validacion de claves -----------------------------------------------------
def validar_claves(valores, campo_clave: str, primera_fila: int = 1) -> list:
    """
    Normaliza la lista de claves crudas y devuelve las validas, o levanta
    ErrorIntercambio con TODOS los problemas juntos: encontrarlos de a uno por
    corrida es inusable con cientos de filas.

    primera_fila desplaza los numeros de fila del mensaje (el XLSX arranca en 2
    porque la 1 es el encabezado).
    """
    es_fid = campo_clave == CLAVE_FID
    claves: list = []
    vacias: list = []
    invalidas: list = []
    primera_vez: dict = {}
    duplicadas: dict = {}

    for desplazamiento, crudo in enumerate(valores):
        fila = primera_fila + desplazamiento
        try:
            clave = normalizar_clave(crudo, es_fid)
        except ErrorIntercambio as exc:
            if esta_vacio(crudo):
                vacias.append(fila)
            else:
                invalidas.append(f"fila {fila}: {exc}")
            continue
        if clave in primera_vez:
            duplicadas.setdefault(clave, [primera_vez[clave]]).append(fila)
        else:
            primera_vez[clave] = fila
        claves.append(clave)

    problemas = []
    if vacias:
        problemas.append(f"Claves vacias en las filas: {_muestra(vacias)}")
    if invalidas:
        problemas.append(
            f"Claves no validas para campo_clave='{campo_clave}': {_muestra(invalidas)}"
        )
    if duplicadas:
        detalle = [
            "{} (filas {})".format(clave, ", ".join(str(f) for f in filas))
            for clave, filas in list(duplicadas.items())[:10]
        ]
        extra = f" ... y {len(duplicadas) - 10} mas" if len(duplicadas) > 10 else ""
        problemas.append("Claves duplicadas: " + "; ".join(detalle) + extra)

    if problemas:
        raise ErrorIntercambio(
            f"El campo clave '{campo_clave}' no sirve como clave 1:1:\n  - "
            + "\n  - ".join(problemas)
        )
    return claves


def _muestra(items, tope: int = 20) -> str:
    visibles = ", ".join(str(i) for i in items[:tope])
    return visibles + (f" ... y {len(items) - tope} mas" if len(items) > tope else "")


# -- Comparacion de metadatos -------------------------------------------------
_RE_LAYERNAME = re.compile(r"layername=([^|]+)", re.I)
_RE_TABLE = re.compile(r'table=(?:"[^"]+"\.)?"?([A-Za-z0-9_]+)"?', re.I)


def partes_fuente(uri: str) -> tuple:
    """
    (archivo, tabla, directorio) de una URI de capa. Sirve para gpkg
    ("ruta.gpkg|layername=tabla") y para PostGIS (... table="esquema"."tabla" ...).
    """
    uri = uri or ""
    ruta = uri.split("|", 1)[0].strip()
    encontrado = _RE_LAYERNAME.search(uri) or _RE_TABLE.search(uri)
    tabla = encontrado.group(1).strip() if encontrado else ""
    normalizada = ruta.replace("\\", "/").rstrip("/")
    if "/" in normalizada:
        directorio, _, archivo = normalizada.rpartition("/")
    else:
        directorio, archivo = "", normalizada
    return archivo.casefold(), tabla.casefold(), directorio.casefold()


def comparar_metadatos(meta: dict, proveedor: str, fuente: str) -> tuple:
    """
    Devuelve (errores, avisos) al cruzar el _meta del envio contra la capa destino.

    Aborta solo si cambio el proveedor, el archivo o el nombre de la tabla: eso
    significa que la capa destino no es la que se exporto. Un cambio de directorio
    a secas es aviso, porque la capa vive en un proyecto QFieldCloud sincronizado
    y la misma tabla aparece en rutas distintas segun la maquina.
    """
    errores: list = []
    avisos: list = []

    proveedor_meta = str(meta.get("proveedor") or "")
    fuente_meta = str(meta.get("fuente") or "")

    if proveedor_meta and proveedor_meta != proveedor:
        errores.append(
            f"El proveedor cambio desde el envio: '{proveedor_meta}' -> '{proveedor}'."
        )

    if fuente_meta:
        archivo_meta, tabla_meta, dir_meta = partes_fuente(fuente_meta)
        archivo_hoy, tabla_hoy, dir_hoy = partes_fuente(fuente)
        if tabla_meta and tabla_meta != tabla_hoy:
            errores.append(f"La tabla cambio: '{tabla_meta}' -> '{tabla_hoy}'.")
        if archivo_meta != archivo_hoy:
            errores.append(
                f"El archivo de origen cambio: '{archivo_meta}' -> '{archivo_hoy}'."
            )
        elif dir_meta != dir_hoy:
            avisos.append(
                "La capa esta en otro directorio que cuando se exporto (misma tabla "
                "y mismo archivo, se continua).\n"
                f"    envio: {fuente_meta}\n"
                f"    hoy  : {fuente}"
            )
    return errores, avisos


# -- Diff ---------------------------------------------------------------------
ACC_ACTUALIZADO    = "actualizado"
ACC_SIN_CAMBIO     = "sin_cambio"
ACC_IGNORADO_VACIO = "ignorado_vacio"
ACC_VACIADO        = "vaciado"
ACC_HUERFANA       = "clave_huerfana"
ACC_NO_DEVUELTA    = "no_devuelta"
ACC_DESALINEADO    = "desalineado"

# Orden en que se muestran los conteos: primero lo que se escribe, despues lo que
# no paso, al final lo que hay que mirar a mano.
ACCIONES = (
    ACC_ACTUALIZADO, ACC_VACIADO, ACC_SIN_CAMBIO, ACC_IGNORADO_VACIO,
    ACC_NO_DEVUELTA, ACC_HUERFANA, ACC_DESALINEADO,
)

ACCIONES_A_REVISAR = (ACC_HUERFANA, ACC_DESALINEADO)


class Evaluacion(NamedTuple):
    clave: object
    n_problema: str
    valor_anterior: str
    valor_nuevo: str
    accion: str


def _contexto_alineado(del_archivo, de_la_capa, es_fid: bool) -> bool:
    """
    Con fid, un N_Problema vacio de cualquier lado no verifica nada: en esta capa
    hay filas sin N_Problema, y tratarlas como iguales dejaria pasar en silencio
    justo el cruce de claves que este control existe para detectar.
    """
    archivo, capa = normalizar(del_archivo), normalizar(de_la_capa)
    if es_fid and (not archivo or not capa):
        return False
    return archivo == capa


def diferencias(filas_archivo, filas_capa, claves_enviadas, campo_clave,
                permitir_vaciar: bool) -> tuple:
    """
    filas_archivo: [{'clave': <ya normalizada>, 'N_Problema':..., 'Permisos_UCCRIU':...}]
    filas_capa   : {clave_normalizada: {'fid': int, 'N_Problema':..., 'Permisos_UCCRIU':...}}
    claves_enviadas: las del _meta, para detectar las que no volvieron.

    Devuelve (evaluaciones, escrituras) con escrituras = {fid: valor_a_escribir}.
    Decidir y escribir estan separados a proposito: el modo simulacion corre
    exactamente este mismo diff y despues descarta las escrituras.
    """
    es_fid = campo_clave == CLAVE_FID
    evaluaciones: list = []
    escrituras: dict = {}

    for fila in filas_archivo:
        clave = fila["clave"]
        nuevo = texto_limpio(fila.get(CAMPO_PERMISO))
        contexto_archivo = texto_limpio(fila.get(CAMPO_CONTEXTO))
        destino = filas_capa.get(clave)

        if destino is None:
            evaluaciones.append(
                Evaluacion(clave, contexto_archivo, "", nuevo, ACC_HUERFANA))
            continue

        anterior = texto_limpio(destino.get(CAMPO_PERMISO))
        contexto_capa = texto_limpio(destino.get(CAMPO_CONTEXTO))

        if not _contexto_alineado(contexto_archivo, contexto_capa, es_fid):
            evaluaciones.append(Evaluacion(
                clave,
                "{} != capa:{}".format(
                    contexto_archivo or "(vacio)", contexto_capa or "(vacio)"),
                anterior, nuevo, ACC_DESALINEADO,
            ))
            continue

        if not nuevo:
            if permitir_vaciar and anterior:
                accion = ACC_VACIADO
                escrituras[destino["fid"]] = None
            elif permitir_vaciar:
                accion = ACC_SIN_CAMBIO
            else:
                accion = ACC_IGNORADO_VACIO
        elif normalizar(nuevo) == normalizar(anterior):
            accion = ACC_SIN_CAMBIO
        else:
            accion = ACC_ACTUALIZADO
            escrituras[destino["fid"]] = nuevo

        evaluaciones.append(
            Evaluacion(clave, contexto_capa, anterior, nuevo, accion))

    devueltas = {fila["clave"] for fila in filas_archivo}
    for clave in claves_enviadas:
        if clave in devueltas:
            continue
        destino = filas_capa.get(clave) or {}
        evaluaciones.append(Evaluacion(
            clave,
            texto_limpio(destino.get(CAMPO_CONTEXTO)),
            texto_limpio(destino.get(CAMPO_PERMISO)),
            "", ACC_NO_DEVUELTA,
        ))

    return evaluaciones, escrituras


def resumen(evaluaciones) -> dict:
    cuenta = Counter(e.accion for e in evaluaciones)
    return {accion: cuenta[accion] for accion in ACCIONES if cuenta[accion]}
