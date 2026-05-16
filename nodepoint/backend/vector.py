from functools import lru_cache

from nodepoint.agent.agent import Agent
from nodepoint.quadrant.manager import ingest_vector


@lru_cache(maxsize=1)
def get_agent() -> Agent:
    return Agent()


def create_entity_payload(entity):
    attributes = ", ".join(
        f"{key}: {value}" for key, value in (entity.attributes or {}).items()
    )
    workspace_name = entity.document.workspace.name
    doc = f"{entity.name} entity of type {entity.entity_type} with attributes {attributes}"
    vector = get_agent().vector(doc).squeeze().tolist()
    return {
        "type": "entity",
        "entity_type": entity.entity_type,
        "workspace": workspace_name,
        "vector": vector,
    }


def create_relation_payload(relation):
    workspace_name = relation.document.workspace.name
    doc = (
        f"{relation.source.name} {relation.type_description} {relation.target.name}. "
        f"{relation.description}"
    )
    vector = get_agent().vector(doc).squeeze().tolist()
    return {
        "type": "relation",
        "workspace": workspace_name,
        "vector": vector,
    }


def ingest_entity_vector(point_id, entity):
    payload = create_entity_payload(entity)
    vector = payload.pop("vector")
    return ingest_vector(str(point_id), vector, payload)


def ingest_relation_vector(point_id, relation):
    payload = create_relation_payload(relation)
    vector = payload.pop("vector")
    return ingest_vector(str(point_id), vector, payload)
