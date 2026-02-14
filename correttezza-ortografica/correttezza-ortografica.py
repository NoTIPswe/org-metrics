import os
import subprocess
import re
import json
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

def get_google_credentials() -> Credentials:
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise ValueError("ERRORE: La variabile d'ambiente GOOGLE_CREDENTIALS_JSON non è impostata.")

    creds_info = json.loads(creds_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    return Credentials.from_service_account_info(creds_info, scopes=scopes)

def append_to_google_sheet(voto_qualita: float, errori_totali: int, credentials: Credentials) -> None:

    client: gspread.Client = gspread.authorize(credentials)

    try:
        spreadsheet: gspread.Spreadsheet = client.open("notip-dashboard")
    except gspread.SpreadsheetNotFound:
        spreadsheet = client.open_by_key("10oebZdOQ3V6xdN9PDHuovwrSslg6_UaaWZx9xd7SnmE")

    nome_foglio = "spelling-quality"
    
    try:
        worksheet = spreadsheet.worksheet(nome_foglio)
    except gspread.WorksheetNotFound:
        print(f"Foglio '{nome_foglio}' non trovato. Creo uno dedicato...")
        worksheet = spreadsheet.add_worksheet(title=nome_foglio, rows=1000, cols=3)
        worksheet.update(values=[["Timestamp", "Voto Qualita", "Numero Errori"]], range_name="A1:C1")

    timestamp: str = datetime.now(timezone.utc).isoformat()

    worksheet.append_row([timestamp, round(voto_qualita, 2), errori_totali])
    print(f"Dati inseriti nel foglio '{nome_foglio}': {timestamp}, Voto: {voto_qualita}, Errori: {errori_totali}")

def main() -> None:
    print("Avvio calcolo metrica MP12 (Controllo Ortografico)...")

    docs_repo_path = os.environ.get('DOCS_REPO_PATH', './docs-repo')

    if not os.path.exists(docs_repo_path):
        print(f"ERRORE: Cartella {docs_repo_path} non trovata.")
        exit(1)

    print(f"Esecuzione di notipdo nella cartella: {docs_repo_path}")

    command = ["notipdo", "check", "baseline-docs"]
    try:
        result = subprocess.run(command, cwd=docs_repo_path, capture_output=True, text=True, check=False)
        output = result.stdout + "\n" + result.stderr
        print("--- LOG RAW DI NOTIPDO ---")
        print(output)
        print("--------------------------")
    except FileNotFoundError:
        print("ERRORE: 'notipdo' non trovato. Controlla lo step 'Install Dependencies' nello YAML.")
        exit(1)

    print("\n--- Risultati Analisi Ortografica ---")
    numero_errori = 0
    pattern_errori = re.compile(r"Spellcheck:\s*FAIL\s*\(([^)]+)\)")

    for linea in output.splitlines():
        match = pattern_errori.search(linea)
        if match:

            parole_sbagliate_str = match.group(1)
            lista_parole = [parola.strip() for parola in parole_sbagliate_str.split(",")]
            
            numero_errori += len(lista_parole)
            
            if "Checking document" in linea:
                nome_file = linea.split("Checking document ")[-1].split(". Formatting")[0]
            else:
                nome_file = "Documento Sconosciuto"
                
            print(f"[{nome_file}] -> {len(lista_parole)} errori: {', '.join(lista_parole)}")

    print("-------------------------------------")
    print(f"TOTALE ERRORI RILEVATI: {numero_errori}")

    print("\n--- Conteggio Parole Totali ---")
    parole_totali = 0
    for root, dirs, files in os.walk(docs_repo_path):
        for file in files:
            if file.endswith(".typ"):
                file_path = os.path.join(root, file)
                with open(file_path, 'r', encoding='utf-8') as f:
                    testo = f.read()
                    parole_totali += len(testo.split())

    print(f"TOTALE PAROLE STIMATE: {parole_totali}")

    voto_qualita = (1.0 - (numero_errori / parole_totali)) * 100
    print(f"Voto MP12 calcolato: {voto_qualita:.2f}%")


    print("\nConnessione a Google Sheets...")
    try:
        google_creds = get_google_credentials()
        append_to_google_sheet(voto_qualita, numero_errori, google_creds)
    except Exception as e:
        print(f"Errore durante l'invio a Google Sheets: {e}")

    print("\nScript terminato con successo!")

if __name__ == "__main__":
    main()