"""
Registrar Problema Sur - Plugin QGIS
Fork de registrar_os_plugin para Zona 8 - Sur.
DICA - Grupo TAU
"""


def classFactory(iface):
    from .registrar_os import RegistrarOSPlugin
    return RegistrarOSPlugin(iface)
