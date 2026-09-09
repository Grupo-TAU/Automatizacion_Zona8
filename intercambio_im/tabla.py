"""
Lectura generica de planillas CSV o XLSX, por nombre de columna.

Es la parte que no tiene nada que ver con el intercambio con la IM: sirve para
cualquier planilla que baje del sistema. La usan el conteo de Problemas y, por
abajo, el intercambio.

El encabezado no se asume en la primera fila y los nombres de columna se buscan
sin distinguir acentos ni mayusculas, porque las exportaciones del sistema
cambian de formato mas seguido que el codigo.
"""

from __future__ import annotations

import csv
import io
import unicodedata
from pathlib import Path

from .nucleo import ErrorIntercambio, exigir_openpyxl

EXTENSIONES_EXCEL = (".xlsx", ".xlsm")


def plegar(texto) -> str:
    """
    Minusculas, sin acentos y con los espacios colapsados.

    Es la normalizacion para comparar ETIQUETAS (nombres de columna, nombres de
    hoja, categorias de Tipo): el mismo valor escrito 'Boca de tormenta OBSTRUIDA'
    o 'boca de tormenta obstruida' tiene que dar lo mismo. Es mas agresiva que
    nucleo.normalizar(), que compara DATOS del intercambio y no puede borrar
    acentos ni espacios internos sin cambiar el valor que se escribe en la capa.
    """
    if texto is None:
        return ""
    descompuesto = unicodedata.normalize("NFKD", str(texto))
    sin_acentos = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return " ".join(sin_acentos.casefold().split())


def _texto(valor) -> str:
    """Celda a string. openpyxl devuelve tipos; aca todo se compara como texto."""
    if valor is None:
        return ""
    if isinstance(valor, str):
        return valor
    # Una celda numerica entera llega como 123.0 y '123.0' no matchea ningun
    # encabezado ni ninguna categoria.
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor)


# ── CSV ───────────────────────────────────────────────────────────────────────
def _leer_texto(ruta) -> str:
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


def _filas_de_csv(ruta, campo, opcionales):
    texto = _leer_texto(ruta)
    lector = csv.DictReader(io.StringIO(texto), delimiter=_detectar_delimitador(texto[:4096]))
    encabezados = lector.fieldnames or []

    def buscar(nombre):
        objetivo = plegar(nombre)
        return next((h for h in encabezados if plegar(h) == objetivo), None)

    columna = buscar(campo)
    if columna is None:
        raise ErrorIntercambio(
            f"El archivo no tiene la columna {campo!r}.\n"
            f"Columnas encontradas: {', '.join(encabezados) or '(ninguna)'}"
        )
    presentes = {campo: columna}
    for nombre in opcionales:
        real = buscar(nombre)
        if real is not None:
            presentes[nombre] = real

    return [{k: (fila.get(real) or "") for k, real in presentes.items()} for fila in lector]


# ── XLSX ──────────────────────────────────────────────────────────────────────
def _elegir_hoja(libro, hoja):
    """hoja puede ser nombre, indice o None (la primera, de donde salen los calculos)."""
    if hoja is None:
        return libro.worksheets[0]
    if isinstance(hoja, int):
        try:
            return libro.worksheets[hoja]
        except IndexError:
            raise ErrorIntercambio(
                f"El archivo no tiene una hoja en la posicion {hoja}. "
                f"Hojas: {', '.join(libro.sheetnames)}"
            ) from None
    objetivo = plegar(hoja)
    for nombre in libro.sheetnames:
        if plegar(nombre) == objetivo:
            return libro[nombre]
    raise ErrorIntercambio(
        f"El archivo no tiene la hoja {hoja!r}. Hojas: {', '.join(libro.sheetnames)}"
    )


def leer_filas_xlsx(ruta, hoja=None) -> list:
    """Las filas de una hoja como listas de strings."""
    exigir_openpyxl()
    from openpyxl import load_workbook

    libro = load_workbook(str(ruta), data_only=True, read_only=True)
    try:
        return [
            [_texto(celda) for celda in fila]
            for fila in _elegir_hoja(libro, hoja).iter_rows(values_only=True)
        ]
    finally:
        libro.close()


def _filas_de_xlsx(ruta, campo, opcionales, hoja=None):
    filas = leer_filas_xlsx(ruta, hoja)
    objetivo = plegar(campo)

    # El encabezado no siempre es la primera fila (puede haber un titulo arriba),
    # asi que se busca la primera fila que contenga la columna obligatoria.
    for numero, fila in enumerate(filas):
        indice = next((i for i, celda in enumerate(fila) if plegar(celda) == objetivo), None)
        if indice is None:
            continue
        # Las opcionales se buscan en esa misma fila de encabezado; las que no
        # esten quedan afuera del dict y el que llama decide que hacer.
        indices = {campo: indice}
        for nombre in opcionales:
            objetivo_opcional = plegar(nombre)
            i = next(
                (j for j, celda in enumerate(fila) if plegar(celda) == objetivo_opcional), None
            )
            if i is not None:
                indices[nombre] = i
        return [
            {k: (f[i] if i < len(f) else "") for k, i in indices.items()}
            for f in filas[numero + 1:]
            if any(celda.strip() for celda in f)   # saltear las filas vacias del final
        ]

    primera = ", ".join(c for c in (filas[0] if filas else []) if c)
    raise ErrorIntercambio(
        f"La hoja no tiene la columna {campo!r}. Primera fila: {primera or '(vacia)'}"
    )


# ── API ───────────────────────────────────────────────────────────────────────
def leer_filas(ruta, campo, opcionales=(), hoja=None) -> list:
    """
    Lista de dicts con la columna obligatoria `campo` y las de `opcionales` que
    existan. Las opcionales que falten no aparecen en los dicts: la planilla del
    sistema no siempre trae las mismas columnas.
    """
    if Path(ruta).suffix.lower() in EXTENSIONES_EXCEL:
        return _filas_de_xlsx(ruta, campo, opcionales, hoja)
    return _filas_de_csv(ruta, campo, opcionales)


def leer_columna(ruta, campo, hoja=None) -> list:
    """Los valores de una sola columna, en orden."""
    return [fila[campo] for fila in leer_filas(ruta, campo, (), hoja)]
