"""Recipients resolve from an explicit address or a trusted contact; anything else is a question, never a guess."""

from __future__ import annotations

from pathlib import Path

from seleric_swarm.mail.recipients import Clarify, ContactBook, Resolved, resolve

BOOK = ContactBook.from_dict({
    "contacts": [
        {"name": "Ajay Sharma", "email": "ajay.sharma@seleric.ai"},
        {"name": "Ajay Verma", "email": "ajay.verma@seleric.ai"},
        {"name": "Gaurav", "email": "gaurav@seleric.ai"},
    ],
    "groups": {"marketing": ["gaurav@seleric.ai", "ajay.verma@seleric.ai"]},
})


def test_explicit_address_is_accepted_and_normalised() -> None:
    assert resolve("  Gaurav@Seleric.ai ", BOOK) == Resolved(["gaurav@seleric.ai"])


def test_malformed_address_is_a_clarification() -> None:
    out = resolve("gaurav@seleric", BOOK)
    assert isinstance(out, Clarify) and "gaurav@seleric" in out.reason


def test_unique_name_resolves_by_full_or_first_name() -> None:
    assert resolve("gaurav", BOOK) == Resolved(["gaurav@seleric.ai"])
    assert resolve("Ajay Sharma", BOOK) == Resolved(["ajay.sharma@seleric.ai"])


def test_ambiguous_name_lists_the_candidates_and_does_not_pick() -> None:
    out = resolve("Ajay", BOOK)
    assert isinstance(out, Clarify)
    assert set(out.candidates) == {"Ajay Sharma <ajay.sharma@seleric.ai>", "Ajay Verma <ajay.verma@seleric.ai>"}


def test_unknown_name_is_never_guessed_into_an_address() -> None:
    out = resolve("Priya", BOOK)
    assert isinstance(out, Clarify) and out.candidates == []


def test_group_resolves_to_its_members() -> None:
    assert resolve("the marketing team", BOOK) == Resolved(["gaurav@seleric.ai", "ajay.verma@seleric.ai"])


def test_shipped_contacts_file_lists_the_approved_addresses() -> None:
    book = ContactBook.load(Path("config/email_contacts.yaml"))
    assert [c.email for c in book.contacts] == ["gaurav@seleric.ai", "tripti@seleric.ai"]


# ---- explicit addresses outside the company are not accepted on the strength of one (mis)heard sentence

INTERNAL = ContactBook.from_dict({"contacts": [{"name": "Partner", "email": "partner@agency.com"}],
                                  "allowed_domains": ["seleric.ai"]})


def test_an_explicit_address_on_an_allowed_domain_is_accepted() -> None:
    assert resolve("anyone@seleric.ai", INTERNAL) == Resolved(["anyone@seleric.ai"])


def test_an_explicit_address_on_another_domain_is_a_question_not_a_recipient() -> None:
    out = resolve("someone@gmail.com", INTERNAL)
    assert isinstance(out, Clarify) and "seleric.ai" in out.reason and "contacts" in out.reason


def test_a_listed_contact_on_another_domain_is_trusted_by_name_and_by_address() -> None:
    assert resolve("partner", INTERNAL) == Resolved(["partner@agency.com"])
    assert resolve("Partner@Agency.com", INTERNAL) == Resolved(["partner@agency.com"])


def test_no_allowlist_means_no_restriction() -> None:
    assert resolve("someone@gmail.com", BOOK) == Resolved(["someone@gmail.com"])
