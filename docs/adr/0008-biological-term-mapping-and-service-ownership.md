# ADR 0008: Biological naming map to service ownership boundaries

- **Status:** Proposed
- **Parent issue:** #22
- **Related to:** epic decomposition and bounded capability ownership

## Context

The roadmap names multiple biological zones. For safe execution and future reviews, each term must map to a clear service owner.

## Mapping

- **Mimétisme:** `src/mongars/adaptation/feedback.py`, `src/mongars/adaptation/mimicry.py`, `src/mongars/adaptation/repository.py`
- **AffectSignal:** `src/mongars/orchestrator/emotion.py` and cognitive context assembly.
- **PersonalitySnapshot:** `src/mongars/orchestrator/personality.py` and persistence models in adaptation storage.
- **Sommeil Paradoxal / Scheduler:** `src/mongars/evolution/scheduler.py`, `src/mongars/evolution/gap_detection.py`, `src/mongars/evolution/consolidation.py`
- **Mains Virtuelles:** `src/mongars/rm/contracts.py`, `src/mongars/rm/worker.py`, task governance routes and approval surface.
- **Modelevolution / Bouche pointer:** `src/mongars/evolution/governance.py` and readiness/heartbeat exposure.
- **P2P:** `src/mongars/p2p/protocol.py` and future exchange protocols.

## Decision

- The ownership mapping is documented as normative for future PR and review scopes.
- New capability work should cite the owning service path and cannot bypass that subsystem’s policy layer.

## Consequences

- Faster security review and ownership checks for future capability expansion.
- Clearer boundary for audit and child-issue slicing.
