#!/usr/bin/env python3
"""
Genera la hoja Rubro_D a partir del Parte Diario mensual.

Lee todas las hojas de fecha (nombre tipo AAAAMMDD) del parte diario y,
por cada fila con Rubro (col H) que empiece con D1..D7, acumula la cantidad
de ocurrencias por N° Problema.

Genera un Excel nuevo con una hoja Rubro_D:
    Col A: N° Problema
    Col B: Direccion
    Col C..I: conteo de ocurrencias de D1..D7

Uso:
    python cargar_rubros.py PD_-_202606_2.xlsx
"""

import re
import sys
from collections import Counter, defaultdict
from openpyxl import Workbook, load_workbook

# Codigo de rubro -> columna en Rubro_D (1-indexed)
CODIGOS = [
    "D1", "D2", "D3", "D4", "D5", "D6", "D7",
    "E1", "E2", "E3", "E4", "E5", "E6", "E7",
    "F1", "F2", "F3", "F4", "F5",
    "No Corresponde",
]

# Columnas en el parte diario (hojas de fecha)
COL_PROBLEMA = 1   # A
COL_DIRECCION = 3  # C
COL_RUBRO = 8      # H

HOJA_FECHA = re.compile(r"^\d{8}$")
RE_CODIGO = re.compile(r"^\s*(D[1-7]|E[1-7]|F[1-5]|No\s+Corresponde)", re.IGNORECASE)


def _normalizar_codigo(raw):
    """Devuelve la forma canonica del codigo (ej: 'd1' -> 'D1', 'no corresponde' -> 'No Corresponde')."""
    if len(raw) == 2:
        return raw.upper()
    return "No Corresponde"


def recolectar_rubros(path_parte):
    """Devuelve (rubros_con_num, rubros_sin_num, descartados).

    rubros_con_num: {n_problema: {'direccion': str, 'conteos': Counter}}
    rubros_sin_num: {direccion: {'conteos': Counter}}  — filas sin N° Problema pero con ubicacion
    """
    wb = load_workbook(path_parte, data_only=True)
    rubros = defaultdict(lambda: {'direccion': '', 'conteos': Counter()})
    sin_numero = defaultdict(lambda: {'conteos': Counter()})
    descartados = []
    for nombre in wb.sheetnames:
        if not HOJA_FECHA.match(nombre):
            continue
        ws = wb[nombre]
        for r in range(1, ws.max_row + 1):
            rubro = ws.cell(r, COL_RUBRO).value
            if not rubro:
                continue
            m = RE_CODIGO.match(str(rubro))
            if not m:
                prob = ws.cell(r, COL_PROBLEMA).value
                if isinstance(prob, (int, float)):
                    descartados.append((nombre, str(int(prob)), str(rubro)[:40]))
                continue
            prob = ws.cell(r, COL_PROBLEMA).value
            direccion = ws.cell(r, COL_DIRECCION).value
            codigo = _normalizar_codigo(m.group(1))
            if not isinstance(prob, (int, float)):
                if direccion:
                    sin_numero[str(direccion)]['conteos'][codigo] += 1
                continue
            n = str(int(prob))
            if not rubros[n]['direccion'] and direccion:
                rubros[n]['direccion'] = str(direccion)
            rubros[n]['conteos'][codigo] += 1
    wb.close()
    return (
        {n: v for n, v in rubros.items() if v['conteos']},
        dict(sin_numero),
        descartados,
    )


def main():
    # ── CONFIGURACION ────────────────────────────────────────────────────────
    RUTA_PARTE_DIARIO = r"G:\Unidades compartidas\GRUPO TAU\INTENDENCIA DE MONTEVIDEO\02 - ZONA 8\02 - EN PROCESO\03 - INFORMES\0 - PARTES DIARIOS\2026\202606\PD - 202606.xlsx"
    # ─────────────────────────────────────────────────────────────────────────

    path_parte = RUTA_PARTE_DIARIO.strip()
    if not path_parte:
        print("Completar RUTA_PARTE_DIARIO en el script.")
        sys.exit(1)

    rubros, sin_numero, descartados = recolectar_rubros(path_parte)
    print(f"Problemas con rubro valido encontrados: {len(rubros)}")
    print(f"Filas sin N° Problema (solo ubicacion): {len(sin_numero)}")

    wb = Workbook()
    ws = wb.active
    ws.title = "Rubro_D"

    # Encabezado
    ws.append(["N° Problema", "Direccion"] + CODIGOS)

    for n, datos in rubros.items():
        fila = [n, datos['direccion']]
        for codigo in CODIGOS:
            fila.append(datos['conteos'].get(codigo) or None)
        ws.append(fila)

    for direccion, datos in sin_numero.items():
        fila = [None, direccion]
        for codigo in CODIGOS:
            fila.append(datos['conteos'].get(codigo) or None)
        ws.append(fila)

    salida = path_parte.replace(".xlsx", "_Rubro_D.xlsx")
    wb.save(salida)

    print(f"\nFilas generadas: {len(rubros) + len(sin_numero)}")
    if descartados:
        print(f"\nIgnorados (rubro sin codigo D): {len(descartados)}")
        for hoja, n, txt in descartados:
            print(f"  [{hoja}] Problema {n}: {txt}")
    print(f"\nGuardado: {salida}")


if __name__ == "__main__":
    main()
