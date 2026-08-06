function generarTodosLosLinks() {
  const hoja = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  const ID_CARPETA_PADRE = '1bvya9ioIP_cf6YzlN-zqv53Ndn046hfB';
  const carpetaPadre = DriveApp.getFolderById(ID_CARPETA_PADRE);
  
  const ultimaFila = hoja.getLastRow();
  if (ultimaFila < 2) return;

  for (let i = 2; i <= ultimaFila; i++) {
    const nProblema = hoja.getRange(i, 1).getValue();
    if (!nProblema) continue; // salta filas vacías

    // Si ya tiene link, lo saltea
    const celdaLink = hoja.getRange(i, 2);
    if (celdaLink.getValue() !== '') continue;

    try {
      const resultados = carpetaPadre.getFoldersByName(String(nProblema));
      if (resultados.hasNext()) {
        const carpeta = resultados.next();
        const url = 'https://drive.google.com/drive/folders/' + carpeta.getId();
        celdaLink.setFormula('=HYPERLINK("' + url + '","' + nProblema + '")');
      } else {
        celdaLink.setValue('No encontrada');
      }
    } catch(err) {
      celdaLink.setValue('Error: ' + err.message);
    }
  }
}