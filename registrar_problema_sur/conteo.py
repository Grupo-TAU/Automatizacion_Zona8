"""
Conteo de Problemas por categoria de "Tipo", cruzado con el campo Dentro_Zona.

Dos fuentes, un solo criterio:
    1. La capa problemas_sur cargada en QGIS (se recalcula sola en el panel).
    2. Una planilla CSV o XLSX bajada del sistema (se congela en "Comparacion").

Las dos pasan por clasificar(), asi que si los numeros no coinciden la
diferencia esta en los datos, nunca en el criterio de conteo.

Sin dependencias de UI ni de terceros: csv, zipfile y ElementTree son de la
biblioteca estandar (el xlsx se lee a mano, sin openpyxl, que no viene en el
Python de QGIS).

OJO: CATEGORIAS esta duplicada en sacar_numeros.py, en la raiz del repo, que es
la version de linea de comandos de este mismo conteo. Si se agrega o se cambia
una categoria hay que tocar los dos archivos o los numeros van a divergir.
"""

import csv
import io
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path

CAMPO_TIPO = "Tipo"
CAMPO_ETAPA = "Etapa"

# Los problemas finalizados no se cuentan: el panel muestra lo que queda por
# hacer, no el historico. Se compara contra el PREFIJO de la Etapa normalizada,
# asi que "finalizad" toma Finalizada / Finalizado / Finalizadas, pero no
# convierte un hipotetico "No finalizada" en un descarte.
ETAPAS_DESCARTADAS = ("finalizad",)


# Categorias en ORDEN DE PRIORIDAD: gana la primera que coincide y el problema
# se cuenta una sola vez, asi los subtotales suman exactamente el total.
#
# Los patrones se buscan como subcadena dentro del Tipo normalizado (minusculas,
# sin acentos y con los espacios colapsados), asi que sirven tanto los Tipos
# completos como un prefijo ("obstru" tomaria obstruido / obstruida / obstruidas).
CATEGORIAS = [
    ("Inspecciones", (
        "Varios",
        "Acera y/o Pavimento Hundido",
    )),
    ("Tapas", (
        "Boca de Tormenta sin Tapa",
        "Registro sin Tapa",
    )),
    ("Limpieza", (
        "Alcantarilla Obstruida",
        "Boca de Tormenta Obstruida",
        "Cañada Obstruida",
        "Colector Interno Obstruido",
        "Colector Obstruido",
        "Colector Sucio",
        "Conexion Obstruida",
        "Conexion Sucia",
        "Conexión de Boca de Tormenta Obstruida",
        "Registro Sucio",
    )),
    ("Obras", (
        "Alcantarilla Rota",
        "Boca de Tormenta Dañada",
        "Colector Interno Roto",
        "Colector Roto",
        "Conexion Nueva",
        "Conexion Rota",
        "Conexión de Boca de Tormenta Rota",
        "Registro Nuevo",
        "Registro Roto",
        "Reposición de Calzada",
        "Reposición de Vereda",
        "Reposición de Vereda y Calzada",
    )),
]
CATEGORIA_RESTO = "Otros"

# Los Tipo vacios no se mezclan con "Otros": si aparecen, salen en su propia
# fila. Una planilla y una capa que difieren suelen diferir justo aca.
CATEGORIA_SIN_DATO = "(sin dato)"


# ─────────────────────────────────────────────────────────────────────────────
# CLASIFICACION (la unica fuente de verdad, compartida por las dos fuentes)
# ─────────────────────────────────────────────────────────────────────────────
def normalizar(texto):
    """Minusculas, sin acentos y con los espacios colapsados: el mismo Tipo
    escrito 'Boca de tormenta OBSTRUIDA' o 'boca de tormenta obstruida' tiene
    que caer en la misma categoria."""
    if texto is None:
        return ""
    s = unicodedata.normalize("NFKD", str(texto))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.casefold().split())


# Los patrones se normalizan una sola vez al importar, no en cada fila.
_CATEGORIAS_NORM = [
    (nombre, tuple(normalizar(p) for p in patrones))
    for nombre, patrones in CATEGORIAS
]


