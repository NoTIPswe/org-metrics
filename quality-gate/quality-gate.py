#!/usr/bin/env python3
"""
MP19 - Quality Gate Pass Rate Metric (Multi-Repo)

Fetches workflow runs for ALL repositories in the organization
and calculates the percentage of successful runs.
"""

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, TypedDict, List, Tuple

import gspread
import requests
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

SPREADSHEET_NAME = "notip-dashboard"
SHEET_NAME = "mp19-quality-gate"

# --- TYPE DEFINITIONS ---

class GitHubRepo(TypedDict):
    name: str
    id: int

class GitHubWorkflowRun(TypedDict):
    id: int
    name: str
    status: str
    conclusion: str
    created_at: str

class WorkflowRunsResponse(TypedDict):
    total_count: int
    workflow_runs: List[GitHubWorkflowRun]

# --- HELPER FUNCTIONS ---

def get_env_var(name: str, default: str = "") -> str:
    val = os.environ.get(name, default)
    if not val and not default:
        raise ValueError(f"Environment variable '{name}' is not set")
    return val

def get_google_credentials() -> Credentials:
    creds_json = get_env_var("GOOGLE_CREDENTIALS_JSON")
    try:
        creds_info = json.loads(creds_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in GOOGLE_CREDENTIALS_JSON: {e}")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    return Credentials.from_service_account_info(creds_info, scopes=scopes)

def fetch_organization_repos(org: str, token: str) -> List[GitHubRepo]:
    """Fetch all repositories in the organization."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    
    repos: List[GitHubRepo] = []
    page = 1
    per_page = 100
    
    print(f"Fetching repository list for {org}...")

    while True:
        url = f"https://api.github.com/orgs/{org}/repos"
        params = {"type": "all", "per_page": per_page, "page": page}
        
        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            if not data:
                break
                
            repos.extend(data)
            page += 1
        except requests.exceptions.RequestException as e:
            print(f"Error fetching repos: {e}")
            break
            
    print(f"Found {len(repos)} repositories.")
    return repos

def fetch_all_workflow_runs(org: str, repo: str, token: str) -> List[GitHubWorkflowRun]:
    """Fetch ALL completed workflow runs from a specific repo."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    
    all_runs: List[GitHubWorkflowRun] = []
    page = 1
    per_page = 100
    
    # print(f"  Fetching runs for {repo}...") # Decommenta per debug dettagliato

    while True:
        url = f"https://api.github.com/repos/{org}/{repo}/actions/runs"
        params = {"status": "completed", "per_page": per_page, "page": page}

        try:
            response = requests.get(url, headers=headers, params=params)
            if response.status_code == 404: # Repo senza actions o non accessibile
                return []
            response.raise_for_status()
            
            data: WorkflowRunsResponse = response.json()
            runs = data.get("workflow_runs", [])
            
            if not runs:
                break
                
            all_runs.extend(runs)
            page += 1
            
        except requests.exceptions.RequestException as e:
            # print(f"    Error fetching runs for {repo}: {e}")
            break

    return all_runs

def calculate_pass_rate(runs: List[GitHubWorkflowRun]) -> Tuple[int, int, float]:
    total = len(runs)
    if total == 0:
        return 0, 0, 0.0
    passed = sum(1 for run in runs if run.get("conclusion") == "success")
    rate = (passed / total) * 100
    return total, passed, rate

def append_to_google_sheet(
    repo_name: str, 
    total: int, 
    passed: int, 
    rate: float, 
    credentials: Credentials
) -> None:
    client = gspread.authorize(credentials)
    spreadsheet = client.open(SPREADSHEET_NAME)

    try:
        worksheet = spreadsheet.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=SHEET_NAME, rows=1000, cols=5)
        worksheet.update("A1:E1", [["Timestamp", "Repo", "Total Gates", "Passed Gates", "Pass Rate (%)"]])

    timestamp = datetime.now(timezone.utc).isoformat()
    worksheet.append_row([timestamp, repo_name, total, passed, round(rate, 2)])
    print(f"  [SAVED] {repo_name}: {rate:.2f}% ({passed}/{total})")
    # Piccolo sleep per evitare di colpire il rate limit di Google Sheets in scrittura
    time.sleep(1) 

def main() -> None:
    print("--- Starting MP19 Multi-Repo Check ---")

    token = get_env_var("ORG_GITHUB_TOKEN")
    org = get_env_var("GITHUB_ORG")
    google_creds = get_google_credentials()

    # 1. Recupera TUTTE le repo dell'organizzazione
    repos = fetch_organization_repos(org, token)

    # 2. Cicla su ogni repo
    for repo_data in repos:
        repo_name = repo_data["name"]
        
        # 3. Scarica le run per quella specifica repo
        runs = fetch_all_workflow_runs(org, repo_name, token)
        
        # 4. Calcola
        total, passed, rate = calculate_pass_rate(runs)
        
        if total > 0:
            # 5. Salva (solo se ci sono dati)
            append_to_google_sheet(repo_name, total, passed, rate, google_creds)
        else:
            print(f"  [SKIP]  {repo_name}: No runs found.")

    print("--- Done! ---")

if __name__ == "__main__":
    main()