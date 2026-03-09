import os
import sys
import json
import gspread
from datetime import datetime
from dotenv import load_dotenv
from jira import JIRA
from google.oauth2.service_account import Credentials

load_dotenv()

# ── Configuration ────────────────────────────
JIRA_URL = os.environ["JIRA_URL"]
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]
PROJECT_KEY = os.environ.get("JIRA_PROJECT_KEY", "NT")

SPREADSHEET_NAME = "notip-dashboard"
SHEET_NAME = "time-efficiency"

# ── Functions ────────────────────────────────

def connect_jira():
    return JIRA(server=JIRA_URL, basic_auth=(JIRA_EMAIL, JIRA_API_TOKEN))

def get_all_closed_sprints(jira):
    boards = jira.boards(projectKeyOrID=PROJECT_KEY)
    if not boards:
        boards = jira.boards()

    all_sprints = []
    for board in boards:
        try:
            all_sprints.extend(jira.sprints(board.id, state='closed'))
        except Exception:
            continue

    unique_sprints = {s.id: s for s in all_sprints}.values()
    return sorted(unique_sprints, key=lambda s: getattr(s, "endDate", "0000"))

def calculate_sprint_efficiency(jira, sprint):
    """Restituisce un DIZIONARIO con i dati dello sprint."""
    jql = f'sprint = {sprint.id}'
    issues = jira.search_issues(jql, fields="timespent,timeoriginalestimate,status")
    
    productive_seconds = 0
    total_estimated_seconds = 0

    raw_date = getattr(sprint, "endDate", None)
    
    if raw_date:
        try:
            clean_date = raw_date.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_date)
            # Formato richiesto dall'utente con microsecondi
            sprint_timestamp = dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "+00:00"
        except Exception:
            sprint_timestamp = raw_date
    else:
        sprint_timestamp = "N/D"

    for issue in issues:
        total_estimated_seconds += (getattr(issue.fields, 'timeoriginalestimate', 0) or 0)
        status_category = issue.fields.status.statusCategory.key
        if status_category == "done":
            productive_seconds += (getattr(issue.fields, 'timespent', 0) or 0)

    if total_estimated_seconds == 0:
        return None

    prod_h = round(productive_seconds / 3600, 2)
    total_h = round(total_estimated_seconds / 3600, 2)
    efficiency = round((prod_h / total_h) * 100, 2) if total_h > 0 else 0.0

    # RITORNO COME DIZIONARIO
    return {
        "timestamp": sprint_timestamp,
        "name": sprint.name,
        "prod_h": prod_h,
        "total_h": total_h,
        "efficiency": f"{efficiency}%",
        "raw_prod": prod_h,
        "raw_total": total_h
    }

def upload_to_sheets(rows, credentials):
    client = gspread.authorize(credentials)
    spreadsheet = client.open(SPREADSHEET_NAME)

    try:
        worksheet = spreadsheet.worksheet(SHEET_NAME)
        worksheet.clear()
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=SHEET_NAME, rows=1000, cols=6)

    header = ["Timestamp", "Sprint Name", "Ore Produttive (h)", "Ore Totali (h)", "Efficiency Sprint %", "Efficiency Totale %"]
    worksheet.update("A1:F1", [header])
    
    if rows:
        worksheet.append_rows(rows)

# ── Main ─────────────────────────────────────

def main():
    print(f"Connessione a {JIRA_URL}...")
    jira = connect_jira()
    
    print("Recupero sprint chiusi e calcolo metriche...")
    sprints = get_all_closed_sprints(jira)
    
    all_results_rows = []
    running_prod_h = 0
    running_total_h = 0
    
    for s in sprints:
        data = calculate_sprint_efficiency(jira, s)
        # Ora 'data' è sicuramente un dizionario o None
        if data and isinstance(data, dict):
            running_prod_h += data["raw_prod"]
            running_total_h += data["raw_total"]
            
            cum_efficiency = round((running_prod_h / running_total_h) * 100, 2) if running_total_h > 0 else 0.0
            
            row = [
                data["timestamp"],
                data["name"],
                data["prod_h"],
                data["total_h"],
                data["efficiency"],
                f"{cum_efficiency}%"
            ]
            all_results_rows.append(row)
            print(f" - Elaborato: {data['name']}")

    if not all_results_rows:
        print("Nessun dato trovato.")
        return

    try:
        creds_json = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
        upload_to_sheets(all_results_rows, creds)
        print("Caricamento completato con successo.")
    except Exception as e:
        print(f"Errore caricamento: {e}")

if __name__ == "__main__":
    main()