def clasificar(tipo):
    t = normalizar(tipo)
    if not t:
        return CATEGORIA_SIN_DATO
    for nombre, patrones in _CATEGORIAS_NORM:
        if any(p in t for p in patrones):
            return nombre
    return CATEGORIA_RESTO


def es_descartable(etapa):
    """True si la Etapa indica un problema que no hay que contar."""
    t = normalizar(etapa)
    return t.startswith(ETAPAS_DESCARTADAS)


def categorias_visibles(*conteos):
    """Las cuatro categorias fijas mas "Otros", y "(sin dato)" solo si alguno de
    los conteos lo trae: una fila vacia permanente confunde mas de lo que informa."""
    nombres = [nombre for nombre, _ in CATEGORIAS] + [CATEGORIA_RESTO]
    if any(c.get(CATEGORIA_SIN_DATO) for c in conteos):
        nombres.append(CATEGORIA_SIN_DATO)
    return nombres


# ─────────────────────────────────────────────────────────────────────────────
# LECTURA DE LA PLANILLA (CSV o XLSX)
# ─────────────────────────────────────────────────────────────────────────────
def _leer_texto(ruta):
    """Los CSV del sistema vienen a veces en UTF-8 y a veces en cp1252; leerlos
    con el encoding equivocado rompe los acentos y descoloca la clasificacion."""
    crudo = Path(ruta).read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return crudo.decode(encoding)
        except UnicodeDecodeError:
            continue
    return crudo.decode("latin-1", errors="replace")


def _detectar_delimitador(muestra):
    try:
        return csv.Sniffer().sniff(muestra, delimiters=",;\t|").delimiter
    except csv.Error:
        # El Sniffer falla con encabezados raros o de una sola columna.
        return max(",;\t|", key=muestra.count)


# El xlsx se lee con zipfile + ElementTree en vez de openpyxl: QGIS no lo trae.
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_NS_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _indice_de_columna(ref):
    """De la referencia de celda al indice de columna: B7 -> 1. Las celdas
    vacias no se escriben en el XML, asi que la posicion hay que sacarla de la
    referencia y no del orden de aparicion."""
    n = 0
    for caracter in ref:
        if caracter.isalpha():
            n = n * 26 + (ord(caracter.upper()) - 64)
    return n - 1


def _texto_de(elemento):
    return "".join(nodo.text or "" for nodo in elemento.iter(f"{_NS}t"))


def leer_filas_xlsx(ruta, hoja=None):
    """
    Devuelve las filas de una hoja como listas de strings. hoja puede ser el
    nombre o el indice; None = la primera, que es de donde salen los calculos.
    Las fechas quedan como numero de serie de Excel: no se usan para contar.
    """
    with zipfile.ZipFile(ruta) as z:
        compartidas = []
        if "xl/sharedStrings.xml" in z.namelist():
            raiz = ET.fromstring(z.read("xl/sharedStrings.xml"))
            compartidas = [_texto_de(si) for si in raiz.findall(f"{_NS}si")]

        libro = ET.fromstring(z.read("xl/workbook.xml"))
        hojas = libro.find(f"{_NS}sheets").findall(f"{_NS}sheet")
        nombres = ", ".join(h.get("name") for h in hojas)

        if hoja is None:
            elegida = hojas[0]
        elif isinstance(hoja, int):
            elegida = hojas[hoja]
        else:
            objetivo = normalizar(hoja)
            elegida = next((h for h in hojas if normalizar(h.get("name")) == objetivo), None)
            if elegida is None:
                raise ValueError(f"El archivo no tiene la hoja {hoja!r}. Hojas: {nombres}")

        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        destinos = {r.get("Id"): r.get("Target") for r in rels.findall(f"{_NS_PKG}Relationship")}
        destino = destinos[elegida.get(f"{_NS_REL}id")].lstrip("/")
        raiz = ET.fromstring(z.read(destino if destino.startswith("xl/") else f"xl/{destino}"))

    filas = []
    for fila_el in raiz.iter(f"{_NS}row"):
        celdas = {}
        for celda in fila_el.findall(f"{_NS}c"):
            tipo = celda.get("t")
            if tipo == "inlineStr":
                bloque = celda.find(f"{_NS}is")
                valor = _texto_de(bloque) if bloque is not None else ""
            else:
                v = celda.find(f"{_NS}v")
                valor = (v.text or "") if v is not None else ""
                if tipo == "s" and valor:
                    valor = compartidas[int(valor)]
            if valor != "":
                celdas[_indice_de_columna(celda.get("r") or "")] = valor
        filas.append([celdas.get(i, "") for i in range(max(celdas) + 1)] if celdas else [])
    return filas


