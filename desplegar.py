#!/usr/bin/env python3
"""
desplegar.py - Empaquetado, publicacion e instalacion del plugin Automatizacion Zona 8.

Ciclo de publicacion completo:

    1. Editar la version en Plugin_Automatizacion_Zona8/metadata.txt.
    2. python desplegar.py --lanzamiento
    3. Ejecutar los comandos git que imprime el script.

metadata.txt es la UNICA fuente de verdad: plugins.xml se deriva de el, nunca al
reves. QGIS decide si hay actualizacion comparando solo el numero de version del
XML contra el instalado, asi que publicar sin subir la version no produce ningun
error visible: simplemente los usuarios no reciben nada.
"""
from __future__ import annotations

import argparse
import configparser
import fnmatch
import io
import os
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import urlsplit


# ── Rutas del repo ────────────────────────────────────────────────────────────
RAIZ          = Path(__file__).resolve().parent
NOMBRE_PLUGIN = "Plugin_Automatizacion_Zona8"
DIR_PLUGIN    = RAIZ / NOMBRE_PLUGIN
METADATA      = DIR_PLUGIN / "metadata.txt"
PLUGINS_XML   = RAIZ / "plugins.xml"

# El nombre del zip es FIJO y no lleva numero de version: el <download_url> de
# plugins.xml es una URL estatica. Si el nombre cambiara en cada release, la URL
# dejaria de encontrar el archivo.
ZIP_DESTINO = RAIZ / "Lanzamientos" / f"{NOMBRE_PLUGIN}.zip"

# Paquetes propios que viven fuera de la carpeta del plugin y tienen que viajar
# adentro del zip: QGIS copia unicamente la carpeta del plugin al perfil del
# usuario, cualquier cosa que quede afuera no llega. Formato:
#     (ruta_en_el_repo, ruta_relativa_dentro_de_la_carpeta_del_plugin)
# Hoy esta vacio a proposito: el plugin solo importa qgis.core, processing y sus
# propios modulos relativos. Si mas adelante aparece un paquete compartido, se
# agrega aca y verificar_zip() controla que llegue completo.
PAQUETES_VENDORIZADOS: list[tuple[Path, str]] = []

# ── Empaquetado ───────────────────────────────────────────────────────────────
DIRS_EXCLUIDOS = {
    "__pycache__", ".git", ".idea", ".vscode", ".mypy_cache", ".pytest_cache",
}
PATRONES_EXCLUIDOS = (
    "*.pyc", "*.pyo", "*.pyd", "*.swp", "*.orig", "*.rej", "*.log",
    ".DS_Store", "Thumbs.db", "~$*",
)
ARCHIVOS_REQUERIDOS = ("__init__.py", "metadata.txt", "plugin.py", "provider.py")

# Fecha fija para todas las entradas del zip. Hace que el empaquetado sea
# reproducible: si el codigo no cambio, el zip es byte a byte identico y git no
# lo ve como modificado.
FECHA_ZIP = (1980, 1, 1, 0, 0, 0)

# Windows corta las rutas en 260 caracteres. Si el plugin queda instalado mas
# profundo, qgis_process no encuentra los .py y falla con un ModuleNotFoundError
# que no menciona el largo de la ruta.
LIMITE_RUTA_WINDOWS = 260
AVISO_RUTA_WINDOWS  = 240


class ErrorDespliegue(Exception):
    """Falla esperable del despliegue, se reporta sin traceback."""


def info(texto: str) -> None:
    print(texto)


def ok(texto: str) -> None:
    print(f"  [OK] {texto}")


def aviso(texto: str) -> None:
    print(f"  [!]  {texto}")


# ── metadata.txt ──────────────────────────────────────────────────────────────

