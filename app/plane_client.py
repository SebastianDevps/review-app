"""
Plane API client — fetch ticket context, post comments, move states.

Uses direct httpx calls against the Plane REST API.
State UUIDs are loaded from review-config.yml (parametrizable per project).
"""

import logging

import httpx

from app.config import settings
from app.review_config import load_config

logger = logging.getLogger(__name__)


class PlaneClient:
    """Minimal Plane API client for code review integration."""

    def __init__(self):
        self.base_url = settings.plane_base_url.rstrip("/")
        self.api_key = settings.plane_api_key
        self.config = load_config()
        self.plane_config = self.config.get("plane", {})

    @property
    def _headers(self) -> dict:
        return {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }

    def _workspace(self) -> str:
        workspace = self.plane_config.get("workspace_slug", "")
        if not workspace:
            raise ValueError("plane.workspace_slug is not set in review-config.yml")
        return workspace

    def _project_id(self) -> str:
        project_id = self.plane_config.get("project_id", "")
        if not project_id:
            raise ValueError("plane.project_id is not set in review-config.yml")
        return project_id

    def get_ticket_context(self, issue_id: str) -> str:
        """
        Fetch a Plane work item and return formatted context for the AI prompt.

        Args:
            issue_id: The numeric sequence ID (e.g. "42" from NELLUP-42).

        Returns:
            Formatted string with title, description, and acceptance criteria.
        """
        workspace = self._workspace()
        project_id = self._project_id()

        try:
            # Search by sequence number
            url = f"{self.base_url}/api/v1/workspaces/{workspace}/projects/{project_id}/issues/"
            with httpx.Client(timeout=10) as client:
                response = client.get(
                    url,
                    headers=self._headers,
                    params={"sequence_id": issue_id},
                )

            if response.status_code != 200:
                logger.warning("Plane search returned %s for issue %s", response.status_code, issue_id)
                return ""

            data = response.json()
            results = data.get("results", [])
            if not results:
                logger.warning("No Plane issue found with sequence_id=%s", issue_id)
                return ""

            issue = results[0]
            title = issue.get("name", "")
            description = issue.get("description_stripped", "") or ""
            state_name = issue.get("state_detail", {}).get("name", "")

            # Cap description to avoid token blowup
            if len(description) > 800:
                description = description[:800] + "..."

            context = f"**Ticket:** {title}\n"
            context += f"**State:** {state_name}\n"
            if description:
                context += f"**Description:** {description}\n"

            logger.info("Loaded Plane context for issue %s: %s", issue_id, title)
            return context

        except Exception as exc:
            logger.warning("Could not fetch Plane ticket %s: %s", issue_id, exc)
            return ""

    def post_comment(self, issue_id: str, comment_body: str) -> None:
        """
        Post a comment on a Plane work item.

        Args:
            issue_id: Numeric sequence ID (e.g. "42").
            comment_body: Markdown comment body.
        """
        workspace = self._workspace()
        project_id = self._project_id()

        # First resolve the sequence_id to the internal UUID
        issue_uuid = self._resolve_issue_uuid(issue_id)
        if not issue_uuid:
            logger.warning("Cannot post comment — issue UUID not found for sequence_id=%s", issue_id)
            return

        url = f"{self.base_url}/api/v1/workspaces/{workspace}/projects/{project_id}/issues/{issue_uuid}/comments/"

        try:
            with httpx.Client(timeout=10) as client:
                response = client.post(
                    url,
                    headers=self._headers,
                    json={"comment_html": f"<p>{comment_body}</p>"},
                )

            if response.status_code in (200, 201):
                logger.info("Posted comment on Plane issue %s", issue_id)
            else:
                logger.warning(
                    "Failed to post Plane comment: %s %s",
                    response.status_code, response.text[:200],
                )
        except Exception as exc:
            logger.warning("Could not post Plane comment for issue %s: %s", issue_id, exc)

    def transition_state(self, issue_id: str, target_state_key: str) -> None:
        """
        Move a Plane work item to a new state.

        Args:
            issue_id: Numeric sequence ID (e.g. "42").
            target_state_key: One of: 'qa_testing', 'code_review', 'refused', 'impediment'.
        """
        states = self.plane_config.get("states", {})
        state_uuid = states.get(target_state_key)

        if not state_uuid:
            logger.warning(
                "No UUID configured for state '%s' in review-config.yml — skipping transition",
                target_state_key,
            )
            return

        workspace = self._workspace()
        project_id = self._project_id()
        issue_uuid = self._resolve_issue_uuid(issue_id)

        if not issue_uuid:
            logger.warning("Cannot transition state — issue UUID not found for sequence_id=%s", issue_id)
            return

        url = f"{self.base_url}/api/v1/workspaces/{workspace}/projects/{project_id}/issues/{issue_uuid}/"

        try:
            with httpx.Client(timeout=10) as client:
                response = client.patch(
                    url,
                    headers=self._headers,
                    json={"state": state_uuid},
                )

            if response.status_code == 200:
                logger.info("Transitioned Plane issue %s → %s", issue_id, target_state_key)
            else:
                logger.warning(
                    "Failed to transition Plane state: %s %s",
                    response.status_code, response.text[:200],
                )
        except Exception as exc:
            logger.warning("Could not transition Plane state for issue %s: %s", issue_id, exc)

    def _resolve_issue_uuid(self, sequence_id: str) -> str | None:
        """Resolve a numeric sequence ID to the internal Plane UUID."""
        workspace = self._workspace()
        project_id = self._project_id()
        url = f"{self.base_url}/api/v1/workspaces/{workspace}/projects/{project_id}/issues/"

        try:
            with httpx.Client(timeout=10) as client:
                response = client.get(
                    url,
                    headers=self._headers,
                    params={"sequence_id": sequence_id},
                )
            results = response.json().get("results", [])
            if results:
                return results[0].get("id")
        except Exception as exc:
            logger.warning("Could not resolve issue UUID: %s", exc)

        return None
