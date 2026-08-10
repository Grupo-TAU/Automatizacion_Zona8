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
| Eliminar problemas finalizados | Booleano | Si está activo (por defecto), borra de la capa maestra los reclamos que ya no figuran en el WFS. |

#### Flujo interno

1. **Conexión al WFS** — El algoritmo se conecta directamente a `https://geoserver-ssl.imm.gub.uy/geoserver/ows` y descarga la capa `V_RE_RECLAMOS_SANEA_PORTAL`. No es necesario exportar la capa manualmente.
2. **Intersección** — Se recortan los reclamos al área definida por `Zona_delimitada`.
3. **Deduplicación** — Se comparan los IDs de los reclamos intersectados contra los ya presentes en `Reclamos_limitado`. Solo se procesan los que no existen, descartando además los IDs repetidos dentro de la misma corrida.
4. **Detección de finalizados** — Se marcan los registros de la capa maestra cuyo ID ya no está en el WFS: como el servicio solo publica reclamos abiertos, esos se consideran finalizados. La comparación usa el WFS completo, no la intersección, para no borrar reclamos que sigan abiertos fuera de la zona.
5. **Alta y baja** — Los reclamos nuevos se agregan a `Reclamos_limitado` mapeando únicamente los campos existentes en esa capa, y los finalizados se eliminan. Ambas operaciones van en una sola sesión de edición.
6. **Limpieza** — Se eliminan las capas auxiliares temporales generadas durante el proceso.

> La limpieza se omite si el WFS no devuelve ningún ID, para que una falla del servicio no vacíe la capa. Los registros sin valor en el campo ID (cargados a mano) nunca se eliminan.

#### Salida

- **Log del algoritmo**: cantidad de reclamos incorporados y eliminados, con el detalle de IDs de cada grupo.
- **Capa Reclamos_limitado**: actualizada con los registros nuevos y sin los finalizados.

---

## Servicio WFS

| Propiedad | Valor |
|---|---|
| URL | `https://geoserver-ssl.imm.gub.uy/geoserver/ows` |
| Capa | `imm:V_RE_RECLAMOS_SANEA_PORTAL` |
| SRS | `EPSG:32721` |
| Autenticación | Sin autenticación |
