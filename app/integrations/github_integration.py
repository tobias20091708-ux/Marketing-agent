"""
GitHub integration — PRs, deployments, commits, webhooks.
"""
import structlog
from github import Github
from app.config import settings

log = structlog.get_logger()


class GitHubClient:
    def __init__(self):
        self._gh = None

    def _get_client(self):
        if self._gh:
            return self._gh
        if not settings.github_token:
            return None
        self._gh = Github(settings.github_token)
        return self._gh

    async def get_open_prs(self, repo: str = None) -> list[dict]:
        """Get open pull requests."""
        gh = self._get_client()
        if not gh:
            return []
        try:
            if repo:
                repos = [gh.get_repo(repo)]
            elif settings.github_org:
                org = gh.get_organization(settings.github_org)
                repos = list(org.get_repos(type="all"))[:10]
            else:
                repos = list(gh.get_user().get_repos())[:10]

            prs = []
            for r in repos:
                for pr in r.get_pulls(state="open"):
                    prs.append({
                        "repo": r.full_name,
                        "number": pr.number,
                        "title": pr.title,
                        "author": pr.user.login,
                        "description": pr.body or "",
                        "files_changed": pr.changed_files,
                        "created_at": pr.created_at.isoformat(),
                        "reviewed": bool(list(pr.get_reviews())),
                    })
            return prs
        except Exception as e:
            log.error("github.prs_failed", error=str(e))
            return []

    async def get_pr_diff(self, repo: str, pr_number: int) -> str:
        """Get the diff for a PR."""
        gh = self._get_client()
        if not gh:
            return ""
        try:
            r = gh.get_repo(repo)
            pr = r.get_pull(pr_number)
            files = pr.get_files()
            diff_parts = []
            for f in files:
                diff_parts.append(f"--- {f.filename}\n{f.patch or ''}")
            return "\n\n".join(diff_parts)[:10000]
        except Exception as e:
            log.error("github.diff_failed", error=str(e))
            return ""

    async def post_review(self, repo: str, pr_number: int, body: str, event: str = "COMMENT"):
        """Post a review on a PR."""
        gh = self._get_client()
        if not gh:
            return
        try:
            r = gh.get_repo(repo)
            pr = r.get_pull(pr_number)
            pr.create_review(body=body, event=event)
        except Exception as e:
            log.error("github.review_failed", error=str(e))

    async def get_recent_deployments(self) -> list[dict]:
        """Get recent deployments across repos."""
        gh = self._get_client()
        if not gh:
            return []
        try:
            if not settings.github_org:
                return []
            org = gh.get_organization(settings.github_org)
            deployments = []
            for repo in list(org.get_repos())[:5]:
                for dep in repo.get_deployments()[:3]:
                    statuses = list(dep.get_statuses())
                    status = statuses[0].state if statuses else "unknown"
                    deployments.append({
                        "repo": repo.full_name,
                        "ref": dep.ref,
                        "status": status,
                        "created_at": dep.created_at.isoformat(),
                    })
            return deployments
        except Exception as e:
            log.error("github.deployments_failed", error=str(e))
            return []


github_client = GitHubClient()
