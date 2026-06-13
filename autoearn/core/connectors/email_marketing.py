"""Email marketing platform connectors.

Platforms: Mailchimp, ConvertKit, ActiveCampaign, Klaviyo, Brevo (Sendinblue),
Mailgun, GetResponse.  Each exposes subscriber-management and campaign-sending
methods and returns :class:`ConnectorResult` instances.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import requests

from .base import Connector, ConnectorResult, register


# ---------------------------------------------------------------------------
# Mailchimp
# ---------------------------------------------------------------------------

@register
class MailchimpConnector(Connector):
    name = "mailchimp"
    label = "Mailchimp"
    config_section = "mailchimp"
    required_keys = ("api_key", "list_id")
    capabilities = ("email_marketing",)

    def _base(self, cfg: dict) -> str:
        dc = cfg["api_key"].split("-")[-1]
        return f"https://{dc}.api.mailchimp.com/3.0"

    def _auth(self, cfg: dict) -> tuple[str, str]:
        return ("anystring", cfg["api_key"])

    def add_subscriber(
        self, email: str, name: str = "", tags: list[str] | None = None
    ) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        tags = tags or []
        list_id = cfg["list_id"]
        payload: dict[str, Any] = {
            "email_address": email,
            "status": "subscribed",
        }
        if name:
            parts = name.split(" ", 1)
            payload["merge_fields"] = {
                "FNAME": parts[0],
                "LNAME": parts[1] if len(parts) > 1 else "",
            }
        if tags:
            payload["tags"] = tags
        resp = requests.post(
            f"{self._base(cfg)}/lists/{list_id}/members",
            auth=self._auth(cfg),
            json=payload,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            return ConnectorResult(
                ok=True,
                message=f"Subscribed {email} to Mailchimp list.",
                data=resp.json(),
            )
        # 400 with title "Member Exists" is a soft conflict
        body = resp.json()
        title = body.get("title", "")
        if title == "Member Exists":
            return ConnectorResult(ok=True, message=f"{email} already subscribed.", data=body)
        return ConnectorResult(
            ok=False,
            message=f"Mailchimp add_subscriber error {resp.status_code}: {body.get('detail', resp.text[:160])}",
        )

    def remove_subscriber(self, email: str) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        list_id = cfg["list_id"]
        subscriber_hash = hashlib.md5(email.lower().encode()).hexdigest()
        resp = requests.patch(
            f"{self._base(cfg)}/lists/{list_id}/members/{subscriber_hash}",
            auth=self._auth(cfg),
            json={"status": "unsubscribed"},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Mailchimp remove_subscriber error {resp.status_code}: {resp.text[:160]}",
            )
        return ConnectorResult(ok=True, message=f"Unsubscribed {email} from Mailchimp.", data=resp.json())

    def send_campaign(
        self, subject: str, content: str, list_id: str = ""
    ) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        target_list = list_id or cfg["list_id"]
        # Step 1: create campaign
        create_resp = requests.post(
            f"{self._base(cfg)}/campaigns",
            auth=self._auth(cfg),
            json={
                "type": "regular",
                "recipients": {"list_id": target_list},
                "settings": {
                    "subject_line": subject,
                    "from_name": cfg.get("from_name", "AutoEarn"),
                    "reply_to": cfg.get("reply_to", "noreply@autoearn.ai"),
                },
            },
            timeout=30,
        )
        if create_resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Mailchimp create campaign error {create_resp.status_code}: {create_resp.text[:160]}",
            )
        campaign_id = create_resp.json()["id"]
        # Step 2: set content
        content_resp = requests.put(
            f"{self._base(cfg)}/campaigns/{campaign_id}/content",
            auth=self._auth(cfg),
            json={"html": content},
            timeout=30,
        )
        if content_resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Mailchimp set content error {content_resp.status_code}: {content_resp.text[:160]}",
            )
        # Step 3: send
        send_resp = requests.post(
            f"{self._base(cfg)}/campaigns/{campaign_id}/actions/send",
            auth=self._auth(cfg),
            timeout=30,
        )
        if send_resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Mailchimp send error {send_resp.status_code}: {send_resp.text[:160]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Mailchimp campaign {campaign_id} sent.",
            data={"campaign_id": campaign_id},
        )

    def get_stats(self, campaign_id: str) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._base(cfg)}/reports/{campaign_id}",
            auth=self._auth(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Mailchimp get_stats error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"Campaign stats: opens={data.get('opens', {}).get('unique_opens')}, "
                    f"clicks={data.get('clicks', {}).get('unique_clicks')}",
            data=data,
        )

    def get_subscribers(self, limit: int = 100) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        list_id = cfg["list_id"]
        resp = requests.get(
            f"{self._base(cfg)}/lists/{list_id}/members",
            auth=self._auth(cfg),
            params={"count": limit, "status": "subscribed"},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Mailchimp get_subscribers error {resp.status_code}: {resp.text[:160]}",
            )
        members = resp.json().get("members", [])
        return ConnectorResult(
            ok=True,
            message=f"{len(members)} subscribers fetched.",
            data={"members": members, "total": resp.json().get("total_items", len(members))},
        )


# ---------------------------------------------------------------------------
# ConvertKit
# ---------------------------------------------------------------------------

@register
class ConvertKitConnector(Connector):
    name = "convertkit"
    label = "ConvertKit"
    config_section = "convertkit"
    required_keys = ("api_key", "api_secret", "form_id")
    capabilities = ("email_marketing",)

    _BASE = "https://api.convertkit.com/v3"

    def add_subscriber(
        self, email: str, first_name: str = "", tags: list[str] | None = None
    ) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        form_id = cfg["form_id"]
        payload: dict[str, Any] = {
            "api_key": cfg["api_key"],
            "email": email,
        }
        if first_name:
            payload["first_name"] = first_name
        if tags:
            payload["tags"] = tags
        resp = requests.post(
            f"{self._BASE}/forms/{form_id}/subscribe",
            json=payload,
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"ConvertKit add_subscriber error {resp.status_code}: {resp.text[:160]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Subscribed {email} via ConvertKit form {form_id}.",
            data=resp.json(),
        )

    def remove_subscriber(self, email: str) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        # Look up subscriber id first
        search = requests.get(
            f"{self._BASE}/subscribers",
            params={"api_secret": cfg["api_secret"], "email_address": email},
            timeout=30,
        )
        if search.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"ConvertKit subscriber lookup error {search.status_code}: {search.text[:160]}",
            )
        subscribers = search.json().get("subscribers", [])
        if not subscribers:
            return ConnectorResult(ok=False, message=f"ConvertKit: no subscriber found for {email}.")
        sub_id = subscribers[0]["id"]
        resp = requests.put(
            f"{self._BASE}/subscribers/{sub_id}/unsubscribe",
            json={"api_secret": cfg["api_secret"]},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"ConvertKit unsubscribe error {resp.status_code}: {resp.text[:160]}",
            )
        return ConnectorResult(ok=True, message=f"Unsubscribed {email} from ConvertKit.", data=resp.json())

    def get_sequences(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/sequences",
            params={"api_key": cfg["api_key"]},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"ConvertKit get_sequences error {resp.status_code}: {resp.text[:160]}",
            )
        seqs = resp.json().get("courses", [])
        return ConnectorResult(
            ok=True,
            message=f"{len(seqs)} sequences found.",
            data={"sequences": seqs},
        )

    def get_stats(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/subscribers",
            params={"api_secret": cfg["api_secret"]},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"ConvertKit get_stats error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        total = data.get("total_subscribers", 0)
        return ConnectorResult(
            ok=True,
            message=f"ConvertKit: {total} total subscribers.",
            data={"total_subscribers": total},
        )


# ---------------------------------------------------------------------------
# ActiveCampaign
# ---------------------------------------------------------------------------

@register
class ActiveCampaignConnector(Connector):
    name = "activecampaign"
    label = "ActiveCampaign"
    config_section = "activecampaign"
    required_keys = ("api_url", "api_key")
    capabilities = ("email_marketing",)

    def _headers(self, cfg: dict) -> dict[str, str]:
        return {"Api-Token": cfg["api_key"], "Content-Type": "application/json"}

    def _base(self, cfg: dict) -> str:
        return cfg["api_url"].rstrip("/") + "/api/3"

    def add_contact(
        self,
        email: str,
        first_name: str = "",
        last_name: str = "",
        tags: list[str] | None = None,
    ) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        contact: dict[str, Any] = {"email": email}
        if first_name:
            contact["firstName"] = first_name
        if last_name:
            contact["lastName"] = last_name
        resp = requests.post(
            f"{self._base(cfg)}/contacts",
            headers=self._headers(cfg),
            json={"contact": contact},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"ActiveCampaign add_contact error {resp.status_code}: {resp.text[:160]}",
            )
        contact_data = resp.json().get("contact", {})
        contact_id = contact_data.get("id")
        # Add tags if provided
        tag_errors: list[str] = []
        for tag_name in (tags or []):
            # create/get tag
            tag_resp = requests.post(
                f"{self._base(cfg)}/tags",
                headers=self._headers(cfg),
                json={"tag": {"tag": tag_name, "tagType": "contact"}},
                timeout=30,
            )
            if tag_resp.status_code < 400:
                tag_id = tag_resp.json().get("tag", {}).get("id")
                if tag_id and contact_id:
                    requests.post(
                        f"{self._base(cfg)}/contactTags",
                        headers=self._headers(cfg),
                        json={"contactTag": {"contact": contact_id, "tag": tag_id}},
                        timeout=30,
                    )
            else:
                tag_errors.append(tag_name)
        msg = f"Added contact {email} to ActiveCampaign (id={contact_id})."
        if tag_errors:
            msg += f" Tag errors: {tag_errors}"
        return ConnectorResult(ok=True, message=msg, data=contact_data)

    def add_to_list(self, email: str, list_id: str) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        # Resolve contact id
        search = requests.get(
            f"{self._base(cfg)}/contacts",
            headers=self._headers(cfg),
            params={"email": email},
            timeout=30,
        )
        contacts = search.json().get("contacts", []) if search.status_code < 400 else []
        if not contacts:
            return ConnectorResult(ok=False, message=f"ActiveCampaign: contact {email} not found.")
        contact_id = contacts[0]["id"]
        resp = requests.post(
            f"{self._base(cfg)}/contactLists",
            headers=self._headers(cfg),
            json={"contactList": {"list": list_id, "contact": contact_id, "status": 1}},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"ActiveCampaign add_to_list error {resp.status_code}: {resp.text[:160]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Added contact {email} to list {list_id}.",
            data=resp.json(),
        )

    def create_campaign(
        self,
        subject: str,
        body: str,
        list_id: str,
        from_name: str,
        from_email: str,
    ) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        # Create message first
        msg_resp = requests.post(
            f"{self._base(cfg)}/messages",
            headers=self._headers(cfg),
            json={
                "message": {
                    "name": subject[:100],
                    "subject": subject,
                    "fromemail": from_email,
                    "fromname": from_name,
                    "reply2": from_email,
                    "html": body,
                    "text": "",
                    "format": "html",
                }
            },
            timeout=30,
        )
        if msg_resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"ActiveCampaign create message error {msg_resp.status_code}: {msg_resp.text[:160]}",
            )
        message_id = msg_resp.json().get("message", {}).get("id")
        # Create campaign
        camp_resp = requests.post(
            f"{self._base(cfg)}/campaigns",
            headers=self._headers(cfg),
            json={
                "campaign": {
                    "type": "single",
                    "name": subject[:100],
                    "sdate": "",
                    "status": 0,
                    "public": 0,
                    "tracklinks": "all",
                    "trackreads": 1,
                    "segmentid": 0,
                    "lists": [{"id": list_id}],
                    "messages": [{"id": message_id}],
                }
            },
            timeout=30,
        )
        if camp_resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"ActiveCampaign create_campaign error {camp_resp.status_code}: {camp_resp.text[:160]}",
            )
        camp_data = camp_resp.json().get("campaign", {})
        return ConnectorResult(
            ok=True,
            message=f"ActiveCampaign campaign created (id={camp_data.get('id')}).",
            data=camp_data,
        )

    def send_campaign(self, campaign_id: str) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.put(
            f"{self._base(cfg)}/campaigns/{campaign_id}",
            headers=self._headers(cfg),
            json={"campaign": {"status": 1}},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"ActiveCampaign send_campaign error {resp.status_code}: {resp.text[:160]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"ActiveCampaign campaign {campaign_id} scheduled for sending.",
            data=resp.json().get("campaign", {}),
        )


# ---------------------------------------------------------------------------
# Klaviyo
# ---------------------------------------------------------------------------

@register
class KlaviyoConnector(Connector):
    name = "klaviyo"
    label = "Klaviyo"
    config_section = "klaviyo"
    required_keys = ("api_key", "list_id")
    capabilities = ("email_marketing",)

    _BASE = "https://a.klaviyo.com/api"

    def _headers(self, cfg: dict) -> dict[str, str]:
        return {
            "Authorization": f"Klaviyo-API-Key {cfg['api_key']}",
            "Content-Type": "application/json",
            "revision": "2023-12-15",
        }

    def add_profile(self, email: str, properties: dict[str, Any] | None = None) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        attributes: dict[str, Any] = {"email": email}
        if properties:
            attributes.update(properties)
        resp = requests.post(
            f"{self._BASE}/profiles/",
            headers=self._headers(cfg),
            json={"data": {"type": "profile", "attributes": attributes}},
            timeout=30,
        )
        if resp.status_code in (200, 201):
            profile_id = resp.json().get("data", {}).get("id", "")
            return ConnectorResult(
                ok=True,
                message=f"Klaviyo profile created for {email} (id={profile_id}).",
                data=resp.json().get("data", {}),
            )
        if resp.status_code == 409:
            # Already exists — extract id from conflict
            dup = resp.json().get("errors", [{}])[0]
            dup_id = dup.get("meta", {}).get("duplicate_profile_id", "")
            return ConnectorResult(
                ok=True,
                message=f"Klaviyo profile already exists for {email} (id={dup_id}).",
                data={"id": dup_id},
            )
        return ConnectorResult(
            ok=False,
            message=f"Klaviyo add_profile error {resp.status_code}: {resp.text[:160]}",
        )

    def add_to_list(self, email: str) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        list_id = cfg["list_id"]
        # Subscribe profile to list
        resp = requests.post(
            f"{self._BASE}/lists/{list_id}/relationships/profiles/",
            headers=self._headers(cfg),
            json={"data": [{"type": "profile", "attributes": {"email": email}}]},
            timeout=30,
        )
        if resp.status_code in (200, 201, 204):
            return ConnectorResult(ok=True, message=f"Added {email} to Klaviyo list {list_id}.")
        return ConnectorResult(
            ok=False,
            message=f"Klaviyo add_to_list error {resp.status_code}: {resp.text[:160]}",
        )

    def send_email_campaign(self, subject: str, content: str) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        # Create campaign
        camp_resp = requests.post(
            f"{self._BASE}/campaigns/",
            headers=self._headers(cfg),
            json={
                "data": {
                    "type": "campaign",
                    "attributes": {
                        "name": subject[:100],
                        "audiences": {"included": [cfg["list_id"]]},
                        "send_options": {"use_smart_sending": True},
                        "tracking_options": {"is_tracking_opens": True, "is_tracking_clicks": True},
                        "send_strategy": {"method": "immediate"},
                    },
                }
            },
            timeout=30,
        )
        if camp_resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Klaviyo create campaign error {camp_resp.status_code}: {camp_resp.text[:160]}",
            )
        campaign_id = camp_resp.json().get("data", {}).get("id", "")
        # Create message for campaign
        msg_resp = requests.post(
            f"{self._BASE}/campaign-messages/",
            headers=self._headers(cfg),
            json={
                "data": {
                    "type": "campaign-message",
                    "attributes": {
                        "channel": "email",
                        "label": subject[:100],
                        "content": {"subject": subject, "html_body": content},
                    },
                    "relationships": {
                        "campaign": {"data": {"type": "campaign", "id": campaign_id}}
                    },
                }
            },
            timeout=30,
        )
        if msg_resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Klaviyo create message error {msg_resp.status_code}: {msg_resp.text[:160]}",
            )
        # Send
        send_resp = requests.post(
            f"{self._BASE}/campaign-send-jobs/",
            headers=self._headers(cfg),
            json={"data": {"type": "campaign-send-job", "attributes": {"campaign_id": campaign_id}}},
            timeout=30,
        )
        if send_resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Klaviyo send campaign error {send_resp.status_code}: {send_resp.text[:160]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Klaviyo campaign {campaign_id} send job created.",
            data={"campaign_id": campaign_id},
        )


# ---------------------------------------------------------------------------
# Brevo (formerly Sendinblue)
# ---------------------------------------------------------------------------

@register
class BrevoConnector(Connector):
    name = "brevo"
    label = "Brevo"
    config_section = "brevo"
    required_keys = ("api_key", "sender_email", "sender_name")
    capabilities = ("email_marketing",)

    _BASE = "https://api.brevo.com/v3"

    def _headers(self, cfg: dict) -> dict[str, str]:
        return {"api-key": cfg["api_key"], "Content-Type": "application/json", "Accept": "application/json"}

    def send_transactional(self, to_email: str, subject: str, html: str) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            f"{self._BASE}/smtp/email",
            headers=self._headers(cfg),
            json={
                "sender": {"email": cfg["sender_email"], "name": cfg["sender_name"]},
                "to": [{"email": to_email}],
                "subject": subject,
                "htmlContent": html,
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Brevo send_transactional error {resp.status_code}: {resp.text[:160]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Brevo transactional email sent to {to_email}.",
            data=resp.json(),
        )

    def create_contact(self, email: str, attributes: dict[str, Any] | None = None) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        payload: dict[str, Any] = {"email": email}
        if attributes:
            payload["attributes"] = attributes
        resp = requests.post(
            f"{self._BASE}/contacts",
            headers=self._headers(cfg),
            json=payload,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            return ConnectorResult(
                ok=True,
                message=f"Brevo contact created for {email}.",
                data=resp.json(),
            )
        if resp.status_code == 400:
            body = resp.json()
            if body.get("code") == "duplicate_parameter":
                return ConnectorResult(ok=True, message=f"Brevo: {email} already exists.", data=body)
        return ConnectorResult(
            ok=False,
            message=f"Brevo create_contact error {resp.status_code}: {resp.text[:160]}",
        )

    def add_to_list(self, email: str, list_id: int) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            f"{self._BASE}/contacts/lists/{list_id}/contacts/add",
            headers=self._headers(cfg),
            json={"emails": [email]},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Brevo add_to_list error {resp.status_code}: {resp.text[:160]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Added {email} to Brevo list {list_id}.",
            data=resp.json() if resp.text else {},
        )


# ---------------------------------------------------------------------------
# Mailgun
# ---------------------------------------------------------------------------

@register
class MailgunConnector(Connector):
    name = "mailgun"
    label = "Mailgun"
    config_section = "mailgun"
    required_keys = ("api_key", "domain")
    capabilities = ("email_marketing",)

    def _base(self, cfg: dict) -> str:
        region = cfg.get("region", "us")
        if region == "eu":
            return "https://api.eu.mailgun.net/v3"
        return "https://api.mailgun.net/v3"

    def send(
        self,
        to: str,
        subject: str,
        html: str,
        text: str = "",
        from_email: str = "",
    ) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        from_addr = from_email or f"AutoEarn <mailgun@{cfg['domain']}>"
        data: dict[str, str] = {
            "from": from_addr,
            "to": to,
            "subject": subject,
            "html": html,
        }
        if text:
            data["text"] = text
        resp = requests.post(
            f"{self._base(cfg)}/{cfg['domain']}/messages",
            auth=("api", cfg["api_key"]),
            data=data,
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Mailgun send error {resp.status_code}: {resp.text[:160]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Mailgun email sent to {to}.",
            data=resp.json(),
        )

    def get_stats(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._base(cfg)}/{cfg['domain']}/stats/total",
            auth=("api", cfg["api_key"]),
            params={"event": ["accepted", "delivered", "opened", "clicked"]},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Mailgun get_stats error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        return ConnectorResult(ok=True, message="Mailgun stats retrieved.", data=data)

    def add_to_mailing_list(self, email: str, address: str) -> ConnectorResult:
        """Add an email to a Mailgun mailing list.

        Args:
            email: subscriber email address.
            address: the mailing list address (e.g. list@yourdomain.com).
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            f"{self._base(cfg)}/lists/{address}/members",
            auth=("api", cfg["api_key"]),
            data={"subscribed": "true", "address": email},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Mailgun add_to_mailing_list error {resp.status_code}: {resp.text[:160]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Added {email} to Mailgun list {address}.",
            data=resp.json(),
        )


