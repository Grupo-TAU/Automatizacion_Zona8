"""
Extracción de datos de OS desde el PDF del Sistema Único de Respuesta (IM).
Requiere pdfplumber (no viene con QGIS/OSGeo4W por defecto).
"""

import re


def pdfplumber_disponible():
    try:
        import pdfplumber  # noqa: F401
        return True
    except ImportError:
        return False


def instalar_pdfplumber():
    """
    Instala pdfplumber en el intérprete de QGIS. Pensado para llamarse
    desde un botón de la UI, con confirmación previa del usuario
    (no se ejecuta automáticamente).
    Devuelve (ok, salida) con el resultado del proceso de pip.
    """
    import subprocess
    import sys
    import os

    target = os.path.join(sys.prefix, "Lib", "site-packages")
    resultado = subprocess.run(
        [
            os.path.join(sys.prefix, "python.exe"),
            "-m", "pip", "install", "pdfplumber", "--target", target,
        ],
        capture_output=True, text=True,
    )
    salida = (resultado.stdout or "") + (resultado.stderr or "")
    return resultado.returncode == 0, salida


def _abrir_pdfplumber():
    try:
        import pdfplumber
        return pdfplumber
    except ImportError:
        raise ImportError(
            "Falta la librería pdfplumber.\n\n"
            "Instalala ejecutando en la consola de QGIS:\n"
            "  import subprocess, sys, os\n"
            "  target = os.path.join(sys.prefix, 'Lib', 'site-packages')\n"
            "  subprocess.call([os.path.join(sys.prefix, 'python.exe'),\n"
            "      '-m', 'pip', 'install', 'pdfplumber', '--target', target])"
        )


# ── Sub-extracciones compartidas entre el PDF individual y el de itinerario ──
# (misma forma de campo en ambos formatos de OS del Sistema Único de Respuesta)

def _extraer_ubicacion_y_padron(texto):
    """Devuelve (ubicacion, padron); cualquiera puede ser None si no matchea."""
    m = re.search(r'Ubicaci[oó]n:\s*(.+?)\s*Observaci[oó]n:', texto, re.DOTALL)
    if not m:
        return None, None
    ubic = re.sub(r'\s+', ' ', m.group(1)).strip()
    ubic = re.sub(r'N[°º]:\s*', 'Nº ', ubic)

    padron = None
    m_pad = re.search(r'\[Padron:\s*(\d+)\]', ubic, re.IGNORECASE)
    if m_pad:
        padron = m_pad.group(1)

    return ubic, padron


def _extraer_n_problema(texto):
    m = re.search(r'Problema\s*N[°º]:\s*(.+?)\s*Fecha problema:', texto, re.DOTALL)
    return re.sub(r'\s+', ' ', m.group(1)).strip() if m else None


def _extraer_tipo(texto):
    m = re.search(r'Tipo:\s*(.+?)\s*Grupo:', texto, re.DOTALL)
    return re.sub(r'\s+', ' ', m.group(1)).strip() if m else None


def _extraer_fecha_ingreso(texto):
    """
    Timestamp de cabecera (pdfplumber lo extrae ANTES del título "Orden de
    Servicio" en el texto crudo, aunque visualmente esté arriba a la
    derecha). En el PDF individual es la fecha de esa OS puntual; en el de
    itinerario es la fecha en que el itinerario completo (con todas sus OS)
    llega a Grupo TAU — por eso ahí sale igual para todas las OS del lote.
    """
    m = re.search(r'(\d{2}/\d{2}/\d{4})\s+\d{2}:\d{2}.*?Orden de Servicio', texto, re.DOTALL)
    return m.group(1) if m else None


