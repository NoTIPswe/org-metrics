import os
import sys
import json
import numpy as np
import gspread
from datetime import datetime, timezone
from dotenv import load_dotenv
from jira import JIRA
from google.oauth2.service_account import Credentials

# Caricamento variabili d'ambiente
load_dotenv()

# ── Configuration ────────────────────────────
JIRA_URL = os.environ["JIRA_URL"]
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]
PROJECT_KEY = os.environ.get("JIRA_PROJECT_KEY", "NT")
SPRINT_NAME = os.environ["JIRA_SPRINT_NAME"]

# Google Sheets Configuration
SPREADSHEET_NAME = "notip-dashboard"
SHEET_NAME = "sprint-velocity-stability"

# ── JIRA Functions ───────────────────────────

def connect_jira():
    return JIRA(server=JIRA_URL, basic_auth=(JIRA_EMAIL, JIRA_API_TOKEN))

def get_project_sprints(jira):
    boards = jira.boards(projectKeyOrID=PROJECT_KEY)
    if not boards: boards = jira.boards()
    all_sprints = []
    for board in boards:
        try: all_sprints.extend(jira.sprints(board.id))
        except Exception: continue
    unique = {s.id: s for s in all_sprints}.values()
    return sorted(unique, key=lambda s: getattr(s, "startDate", None) or str(s.id))

def get_sprint_history(jira, target_name):
    history = []
    for sprint in get_project_sprints(jira):
        history.append(sprint)
        if sprint.name.strip() == target_name.strip():
            return history
    print(f"Sprint '{target_name}' non trovato.")
    sys.exit(1)

def calculate_stability(jira, sprint_history):
    velocities = []
    sprint_details = []
    closed_sprints = [s for s in sprint_history if getattr(s, "state", "").lower() == "closed"]
    
    print(f"Analisi per {len(closed_sprints)} sprint chiusi...")

    for sprint in closed_sprints:
        jql = f'sprint = {sprint.id} AND statusCategory = "done"'
        issues = jira.search_issues(jql, fields="timespent")
        
        hours_velocity = round(sum(getattr(issue.fields, 'timespent', 0) or 0 for issue in issues) / 3600.0, 2)
        velocities.append(hours_velocity)
        
        # Timestamp della timeline (endDate)
        sprint_end = getattr(sprint, "endDate", None)
        if sprint_end:
            base_date = sprint_end.split("T")[0]
            formatted_timestamp = f"{base_date}T23:00:00.000000+00:00"
        else:
            formatted_timestamp = "N/A"

        # Calcoli progressivi
        current_media = np.mean(velocities)
        current_std = np.std(velocities)
        
        if len(velocities) > 1 and current_media > 0:
            # Calcolo decimale (es. 0.92) invece di percentuale (92)
            current_stability = 1 - (current_std / current_media)
            current_variability = current_std / current_media
        else:
            current_stability = 1.0
            current_variability = 0.0

        sprint_details.append({
            "timestamp": formatted_timestamp,
            "sprint_name": sprint.name,
            "media": round(current_media, 2),
            "deviazione": round(current_std, 2),
            "variabilita_decimal": round(current_variability, 4),
            "stabilita_decimal": round(current_stability, 4)
        })
        
    return {"details": sprint_details}

# ── Google Sheets Functions ──────────────────

def get_google_credentials():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    creds_info = json.loads(creds_json)
    return Credentials.from_service_account_info(creds_info, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])

def append_to_google_sheet(result, credentials):
    client = gspread.authorize(credentials)
    try:
        spreadsheet = client.open(SPREADSHEET_NAME)
    except gspread.SpreadsheetNotFound:
        spreadsheet = client.open_by_key("10oebZdOQ3V6xdN9PDHuovwrSslg6_UaaWZx9xd7SnmE")

    try:
        worksheet = spreadsheet.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=SHEET_NAME, rows=1000, cols=6)

    worksheet.clear()
    # Header aggiornato
    headers = [["Timestamp", "Sprint Target", "Media (h)", "Deviazione (h)", "Variabilità", "Stabilità"]]
    worksheet.update("A1:F1", headers)

    # Preparazione righe con numeri decimali
    rows_to_append = []
    for e in result['details']:
        rows_to_append.append([
            e['timestamp'],
            e['sprint_name'],
            e['media'],
            e['deviazione'],
            e['variabilita_decimal'], # Ora è un numero (es: 0.0542)
            e['stabilita_decimal']    # Ora è un numero (es: 0.9458)
        ])
    
    worksheet.append_rows(rows_to_append)
    print(f"Dati caricati su Google Sheets in formato decimale.")

# ── Main ─────────────────────────────────────

def main():
    jira = connect_jira()
    sprint_history = get_sprint_history(jira, SPRINT_NAME)
    result = calculate_stability(jira, sprint_history)
    if result:
        append_to_google_sheet(result, get_google_credentials())

if __name__ == "__main__":
    main()