def leer_metadata() -> dict[str, str]:
    """Lee metadata.txt, la unica fuente de verdad de la version."""
    if not METADATA.is_file():
        raise ErrorDespliegue(f"No se encontro metadata.txt en {METADATA}")

    cp = configparser.ConfigParser()
    cp.optionxform = str          # preservar el camelCase de qgisMinimumVersion
    try:
        cp.read(METADATA, encoding="utf-8")
    except configparser.Error as exc:
        raise ErrorDespliegue(f"metadata.txt esta mal formado: {exc}") from exc

    if not cp.has_section("general"):
        raise ErrorDespliegue("metadata.txt no tiene la seccion [general].")

    general = cp["general"]
    datos: dict[str, str] = {}

    for clave in ("version", "qgisMinimumVersion", "qgisMaximumVersion"):
        valor = general.get(clave, "").strip()
        if not valor:
            raise ErrorDespliegue(
                f"metadata.txt no define '{clave}'. Es obligatorio para publicar."
            )
        datos[clave] = valor

    for clave in ("name", "description", "author", "experimental"):
        datos[clave] = general.get(clave, "").strip()

    return datos


# ── Construccion del zip ──────────────────────────────────────────────────────

def _incluir(rel: Path) -> bool:
    if any(parte in DIRS_EXCLUIDOS for parte in rel.parts):
        return False
    return not any(fnmatch.fnmatch(rel.name, patron) for patron in PATRONES_EXCLUIDOS)


def _recolectar(origen: Path, prefijo: str) -> list[tuple[Path, str]]:
    entradas = []
    for ruta in origen.rglob("*"):
        if not ruta.is_file():
            continue
        rel = ruta.relative_to(origen)
        if not _incluir(rel):
            continue
        entradas.append((ruta, f"{prefijo}/{rel.as_posix()}"))
    return entradas


def construir_zip() -> tuple[bytes, list[str]]:
    """
    Arma el zip en memoria con la carpeta del plugin en la raiz, que es el
    formato que espera QGIS. No escribe nada: si la verificacion posterior
    falla, el zip publicado queda intacto.
    """
    if not DIR_PLUGIN.is_dir():
        raise ErrorDespliegue(f"No existe la carpeta del plugin: {DIR_PLUGIN}")

    entradas = _recolectar(DIR_PLUGIN, NOMBRE_PLUGIN)

    for origen, destino_rel in PAQUETES_VENDORIZADOS:
        if not origen.is_dir():
            raise ErrorDespliegue(
                f"El paquete a vendorizar no existe: {origen}\n"
                "Revisa PAQUETES_VENDORIZADOS en desplegar.py."
            )
        entradas += _recolectar(origen, f"{NOMBRE_PLUGIN}/{destino_rel}")

    entradas.sort(key=lambda par: par[1])

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for ruta, arcname in entradas:
            entrada = zipfile.ZipInfo(arcname, date_time=FECHA_ZIP)
            entrada.compress_type = zipfile.ZIP_DEFLATED
            entrada.external_attr = 0o644 << 16
            z.writestr(entrada, ruta.read_bytes())

    return buffer.getvalue(), [arcname for _, arcname in entradas]


