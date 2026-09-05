#!/usr/bin/env python3
"""Send new 'gestionnaire de paie' alternance listings to Telegram.

Sources: France Travail API, La bonne alternance API, HelloWork public results,
and optional official Indeed alert emails (IMAP). It deliberately does not
bypass bot controls or scrape Indeed.
"""
from __future__ import annotations

import email
import hashlib
import html
import imaplib
import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.header import decode_header
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
STATE_FILE = BASE / "state.json"
TIMEOUT = 25
KEYWORDS = ("gestionnaire de paie", "gestionnaire paie", "paie")


@dataclass(frozen=True)
class Job:
    source: str
    title: str
    company: str = ""
    location: str = ""
    url: str = ""
    published: str = ""
    description: str = ""

    @property
    def identifier(self) -> str:
        # A source-neutral fingerprint prevents the same offer, republished by
        # France Travail/La bonne alternance/HelloWork, from being sent twice.
        # URL is only a fallback: it often differs between aggregators.
        def normalise(value: str) -> str:
            return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
        raw = "|".join(filter(None, (normalise(self.title), normalise(self.company), normalise(self.location))))
        if not raw:
            raw = self.url.split("?", 1)[0]
        return hashlib.sha256(raw.encode()).hexdigest()[:24]


def text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(text(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(text(v) for v in value)
    return str(value or "")


def relevant(job: Job) -> bool:
    corpus = (job.title + " " + job.description).lower()
    excluded = [word.strip().lower() for word in os.getenv("EXCLUDED_KEYWORDS", "").split(",") if word.strip()]
    return any(k in corpus for k in KEYWORDS) and not any(word in corpus for word in excluded)


def request_json(session: requests.Session, url: str, **kwargs: Any) -> Any:
    response = session.get(url, timeout=TIMEOUT, **kwargs)
    response.raise_for_status()
    return response.json()


def france_travail(session: requests.Session) -> list[Job]:
    client_id, secret = os.getenv("FRANCE_TRAVAIL_CLIENT_ID"), os.getenv("FRANCE_TRAVAIL_CLIENT_SECRET")
    if not client_id or not secret:
        return []
    token_response = session.post(
        "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire",
        data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret,
              "scope": "api_offresdemploiv2 o2dsoffres"}, timeout=TIMEOUT)
    token_response.raise_for_status()
    token = token_response.json()["access_token"]
    payload = request_json(session,
        "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search",
        headers={"Authorization": f"Bearer {token}"},
        # No contract filter: the user also receives standard employment offers
        # that may be converted into an alternance through direct contact.
        params={"motsCles": "gestionnaire paie", "range": "0-149"})
    jobs = []
    for item in payload.get("resultats", []):
        jobs.append(Job("France Travail", item.get("intitule", ""),
            text(item.get("entreprise", {}).get("nom")), text(item.get("lieuTravail", {}).get("libelle")),
            item.get("origineOffre", {}).get("urlOrigine", "") or
            f"https://candidat.francetravail.fr/offres/recherche/detail/{item.get('id', '')}",
            item.get("dateCreation", ""), item.get("description", "")))
    return jobs


def la_bonne_alternance(session: requests.Session) -> list[Job]:
    key = os.getenv("LBA_API_KEY")
    if not key:
        return []
    params = {"romes": "M1507", "radius": os.getenv("SEARCH_RADIUS_KM") or "30",
              "caller": os.getenv("LBA_CALLER") or "veille-alternance"}
    if os.getenv("SEARCH_LATITUDE") and os.getenv("SEARCH_LONGITUDE"):
        params.update({"latitude": os.environ["SEARCH_LATITUDE"], "longitude": os.environ["SEARCH_LONGITUDE"]})
    payload = request_json(session, "https://api.apprentissage.beta.gouv.fr/api/job/v1/search",
        headers={"X-Api-Key": key}, params=params)
    # The service groups offers by partner. Extract only objects that look like a job.
    def walk(node: Any) -> Iterable[dict[str, Any]]:
        if isinstance(node, dict):
            if any(k in node for k in ("title", "intitule", "job")) and any(k in node for k in ("apply", "url", "offer")):
                yield node
            for value in node.values(): yield from walk(value)
        elif isinstance(node, list):
            for value in node: yield from walk(value)
    jobs = []
    for item in walk(payload):
        title = text(item.get("title") or item.get("intitule") or item.get("job", {}).get("title"))
        apply = item.get("apply", {}) if isinstance(item.get("apply"), dict) else {}
        offer = item.get("offer", {}) if isinstance(item.get("offer"), dict) else {}
        url = text(apply.get("url") or item.get("url") or offer.get("url"))
        workplace = item.get("workplace", {}) if isinstance(item.get("workplace"), dict) else {}
        jobs.append(Job("La bonne alternance", title, text(workplace.get("name") or item.get("company")),
            text(workplace.get("address") or item.get("place")), url,
            text(item.get("created_at") or item.get("date")), text(item.get("description"))))
    return jobs


def hellowork(session: requests.Session) -> list[Job]:
    # Public search page; only one polite request per execution.
    url = "https://www.hellowork.com/fr-fr/emploi/metier_gestionnaire-paie.html"
    response = session.get(url, timeout=TIMEOUT, headers={"User-Agent": "AlternanceWatch/1.0 (personal job alert)"})
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    jobs: list[Job] = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try: data = json.loads(tag.string or "")
        except json.JSONDecodeError: continue
        entries = data if isinstance(data, list) else [data]
        for item in entries:
            if item.get("@type") != "JobPosting": continue
            org = item.get("hiringOrganization", {})
            loc = item.get("jobLocation", {})
            jobs.append(Job("HelloWork", text(item.get("title")), text(org.get("name")), text(loc),
                text(item.get("url")), text(item.get("datePosted")), text(item.get("description"))))
    # Fallback: links remain useful even if the page does not expose JSON-LD.
    if not jobs:
        for link in soup.select('a[href*="/emplois/"]'):
            title = link.get_text(" ", strip=True)
            if title:
                jobs.append(Job("HelloWork", title, url=urljoin(url, link.get("href", ""))))
    return jobs


def decode_subject(msg: email.message.Message) -> str:
    return "".join(part.decode(charset or "utf-8", errors="replace") if isinstance(part, bytes) else part
                   for part, charset in decode_header(msg.get("Subject", "")))


def indeed_alert_emails() -> list[Job]:
    host, username, password = (os.getenv("INDEED_IMAP_HOST"), os.getenv("INDEED_IMAP_USERNAME"), os.getenv("INDEED_IMAP_APP_PASSWORD"))
    if not all((host, username, password)):
        return []
    mail = imaplib.IMAP4_SSL(host, int(os.getenv("INDEED_IMAP_PORT") or "993"))
    try:
        mail.login(username, password); mail.select("INBOX")
        _, ids = mail.search(None, '(UNSEEN FROM "indeed")')
        jobs = []
        for uid in ids[0].split():
            _, data = mail.fetch(uid, "(RFC822)")
            msg = email.message_from_bytes(data[0][1])
            body = next((p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "replace")
                         for p in msg.walk() if p.get_content_type() == "text/html"), "")
            soup = BeautifulSoup(body, "html.parser")
            for a in soup.select("a[href]"):
                label, href = a.get_text(" ", strip=True), a["href"]
                if label and "indeed" in href.lower():
                    jobs.append(Job("Indeed (alerte officielle)", label, url=href, published=decode_subject(msg)))
            mail.store(uid, "+FLAGS", "\\Seen")
        return jobs
    finally:
        mail.logout()


