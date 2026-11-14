"""Send through Microsoft Graph as the configured mailbox (the account ReportAutomation sends its reports from).

The message goes as MIME (``sendMail`` with a base64 ``text/plain`` body), because the JSON form cannot carry an HTML
body with a plain-text alternative. Graph answers 202 with no message id, so ``provider_message_id`` stays None.
"""

from __future__ import annotations

import base64
from email.message import EmailMessage

import httpx

from seleric_swarm.mail.send import Draft, Rejected, SendReceipt

_GRAPH = "https://graph.microsoft.com/v1.0"
_LOGIN = "https://login.microsoftonline.com"


def to_mime(draft: Draft) -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = draft.sender, ", ".join(draft.to), draft.subject
    if draft.cc:
        msg["Cc"] = ", ".join(draft.cc)
    msg.set_content(draft.text, cte="base64")  # not quoted-printable: its soft line breaks split a multi-byte ₹ in some viewers
    msg.add_alternative(draft.html, subtype="html", cte="base64")
    html = list(msg.iter_parts())[-1]
    for a in draft.attachments:
        maintype, _, subtype = a.mime.partition("/")
        if a.cid:  # shown inline by the HTML body, not listed as a file
            html.add_related(a.content, maintype=maintype, subtype=subtype, cid=f"<{a.cid}>", disposition="inline", filename=a.name)
        else:
            msg.add_attachment(a.content, maintype=maintype, subtype=subtype, filename=a.name)
    return msg.as_bytes()


class GraphTransport:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._tenant, self._id, self._secret = tenant_id, client_id, client_secret
        self._client = client or httpx.AsyncClient(timeout=30)

    async def _token(self) -> str:
        try:
            r = await self._client.post(f"{_LOGIN}/{self._tenant}/oauth2/v2.0/token", data={
                "grant_type": "client_credentials", "client_id": self._id, "client_secret": self._secret,
                "scope": "https://graph.microsoft.com/.default"})
        except httpx.HTTPError as exc:  # the token step precedes the message: whatever went wrong, nothing was sent
            raise Rejected(f"could not reach the sign-in service ({type(exc).__name__}); nothing was sent") from exc
        if r.status_code != 200 or "access_token" not in r.json():
            raise Rejected(f"could not get a Graph token ({r.status_code}); nothing was sent")
        return str(r.json()["access_token"])

    async def send(self, draft: Draft) -> SendReceipt:
        try:
            body = base64.b64encode(to_mime(draft))
        except (ValueError, TypeError) as exc:  # e.g. a newline in the subject
            raise Rejected(f"the message could not be built ({exc}); nothing was sent") from exc
        token = await self._token()
        try:
            r = await self._client.post(f"{_GRAPH}/users/{draft.sender}/sendMail", content=body,
                                        headers={"Authorization": f"Bearer {token}", "Content-Type": "text/plain"})
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:  # never connected: the request did not leave
            raise Rejected(f"could not reach Microsoft Graph ({type(exc).__name__}); nothing was sent") from exc
        if r.status_code == 202:
            return SendReceipt(None)
        if 400 <= r.status_code < 500:  # the provider declined it, so nothing went out
            raise Rejected(f"Graph refused the message: {r.status_code} {r.text[:200]}")
        # 5xx or anything unexpected: it may have been taken; the caller records the outcome as unknown
        raise httpx.HTTPStatusError(f"unexpected Graph status {r.status_code}", request=r.request, response=r)