def _filas_de_xlsx(ruta, campo, opcionales, hoja=None):
    filas = leer_filas_xlsx(ruta, hoja)
    objetivo = normalizar(campo)

    # El encabezado no siempre es la primera fila (puede haber un titulo arriba),
    # asi que se busca la primera fila que contenga la columna obligatoria.
    for n, fila in enumerate(filas):
        indice = next((i for i, celda in enumerate(fila) if normalizar(celda) == objetivo), None)
        if indice is None:
            continue
        # Las opcionales se buscan en esa misma fila de encabezado; las que no
        # esten quedan afuera del dict y el que llama decide que hacer.
        indices = {campo: indice}
        for nombre in opcionales:
            objetivo_opc = normalizar(nombre)
            i = next((j for j, celda in enumerate(fila) if normalizar(celda) == objetivo_opc), None)
            if i is not None:
                indices[nombre] = i
        return [
            {k: (f[i] if i < len(f) else "") for k, i in indices.items()}
            for f in filas[n + 1:]
            if any(celda.strip() for celda in f)   # saltear las filas vacias del final
        ]

    primera = ", ".join(c for c in (filas[0] if filas else []) if c)
    raise ValueError(f"La hoja no tiene la columna {campo!r}. Primera fila: {primera or '(vacia)'}")


def _filas_de_csv(ruta, campo, opcionales):
    texto = _leer_texto(ruta)
    lector = csv.DictReader(io.StringIO(texto), delimiter=_detectar_delimitador(texto[:4096]))
    encabezados = lector.fieldnames or []

    def buscar(nombre):
        objetivo = normalizar(nombre)
        return next((h for h in encabezados if normalizar(h) == objetivo), None)

    columna = buscar(campo)
    if columna is None:
        raise ValueError(
            f"El archivo no tiene la columna {campo!r}.\n"
            f"Columnas encontradas: {', '.join(encabezados) or '(ninguna)'}"
        )
    presentes = {campo: columna}
    for nombre in opcionales:
        real = buscar(nombre)
        if real is not None:
            presentes[nombre] = real

    return [{k: (fila.get(real) or "") for k, real in presentes.items()} for fila in lector]


def leer_filas(ruta, campo=CAMPO_TIPO, opcionales=(), hoja=None):
    """
    Devuelve una lista de dicts con la columna obligatoria `campo` y las de
    `opcionales` que existan. Las opcionales que falten simplemente no aparecen
    en los dicts: la planilla del sistema no siempre trae las mismas columnas.
    """
    if Path(ruta).suffix.lower() in (".xlsx", ".xlsm"):
        return _filas_de_xlsx(ruta, campo, opcionales, hoja)
    return _filas_de_csv(ruta, campo, opcionales)


def leer_columna(ruta, campo=CAMPO_TIPO, hoja=None):
    """Los valores de una sola columna, en orden."""
    return [f[campo] for f in leer_filas(ruta, campo, (), hoja)]


def contar_planilla(ruta, campo=CAMPO_TIPO, hoja=None):
    """
    categoria -> cantidad, leyendo la columna Tipo de un CSV o XLSX.

    Si la planilla trae la columna Etapa, los finalizados se descartan igual que
    en la capa. Si no la trae (es lo que pasa hoy: la exportacion del sistema
    solo tiene Problema, Tipo, Ubicacion y Fecha), se cuenta todo, asumiendo que
    el sistema ya exporto unicamente los problemas abiertos.
    """
    conteo = Counter()
    descartados = 0
    for fila in leer_filas(ruta, campo, (CAMPO_ETAPA,), hoja):
        if es_descartable(fila.get(CAMPO_ETAPA)):
            descartados += 1
            continue
        conteo[clasificar(fila[campo])] += 1
    return conteo, descartados


