import logging
from collections import defaultdict
from nodepoint.agent.schema import AgentParseEmptyResult, AgentParseSuccessResult, AgentParseErrorResult
from nodepoint.registry import Thread, Prompt
from nodepoint.registry.schema import Schema, get_entity_types
from nodepoint.agent.agent import Agent
from nodepoint.models import KnowledgeEntity, KnowledgeRelation
from typing import Type

logger = logging.getLogger(__name__)

EXTRACTION_TEMPERATURE = 1.2
TRIALS = 3


def entity_types_as_md_table(entity_types: dict[str, str]) -> str:
    table = "| Type | Description |\n|------|-------------|\n"
    for type_, description in entity_types.items():
        table += f"| {type_} | {description} |\n"
    return table


def extract_entities(doc: str, entity_types: str, agent: Agent) -> list:
    trial = 0
    thread = Thread()
    thread.addSystem(Prompt["entity_extractor_system"])
    thread.addUser(Prompt["entity_extractor_user"].format(entity_types=entity_types, doc=doc))
    while trial < TRIALS:
        response = agent.parse(
            messages=thread,
            model=agent.model,
            response_schema=Schema.Entities,
            temperature=EXTRACTION_TEMPERATURE,
        )
        if isinstance(response, AgentParseSuccessResult):
            return response.response.entities
        elif isinstance(response, AgentParseEmptyResult):
            print("empty response")
            logger.warning("No entities extracted for document. Trying again.")
            thread.addAssistant(response.message)
            thread.addUser("error: got an empty response, No json content. Please try again.")
        elif type(response) == Type[AgentParseErrorResult]:
            print("incorrect output")
            logger.error("Error extracting entities for document with error: %s", response.error)
            thread.addAssistant(response.message)
            thread.addUser(response.error)
        trial += 1
    return []
    
def extract_relations(doc: str, entities: list[str], agent: Agent) -> list:
    trial = 0
    thread = Thread()
    thread.addSystem(Prompt["relation_extractor_system"])
    thread.addUser(Prompt["relation_extractor_user"].format(entities=entities, doc=doc))
    while trial < TRIALS:
        response = agent.parse(
            messages=thread,
            model=agent.model,
            response_schema=Schema.Relations,
            temperature=EXTRACTION_TEMPERATURE,
        )
        if type(response) == Type[AgentParseSuccessResult]:
            return response.response.relations
        elif type(response) == Type[AgentParseEmptyResult]:
            logger.warning("No relations extracted for document, trying again.")
            thread.addAssistant(response.message)
            thread.addUser("error: got an empty response, expecting a json with key relations and value as a list of relation or an empty list. Please try again.")
        elif type(response) == Type[AgentParseErrorResult]:
            logger.error("Error extracting relations for document with error: %s, trying again.", response.error)
            thread.addAssistant(response.message)
            thread.addUser(response.error)
        trial += 1
    return []


def extract_knowledge_graph(doc: str) -> Schema.KnowledgeGraph:
    agent = Agent()
    entity_types = entity_types_as_md_table(get_entity_types())
    entities = extract_entities(doc, entity_types, agent)
    if len(entities) > 1:
        relations = extract_relations(doc, [entity.name for entity in entities], agent)
    else:
        relations = []
    return Schema.KnowledgeGraph(entities=entities, relations=relations)


def ingest_knowledge_graph(doc, knowledge_graph: Schema.KnowledgeGraph) -> tuple[bool, list, list]:
    entity_ids: list = []
    relation_ids: list = []

    try:
        KnowledgeRelation.objects.filter(document=doc).delete()
        KnowledgeEntity.objects.filter(document=doc).delete()

        entity_by_name: dict[str, KnowledgeEntity] = {}
        for entity in knowledge_graph.entities:
            row = KnowledgeEntity.objects.create(
                document=doc,
                name=entity.name,
                entity_type=entity.type,
                attributes=entity.attributes or {},
            )
            entity_by_name[entity.name] = row
            entity_ids.append(row.id)

        for relation in knowledge_graph.relations:
            source = entity_by_name.get(relation.source)
            target = entity_by_name.get(relation.target)
            if source is None or target is None:
                logger.warning(
                    "Skipping relation %s -> %s: missing entity for document %s",
                    relation.source,
                    relation.target,
                    doc.id,
                )
                continue
            row = KnowledgeRelation.objects.create(
                document=doc,
                source=source,
                target=target,
                type_description=relation.type_description,
                description=relation.description,
            )
            relation_ids.append(row.id)

        return True, entity_ids, relation_ids

    except Exception:
        logger.exception("Failed to ingest knowledge graph for document %s", doc.id)
        return False, entity_ids, relation_ids
