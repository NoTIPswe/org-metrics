import os
from datetime import datetime, timezone
import requests

LOOKBACK_DAYS = 14

def get_env_var(name):
    val = os.environ.get(name)
    if not val:
        raise ValueError(f"Ambiente non configurato: {name} manca.")
    return val

def get_all_repos(org, token):
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
    repos = []
    page = 1
    while True:
        res = requests.get(f"https://api.github.com/orgs/{org}/repos", 
                           headers=headers, params={"page": page, "per_page": 100})
        res.raise_for_status()
        data = res.json()
        if not data: break
        repos.extend(data)
        page += 1
    return repos

def get_pr_stats(org, repo_name, token):
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
    url = f"https://api.github.com/repos/{org}/{repo_name}/pulls"
    params = {"state": "closed", "sort": "updated", "direction": "desc", "per_page": 100}
    
    res = requests.get(url, headers=headers, params=params)
    if res.status_code != 200: return []
    
    durations = []
    now = datetime.now(timezone.utc)
    
    for pr in res.json():
        if pr.get("merged_at"):
            merged_at = datetime.fromisoformat(pr["merged_at"].replace("Z", "+00:00"))
            created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
            
            # Filtro per le ultime 2 settimane
            if (now - merged_at).days <= LOOKBACK_DAYS:
                diff = (merged_at - created_at).total_seconds()
                durations.append(diff)
                
    return durations

def main():
    token = get_env_var("ORG_GITHUB_TOKEN")
    org = get_env_var("GITHUB_ORG")
    
    print(f"--- Analisi PR Organizzazione: {org} ---")
    repos = get_all_repos(org, token)
    
    total_durations = []
    
    for repo in repos:
        name = repo['name']
        durations = get_pr_stats(org, name, token)
        
        if durations:
            avg_repo = (sum(durations) / len(durations)) / 3600
            print(f"Repo: {name:20} | PR Risolte: {len(durations):3} | Media: {avg_repo:.2f} ore")
            total_durations.extend(durations)
        else:
            print(f"Repo: {name:20} | Nessuna PR risolta nel periodo.")

    print("\n--- RISULTATO FINALE ---")
    if total_durations:
        final_avg = (sum(total_durations) / len(total_durations)) / 3600
        print(f"Media Totale Organizzazione: {final_avg:.2f} ore")
        print(f"Totale PR analizzate: {len(total_durations)}")
    else:
        print("Nessun dato disponibile per il periodo selezionato.")

if __name__ == "__main__":
    main()