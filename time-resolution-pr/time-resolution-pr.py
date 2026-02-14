import os
from datetime import datetime, timezone
import requests

TARGET_REPO = "RepoDocumentale"

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
    
    print(f"Recupero dati da {repo_name}...")
    
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

def main():
    token = get_env_var("ORG_GITHUB_TOKEN")
    org = get_env_var("GITHUB_ORG")

    print(f"--- Analisi PR Resolution Time TOTALE (Senza Limiti) ---")
    
    durations = get_pr_stats(org, TARGET_REPO, token)

    if not durations:
        print(f"Nessuna PR mergiata trovata in '{TARGET_REPO}'.")
        return

    avg_hours = (sum(durations) / len(durations)) / 3600

    print("\n--- RISULTATO STORICO ---")
    print(f"Totale PR analizzate: {len(durations)}")
    print(f"Media storica risoluzione: {avg_hours:.2f} ore")

if __name__ == "__main__":
    main()