#!/usr/bin/env python3
"""
desplegar.py - Empaquetado, publicacion e instalacion de los plugins de este repo.

Este repo aloja MAS DE UN plugin de QGIS (cada uno en su propia carpeta, con su
propio metadata.txt), publicados a traves de un unico plugins.xml. Por eso todas
las acciones piden --plugin <carpeta> explicito: no hay "el" plugin por defecto.

Ciclo de publicacion completo:

    1. Editar la version en <carpeta_del_plugin>/metadata.txt.
    2. python desplegar.py --plugin <carpeta> --lanzamiento
    3. Ejecutar los comandos git que imprime el script.

metadata.txt es la UNICA fuente de verdad de cada plugin: plugins.xml se deriva
de el, nunca al reves. QGIS decide si hay actualizacion comparando solo el
numero de version del XML contra el instalado, asi que publicar sin subir la
version no produce ningun error visible: simplemente los usuarios no reciben
nada.

Plugins registrados en PLUGINS (ver mas abajo):
    Plugin_Automatizacion_Zona8  - sincronizacion de Reclamos/Problemas via WFS
    registrar_problema_sur       - fork de registrar_os_plugin para Zona 8 - Sur
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
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
import zipfile


# ── Rutas del repo ────────────────────────────────────────────────────────────
RAIZ        = Path(__file__).resolve().parent
PLUGINS_XML = RAIZ / "plugins.xml"

# URLs base del repo en GitHub: de aca se arma el download_url esperado de cada
# plugin al crear su entrada en plugins.xml por primera vez. RAW_REPO usa el
# mismo dominio (raw.githubusercontent.com) que ya usa la entrada existente.
HOMEPAGE_REPO = "https://github.com/Grupo-TAU/Automatizacion_Zona8"
RAW_REPO = "https://raw.githubusercontent.com/Grupo-TAU/Automatizacion_Zona8/main"


@dataclass(frozen=True)
class Plugin:
    """Un plugin publicado desde este repo. La carpeta es tambien el nombre del zip."""
    carpeta: str
    archivos_requeridos: tuple[str, ...]
    # (ruta_en_el_repo, ruta_relativa_dentro_de_la_carpeta_del_plugin)
    paquetes_vendorizados: tuple[tuple[Path, str], ...] = field(default_factory=tuple)

    @property
    def dir(self) -> Path:
        return RAIZ / self.carpeta

    @property
    def metadata_path(self) -> Path:
        return self.dir / "metadata.txt"

    @property
    def zip_destino(self) -> Path:
        return RAIZ / "Lanzamientos" / f"{self.carpeta}.zip"


# Paquetes propios que viven fuera de la carpeta del plugin y tienen que viajar
# adentro del zip: QGIS copia unicamente la carpeta del plugin al perfil del
# usuario, cualquier cosa que quede afuera no llega. Hoy ningun plugin de este
# repo depende de uno; si aparece un paquete compartido, se agrega a la entrada
# correspondiente en PLUGINS y verificar_zip() controla que llegue completo.
PLUGINS: dict[str, Plugin] = {
    "Plugin_Automatizacion_Zona8": Plugin(
        carpeta="Plugin_Automatizacion_Zona8",
        archivos_requeridos=("__init__.py", "metadata.txt", "plugin.py", "provider.py"),
    ),
    "registrar_problema_sur": Plugin(
        carpeta="registrar_problema_sur",
        archivos_requeridos=("__init__.py", "metadata.txt"),
    ),
}


def _elegir_plugin(nombre: str | None) -> Plugin:
    if nombre is None:
        raise ErrorDespliegue(
            "Falta --plugin <carpeta>. Este repo publica mas de un plugin, no hay "
            f"uno por defecto. Opciones: {', '.join(sorted(PLUGINS))}"
        )
    if nombre not in PLUGINS:
        raise ErrorDespliegue(
            f"'{nombre}' no es un plugin conocido. Opciones: {', '.join(sorted(PLUGINS))}"
        )
    return PLUGINS[nombre]


# ── Empaquetado ───────────────────────────────────────────────────────────────
DIRS_EXCLUIDOS = {
    "__pycache__", ".git", ".idea", ".vscode", ".mypy_cache", ".pytest_cache",
}
PATRONES_EXCLUIDOS = (
    "*.pyc", "*.pyo", "*.pyd", "*.swp", "*.orig", "*.rej", "*.log",
    ".DS_Store", "Thumbs.db", "~$*",
)

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

def leer_metadata(plugin: Plugin) -> dict[str, str]:
    """Lee metadata.txt del plugin, la unica fuente de verdad de la version."""
    if not plugin.metadata_path.is_file():
        raise ErrorDespliegue(f"No se encontro metadata.txt en {plugin.metadata_path}")

    cp = configparser.ConfigParser()
    cp.optionxform = str          # preservar el camelCase de qgisMinimumVersion
    try:
        cp.read(plugin.metadata_path, encoding="utf-8")
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


def construir_zip(plugin: Plugin) -> tuple[bytes, list[str]]:
    """
    Arma el zip en memoria con la carpeta del plugin en la raiz, que es el
    formato que espera QGIS. No escribe nada: si la verificacion posterior
    falla, el zip publicado queda intacto.
    """
    if not plugin.dir.is_dir():
        raise ErrorDespliegue(f"No existe la carpeta del plugin: {plugin.dir}")

    entradas = _recolectar(plugin.dir, plugin.carpeta)

    for origen, destino_rel in plugin.paquetes_vendorizados:
        if not origen.is_dir():
            raise ErrorDespliegue(
                f"El paquete a vendorizar no existe: {origen}\n"
                f"Revisa paquetes_vendorizados de '{plugin.carpeta}' en desplegar.py."
            )
        entradas += _recolectar(origen, f"{plugin.carpeta}/{destino_rel}")

    entradas.sort(key=lambda par: par[1])

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for ruta, arcname in entradas:
            entrada = zipfile.ZipInfo(arcname, date_time=FECHA_ZIP)
            entrada.compress_type = zipfile.ZIP_DEFLATED
            entrada.external_attr = 0o644 << 16
            z.writestr(entrada, ruta.read_bytes())

    return buffer.getvalue(), [arcname for _, arcname in entradas]


def verificar_zip(plugin: Plugin, contenido: bytes, version_esperada: str) -> None:
    """Falla ruidosamente si el paquete quedo incompleto o inconsistente."""
    with zipfile.ZipFile(io.BytesIO(contenido)) as z:
        nombres = z.namelist()

        if not nombres:
            raise ErrorDespliegue("El zip quedo vacio.")

        fuera = [n for n in nombres if not n.startswith(f"{plugin.carpeta}/")]
        if fuera:
            raise ErrorDespliegue(
                "Hay archivos fuera de la carpeta del plugin; QGIS no los va a "
                f"instalar: {fuera[:5]}"
            )

        faltantes = [
            f"{plugin.carpeta}/{req}"
            for req in plugin.archivos_requeridos
            if f"{plugin.carpeta}/{req}" not in nombres
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

        for _, destino_rel in plugin.paquetes_vendorizados:
            prefijo = f"{plugin.carpeta}/{destino_rel}"
            if f"{prefijo}/__init__.py" not in nombres:
                raise ErrorDespliegue(
                    f"El paquete vendorizado '{destino_rel}' no llego al zip o no "
                    "tiene __init__.py."
                )

        # La version que declara el plugin instalado tiene que coincidir con la
        # que anuncia el XML. Si no, QGIS ofrece la actualizacion, la instala, y
        # al volver a comparar sigue viendo una version vieja: la ofrece para
        # siempre.
        crudo = z.read(f"{plugin.carpeta}/metadata.txt").decode("utf-8")
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

def _cargar_xml() -> ET.ElementTree:
    if not PLUGINS_XML.is_file():
        raise ErrorDespliegue(
            f"No se encontro plugins.xml en {PLUGINS_XML}. Es el archivo que "
            "consulta QGIS para saber si hay version nueva."
        )
    try:
        return ET.parse(PLUGINS_XML)
    except ET.ParseError as exc:
        raise ErrorDespliegue(f"plugins.xml esta mal formado: {exc}") from exc


def _buscar_entrada(arbol: ET.ElementTree, plugin: Plugin) -> ET.Element | None:
    """
    Ubica la entrada <pyqgis_plugin> de este plugin por su download_url (el
    unico dato que identifica sin ambiguedad a que carpeta/zip corresponde),
    no por el atributo name= (ese es el nombre visible y puede repetirse o
    cambiar). Devuelve None si el plugin todavia no tiene entrada.
    """
    sufijo = f"/{plugin.zip_destino.relative_to(RAIZ).as_posix()}"
    for elemento in arbol.getroot().findall("pyqgis_plugin"):
        url_el = elemento.find("download_url")
        url = (url_el.text or "").strip() if url_el is not None else ""
        if urlsplit(url).path.endswith(sufijo):
            return elemento
    return None


def estado_xml(plugin: Plugin) -> tuple[str, str]:
    """
    Devuelve (version anunciada hoy, download_url). Si el plugin todavia no
    tiene entrada en plugins.xml, devuelve ("", "") sin fallar: la entrada se
    crea en actualizar_plugins_xml().
    """
    arbol = _cargar_xml()
    entrada = _buscar_entrada(arbol, plugin)
    if entrada is None:
        return "", ""
    elemento = entrada.find("version")
    version = (elemento.text or "").strip() if elemento is not None else ""
    url_el = entrada.find("download_url")
    url = (url_el.text or "").strip() if url_el is not None else ""
    return version, url


def actualizar_plugins_xml(plugin: Plugin, datos: dict[str, str]) -> str:
    """
    Reescribe (o crea) la entrada <pyqgis_plugin> de este plugin derivando todo
    de metadata.txt, y devuelve el download_url ya validado contra la ruta del
    zip. Las entradas de los demas plugins del repo quedan intactas.
    """
    arbol = _cargar_xml()
    entrada = _buscar_entrada(arbol, plugin)
    cambios: list[str] = []

    if entrada is None:
        entrada = ET.SubElement(arbol.getroot(), "pyqgis_plugin")
        zip_rel = plugin.zip_destino.relative_to(RAIZ).as_posix()
        ET.SubElement(entrada, "download_url").text = f"{RAW_REPO}/{zip_rel}"
        ET.SubElement(entrada, "homepage").text = HOMEPAGE_REPO
        ET.SubElement(entrada, "author_name").text = ""
        ET.SubElement(entrada, "experimental").text = "True"
        cambios.append(f"entrada nueva para '{plugin.carpeta}'")

    def poner_atributo(nombre: str, valor: str) -> None:
        if valor and entrada.get(nombre) != valor:
            cambios.append(f"{nombre}= : {entrada.get(nombre)!r} -> {valor!r}")
            entrada.set(nombre, valor)

    def poner_elemento(etiqueta: str, valor: str) -> None:
        if not valor:
            return
        elemento = entrada.find(etiqueta)
        if elemento is None:
            elemento = ET.SubElement(entrada, etiqueta)
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

    url = _verificar_download_url(plugin, entrada)

    ET.indent(arbol, space="  ")
    texto = ET.tostring(arbol.getroot(), encoding="unicode")
    PLUGINS_XML.write_text(f'<?xml version="1.0"?>\n{texto}\n', encoding="utf-8")

    if cambios:
        for cambio in cambios:
            ok(cambio)
    else:
        ok("plugins.xml ya estaba al dia")
    return url


def _verificar_download_url(plugin: Plugin, entrada: ET.Element) -> str:
    """Controla que el <download_url> de esta entrada apunte al zip de este plugin."""
    elemento = entrada.find("download_url")
    url = (elemento.text or "").strip() if elemento is not None else ""
    if not url:
        raise ErrorDespliegue(f"La entrada de '{plugin.carpeta}' no declara <download_url>.")

    esperado = plugin.zip_destino.relative_to(RAIZ).as_posix()
    if not urlsplit(url).path.endswith(f"/{esperado}"):
        raise ErrorDespliegue(
            "El <download_url> no apunta al zip que escribe este script; los "
            "usuarios bajarian un archivo que no existe.\n"
            f"  download_url : {url}\n"
            f"  zip publicado: {esperado}\n"
            "Corregi el download_url en plugins.xml, o la carpeta del plugin en desplegar.py."
        )
    return url


# ── Lanzamiento ───────────────────────────────────────────────────────────────

def lanzamiento(plugin: Plugin, forzar: bool) -> int:
    datos = leer_metadata(plugin)
    version = datos["version"]

    info(f"Publicando {datos['name'] or plugin.carpeta} {version}")

    info("\n[1/4] Verificando plugins.xml ...")
    anterior, _ = estado_xml(plugin)              # leer antes de tocar el XML
    ok(f"version anunciada hoy: {anterior or '(ninguna, entrada nueva)'}")

    info("\n[2/4] Empaquetando ...")
    contenido, arcnames = construir_zip(plugin)
    verificar_zip(plugin, contenido, version)

    identico = plugin.zip_destino.is_file() and plugin.zip_destino.read_bytes() == contenido

    if anterior == version and anterior != "" and not identico and not forzar:
        raise ErrorDespliegue(
            f"El contenido del plugin cambio pero la version sigue en {version}.\n"
            "QGIS compara solo el numero de version: si lo publicas asi, nadie\n"
            "recibe la actualizacion y no aparece ningun error.\n"
            f"  - Subi 'version=' en {plugin.metadata_path.relative_to(RAIZ)}, o\n"
            "  - volve a correr con --forzar si estas reparando este mismo release."
        )

    if identico and anterior == version:
        aviso("El zip publicado ya es identico y la version no cambio: nada que publicar.")

    info(f"\n[3/4] Escribiendo {plugin.zip_destino.relative_to(RAIZ)} ...")
    plugin.zip_destino.parent.mkdir(parents=True, exist_ok=True)
    plugin.zip_destino.write_bytes(contenido)
    ok(f"{len(contenido):,} bytes (nombre fijo, sin numero de version)")

    info("\n[4/4] Actualizando plugins.xml desde metadata.txt ...")
    url = actualizar_plugins_xml(plugin, datos)
    ok(f"download_url verificado: {url}")

    if anterior == version and anterior != "" and forzar and not identico:
        aviso(
            f"Republicando {version} sin cambiar el numero: los usuarios que ya "
            "tengan esa version instalada no van a recibir esto."
        )

    rel = plugin.zip_destino.relative_to(RAIZ).as_posix()
    info(
        "\n"
        "=== Falta subirlo al remoto ===\n"
        "QGIS baja del repositorio remoto, no de tu disco. Hasta que no hagas "
        "push, los usuarios siguen viendo la version anterior:\n\n"
        f"    git add {rel} plugins.xml {plugin.metadata_path.relative_to(RAIZ).as_posix()}\n"
        f'    git commit -m "Lanzamiento {plugin.carpeta} {version}"\n'
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


def instalar(plugin: Plugin, perfil: str) -> int:
    info(f"Instalando '{plugin.carpeta}' en el perfil '{perfil}'")

    datos = leer_metadata(plugin)
    contenido, arcnames = construir_zip(plugin)
    verificar_zip(plugin, contenido, datos["version"])

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

    destino = destino_base / plugin.carpeta
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
        description="Empaquetado, publicacion e instalacion de los plugins de este repo.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "La version se edita SOLO en metadata.txt; plugins.xml se deriva de ahi.\n"
            f"Plugins disponibles: {', '.join(sorted(PLUGINS))}\n\n"
            "Ejemplos:\n"
            "  python desplegar.py --plugin Plugin_Automatizacion_Zona8 --lanzamiento\n"
            "  python desplegar.py --plugin registrar_problema_sur --lanzamiento\n"
            "  python desplegar.py --plugin registrar_problema_sur --instalar\n"
        ),
    )
    parser.add_argument(
        "--plugin", choices=sorted(PLUGINS), default=None,
        help="carpeta del plugin sobre el que actuar (obligatorio)",
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
        plugin = _elegir_plugin(args.plugin)
        if args.lanzamiento:
            codigo = lanzamiento(plugin, args.forzar)
            if codigo:
                return codigo
        if args.instalar:
            return instalar(plugin, args.perfil)
    except ErrorDespliegue as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
