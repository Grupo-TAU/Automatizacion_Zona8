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
    python enviar_conteo_semanal.py --probar   # cuenta, imprime y guarda una vista previa HTML; no envia
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
RUTA_PREVIEW = AQUI / "envio_conteo_preview.html"

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


def _datos_tabla(cnt, resultado):
    """(columnas, filas, notas): la tabla del panel (sin la columna Comparacion,
    que vive en el proyecto QGIS y no en el GeoPackage) y los avisos al pie.
    La version texto y la HTML salen de aca, asi no pueden divergir."""
    categorias = cnt.categorias_visibles(resultado.total)
    columnas = ("Fuera de Zona", "Dentro de Zona", "Total Obras", "Total Sur")
    contadores = (resultado.fuera, resultado.dentro, resultado.total, resultado.total_sur)
    filas = [(c, tuple(contador[c] for contador in contadores)) for c in categorias]
    filas.append(("TOTAL", tuple(sum(contador[c] for c in categorias) for contador in contadores)))

    notas = []
    if resultado.descartados:
        notas.append(f"Se descartaron {resultado.descartados} finalizados/no corresponde.")
    if resultado.excluidos:
        notas.append(
            f"Se excluyeron {resultado.excluidos} de Limpieza/Tapas: no son Total Obras, "
            "pero cuentan en Total Sur (en Otros)."
        )
    if resultado.n_sin_clasificar:
        notas.append(
            f"{resultado.n_sin_clasificar} sin '{cnt.CAMPO_DENTRO_ZONA}': no suman ni a Fuera ni a Dentro."
        )
    return columnas, filas, notas


def formatear_texto(cnt, resultado, titulo: str) -> str:
    """Version en texto plano, alineada con espacios: es el respaldo para los
    clientes de mail que no muestran HTML, y lo que imprime --probar."""
    columnas, filas, notas = _datos_tabla(cnt, resultado)
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
    if notas:
        lineas += [""] + notas
    return "\n".join(lineas)


# Paleta del panel de QGIS (el azul del boton "Actualizar comparacion").
_AZUL = "#0a3d62"
_AZUL_SUR = "#1a5276"      # encabezado de Total Sur: distingue la columna "todo"
_CELESTE = "#eaf1f7"       # fondo de la columna Total Sur
_ZEBRA = "#f5f7f9"
_BORDE = "#d5dde5"
_GRIS = "#8a96a3"
_FUENTE = "font-family:'Segoe UI',Arial,Helvetica,sans-serif;"


def _hoja(nivel: int, nombre: str, atributos: dict, contenido: str) -> list[str]:
    """Un elemento con texto, indentado: atributos uno por linea y el contenido
    en su propia linea. Cada nivel son 2 espacios; el espacio en blanco entre
    etiquetas no cambia lo que se ve."""
    sangria = "  " * nivel
    abre = [f"{sangria}<{nombre}"]
    for clave, valor in atributos.items():
        if clave == "style":
            # Una declaracion CSS por linea; entre atributos HTML el espacio en
            # blanco (saltos de linea incluidos) no cambia nada.
            declaraciones = [d for d in valor.split(";") if d]
            abre.append(f'{sangria}    style="{declaraciones[0]};')
            abre += [f"{sangria}           {d};" for d in declaraciones[1:-1]]
            abre.append(f'{sangria}           {declaraciones[-1]};"')
        else:
            abre.append(f'{sangria}    {clave}="{valor}"')
    abre[-1] += ">"
    return abre + [f"{sangria}  {contenido}", f"{sangria}</{nombre}>"]