def load_seen() -> set[str]:
    try: return set(json.loads(STATE_FILE.read_text()).get("seen", []))
    except (OSError, json.JSONDecodeError): return set()


def save_seen(seen: set[str]) -> None:
    STATE_FILE.write_text(json.dumps({"seen": list(seen)[-5000:], "updated_at": datetime.now(UTC).isoformat()}, indent=2))


def send_telegram(session: requests.Session, job: Job) -> None:
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id: raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required.")
    lines = [f"<b>{html.escape(job.title)}</b>", f"Source : {html.escape(job.source)}"]
    if job.company: lines.append(f"Entreprise : {html.escape(job.company)}")
    if job.location: lines.append(f"Lieu : {html.escape(job.location)}")
    if job.published: lines.append(f"Publié : {html.escape(job.published)}")
    if job.url: lines.append(f'<a href="{html.escape(job.url, quote=True)}">Voir l’annonce</a>')
    response = session.post(f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": "\n".join(lines), "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=TIMEOUT)
    response.raise_for_status()


def main() -> int:
    load_dotenv(BASE / ".env")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    session = requests.Session()
    sources = (france_travail, la_bonne_alternance, hellowork)
    collected: list[Job] = indeed_alert_emails()
    for source in sources:
        try: collected.extend(source(session))
        except requests.RequestException as exc: logging.warning("%s indisponible : %s", source.__name__, exc)
    unique = {job.identifier: job for job in collected if relevant(job)}
    seen = load_seen(); new = [job for key, job in unique.items() if key not in seen]
    logging.info("%d offre(s) correspondante(s), %d nouvelle(s)", len(unique), len(new))
    for job in new:
        send_telegram(session, job); seen.add(job.identifier)
    save_seen(seen | set(unique))
    return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except Exception as exc:
        logging.error("Échec : %s", exc); raise SystemExit(1)
