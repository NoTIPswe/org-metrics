#!/usr/bin/env python3
"""
Commit Message Quality Metric

Fetches all commit messages from all repositories in the organization
and calculates the percentage that adhere to the Conventional Commits standard.
Results are appended to the 'commit-message-quality' sheet in the 'notip-dashboard' Google Sheet.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

import gspread
import requests
from google.oauth2.service_account import Credentials

# Conventional Commits pattern
# Format: <type>[optional scope][optional !]: <description>
# Types: feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert
CONVENTIONAL_COMMIT_PATTERN: re.Pattern[str] = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(\(.+\))?"  # optional scope
    r"!?"  # optional breaking change indicator
    r": "  # colon and space
    r".+",  # description
    re.IGNORECASE,
)

LOOKBACK_DAYS: int = 14


class GitHubOrg(TypedDict):
    login: str
    id: int


class GitHubRepo(TypedDict):
    name: str
    full_name: str
    id: int


class GitHubCommitData(TypedDict):
    message: str
    author: dict[str, Any]


class GitHubCommit(TypedDict):
    sha: str
    commit: GitHubCommitData


def get_github_token() -> str:
    """Get GitHub token from environment."""
    token = os.environ.get("ORG_GITHUB_TOKEN")
    if not token:
        raise ValueError("ORG_GITHUB_TOKEN environment variable is not set")
    return token


def get_google_credentials() -> Credentials:
    """Get Google credentials from environment."""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS_JSON environment variable is not set")

    creds_info = json.loads(creds_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    return Credentials.from_service_account_info(creds_info, scopes=scopes)


def get_organization_name(token: str) -> str:
    """Get the organization name from an environment variable."""
    org = os.environ.get("GITHUB_ORG")
    if not org:
        raise ValueError("GITHUB_ORG environment variable is not set")
    return org


def get_all_repos(org: str, token: str) -> list[GitHubRepo]:
    """Fetch all repositories for the organization."""
    headers: dict[str, str] = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    repos: list[GitHubRepo] = []
    page: int = 1
    per_page: int = 100

    while True:
        url: str = f"https://api.github.com/orgs/{org}/repos"
        params: dict[str, int | str] = {
            "page": page,
            "per_page": per_page,
            "type": "all",
        }
        response: requests.Response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()

        page_repos: list[GitHubRepo] = response.json()
        if not page_repos:
            break

        repos.extend(page_repos)
        page += 1

    return repos


def get_commits_since(
    org: str, repo_name: str, token: str, days: int
) -> list[GitHubCommit]:
    """Fetch commits from a repository since the specified number of days ago."""
    headers: dict[str, str] = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    # Get commits from the last N days
    since: str = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    commits: list[GitHubCommit] = []
    page: int = 1
    per_page: int = 100

    while True:
        url: str = f"https://api.github.com/repos/{org}/{repo_name}/commits"
        params: dict[str, int | str] = {
            "page": page,
            "per_page": per_page,
            "since": since,
        }

        try:
            response: requests.Response = requests.get(
                url, headers=headers, params=params
            )
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            # Skip repos with no commits or access issues
            if e.response is not None and e.response.status_code in (404, 409):
                break
            raise

        page_commits: list[GitHubCommit] = response.json()
        if not page_commits:
            break

        commits.extend(page_commits)
        page += 1

    return commits


def is_conventional_commit(message: str) -> bool:
    """Check if a commit message follows the Conventional Commits standard."""
    # Get the first line of the commit message
    first_line: str = message.split("\n")[0].strip()
    return bool(CONVENTIONAL_COMMIT_PATTERN.match(first_line))


def calculate_conventional_commit_percentage(commits: list[GitHubCommit]) -> float:
    """Calculate the percentage of commits that follow Conventional Commits."""
    if not commits:
        return 0.0

    conventional_count: int = sum(
        1
        for commit in commits
        if is_conventional_commit(commit.get("commit", {}).get("message", ""))
    )

    return (conventional_count / len(commits)) * 100


def append_to_google_sheet(percentage: float, credentials: Credentials) -> None:
    """Append the percentage and timestamp to the Google Sheet."""
    client: gspread.Client = gspread.authorize(credentials)

    # Open the spreadsheet by name
    spreadsheet: gspread.Spreadsheet = client.open("notip-dashboard")

    # Get or create the worksheet
    worksheet: gspread.Worksheet
    try:
        worksheet = spreadsheet.worksheet("commit-message-quality")
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title="commit-message-quality", rows=1000, cols=2
        )
        # Add headers
        worksheet.update("A1:B1", [["Timestamp", "Percentage"]])

    # Get the current timestamp
    timestamp: str = datetime.now(timezone.utc).isoformat()

    # Append the new row
    worksheet.append_row([timestamp, round(percentage, 2)])
    print(f"Appended to sheet: {timestamp}, {percentage:.2f}%")


def main() -> None:
    """Main function to run the commit message quality check."""
    print("Starting commit message quality check...")

    # Get credentials
    token: str = get_github_token()
    google_creds: Credentials = get_google_credentials()

    # Get organization name
    org: str = get_organization_name(token)
    print(f"Organization: {org}")

    # Get all repositories
    repos: list[GitHubRepo] = get_all_repos(org, token)
    print(f"Found {len(repos)} repositories")

    # Collect all commits from all repos
    all_commits: list[GitHubCommit] = []
    for repo in repos:
        repo_name: str = repo["name"]
        commits: list[GitHubCommit] = get_commits_since(
            org, repo_name, token, LOOKBACK_DAYS
        )
        if commits:
            print(f"  {repo_name}: {len(commits)} commits")
            all_commits.extend(commits)

    print(f"Total commits in last {LOOKBACK_DAYS} days: {len(all_commits)}")

    # Calculate the percentage
    percentage: float = calculate_conventional_commit_percentage(all_commits)
    print(f"Conventional commits percentage: {percentage:.2f}%")

    # Append to Google Sheet
    append_to_google_sheet(percentage, google_creds)

    print("Done!")


if __name__ == "__main__":
    main()
