# Automatizacion_Zona8

Plugin de QGIS para automatizar la actualización de capas de datos en Zona 8, desarrollado por DICA - Grupo TAU.

## Requisitos

- QGIS 3.44 o superior (el intercambio con la IM usa `openpyxl`, que QGIS incluye desde esa version)
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

## Intercambio de permisos con la Intendencia (UCCRIU)

Ida y vuelta de la columna `Permisos_UCCRIU` de la capa `inspecciones_os`
(`problemas_sur.gpkg`): se exporta un XLSX, lo completa la IM, y se mergea de
vuelta. Son dos algoritmos separados porque entre uno y otro pasan días.

El núcleo vive en `intercambio_im/`, en la raíz del repo, sin ninguna dependencia
de QGIS. Lo comparten **los dos plugins**: `desplegar.py` lo copia adentro de cada
zip (`paquetes_vendorizados`), así que **para probar cambios hay que reinstalar** —
el código de la raíz no es importable desde el plugin instalado.

| Módulo | Qué hace | Quién lo usa |
|---|---|---|
| `nucleo.py` | Normalización, validación de claves, comparación de metadatos, diff | Intercambio IM |
| `tabla.py` | Lectura genérica de planillas CSV/XLSX por nombre de columna | Intercambio IM y el conteo de Problemas |
| `planilla.py` | El envío, la devolución y el reporte, con su hoja `_meta` | Intercambio IM |

`tabla.py` es lo que antes estaba duplicado: `conteo.py` traía su propio lector de
XLSX escrito a mano con `zipfile` y `ElementTree`, porque cuando se escribió se
asumía que `openpyxl` no venía con QGIS. Desde 3.44 sí viene, así que ahora
`conteo.py` delega y quedaron 153 líneas menos que mantener por duplicado.

Ojo con las dos normalizaciones, que no son intercambiables:

- `nucleo.normalizar()` — trim (incluyendo NBSP) y `casefold()`. Compara **datos**
  del intercambio, así que no puede tocar acentos ni espacios internos sin cambiar
  el valor que termina escrito en la capa.
- `tabla.plegar()` — además saca acentos y colapsa espacios. Compara **etiquetas**:
  nombres de columna, nombres de hoja, categorías de `Tipo`.

Depende de `openpyxl`, que QGIS trae de fábrica desde 3.44 — por eso el plugin
declara esa versión como mínima. Si aun así falta, el algoritmo aborta con el
comando de instalación en el mensaje.

### 1. Exportar permisos para la IM

| Parámetro | Tipo | Descripción |
|---|---|---|
| Capa de problemas | Capa vectorial | `inspecciones_os`. |
| Filtro de filas a exportar | Expresión | Por defecto: etapas abiertas **y** permiso todavía vacío. |
| Campo clave | Texto | `fid` o el nombre de una columna UUID. |
| Planilla a enviar | Archivo XLSX | Salida. |

Produce una hoja `Datos` con `clave | N_Problema | Permisos_UCCRIU`, las dos
últimas con formato de texto explícito para que Excel no se coma ceros a la
izquierda ni invente fechas. La hoja se protege sin contraseña dejando
desbloqueada solo la columna del permiso: es una guía visual, no seguridad — la
garantía real es que el algoritmo 2 no lee ninguna otra columna.

La hoja oculta `_meta` guarda lote, fecha, capa, proveedor, fuente, cantidad de
filas, hash y **la lista completa de claves enviadas**. La lista va entera y no
solo el hash porque de un hash no se puede reconstruir qué filas faltaron.

Antes de terminar reporta los valores de `Etapa` que no están en la lista blanca,
para enterarse de una etapa nueva o mal escrita que esté quedando fuera del envío
en silencio.

Como el filtro exige permiso vacío, reexportar después de un merge deja afuera lo
que ya volvió cargado: la planilla funciona como cola de trabajo.

### 2. Mergear permisos devueltos por la IM

| Parámetro | Tipo | Descripción |
|---|---|---|
| Capa destino | Capa vectorial | `inspecciones_os`. |
| Planilla devuelta | Archivo XLSX | Lo que mandó la IM. |
| Modo simulación | Booleano | **Activo por defecto**: genera el reporte sin tocar la capa. |
| Permitir vaciar | Booleano | Apagado por defecto. Con él, una celda vacía borra el permiso existente. |
| Reporte | Archivo XLSX | Salida, siempre se escribe. |