# ---------------------------------------------------------------------------
# GetResponse
# ---------------------------------------------------------------------------

@register
class GetResponseConnector(Connector):
    name = "getresponse"
    label = "GetResponse"
    config_section = "getresponse"
    required_keys = ("api_key", "campaign_id")
    capabilities = ("email_marketing",)

    _BASE = "https://api.getresponse.com/v3"

    def _headers(self, cfg: dict) -> dict[str, str]:
        return {"X-Auth-Token": f"api-key {cfg['api_key']}", "Content-Type": "application/json"}

    def add_contact(self, email: str, name: str = "") -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        payload: dict[str, Any] = {
            "email": email,
            "campaign": {"campaignId": cfg["campaign_id"]},
        }
        if name:
            payload["name"] = name
        resp = requests.post(
            f"{self._BASE}/contacts",
            headers=self._headers(cfg),
            json=payload,
            timeout=30,
        )
        if resp.status_code in (202, 200, 201):
            return ConnectorResult(
                ok=True,
                message=f"Added {email} to GetResponse campaign.",
                data=resp.json() if resp.text else {},
            )
        if resp.status_code == 400:
            body = resp.json()
            if body.get("httpStatus") == 400 and "already" in body.get("message", "").lower():
                return ConnectorResult(ok=True, message=f"GetResponse: {email} already in campaign.", data=body)
        return ConnectorResult(
            ok=False,
            message=f"GetResponse add_contact error {resp.status_code}: {resp.text[:160]}",
        )

    def send_newsletter(self, subject: str, content: str) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            f"{self._BASE}/newsletters",
            headers=self._headers(cfg),
            json={
                "subject": subject,
                "content": {"html": content},
                "flags": ["openrate", "clicktrack"],
                "campaign": {"campaignId": cfg["campaign_id"]},
                "fromField": {"fromFieldId": cfg.get("from_field_id", "")},
                "replyTo": {"fromFieldId": cfg.get("reply_to_id", "")},
                "sendSettings": {"selectedCampaigns": [{"campaignId": cfg["campaign_id"]}]},
                "status": "enabled",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"GetResponse send_newsletter error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"GetResponse newsletter created (id={data.get('newsletterId', '')}).",
            data=data,
        )

    def get_stats(self) -> ConnectorResult:
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        # Campaign statistics
        resp = requests.get(
            f"{self._BASE}/campaigns/{cfg['campaign_id']}",
            headers=self._headers(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"GetResponse get_stats error {resp.status_code}: {resp.text[:160]}",
            )
        data = resp.json()
        subs = data.get("statistics", {}).get("subscribers", {})
        total = subs.get("total", 0)
        return ConnectorResult(
            ok=True,
            message=f"GetResponse campaign stats: {total} total subscribers.",
            data=data,
        )
