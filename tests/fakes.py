from dataclasses import dataclass, field
from uuid import UUID

from registro_personas.schemas import AccountLink
from registro_personas.security import Principal


@dataclass
class EvidenceVerifier:
    """TEST DOUBLE ONLY: trusted evidence supplied by fixtures, never HTTP credentials."""

    bindings: set[tuple] = field(default_factory=set)

    @staticmethod
    def binding(principal: Principal, person_id: UUID, link: AccountLink):
        return (
            principal.actor_id,
            person_id,
            link.issuer,
            link.subject,
            link.verification_ref,
            link.provenance.system,
            link.provenance.evidence_ref,
        )

    def approve(self, principal: Principal, person_id: UUID, link: AccountLink):
        self.bindings.add(self.binding(principal, person_id, link))

    def verify(self, principal: Principal, person_id: UUID, link: AccountLink) -> bool:
        return self.binding(principal, person_id, link) in self.bindings
