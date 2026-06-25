#!/usr/bin/env python3
"""
Vuelca los rubros del Parte Diario a la planilla Rubro_D.

Por cada N° Problema del 1er Excel que tenga un Rubro (columna H) que empiece
con un codigo D1..D7, suma la cantidad de ocurrencias en la columna correspondiente
de la hoja Rubro_D del 2do Excel:

    D1 -> C    D2 -> D    D3 -> E    D4 -> F    D5 -> G    D6 -> H    D7 -> I

- Recorre TODAS las hojas de fecha (nombre tipo AAAAMMDD) del parte diario.
- Si un mismo N° Problema aparece varias veces con el mismo codigo D, se acumula.
- Si el Rubro esta vacio o no empieza con D, no se cuenta.
- Si el problema ya existe en Rubro_D, suma los conteos a su fila.
- Si NO existe, agrega una fila nueva al final con N° (col A) + Direccion (col B) + los conteos.

Uso:
    python cargar_rubros.py PD_-_202606_2.xlsx PLANILLA_DE_RUBROS_Y_METRAJES_052026.xlsx
"""

import re
import sys
from collections import Counter, defaultdict
from openpyxl import load_workbook

# Codigo de rubro -> letra de columna en Rubro_D
CODIGO_A_COLUMNA = {
    "D1": "C", "D2": "D", "D3": "E", "D4": "F",
    "D5": "G", "D6": "H", "D7": "I",
}

# Columnas en el parte diario (hojas de fecha)
COL_PROBLEMA = 1   # A
COL_DIRECCION = 3  # C
COL_RUBRO = 8      # H

HOJA_FECHA = re.compile(r"^\d{8}$")      # ej: 20260606
RE_CODIGO = re.compile(r"^\s*(D[1-7])\b")


def recolectar_rubros(path_parte):
    """Devuelve dict {n_problema: {'direccion': str, 'conteos': Counter}} de todas las hojas de fecha."""
    wb = load_workbook(path_parte, data_only=True)
    rubros = defaultdict(lambda: {'direccion': '', 'conteos': Counter()})
    descartados = []
    for nombre in wb.sheetnames:
        if not HOJA_FECHA.match(nombre):
            continue
        ws = wb[nombre]
        for r in range(1, ws.max_row + 1):
            prob = ws.cell(r, COL_PROBLEMA).value
            if not isinstance(prob, (int, float)):
                continue
            n = str(int(prob))
            if not rubros[n]['direccion']:
                direccion = ws.cell(r, COL_DIRECCION).value
                if direccion:
                    rubros[n]['direccion'] = str(direccion)
            rubro = ws.cell(r, COL_RUBRO).value
            if not rubro:
                continue
            m = RE_CODIGO.match(str(rubro))
            if not m:
                descartados.append((nombre, n, str(rubro)[:40]))
                continue
            rubros[n]['conteos'][m.group(1)] += 1
    wb.close()
    return {n: v for n, v in rubros.items() if v['conteos']}, descartados


def indice_problemas(ws):
    """Devuelve dict {n_problema: fila} y la ultima fila usada con problema."""
    idx = {}
    ultima = 0
    for r in range(1, ws.max_row + 1):
        a = ws.cell(r, 1).value
        if a is not None and str(a).strip().isdigit():
            idx[str(a).strip()] = r
            ultima = r
    return idx, ultima


def col_idx(letra):
    return ord(letra) - ord("A") + 1


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    path_parte, path_planilla = sys.argv[1], sys.argv[2]

    rubros, descartados = recolectar_rubros(path_parte)
    print(f"Problemas con rubro valido encontrados: {len(rubros)}")

    wb = load_workbook(path_planilla)
    ws = wb["Rubro_D"]
    idx, ultima = indice_problemas(ws)

    marcados, agregados = [], []
    for n, datos in rubros.items():
        direccion = datos['direccion']
        conteos = datos['conteos']
        if n in idx:
            fila = idx[n]
            for codigo, conteo in conteos.items():
                col = col_idx(CODIGO_A_COLUMNA[codigo])
                existente = ws.cell(fila, col).value or 0
                ws.cell(fila, col).value = existente + conteo
            marcados.append((n, conteos, fila))
        else:
            ultima += 1
            ws.cell(ultima, 1).value = n
            ws.cell(ultima, 2).value = direccion
            for codigo, conteo in conteos.items():
                col = col_idx(CODIGO_A_COLUMNA[codigo])
                ws.cell(ultima, col).value = conteo
            idx[n] = ultima
            agregados.append((n, conteos, ultima))

    salida = path_planilla.replace(".xlsx", "_actualizada.xlsx")
    wb.save(salida)

    print(f"\nMarcados en filas existentes: {len(marcados)}")
    for n, conteos, f in marcados:
        resumen = "  ".join(f"{c}={v}" for c, v in sorted(conteos.items()))
        print(f"  Problema {n}  {resumen}  -> fila {f}")
    print(f"\nFilas nuevas agregadas: {len(agregados)}")
    for n, conteos, f in agregados:
        resumen = "  ".join(f"{c}={v}" for c, v in sorted(conteos.items()))
        print(f"  Problema {n}  {resumen}  -> fila {f}")
    if descartados:
        print(f"\nIgnorados (rubro sin codigo D): {len(descartados)}")
        for hoja, n, txt in descartados:
            print(f"  [{hoja}] Problema {n}: {txt}")
    print(f"\nGuardado: {salida}")


if __name__ == "__main__":
    main()
