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
JIRA_TOKEN = os.environ["JIRA_TOKEN"]
PROJECT_KEY = os.environ.get("JIRA_PROJECT_KEY", "NT")
SPRINT_NAME = os.environ["JIRA_SPRINT_NAME"]

# Google Sheets Configuration
SPREADSHEET_NAME = "notip-dashboard"
SHEET_NAME = "sprint-velocity-stability"

# ── JIRA Functions ───────────────────────────

def connect_jira():
    """Stabilisce la connessione con JIRA."""
    return JIRA(server=JIRA_URL, basic_auth=(JIRA_EMAIL, JIRA_TOKEN))

def get_project_sprints(jira):
    """Recupera tutti gli sprint del progetto ordinati cronologicamente."""
    boards = jira.boards(projectKeyOrID=PROJECT_KEY)
    if not boards:
        boards = jira.boards()

    all_sprints = []
    for board in boards:
        try:
            all_sprints.extend(jira.sprints(board.id))
        except Exception:
            continue

    unique = {s.id: s for s in all_sprints}.values()
    sorted_sprints = sorted(unique, key=lambda s: getattr(s, "startDate", None) or "9999")
    return sorted_sprints

def get_sprint_history(jira, target_name):
    """Ritorna la lista degli sprint dall'inizio fino allo sprint target."""
    history = []
    for sprint in get_project_sprints(jira):
        history.append(sprint)
        if sprint.name.strip() == target_name.strip():
            return history
    print(f"Sprint '{target_name}' non trovato.")
    sys.exit(1)

def calculate_stability(jira, sprint_history):
    """Calcola la stabilità basata sulle ore effettive (timespent)."""
    velocities = []
    closed_sprints = [s for s in sprint_history if getattr(s, "state", "").lower() == "closed"]
    
    print(f"Analisi ore effettive per {len(closed_sprints)} sprint chiusi...")

    for sprint in closed_sprints:
        jql = f'sprint = {sprint.id} AND statusCategory = "done"'
        issues = jira.search_issues(jql, fields="timespent")
        
        # Somma timespent (secondi) e conversione in ore
        total_seconds = sum(getattr(issue.fields, 'timespent', 0) or 0 for issue in issues)
        hours_velocity = total_seconds / 3600.0
        
        velocities.append(hours_velocity)
        print(f" - {sprint.name}: {hours_velocity:.2f} ore")

    if not velocities or len(velocities) < 2:
        return None

    media = np.mean(velocities)
    deviazione = np.std(velocities)
    
    # Formula: 1 - (std_dev / mean)
    stability_decimal = 1 - (deviazione / media) if media > 0 else 0.0
    stability_pct = stability_decimal * 100
    variability_pct = (deviazione / media) * 100 if media > 0 else 100.0
    
    return {
        "stability_pct": round(stability_pct, 2),
        "variability_pct": round(variability_pct, 2),
        "media": round(media, 2),
        "deviazione": round(deviazione, 2),
        "num_sprints": len(velocities)
    }

# ── Google Sheets Functions ──────────────────

def get_google_credentials():
    """Recupera le credenziali Google dal JSON nell'ambiente."""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS_JSON environment variable is not set")

    creds_info = json.loads(creds_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    return Credentials.from_service_account_info(creds_info, scopes=scopes)

def append_to_google_sheet(result, credentials):
    """Carica i dati su Google Sheets."""
    client = gspread.authorize(credentials)
    spreadsheet = client.open(SPREADSHEET_NAME)

    try:
        worksheet = spreadsheet.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=SHEET_NAME, rows=1000, cols=6)
        worksheet.update("A1:F1", [["Timestamp", "Sprint Target", "Media (h)", "Deviazione (h)", "Variabilità %", "Stabilità %"]])

    timestamp = datetime.now(timezone.utc).isoformat()
    new_row = [
        timestamp,
        SPRINT_NAME,
        result['media'],
        result['deviazione'],
        f"{result['variability_pct']}%",
        f"{result['stability_pct']}%"
    ]
    worksheet.append_row(new_row)
    print(f"Dati aggiunti al foglio '{SHEET_NAME}' con successo.")

# ── Main ─────────────────────────────────────

def main():
    print(f"Connessione a {JIRA_URL}...")
    jira = connect_jira()
    
    # 1. Calcolo Metrica
    sprint_history = get_sprint_history(jira, SPRINT_NAME)
    result = calculate_stability(jira, sprint_history)

    if not result:
        print("Errore: Dati insufficienti (servono almeno 2 sprint chiusi).")
        return

    # 2. Output Console
    print("\n" + "="*50)
    print(f" [MP21] SPRINT VELOCITY STABILITY: {result['stability_pct']}%")
    print(f" Variabilità rilevata: {result['variability_pct']}%")
    print("="*50)

    # 3. Caricamento su Google Sheets
    try:
        creds = get_google_credentials()
        append_to_google_sheet(result, creds)
    except Exception as e:
        print(f"Errore durante l'invio a Google Sheets: {e}")

if __name__ == "__main__":
    main()