Escribe **únicamente** `Permisos_UCCRIU`. Todo pasa por validaciones antes de
tocar nada: columnas esperadas, metadatos, claves nulas/duplicadas/no enteras, y
que el `N_Problema` del archivo coincida con el de la capa para esa clave.

Una acción por fila evaluada:

| Acción | Significado |
|---|---|
| `actualizado` | Se escribió el permiso nuevo. |
| `vaciado` | Se borró un permiso existente (solo con *permitir vaciar*). |
| `sin_cambio` | El valor devuelto ya estaba en la capa. |
| `ignorado_vacio` | La IM no completó la celda; no se pisa lo que hay. |
| `no_devuelta` | La fila se envió pero no volvió en el archivo. |
| `clave_huerfana` | La clave del archivo no existe en la capa. |
| `desalineado` | El `N_Problema` no coincide. **Nunca se escribe.** |

Todo el lote va en una sola sesión de edición, con rollback ante cualquier error.
Si la capa está en modo edición con cambios sin guardar, el algoritmo aborta: un
commit suyo confirmaría también los cambios del usuario.

### Dos trampas que vale la pena conocer

**`fid` no es una clave estable.** Es el rowid del proveedor. Hoy la tabla es
`INTEGER PRIMARY KEY AUTOINCREMENT`, así que SQLite no reutiliza los borrados y
dentro de este archivo el fid aguanta. Pero se renumera al reexportar la capa y
al migrar a PostGIS. Los dos algoritmos avisan cuando la clave es `fid`, el
algoritmo 2 aborta si cambió el proveedor, el archivo o la tabla, y el control de
`N_Problema` es la red que queda si algo se corrompió igual. La solución de fondo
es una columna UUID, y hay que crearla **antes** de la migración.

**La expresión de filtro se evalúa del lado de QGIS, a propósito.** Si se le pasa
al `QgsFeatureRequest`, el proveedor la compila a SQL y la ejecuta él, con otra
semántica. Acá el efecto era concreto: SQLite tiene `upper()` solo ASCII, así que
`upper('En_Ejecución')` da `'EN_EJECUCIóN'` y no matchea el literal
`'EN_EJECUCIÓN'` de la lista blanca. Las filas `En_Ejecución` quedaban fuera del
envío sin ningún mensaje (60 filas en vez de 62). Evaluar en Python cuesta nada
con este volumen y deja el resultado igual cuando la capa pase a PostGIS, donde
`upper()` se comporta distinto de nuevo.

---

## Servicio WFS

| Propiedad | Valor |
|---|---|
| URL | `https://geoserver-ssl.imm.gub.uy/geoserver/ows` |
| Capa | `imm:V_RE_RECLAMOS_SANEA_PORTAL` |
| SRS | `EPSG:32721` |
| Autenticación | Sin autenticación |

---

## Publicar una versión nueva

El plugin se distribuye por repositorio de complementos personalizado: los usuarios configuran `plugins.xml` en **Complementos → Configuración → Repositorios** y QGIS lo consulta para saber si hay versión nueva.

```
1. Editar version= en <carpeta_del_plugin>/metadata.txt
2. python desplegar.py --plugin Plugin_Automatizacion_Zona8 --lanzamiento
3. Ejecutar los comandos git que imprime el script
```

`desplegar.py --plugin <carpeta> --lanzamiento` empaqueta el plugin, escribe el zip en `Lanzamientos/` y reescribe `plugins.xml` derivándolo de `metadata.txt`. Para probar antes de publicar: `python desplegar.py --plugin <carpeta> --instalar` lo copia al perfil local de QGIS.

**`metadata.txt` es la única fuente de verdad de la versión.** `plugins.xml` no se edita a mano: el número vive en tres lugares (el atributo `version=`, el elemento `<version>` y `metadata.txt`) y moverlos por separado es el error más fácil de cometer.

**QGIS decide si hay actualización comparando solo el número de versión**, no fechas ni contenido. Si se sube un zip nuevo sin subir la versión, los usuarios no reciben nada y no aparece ningún error. Por eso el script se niega a publicar si el contenido cambió y la versión no (usar `--forzar` solo para reparar un release ya publicado).

**El nombre del zip es fijo**, sin número de versión: el `download_url` del XML es una URL estática y versionar el nombre la rompería.
