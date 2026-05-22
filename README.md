# Automatizacion_Zona8

Plugin de QGIS para automatizar la actualización de capas de datos en Zona 8, desarrollado por DICA - Grupo TAU.

## Requisitos

- QGIS 3.0 o superior
- Acceso a la red interna para conectar con el servicio WFS (`geoserver-ssl.imm.gub.uy`)

## Instalación

1. Copiar la carpeta del plugin en el directorio de plugins de QGIS:
   - Windows: `C:\Users\<usuario>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\`
2. Reiniciar QGIS.
3. Activar el plugin desde **Complementos → Administrar e instalar complementos**.

---

## Algoritmos disponibles

### Actualizar Reclamos

Incorpora automáticamente los reclamos nuevos a la capa maestra `Reclamos_limitado`, tomando los datos directamente del servicio WFS `V_RE_RECLAMOS_SANEA_PORTAL`.

#### Parámetros

| Parámetro | Tipo | Descripción |
|---|---|---|
| Capa Zona_delimitada | Capa vectorial | Polígono que define el área de trabajo. Solo se incorporan reclamos que caigan dentro de esta zona. |
| Capa maestra Reclamos_limitado | Capa vectorial | Capa GeoPackage destino donde se agregan los reclamos nuevos. |
| Campo ID único del reclamo | Texto | Nombre del campo que identifica unívocamente cada reclamo. Por defecto: `NUMERO_RECLAMO`. |

#### Flujo interno

1. **Conexión al WFS** — El algoritmo se conecta directamente a `https://geoserver-ssl.imm.gub.uy/geoserver/ows` y descarga la capa `V_RE_RECLAMOS_SANEA_PORTAL`. No es necesario exportar la capa manualmente.
2. **Intersección** — Se recortan los reclamos al área definida por `Zona_delimitada`.
3. **Deduplicación** — Se comparan los IDs de los reclamos intersectados contra los ya presentes en `Reclamos_limitado`. Solo se procesan los que no existen.
4. **Incorporación** — Los reclamos nuevos se agregan a `Reclamos_limitado` mapeando únicamente los campos existentes en esa capa.
5. **Limpieza** — Se eliminan las capas auxiliares temporales generadas durante el proceso.

#### Salida

- **Log del algoritmo**: cantidad de reclamos nuevos incorporados e IDs de cada uno.
- **Capa Reclamos_limitado**: actualizada con los registros nuevos.

---

## Servicio WFS

| Propiedad | Valor |
|---|---|
| URL | `https://geoserver-ssl.imm.gub.uy/geoserver/ows` |
| Capa | `imm:V_RE_RECLAMOS_SANEA_PORTAL` |
| SRS | `EPSG:32721` |
| Autenticación | Sin autenticación |
