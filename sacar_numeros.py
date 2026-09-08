"""
Conteo de Problemas por categoria de "Tipo".

Son dos chequeos que TIENEN que dar los mismos numeros:
    1. Sobre la planilla que se baja del sistema (CSV o XLSX; del XLSX se usa
       la primera hoja salvo que se pida otra).
    2. Sobre la capa cargada en QGIS.

Los dos usan la misma funcion clasificar(), asi que si los numeros no coinciden
la diferencia esta en los datos, nunca en el criterio de conteo.

Uso desde la terminal (solo el chequeo 1, el de la planilla):
    python sacar_numeros.py "problemas para nahuel.xlsx"
    python sacar_numeros.py "problemas para nahuel.xlsx" --detalle
    python sacar_numeros.py problemas.csv

Uso desde la consola de Python de QGIS (los dos chequeos y su comparacion):
    exec(open(r"C:\\ruta\\al\\repo\\sacar_numeros.py", encoding="utf-8").read())
    comparar(r"C:\\ruta\\a\\problemas.csv")
    comparar(r"C:\\ruta\\a\\problemas.csv", detalle=True)   # abre los Tipo que difieren

Sin dependencias: csv, zipfile y ElementTree son de la biblioteca estandar
(el xlsx se lee a mano, sin openpyxl), asi que corre igual en el Python de QGIS
que en uno suelto.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import NamedTuple

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACION
# ─────────────────────────────────────────────────────────────────────────────
CAMPO_TIPO = "Tipo"
CAPA_QGIS = "problemas_sur"

# Categorias en ORDEN DE PRIORIDAD: gana la primera que coincide y el problema
# se cuenta una sola vez, asi los subtotales suman exactamente el total. El
# orden importa porque hay Tipos que podrian matchear dos categorias.
#
# Los patrones se buscan como subcadena dentro del Tipo normalizado (minusculas,
# sin acentos y con los espacios colapsados), asi que sirven tanto los Tipos
# completos como un prefijo ("obstru" tomaria obstruido / obstruida / obstruidas).
CATEGORIAS: list[tuple[str, tuple[str, ...]]] = [
    ("Varios / Acera y pavimento hundido", (
        "Varios",
        "Acera y/o Pavimento Hundido",
    )),
    ("Tapas", (
        "Boca de Tormenta sin Tapa",
        "Registro sin Tapa",
    )),
    (" / sucio", (
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
    ("Roto / nuevo / reposicion", (
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
# fila. Un CSV y una capa que difieren suelen diferir justo aca.
CATEGORIA_SIN_DATO = "(sin dato)"


# ─────────────────────────────────────────────────────────────────────────────
# CLASIFICACION (la unica fuente de verdad, compartida por los dos chequeos)
# ─────────────────────────────────────────────────────────────────────────────
def normalizar(texto) -> str:
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


def clasificar(tipo) -> str:
    t = normalizar(tipo)
    if not t:
        return CATEGORIA_SIN_DATO
    for nombre, patrones in _CATEGORIAS_NORM:
        if any(p in t for p in patrones):
            return nombre
    return CATEGORIA_RESTO


class Resultado(NamedTuple):
    conteo: Counter          # categoria -> cantidad
    detalle: defaultdict     # categoria -> Counter de los Tipo originales

    @property
    def total(self) -> int:
        return sum(self.conteo.values())


def contar(valores) -> Resultado:
    conteo: Counter = Counter()
    detalle: defaultdict = defaultdict(Counter)
    for valor in valores:
        categoria = clasificar(valor)
        conteo[categoria] += 1
        detalle[categoria][str(valor).strip() or "(vacio)"] += 1
    return Resultado(conteo, detalle)


# ─────────────────────────────────────────────────────────────────────────────
# CHEQUEO 1: la planilla (CSV o XLSX)
# ─────────────────────────────────────────────────────────────────────────────
def _leer_texto(ruta: Path) -> str:
    """Los CSV del sistema vienen a veces en UTF-8 y a veces en cp1252; leerlos
    con el encoding equivocado rompe los acentos y descoloca la clasificacion."""
    crudo = Path(ruta).read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return crudo.decode(encoding)
        except UnicodeDecodeError:
            continue
    return crudo.decode("latin-1", errors="replace")


def _detectar_delimitador(muestra: str) -> str:
    try:
        return csv.Sniffer().sniff(muestra, delimiters=",;\t|").delimiter
    except csv.Error:
        # El Sniffer falla con encabezados raros o de una sola columna.
        return max(",;\t|", key=muestra.count)


# El xlsx se lee con zipfile + ElementTree en vez de openpyxl: la consola de
# QGIS no trae openpyxl instalado y el chequeo 2 se corre justamente ahi.
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_NS_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _indice_de_columna(ref: str) -> int:
    """'B7' -> 1. Las celdas vacias no se escriben en el XML, asi que la
    posicion hay que sacarla de la referencia y no del orden de aparicion."""
    n = 0
    for caracter in ref:
        if caracter.isalpha():
            n = n * 26 + (ord(caracter.upper()) - 64)
    return n - 1


def _texto_de(elemento) -> str:
    return "".join(nodo.text or "" for nodo in elemento.iter(f"{_NS}t"))


def leer_filas_xlsx(ruta, hoja=None) -> list[list[str]]:
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
        celdas: dict[int, str] = {}
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


def _columna_de_xlsx(ruta, campo: str, hoja=None) -> list[str]:
    filas = leer_filas_xlsx(ruta, hoja)
    objetivo = normalizar(campo)

    # El encabezado no siempre es la primera fila (puede haber un titulo arriba),
    # asi que se busca la primera fila que contenga la columna pedida.
    for n, fila in enumerate(filas):
        indice = next((i for i, celda in enumerate(fila) if normalizar(celda) == objetivo), None)
        if indice is not None:
            return [
                (f[indice] if indice < len(f) else "")
                for f in filas[n + 1:]
                if any(celda.strip() for celda in f)   # saltear las filas vacias del final
            ]

    primera = ", ".join(c for c in (filas[0] if filas else []) if c)
    raise ValueError(f"La hoja no tiene la columna {campo!r}. Primera fila: {primera or '(vacia)'}")


def _columna_de_csv(ruta, campo: str) -> list[str]:
    texto = _leer_texto(ruta)
    lector = csv.DictReader(io.StringIO(texto), delimiter=_detectar_delimitador(texto[:4096]))
    encabezados = lector.fieldnames or []

    objetivo = normalizar(campo)
    columna = next((h for h in encabezados if normalizar(h) == objetivo), None)
    if columna is None:
        raise ValueError(
            f"El CSV no tiene la columna '{campo}'.\n"
            f"Columnas encontradas: {', '.join(encabezados) or '(ninguna)'}"
        )
    return [fila.get(columna) or "" for fila in lector]


def leer_columna(ruta, campo: str = CAMPO_TIPO, hoja=None) -> list[str]:
    if Path(ruta).suffix.lower() in (".xlsx", ".xlsm"):
        return _columna_de_xlsx(ruta, campo, hoja)
    return _columna_de_csv(ruta, campo)


def contar_planilla(ruta, campo: str = CAMPO_TIPO, hoja=None) -> Resultado:
    return contar(leer_columna(ruta, campo, hoja))


contar_csv = contar_planilla   # nombre viejo, por si quedo alguna llamada dando vueltas


# ─────────────────────────────────────────────────────────────────────────────
# CHEQUEO 2: la capa de QGIS
# ─────────────────────────────────────────────────────────────────────────────
def contar_capa_qgis(
    nombre_capa: str = CAPA_QGIS,
    campo: str = CAMPO_TIPO,
    expresion: str | None = None,
) -> Resultado:
    """
    Cuenta los features de la capa cargada en el proyecto. Respeta el filtro de
    la capa (subset string) porque usa getFeatures(); si la capa esta filtrada
    en el panel de capas, los numeros van a ser los del filtro.

    expresion: filtro adicional opcional, en sintaxis de QGIS
    (ej: "Etapa = 'Pendiente'").
    """
    from qgis.core import QgsProject, QgsFeatureRequest
    from PyQt5.QtCore import QVariant

    capas = QgsProject.instance().mapLayersByName(nombre_capa)
    if not capas:
        # mapLayersByName distingue mayusculas y los nombres del proyecto no
        # siempre respetan el mismo casing.
        objetivo = nombre_capa.casefold()
        capas = [
            c for c in QgsProject.instance().mapLayers().values()
            if c.name().casefold() == objetivo
        ]
    if not capas:
        disponibles = ", ".join(sorted(c.name() for c in QgsProject.instance().mapLayers().values()))
        raise ValueError(f"No se encontro la capa '{nombre_capa}'.\nCapas del proyecto: {disponibles}")

    capa = capas[0]
    idx = capa.fields().lookupField(campo)
    if idx < 0:
        raise ValueError(
            f"La capa '{capa.name()}' no tiene el campo '{campo}'.\n"
            f"Campos: {', '.join(capa.fields().names())}"
        )

    solicitud = QgsFeatureRequest().setSubsetOfAttributes([idx]).setFlags(QgsFeatureRequest.NoGeometry)
    if expresion:
        solicitud.setFilterExpression(expresion)

    valores = []
    for feature in capa.getFeatures(solicitud):
        valor = feature[idx]
        if valor is None or (isinstance(valor, QVariant) and valor.isNull()):
            valor = ""
        valores.append(valor)
    return contar(valores)


# ─────────────────────────────────────────────────────────────────────────────
# SALIDA
# ─────────────────────────────────────────────────────────────────────────────
def _categorias_a_mostrar(*resultados: Resultado) -> list[str]:
    categorias = [nombre for nombre, _ in CATEGORIAS] + [CATEGORIA_RESTO]
    if any(r.conteo.get(CATEGORIA_SIN_DATO) for r in resultados):
        categorias.append(CATEGORIA_SIN_DATO)
    return categorias


def imprimir_conteo(resultado: Resultado, titulo: str = "Conteo") -> None:
    categorias = _categorias_a_mostrar(resultado)
    ancho = max(len(c) for c in categorias + ["TOTAL"])
    print(f"\n{titulo}")
    print("-" * (ancho + 9))
    for categoria in categorias:
        print(f"{categoria:<{ancho}}  {resultado.conteo.get(categoria, 0):>6}")
    print("-" * (ancho + 9))
    print(f"{'TOTAL':<{ancho}}  {resultado.total:>6}")


def imprimir_detalle(resultado: Resultado, titulo: str = "Detalle por Tipo") -> None:
    """Abre cada categoria en los Tipo que la componen. Sirve para auditar que
    'Otros' no se este comiendo algo que deberia estar clasificado, y para ver
    si algun Tipo matcheo una categoria por accidente."""
    print(f"\n{titulo}")
    for categoria in _categorias_a_mostrar(resultado):
        print(f"\n  {categoria} ({resultado.conteo.get(categoria, 0)})")
        for tipo, n in resultado.detalle.get(categoria, Counter()).most_common():
            print(f"    {n:>5}  {tipo}")


def comparar(
    ruta_planilla,
    nombre_capa: str = CAPA_QGIS,
    campo: str = CAMPO_TIPO,
    expresion: str | None = None,
    hoja=None,
    detalle: bool = False,
) -> tuple[Resultado, Resultado]:
    """Corre los dos chequeos y los muestra lado a lado. Para usar desde la
    consola de Python de QGIS, que es donde estan las dos fuentes disponibles."""
    resultado_csv = contar_planilla(ruta_planilla, campo, hoja)
    resultado_qgis = contar_capa_qgis(nombre_capa, campo, expresion)

    categorias = _categorias_a_mostrar(resultado_csv, resultado_qgis)
    ancho = max(len(c) for c in categorias + ["TOTAL"])

    print(f"\nPlanilla: {ruta_planilla}")
    print(f"QGIS: capa '{nombre_capa}'" + (f" filtrada por {expresion!r}" if expresion else ""))
    print(f"\n{'':<{ancho}}  {'CSV':>6}  {'QGIS':>6}  {'DIF':>6}")
    print("-" * (ancho + 24))

    hay_diferencias = False
    for categoria in categorias + ["TOTAL"]:
        if categoria == "TOTAL":
            n_csv, n_qgis = resultado_csv.total, resultado_qgis.total
            print("-" * (ancho + 24))
        else:
            n_csv = resultado_csv.conteo.get(categoria, 0)
            n_qgis = resultado_qgis.conteo.get(categoria, 0)
        diferencia = n_qgis - n_csv
        hay_diferencias = hay_diferencias or diferencia != 0
        marca = "" if diferencia == 0 else "  <<<"
        print(f"{categoria:<{ancho}}  {n_csv:>6}  {n_qgis:>6}  {diferencia:>+6}{marca}")

    if hay_diferencias:
        print("\nLos dos chequeos NO coinciden. Tipos que difieren:")
        _imprimir_diferencias(resultado_csv, resultado_qgis)
    else:
        print("\nOK: los dos chequeos dan los mismos numeros.")

    if detalle:
        imprimir_detalle(resultado_csv, "Detalle de la planilla")
        imprimir_detalle(resultado_qgis, "Detalle de QGIS")

    return resultado_csv, resultado_qgis


def _imprimir_diferencias(resultado_csv: Resultado, resultado_qgis: Resultado) -> None:
    """Baja la diferencia hasta el Tipo concreto: con el subtotal solo no se
    puede saber que problema falta de que lado."""
    por_tipo_csv: Counter = Counter()
    por_tipo_qgis: Counter = Counter()
    for contador in resultado_csv.detalle.values():
        por_tipo_csv.update(contador)
    for contador in resultado_qgis.detalle.values():
        por_tipo_qgis.update(contador)

    tipos = sorted(set(por_tipo_csv) | set(por_tipo_qgis))
    ancho = max((len(t) for t in tipos), default=4)
    print(f"\n  {'Tipo':<{ancho}}  {'CSV':>6}  {'QGIS':>6}  {'DIF':>6}")
    for tipo in tipos:
        n_csv, n_qgis = por_tipo_csv[tipo], por_tipo_qgis[tipo]
        if n_csv != n_qgis:
            print(f"  {tipo:<{ancho}}  {n_csv:>6}  {n_qgis:>6}  {n_qgis - n_csv:>+6}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Cuenta los Problemas por categoria de Tipo (chequeo de la planilla).",
        epilog=(
            "El chequeo contra la capa de QGIS se corre desde la consola de Python "
            "de QGIS:\n"
            '    exec(open(r"...\\sacar_numeros.py", encoding="utf-8").read())\n'
            '    comparar(r"...\\problemas.csv")'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("planilla", type=Path, help="CSV o XLSX bajado del sistema")
    ap.add_argument("--hoja", default=None, help="hoja del XLSX (por defecto: la primera)")
    ap.add_argument("--campo", default=CAMPO_TIPO, help=f"columna a clasificar (por defecto: {CAMPO_TIPO})")
    ap.add_argument("--detalle", action="store_true", help="abrir cada categoria en los Tipo que la componen")
    args = ap.parse_args(argv)

    try:
        resultado = contar_planilla(args.planilla, args.campo, args.hoja)
    except (OSError, ValueError) as exc:
        print(f"\n[ERROR] {exc}")
        return 1

    imprimir_conteo(resultado, f"Chequeo 1 - planilla: {args.planilla.name}")
    if args.detalle:
        imprimir_detalle(resultado)
    return 0


def _en_consola_de_qgis() -> bool:
    """La consola de Python de QGIS ejecuta el archivo con __name__ == '__main__',
    asi que sin este chequeo un exec() del script dispararia el CLI (y su
    SystemExit) en vez de dejar las funciones cargadas."""
    return "qgis.core" in sys.modules


if _en_consola_de_qgis():
    print("sacar_numeros cargado. Ahora corre:  comparar(r'C:/ruta/a/planilla.xlsx')")
elif __name__ == "__main__":
    raise SystemExit(main())
