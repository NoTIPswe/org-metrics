import os
import sys
import json
import gspread
from datetime import datetime, timezone
from dotenv import load_dotenv
from jira import JIRA
from google.oauth2.service_account import Credentials

load_dotenv()

# ── Configuration ────────────────────────────
JIRA_URL = os.environ["JIRA_URL"]
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]
PROJECT_KEY = os.environ.get("JIRA_PROJECT_KEY", "NT")
SPRINT_NAME = os.environ["JIRA_SPRINT_NAME"]

SPREADSHEET_NAME = "notip-dashboard"
SHEET_NAME = "time-efficiency"

# ── Functions ────────────────────────────────

def connect_jira():
    return JIRA(server=JIRA_URL, basic_auth=(JIRA_EMAIL, JIRA_API_TOKEN))

def get_all_closed_sprints(jira):
    """Recupera tutti gli sprint chiusi del progetto."""
    boards = jira.boards(projectKeyOrID=PROJECT_KEY)
    if not boards:
        boards = jira.boards()

    all_sprints = []
    for board in boards:
        try:
            # Recuperiamo solo gli sprint con stato 'closed'
            all_sprints.extend(jira.sprints(board.id, state='closed'))
        except Exception:
            continue

    # Rimuove duplicati e ordina per data
    unique = {s.id: s for s in all_sprints}.values()
    return sorted(unique, key=lambda s: getattr(s, "startDate", None) or "0000")

def calculate_sprint_efficiency(jira, sprint):
    """Calcola l'efficienza per un singolo sprint specifico."""
    jql = f'sprint = {sprint.id}'
    issues = jira.search_issues(jql, fields="timespent,timeoriginalestimate,status")
    
    productive_seconds = 0
    total_estimated_seconds = 0

    for issue in issues:
        # Ore Totali (Pianificate)
        total_estimated_seconds += (getattr(issue.fields, 'timeoriginalestimate', 0) or 0)
        
        # Ore Produttive (Loggate su issue completate)
        status_category = issue.fields.status.statusCategory.key
        if status_category == "done":
            productive_seconds += (getattr(issue.fields, 'timespent', 0) or 0)

    if total_estimated_seconds == 0:
        return None

    prod_h = round(productive_seconds / 3600, 2)
    total_h = round(total_estimated_seconds / 3600, 2)
    efficiency = round((prod_h / total_h) * 100, 2) if total_h > 0 else 0.0

    return [sprint.name, prod_h, total_h, f"{efficiency}%"]

def upload_to_sheets(rows, credentials):
    """Invia tutti i dati raccolti a Google Sheets in un colpo solo."""
    client = gspread.authorize(credentials)
    spreadsheet = client.open(SPREADSHEET_NAME)

    try:
        worksheet = spreadsheet.worksheet(SHEET_NAME)
        worksheet.clear() # Puliamo per evitare duplicati se rilanci lo script
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=SHEET_NAME, rows=1000, cols=5)

    # Aggiungiamo l'intestazione
    header = ["Sprint Name", "Ore Produttive (h)", "Ore Totali (h)", "Time Efficiency %"]
    worksheet.update("A1:D1", [header])
    
    # Inseriamo tutte le righe degli sprint
    if rows:
        worksheet.append_rows(rows)
    print(f"Caricate {len(rows)} righe su Google Sheets.")

# ── Main ─────────────────────────────────────

def main():
    print(f"Connessione a {JIRA_URL}...")
    jira = connect_jira()
    
    print("Recupero di tutti gli sprint chiusi...")
    sprints = get_all_closed_sprints(jira)
    
    all_results = []
    for s in sprints:
        result = calculate_sprint_efficiency(jira, s)
        if result:
            all_results.append(result)
            print(f" - Elaborato: {s.name} (Efficiency: {result[3]})")

    if not all_results:
        print("Nessun dato trovato negli sprint.")
        return

    # Caricamento su Google Sheets
    try:
        creds_json = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
        upload_to_sheets(all_results, creds)
    except Exception as e:
        print(f"Errore durante il caricamento: {e}")

if __name__ == "__main__":
    main()