def verificar_zip(contenido: bytes, version_esperada: str) -> None:
    """Falla ruidosamente si el paquete quedo incompleto o inconsistente."""
    with zipfile.ZipFile(io.BytesIO(contenido)) as z:
        nombres = z.namelist()

        if not nombres:
            raise ErrorDespliegue("El zip quedo vacio.")

        fuera = [n for n in nombres if not n.startswith(f"{NOMBRE_PLUGIN}/")]
        if fuera:
            raise ErrorDespliegue(
                "Hay archivos fuera de la carpeta del plugin; QGIS no los va a "
                f"instalar: {fuera[:5]}"
            )

        faltantes = [
            f"{NOMBRE_PLUGIN}/{req}"
            for req in ARCHIVOS_REQUERIDOS
            if f"{NOMBRE_PLUGIN}/{req}" not in nombres
        ]
        if faltantes:
            raise ErrorDespliegue(f"El paquete quedo incompleto, faltan: {faltantes}")

        basura = [
            n for n in nombres
            if "__pycache__" in n or n.endswith((".pyc", ".pyo"))
        ]
        if basura:
            raise ErrorDespliegue(
                f"El zip incluye basura de desarrollo: {basura[:5]}"
            )

        # Todo directorio con codigo tiene que ser un paquete importable. Esto
        # cubre tanto los subpaquetes propios como cualquier paquete vendorizado.
        directorios = {n.rsplit("/", 1)[0] for n in nombres if n.endswith(".py")}
        sin_init = sorted(d for d in directorios if f"{d}/__init__.py" not in nombres)
        if sin_init:
            raise ErrorDespliegue(
                f"Estos directorios tienen .py pero no __init__.py: {sin_init}"
            )

        for _, destino_rel in PAQUETES_VENDORIZADOS:
            prefijo = f"{NOMBRE_PLUGIN}/{destino_rel}"
            if f"{prefijo}/__init__.py" not in nombres:
                raise ErrorDespliegue(
                    f"El paquete vendorizado '{destino_rel}' no llego al zip o no "
                    "tiene __init__.py."
                )

        # La version que declara el plugin instalado tiene que coincidir con la
        # que anuncia el XML. Si no, QGIS ofrece la actualizacion, la instala, y
        # al volver a comparar sigue viendo una version vieja: la ofrece para
        # siempre.
        crudo = z.read(f"{NOMBRE_PLUGIN}/metadata.txt").decode("utf-8")
        en_zip = next(
            (l.split("=", 1)[1].strip() for l in crudo.splitlines()
             if l.strip().startswith("version=")),
            "",
        )
        if en_zip != version_esperada:
            raise ErrorDespliegue(
                f"El metadata.txt empaquetado declara version={en_zip!r} pero se "
                f"va a publicar {version_esperada!r}."
            )

    ok(f"{len(nombres)} archivos empaquetados, version {version_esperada}")


# ── plugins.xml ───────────────────────────────────────────────────────────────

def _cargar_xml() -> tuple[ET.ElementTree, ET.Element]:
    if not PLUGINS_XML.is_file():
        raise ErrorDespliegue(
            f"No se encontro plugins.xml en {PLUGINS_XML}. Es el archivo que "
            "consulta QGIS para saber si hay version nueva."
        )
    try:
        arbol = ET.parse(PLUGINS_XML)
    except ET.ParseError as exc:
        raise ErrorDespliegue(f"plugins.xml esta mal formado: {exc}") from exc

    plugin = arbol.getroot().find("pyqgis_plugin")
    if plugin is None:
        raise ErrorDespliegue("plugins.xml no contiene ningun <pyqgis_plugin>.")
    return arbol, plugin


def estado_xml() -> tuple[str, str]:
    """
    Devuelve (version anunciada hoy, download_url) validando de entrada que la
    URL apunte a donde se va a escribir el zip. Se controla antes de empaquetar
    para no dejar un zip nuevo publicado bajo una URL que no lo encuentra.
    """
    _, plugin = _cargar_xml()
    elemento = plugin.find("version")
    version = (elemento.text or "").strip() if elemento is not None else ""
    return version, verificar_download_url(plugin)