def formatear_html(cnt, resultado, titulo: str, subtitulo: str) -> str:
    """Version HTML con diseño, indentada. Va todo con estilos en linea y
    <table>: Outlook ignora <style>, flexbox y casi todo el CSS moderno, pero
    respeta esto."""
    columnas, filas, notas = _datos_tabla(cnt, resultado)
    sur = len(columnas) - 1   # indice de Total Sur

    def encabezado(nivel, texto, alineacion, fondo):
        return _hoja(nivel, "th", {
            "align": alineacion,
            "bgcolor": fondo,
            "style": (
                f"background:{fondo};color:#ffffff;padding:9px 14px;font-size:12px;"
                f"font-weight:600;white-space:nowrap;{_FUENTE}"
            ),
        }, texto)

    lineas = []
    lineas += ["<!DOCTYPE html>", "<html>", '  <body style="margin:0;padding:0;background:#ffffff;">']
    lineas += [
        '    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">',
        "      <tr>",
        '        <td style="padding:20px;">',
    ]
    lineas += _hoja(5, "p", {
        "style": f"margin:0 0 2px 0;font-size:20px;font-weight:700;color:{_AZUL};{_FUENTE}",
    }, escape(titulo))
    lineas += _hoja(5, "p", {
        "style": f"margin:0 0 16px 0;font-size:13px;color:{_GRIS};{_FUENTE}",
    }, escape(subtitulo))

    lineas += [
        "          <table",
        '              cellpadding="0" cellspacing="0" border="0"',
        f'              style="border:1px solid {_BORDE};border-collapse:collapse;">',
        "            <tr>",
    ]
    lineas += encabezado(7, "Categor&iacute;a", "left", _AZUL)
    for i, columna in enumerate(columnas):
        lineas += encabezado(7, escape(columna), "right", _AZUL_SUR if i == sur else _AZUL)
    lineas += ["            </tr>"]

    for n, (nombre, valores) in enumerate(filas):
        es_total = nombre == "TOTAL"
        base = "#ffffff" if n % 2 == 0 else _ZEBRA
        borde = f"border-top:2px solid {_AZUL};" if es_total else f"border-top:1px solid {_BORDE};"
        tamano = "14px" if es_total else "13px"

        lineas += ["            <tr>"]
        lineas += _hoja(7, "td", {
            "align": "left",
            "bgcolor": base,
            "style": (
                f"background:{base};{borde}padding:9px 14px;font-size:{tamano};"
                f'font-weight:{"700" if es_total else "600"};color:{_AZUL};{_FUENTE}'
            ),
        }, escape(nombre))
        for i, valor in enumerate(valores):
            fondo = _CELESTE if i == sur else base
            # Un cero es dato, pero en gris se lee mas rapido lo que si hay.
            color = _GRIS if valor == 0 and not es_total else "#1c2833"
            lineas += _hoja(7, "td", {
                "align": "right",
                "bgcolor": fondo,
                "style": (
                    f"background:{fondo};{borde}padding:9px 14px;font-size:{tamano};"
                    f'font-weight:{"700" if es_total else "400"};color:{color};{_FUENTE}'
                ),
            }, str(valor))
        lineas += ["            </tr>"]

    lineas += ["          </table>"]
    if notas:
        lineas += ['          <div style="margin-top:14px;">']
        for nota in notas:
            lineas += _hoja(6, "p", {
                "style": f"margin:4px 0;font-size:12px;color:{_GRIS};{_FUENTE}",
            }, escape(nota))
        lineas += ["          </div>"]
    lineas += ["        </td>", "      </tr>", "    </table>", "  </body>", "</html>"]
    return "\n".join(lineas) + "\n"


def armar_reporte() -> tuple[str, str]:
    """(texto, html) del mismo conteo."""
    cnt = _cargar_conteo()
    resultado = cnt.contar_filas(leer_filas_gpkg())
    fecha = datetime.now().strftime("%d/%m/%Y")
    texto = formatear_texto(cnt, resultado, f"Conteo de Problemas - {fecha}")
    html = formatear_html(
        cnt, resultado, "Conteo de Problemas", f"Zona 8 Sur · problemas abiertos al {fecha}"
    )
    return texto, html


def _config() -> configparser.SectionProxy:
    if not RUTA_INI.is_file():
        raise FileNotFoundError(
            f"Falta {RUTA_INI.name}. Copiar {RUTA_INI.name}.example y completarlo."
        )
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(RUTA_INI, encoding="utf-8")
    return parser["smtp"]


def enviar(texto: str, html: str) -> None:
    cfg = _config()
    destinatarios = [d.strip() for d in cfg["destinatarios"].split(",") if d.strip()]

    mensaje = EmailMessage()
    mensaje["Subject"] = f"Conteo de Problemas Zona 8 Sur - {datetime.now():%d/%m/%Y}"
    mensaje["From"] = cfg["remitente"]
    mensaje["To"] = ", ".join(destinatarios)
    mensaje.set_content(texto)
    mensaje.add_alternative(html, subtype="html")

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
    parser.add_argument(
        "--probar", action="store_true",
        help="imprime el reporte y guarda una vista previa HTML, sin enviar el mail",
    )
    args = parser.parse_args()

    logging.basicConfig(
        filename=RUTA_LOG, level=logging.INFO, encoding="utf-8",
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        texto, html = armar_reporte()
        if args.probar:
            print(texto)
            RUTA_PREVIEW.write_text(html, encoding="utf-8")
            print(f"\nVista previa del mail: {RUTA_PREVIEW}")
            return 0
        enviar(texto, html)
        logging.info("Enviado")
        return 0
    except Exception:
        # Desde el Programador de tareas nadie mira la consola: el log es la unica pista.
        logging.exception("Fallo el envio")
        raise


if __name__ == "__main__":
    sys.exit(main())
