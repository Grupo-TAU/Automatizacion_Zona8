"""
Envia por mail el conteo de Problemas por categoria (la misma tabla del panel
"Conteo de Problemas" del plugin registrar_problema_sur), leyendo directo del
GeoPackage.

No necesita QGIS: el GeoPackage es SQLite, asi que se lee con sqlite3 y se
reutiliza conteo.contar_filas, el mismo criterio que usa el panel, para que los
numeros del mail y los del panel coincidan.

Pensado para correr desatendido desde el Programador de tareas de Windows. La
configuracion del mail vive en envio_conteo.ini, que no se sube al repo; hay un
envio_conteo.ini.example para copiar (es la misma seccion [smtp] que usa
enviar_conteo_semanal.py de Automatizaciones_Orden_Servicio).

    python enviar_conteo_semanal.py            # cuenta y envia
    python enviar_conteo_semanal.py --probar   # cuenta e imprime, sin enviar
"""

from __future__ import annotations

import argparse
import configparser
import importlib
import logging
import smtplib
import sqlite3
import sys
import types
from datetime import datetime
from email.message import EmailMessage
from html import escape
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RUTA_INI = AQUI / "envio_conteo.ini"
RUTA_LOG = AQUI / "envio_conteo.log"

RUTA_GPKG = r"C:\Proyectos-QGisCloud\QField\cloud\Zona8\Datos\problemas_sur.gpkg"
# Es la tabla que la capa "problemas_sur" del proyecto QGIS tiene cargada.
TABLA_PROBLEMAS = "inspecciones_os"


def _cargar_conteo():
    """conteo.py hace `from .intercambio_im import tabla`: dentro del plugin
    instalado intercambio_im esta vendorizado, pero en el repo vive en la raiz.
    Se arma un paquete falso para que el import relativo lo encuentre sin
    ejecutar el __init__ del plugin (que en QGIS trae qgis, y afuera no esta)."""
    sys.path.insert(0, str(AQUI))
    import intercambio_im
    import intercambio_im.tabla

    paquete = types.ModuleType("registrar_problema_sur")
    paquete.__path__ = [str(AQUI / "registrar_problema_sur")]
    sys.modules["registrar_problema_sur"] = paquete
    sys.modules["registrar_problema_sur.intercambio_im"] = intercambio_im
    sys.modules["registrar_problema_sur.intercambio_im.tabla"] = intercambio_im.tabla
    return importlib.import_module("registrar_problema_sur.conteo")


def leer_filas_gpkg(ruta: str = RUTA_GPKG, tabla: str = TABLA_PROBLEMAS) -> list[tuple]:
    """Tuplas (tipo, dentro_zona, etapa, n_problema), en el orden que espera
    conteo.contar_filas. Solo lectura: si QGIS o QField Sync tienen el archivo
    abierto, esto no lo bloquea ni lo modifica."""
    if not Path(ruta).is_file():
        raise FileNotFoundError(f"No existe el GeoPackage: {ruta}")
    con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True, timeout=30)
    try:
        cursor = con.execute(f'SELECT "Tipo", "Dentro_Zona", "Etapa", "N_Problema" FROM "{tabla}"')
        return list(cursor)
    finally:
        con.close()


def formatear_reporte(cnt, resultado, titulo: str) -> str:
    """La tabla del panel (sin la columna Comparacion, que vive en el proyecto
    QGIS y no en el GeoPackage), alineada con espacios: se envia dentro de un <pre>."""
    categorias = cnt.categorias_visibles(resultado.total)
    columnas = ("Fuera de Zona", "Dentro de Zona", "Total")
    contadores = (resultado.fuera, resultado.dentro, resultado.total)
    filas = [(c, tuple(contador[c] for contador in contadores)) for c in categorias]
    filas.append(("TOTAL", tuple(sum(contador[c] for c in categorias) for contador in contadores)))

    ancho_nombre = max(len(nombre) for nombre, _ in filas)
    anchos = [max(len(c), 6) for c in columnas]
    lineas = [
        titulo,
        "=" * len(titulo),
        "",
        " " * ancho_nombre + "  " + "  ".join(c.rjust(a) for c, a in zip(columnas, anchos)),
    ]
    for nombre, valores in filas:
        if nombre == "TOTAL":
            lineas.append("-" * (ancho_nombre + 2 + sum(anchos) + 2 * (len(anchos) - 1)))
        lineas.append(
            nombre.ljust(ancho_nombre) + "  " + "  ".join(str(v).rjust(a) for v, a in zip(valores, anchos))
        )

    notas = []
    if resultado.descartados:
        notas.append(f"Se descartaron {resultado.descartados} finalizados/no corresponde.")
    if resultado.excluidos:
        notas.append(f"Se excluyeron {resultado.excluidos} de Limpieza/Tapas (no se cuentan).")
    if resultado.n_sin_clasificar:
        notas.append(
            f"{resultado.n_sin_clasificar} sin '{cnt.CAMPO_DENTRO_ZONA}': no suman ni a Fuera ni a Dentro."
        )
    if notas:
        lineas += [""] + notas
    return "\n".join(lineas)


def armar_reporte() -> str:
    cnt = _cargar_conteo()
    resultado = cnt.contar_filas(leer_filas_gpkg())
    fecha = datetime.now().strftime("%d/%m/%Y")
    return formatear_reporte(cnt, resultado, f"Conteo de Problemas - {fecha}")


def _config() -> configparser.SectionProxy:
    if not RUTA_INI.is_file():
        raise FileNotFoundError(
            f"Falta {RUTA_INI.name}. Copiar {RUTA_INI.name}.example y completarlo."
        )
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(RUTA_INI, encoding="utf-8")
    return parser["smtp"]


def enviar(reporte: str) -> None:
    cfg = _config()
    destinatarios = [d.strip() for d in cfg["destinatarios"].split(",") if d.strip()]

    mensaje = EmailMessage()
    mensaje["Subject"] = f"Conteo de Problemas Zona 8 Sur - {datetime.now():%d/%m/%Y}"
    mensaje["From"] = cfg["remitente"]
    mensaje["To"] = ", ".join(destinatarios)
    mensaje.set_content(reporte)
    # La tabla esta alineada con espacios: en HTML sin <pre> se desarma.
    mensaje.add_alternative(
        f'<pre style="font-family: Consolas, monospace; font-size: 13px">{escape(reporte)}</pre>',
        subtype="html",
    )

    servidor, puerto = cfg["servidor"], cfg.getint("puerto", 587)
    if puerto == 465:
        smtp = smtplib.SMTP_SSL(servidor, puerto, timeout=60)
    else:
        smtp = smtplib.SMTP(servidor, puerto, timeout=60)
        smtp.starttls()
    with smtp:
        smtp.login(cfg["usuario"], cfg["clave"])
        smtp.send_message(mensaje)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--probar", action="store_true", help="imprime el reporte sin enviar el mail")
    args = parser.parse_args()

    logging.basicConfig(
        filename=RUTA_LOG, level=logging.INFO, encoding="utf-8",
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        reporte = armar_reporte()
        if args.probar:
            print(reporte)
            return 0
        enviar(reporte)
        logging.info("Enviado")
        return 0
    except Exception:
        # Desde el Programador de tareas nadie mira la consola: el log es la unica pista.
        logging.exception("Fallo el envio")
        raise


if __name__ == "__main__":
    sys.exit(main())
