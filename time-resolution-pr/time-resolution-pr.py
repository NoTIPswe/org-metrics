import os
import json
import gspread
import requests
from datetime import datetime, timezone, timedelta
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

TARGET_REPO = "RepoDocumentale"
SPREADSHEET_NAME = "notip-dashboard"
SHEET_NAME = "time-resolution-pr"

# Mappa degli Sprint con le date di Actual End
SPRINT_DATA = [
    {"name": "NT Sprint 1", "start": "2025-11-16", "end": "2025-11-29"},
    {"name": "NT Sprint 2", "start": "2025-11-30", "end": "2025-12-15"},
    {"name": "NT Sprint 3", "start": "2025-12-16", "end": "2026-01-04"},
    {"name": "NT Sprint 4", "start": "2026-01-05", "end": "2026-01-18"},
    {"name": "NT Sprint 5", "start": "2026-01-19", "end": "2026-02-03"},
    {"name": "NT Sprint 6", "start": "2026-02-04", "end": "2026-02-17"},
]

def get_sprint_info(created_at_dt):
    """Ritorna il nome e il timestamp formattato dello sprint per una data PR."""
    for s in SPRINT_DATA:
        start = datetime.fromisoformat(s["start"]).replace(tzinfo=timezone.utc)
        actual_end_dt = datetime.fromisoformat(s["end"]).replace(tzinfo=timezone.utc)
        limit_end = actual_end_dt + timedelta(days=1)
        
        if start <= created_at_dt < limit_end:
            # Formattazione richiesta: yyyy-mm-ddThh:mm:ss.000000+00:00
            formatted_ts = actual_end_dt.replace(hour=23, minute=0, second=0).strftime('%Y-%m-%dT%H:%M:%S.000000+00:00')
            return s["name"], formatted_ts
    return None, None

def main():
    token = os.environ.get("ORG_GITHUB_TOKEN")
    org = os.environ.get("GITHUB_ORG")
    
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
    url = f"https://api.github.com/repos/{org}/{TARGET_REPO}/pulls?state=closed&per_page=100"
    
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    prs = response.json()

    stats_per_sprint = {}

    for pr in prs:
        if pr.get("merged_at"):
            created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
            merged_at = datetime.fromisoformat(pr["merged_at"].replace("Z", "+00:00"))
            
            sprint_name, sprint_timestamp = get_sprint_info(created_at)
            
            if sprint_name:
                # Calcolo differenza in GIORNI invece di ORE
                diff_days = (merged_at - created_at).total_seconds() / 86400  # 86400 secondi in un giorno
                if sprint_name not in stats_per_sprint:
                    stats_per_sprint[sprint_name] = {"ts": sprint_timestamp, "durations": []}
                stats_per_sprint[sprint_name]["durations"].append(diff_days)

    rows_to_upload = []
    for name, data in stats_per_sprint.items():
        avg_days = sum(data["durations"]) / len(data["durations"])
        # Aggiungiamo il valore arrotondato alla riga
        rows_to_upload.append([data["ts"], name, len(data["durations"]), round(avg_days, 2)])

    # Ordina per timestamp
    rows_to_upload.sort(key=lambda x: x[0])

    if rows_to_upload:
        try:
            creds_json = json.loads(os.environ.get("GOOGLE_CREDENTIALS_JSON"))
            creds = Credentials.from_service_account_info(creds_json, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
            client = gspread.authorize(creds)
            
            sh = client.open(SPREADSHEET_NAME)
            worksheet = sh.worksheet(SHEET_NAME)

            # Pulizia e aggiornamento
            worksheet.clear()
            # Header aggiornato con "Giorni"
            header = [["Timestamp", "Sprint Name", "Numero PR", "Media Risoluzione (Giorni)"]]
            worksheet.update(values=header, range_name="A1:D1")
            worksheet.append_rows(rows_to_upload)
            
            print(f"Tabella aggiornata con successo. Metrica convertita in Giorni.")
        except Exception as e:
            print(f"Errore: {e}")

if __name__ == "__main__":
    main()