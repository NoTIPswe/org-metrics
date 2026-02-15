import os
import json
import gspread
import requests
from datetime import datetime, timezone
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

TARGET_REPO = "RepoDocumentale"
SPREADSHEET_NAME = "notip-dashboard"
SHEET_NAME = "time-resolution-pr"

def get_env_var(name):
    val = os.environ.get(name)
    if not val:
        raise ValueError(f"Ambiente non configurato: {name} manca.")
    return val

def get_pr_stats(org, repo_name, token):
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    url = f"https://api.github.com/repos/{org}/{repo_name}/pulls"
    durations = []
    page = 1
    
    print(f"Recupero dati da GitHub: {repo_name}...")
    
    while True:
        params = {
            "state": "closed", 
            "per_page": 100,
            "page": page
        }
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        
        data = response.json()
        if not data:
            break
            
        for pr in data:
            if pr.get("merged_at"):
                merged_at = datetime.fromisoformat(pr["merged_at"].replace("Z", "+00:00"))
                created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
                
                diff_seconds = (merged_at - created_at).total_seconds()
                durations.append(diff_seconds)
        
        page += 1
                
    return durations

def upload_to_sheets(avg_hours, pr_count, credentials):
    """Carica i risultati della PR Resolution sul foglio Google."""
    client = gspread.authorize(credentials)
    spreadsheet = client.open(SPREADSHEET_NAME)

    try:
        worksheet = spreadsheet.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=SHEET_NAME, rows=1000, cols=3)
        worksheet.update(values=[["Timestamp", "Numero PR", "Media Risoluzione (Ore)"]], range_name="A1:C1")

    timestamp = datetime.now(timezone.utc).isoformat()
    worksheet.append_row([timestamp, pr_count, round(avg_hours, 2)])
    print(f"Dati inviati a Google Sheets nel foglio '{SHEET_NAME}'.")

def main():
    token = get_env_var("ORG_GITHUB_TOKEN")
    org = get_env_var("GITHUB_ORG")
    
    # 1. Recupero dati GitHub
    durations = get_pr_stats(org, TARGET_REPO, token)

    if not durations:
        print(f"Nessuna PR mergiata trovata.")
        return

    avg_hours = (sum(durations) / len(durations)) / 3600
    pr_count = len(durations)

    print(f"\nRisultato: {pr_count} PR analizzate. Media: {avg_hours:.2f} ore.")

    # 2. Caricamento Google Sheets
    try:
        creds_json = json.loads(get_env_var("GOOGLE_CREDENTIALS_JSON"))
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
        upload_to_sheets(avg_hours, pr_count, creds)
    except Exception as e:
        print(f"Errore caricamento Sheets: {e}")

if __name__ == "__main__":
    main()