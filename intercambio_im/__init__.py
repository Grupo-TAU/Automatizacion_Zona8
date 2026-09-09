"""
Nucleo del intercambio de permisos con la Intendencia de Montevideo.

Paquete puro: no importa nada de QGIS. Los algoritmos de Processing son
envoltorios finos que traducen features <-> dicts y delegan todo lo demas aca,
asi que el mismo codigo corre desde un script suelto o desde pandas.

Vive en la raiz del repo y viaja adentro del zip del plugin (ver
paquetes_vendorizados en desplegar.py). Para probar cambios hay que reinstalar:

    python desplegar.py --plugin Plugin_Automatizacion_Zona8 --instalar
"""

from .nucleo import ErrorIntercambio  # noqa: F401