def parsear_pdf_os(ruta_pdf):
    """
    Extrae los datos de la OS desde el PDF individual (el que llega por correo
    del SOMS) del Sistema Único de Respuesta (IM).
    Devuelve dict con los campos del formulario.
    Lanza ImportError con instrucciones si falta pdfplumber.
    """
    pdfplumber = _abrir_pdfplumber()

    datos = {}
    with pdfplumber.open(ruta_pdf) as pdf:
        texto = "\n".join(p.extract_text() or "" for p in pdf.pages)

    # ── N°_OS ──────────────────────────────────────────────────────────
    m = re.search(r'Orden de Servicio\s+(\d+)', texto)
    if m:
        datos['orden_servicio'] = m.group(1)

    fecha_ingreso = _extraer_fecha_ingreso(texto)
    if fecha_ingreso:
        datos['fecha_ingreso'] = fecha_ingreso

    # ── Descripción — entre "Observación:" y "Problema Nº:" ───────────
    m = re.search(r'Observaci[oó]n:\s*(.+?)\s*Problema\s*N[°º]:', texto, re.DOTALL)
    if m:
        desc = re.sub(r'\s+', ' ', m.group(1)).strip()
        if desc:
            datos['descripcion'] = desc

    ubic, padron = _extraer_ubicacion_y_padron(texto)
    if ubic:
        datos['ubicacion'] = ubic
    if padron:
        datos['padron'] = padron

    n_problema = _extraer_n_problema(texto)
    if n_problema:
        datos['n_problema'] = n_problema

    # ── Sector — entre "Sector:" y "Generada" ──────────────────────────
    m = re.search(r'Sector:\s*(.+?)\s*Generada', texto, re.DOTALL)
    if m:
        datos['sector'] = re.sub(r'\s+', ' ', m.group(1)).strip()

    tipo = _extraer_tipo(texto)
    if tipo:
        datos['tipo'] = tipo

    return datos


def parsear_pdf_itinerario(ruta_pdf):
    """
    Extrae los datos de TODAS las OS de un PDF de itinerario (varias OS, cada
    una ocupando un número variable de páginas, todas empezando con
    "Orden de Servicio <N°>"). Devuelve una lista de dicts, uno por OS, en el
    mismo formato de claves que parsear_pdf_os (reutilizable por el mismo
    código de la UI).
    Lanza ImportError con instrucciones si falta pdfplumber.
    """
    pdfplumber = _abrir_pdfplumber()

    # ── Agrupar páginas por número de OS (una OS puede ocupar 2+ páginas) ──
    grupos = []  # [(numero_os, texto_concatenado), ...]
    numero_actual = None
    paginas_actual = []
    with pdfplumber.open(ruta_pdf) as pdf:
        for pagina in pdf.pages:
            texto_pagina = pagina.extract_text() or ""
            m = re.search(r'Orden de Servicio\s+(\d+)', texto_pagina)
            numero_pagina = m.group(1) if m else None

            if numero_pagina and numero_pagina != numero_actual:
                if numero_actual is not None:
                    grupos.append((numero_actual, "\n".join(paginas_actual)))
                numero_actual = numero_pagina
                paginas_actual = [texto_pagina]
            else:
                paginas_actual.append(texto_pagina)

        if numero_actual is not None:
            grupos.append((numero_actual, "\n".join(paginas_actual)))

    resultados = []
    for numero_os, texto in grupos:
        datos = {'orden_servicio': numero_os}

        # Fecha_Ingreso da el mismo timestamp de cabecera para todas las OS
        # de este itinerario a propósito (llegan todas juntas a Grupo TAU).
        fecha_ingreso = _extraer_fecha_ingreso(texto)
        if fecha_ingreso:
            datos['fecha_ingreso'] = fecha_ingreso

        # ── Descripción — casi siempre vacía en este formato; se deja
        # vacía si no hay contenido real entre los marcadores ────────────
        m = re.search(r'Observaci[oó]n:\s*(.+?)\s*Problema\s*N[°º]:', texto, re.DOTALL)
        if m:
            desc = re.sub(r'\s+', ' ', m.group(1)).strip()
            if desc:
                datos['descripcion'] = desc

        ubic, padron = _extraer_ubicacion_y_padron(texto)
        if ubic:
            datos['ubicacion'] = ubic
        if padron:
            datos['padron'] = padron

        n_problema = _extraer_n_problema(texto)
        if n_problema:
            datos['n_problema'] = n_problema

        # ── Sector (= Contrato en nuestro esquema) — entre "Sector:" y
        # "Generada" ──────────────────────────────────────────────────
        m = re.search(r'Sector:\s*(.+?)\s*Generada', texto, re.DOTALL)
        if m:
            datos['sector'] = re.sub(r'\s+', ' ', m.group(1)).strip()

        tipo = _extraer_tipo(texto)
        if tipo:
            datos['tipo'] = tipo

        resultados.append(datos)

    return resultados