def actualizar_plugins_xml(datos: dict[str, str]) -> str:
    """
    Reescribe plugins.xml derivando todo de metadata.txt y devuelve el
    download_url ya validado contra la ruta del zip.
    """
    arbol, plugin = _cargar_xml()
    cambios: list[str] = []

    def poner_atributo(nombre: str, valor: str) -> None:
        if valor and plugin.get(nombre) != valor:
            cambios.append(f"{nombre}= : {plugin.get(nombre)!r} -> {valor!r}")
            plugin.set(nombre, valor)

    def poner_elemento(etiqueta: str, valor: str) -> None:
        if not valor:
            return
        elemento = plugin.find(etiqueta)
        if elemento is None:
            elemento = ET.SubElement(plugin, etiqueta)
        actual = (elemento.text or "").strip()
        if actual != valor:
            cambios.append(f"<{etiqueta}> : {actual!r} -> {valor!r}")
            elemento.text = valor

    # El atributo version= y el elemento <version> tienen que moverse juntos:
    # editar uno y olvidar el otro es el error mas facil de cometer.
    poner_atributo("version", datos["version"])
    poner_elemento("version", datos["version"])

    poner_elemento("qgis_minimum_version", datos["qgisMinimumVersion"])
    poner_elemento("qgis_maximum_version", datos["qgisMaximumVersion"])

    poner_atributo("name", datos["name"])
    poner_elemento("description", datos["description"])
    poner_elemento("author_name", datos["author"])
    poner_elemento("experimental", datos["experimental"])

    url = verificar_download_url(plugin)

    ET.indent(arbol, space="  ")
    texto = ET.tostring(arbol.getroot(), encoding="unicode")
    PLUGINS_XML.write_text(f'<?xml version="1.0"?>\n{texto}\n', encoding="utf-8")

    if cambios:
        for cambio in cambios:
            ok(cambio)
    else:
        ok("plugins.xml ya estaba al dia")
    return url


def verificar_download_url(plugin: ET.Element) -> str:
    """Controla que el <download_url> apunte al zip que publica este script."""
    elemento = plugin.find("download_url")
    url = (elemento.text or "").strip() if elemento is not None else ""
    if not url:
        raise ErrorDespliegue("plugins.xml no declara <download_url>.")

    esperado = ZIP_DESTINO.relative_to(RAIZ).as_posix()
    if not urlsplit(url).path.endswith(f"/{esperado}"):
        raise ErrorDespliegue(
            "El <download_url> no apunta al zip que escribe este script; los "
            "usuarios bajarian un archivo que no existe.\n"
            f"  download_url : {url}\n"
            f"  zip publicado: {esperado}\n"
            "Corregi el download_url en plugins.xml, o ZIP_DESTINO en desplegar.py."
        )
    return url


# ── Lanzamiento ───────────────────────────────────────────────────────────────

def lanzamiento(forzar: bool) -> int:
    datos = leer_metadata()
    version = datos["version"]

    info(f"Publicando {datos['name'] or NOMBRE_PLUGIN} {version}")

    info("\n[1/4] Verificando plugins.xml ...")
    anterior, _ = estado_xml()              # leer antes de tocar el XML
    ok(f"version anunciada hoy: {anterior or '(ninguna)'}")

    info("\n[2/4] Empaquetando ...")
    contenido, arcnames = construir_zip()
    verificar_zip(contenido, version)

    identico = ZIP_DESTINO.is_file() and ZIP_DESTINO.read_bytes() == contenido

    if anterior == version and not identico and not forzar:
        raise ErrorDespliegue(
            f"El contenido del plugin cambio pero la version sigue en {version}.\n"
            "QGIS compara solo el numero de version: si lo publicas asi, nadie\n"
            "recibe la actualizacion y no aparece ningun error.\n"
            f"  - Subi 'version=' en {METADATA.relative_to(RAIZ)}, o\n"
            "  - volve a correr con --forzar si estas reparando este mismo release."
        )

    if identico and anterior == version:
        aviso("El zip publicado ya es identico y la version no cambio: nada que publicar.")

    info(f"\n[3/4] Escribiendo {ZIP_DESTINO.relative_to(RAIZ)} ...")
    ZIP_DESTINO.parent.mkdir(parents=True, exist_ok=True)
    ZIP_DESTINO.write_bytes(contenido)
    ok(f"{len(contenido):,} bytes (nombre fijo, sin numero de version)")

    info("\n[4/4] Actualizando plugins.xml desde metadata.txt ...")
    url = actualizar_plugins_xml(datos)
    ok(f"download_url verificado: {url}")

    if anterior == version and forzar and not identico:
        aviso(
            f"Republicando {version} sin cambiar el numero: los usuarios que ya "
            "tengan esa version instalada no van a recibir esto."
        )

    rel = ZIP_DESTINO.relative_to(RAIZ).as_posix()
    info(
        "\n"
        "=== Falta subirlo al remoto ===\n"
        "QGIS baja del repositorio remoto, no de tu disco. Hasta que no hagas "
        "push, los usuarios siguen viendo la version anterior:\n\n"
        f"    git add {rel} plugins.xml {METADATA.relative_to(RAIZ).as_posix()}\n"
        f'    git commit -m "Lanzamiento {version}"\n'
        "    git push\n"
    )
    return 0