# ─────────────────────────────────────────────────────────────────────────────
# CONTEO DE LA CAPA, CRUZADO CON FUERA / DENTRO DE ZONA
# ─────────────────────────────────────────────────────────────────────────────
# El campo se llama Dentro_Zona y guarda "Si" cuando el problema cae DENTRO.
# Se escribe con el tipo que tenga la capa: texto, bool o entero (ver
# _valor_dentro_zona en capa_utils), asi que hay que aceptar los tres. Un NULL
# no es un "No": un problema sin clasificar no es un problema que este fuera.
CAMPO_DENTRO_ZONA = "Dentro_Zona"

_SI = {"si", "s", "true", "t", "1", "x", "dentro"}
_NO = {"no", "n", "false", "f", "0", "fuera"}


def interpretar_dentro_zona(valor):
    """True = dentro de la zona, False = fuera, None = sin clasificar."""
    if valor is None:
        return None
    # Los NULL de QGIS llegan como QVariant nulo, no como None.
    if hasattr(valor, "isNull") and valor.isNull():
        return None
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        return bool(valor)
    texto = normalizar(valor)
    if not texto or texto == "null":
        return None
    if texto in _SI:
        return True
    if texto in _NO:
        return False
    return None


class ConteoCapa:
    """
    Resultado de recorrer la capa una vez.

    fuera / dentro / sin_clasificar son Counter de categoria -> cantidad.
    total suma los tres, asi que si hay features sin Dentro_Zona la fila no
    cierra entre las dos primeras columnas: es a proposito, el panel lo avisa.

    descartados cuenta los finalizados, que no entran en ningun Counter.
    """

    def __init__(self):
        self.fuera = Counter()
        self.dentro = Counter()
        self.sin_clasificar = Counter()
        self.total = Counter()
        self.descartados = 0   # finalizados que quedaron fuera del conteo

    def agregar(self, tipo, dentro):
        categoria = clasificar(tipo)
        self.total[categoria] += 1
        if dentro is True:
            self.dentro[categoria] += 1
        elif dentro is False:
            self.fuera[categoria] += 1
        else:
            self.sin_clasificar[categoria] += 1

    @property
    def n_sin_clasificar(self):
        return sum(self.sin_clasificar.values())


def contar_capa(capa, campo_tipo=CAMPO_TIPO, campo_dentro_zona=CAMPO_DENTRO_ZONA,
                campo_etapa=CAMPO_ETAPA):
    """
    Recorre los features de la capa y los cuenta por categoria x zona.

    Respeta el filtro de la capa (subset string) porque usa getFeatures(): si la
    capa esta filtrada en el panel de capas, los numeros son los del filtro.
    Los problemas con Etapa finalizada se descartan y se cuentan aparte en
    ConteoCapa.descartados. Pide solo los atributos que usa y ninguna
    geometria, que es lo que hace viable recalcular en cada edicion.
    """
    from qgis.core import QgsFeatureRequest

    idx_tipo = capa.fields().lookupField(campo_tipo)
    if idx_tipo < 0:
        raise ValueError(
            f"La capa '{capa.name()}' no tiene el campo '{campo_tipo}'.\n"
            f"Campos: {', '.join(capa.fields().names())}"
        )
    idx_dz = capa.fields().lookupField(campo_dentro_zona)
    idx_etapa = capa.fields().lookupField(campo_etapa)

    atributos = [idx_tipo] + [i for i in (idx_dz, idx_etapa) if i >= 0]
    solicitud = (
        QgsFeatureRequest()
        .setSubsetOfAttributes(atributos)
        .setFlags(QgsFeatureRequest.NoGeometry)
    )

    conteo = ConteoCapa()
    for feature in capa.getFeatures(solicitud):
        # Los finalizados no entran en ninguna columna: el panel muestra lo que
        # queda por hacer. Si la capa no tiene el campo Etapa no se descarta nada.
        if idx_etapa >= 0 and es_descartable(feature[idx_etapa]):
            conteo.descartados += 1
            continue
        dentro = interpretar_dentro_zona(feature[idx_dz]) if idx_dz >= 0 else None
        conteo.agregar(feature[idx_tipo], dentro)
    return conteo