# ── Instalacion local ─────────────────────────────────────────────────────────

def dir_plugins_qgis(perfil: str) -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "QGIS" / "QGIS3" / "profiles" / perfil / "python" / "plugins"
    if sys.platform == "darwin":
        return (Path.home() / "Library" / "Application Support" / "QGIS" / "QGIS3"
                / "profiles" / perfil / "python" / "plugins")
    return Path.home() / ".local" / "share" / "QGIS" / "QGIS3" / "profiles" / perfil / "python" / "plugins"


def instalar(perfil: str) -> int:
    info(f"Instalando en el perfil '{perfil}'")

    datos = leer_metadata()
    contenido, arcnames = construir_zip()
    verificar_zip(contenido, datos["version"])

    destino_base = dir_plugins_qgis(perfil)

    # El aviso va ANTES de copiar: si la ruta se pasa de largo, qgis_process no
    # ve los .py y falla con un ModuleNotFoundError que no menciona el problema.
    if os.name == "nt":
        largo = max(len(str(destino_base / arcname)) for arcname in arcnames)
        if largo >= LIMITE_RUTA_WINDOWS:
            raise ErrorDespliegue(
                f"La ruta de instalacion llega a {largo} caracteres y Windows corta "
                f"en {LIMITE_RUTA_WINDOWS}.\n"
                "QGIS no va a encontrar los modulos y vas a ver un "
                "ModuleNotFoundError que no menciona el largo de la ruta.\n"
                "Instala en un perfil con nombre mas corto o habilita rutas largas "
                "en Windows."
            )
        if largo >= AVISO_RUTA_WINDOWS:
            aviso(f"La ruta mas larga queda en {largo} caracteres, cerca del limite de {LIMITE_RUTA_WINDOWS}.")

    destino = destino_base / NOMBRE_PLUGIN
    destino_base.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        shutil.rmtree(destino)

    with zipfile.ZipFile(io.BytesIO(contenido)) as z:
        z.extractall(destino_base)

    ok(f"instalado en {destino}")
    info(
        "\nReinicia QGIS (o desactiva y reactiva el plugin) para que tome los "
        "cambios: Python no recarga modulos ya importados.\n"
    )
    return 0


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Empaquetado, publicacion e instalacion del plugin Automatizacion Zona 8.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "La version se edita SOLO en metadata.txt; plugins.xml se deriva de ahi.\n"
            "Ejemplos:\n"
            "  python desplegar.py --lanzamiento\n"
            "  python desplegar.py --instalar\n"
        ),
    )
    parser.add_argument(
        "--lanzamiento", action="store_true",
        help="ciclo completo: empaquetar, publicar el zip y actualizar plugins.xml",
    )
    parser.add_argument(
        "--instalar", action="store_true",
        help="instalar el plugin en el perfil local de QGIS para probarlo",
    )
    parser.add_argument(
        "--perfil", default="default",
        help="perfil de QGIS donde instalar (por defecto: default)",
    )
    parser.add_argument(
        "--forzar", action="store_true",
        help="publicar aunque el contenido haya cambiado sin subir la version",
    )
    args = parser.parse_args(argv)

    if not args.lanzamiento and not args.instalar:
        parser.error("elegi una accion: --lanzamiento o --instalar")

    try:
        if args.lanzamiento:
            codigo = lanzamiento(args.forzar)
            if codigo:
                return codigo
        if args.instalar:
            return instalar(args.perfil)
    except ErrorDespliegue as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
