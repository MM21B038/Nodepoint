import logging
from collections import defaultdict
from nodepoint.agent.schema import AgentParseEmptyResult, AgentParseSuccessResult, AgentParseErrorResult
from nodepoint.registry import Thread, Schema, Prompt
from nodepoint.registry.dynamic_schema import Entities
from nodepoint.agent.agent import Agent
from nodepoint.models import DocumentChunk, KnowledgeEntity, KnowledgeRelation
import tiktoken

logger = logging.getLogger(__name__)

EXTRACTION_TEMPERATURE = 0.3
TRIALS = 3
CHUNK_SIZE = 1500
OVERLAP = 100

def split_doc(doc: str) -> list[str]:
    chunks = []
    encoding = tiktoken.get_encoding("o200k_harmony")
    tokens = encoding.encode(doc)
    for i in range(0, len(tokens), CHUNK_SIZE - OVERLAP):
        chunk_tokens = tokens[i : i + CHUNK_SIZE]
        chunk = encoding.decode(chunk_tokens)
        chunks.append(chunk)
    return chunks

def get_entity_types() -> dict[str, str]:
    entity_types = defaultdict(str)
    entity_types["PER"] = "Name of Individuals (e.g., John Doe, Jane Smith) or System Users (e.g., @username, root, kali)"
    entity_types["ORG"] = "Name of Organizations (e.g., Google, Microsoft, OpenAI)"
    entity_types["LOC"] = "Name of Locations (e.g., New York, Paris, Mount Everest)"
    entity_types["PROD"] = "Name of Products (e.g., iPhone, Windows 10, Tesla Model S)"
    entity_types["EVENT"] = "Name of Events (e.g., World War II, Super Bowl, COVID-19 Pandemic, Birthday Party)"
    entity_types["TECH"] = "Name of Technologies with versions (e.g., Python 3.8, TensorFlow 2.0, Blockchain, docker, Apache 2.4)"
    entity_types["VULN"] = "Name of Vulnerabilities (e.g., CVE-2021-12345, Heartbleed, Shellshock)"
    entity_types["MALWARE"] = "Name of Malware (e.g., WannaCry, NotPetya, Emotet)"
    entity_types["TOOL"] = "Name of Tools (e.g., Nmap, Metasploit, Wireshark)"
    entity_types["IP"] = "Name of IP Addresses (e.g., 192.168.1.1, 10.0.0.1)"
    entity_types["DOMAIN"] = "Name of Domains (e.g., google.com, microsoft.com, openai.com)"
    entity_types["SUBDOMAIN"] = "Name of Subdomains (e.g., mail.google.com, www.microsoft.com, api.openai.com)"
    entity_types["OTHER"] = "Any other type of entity that does not fit into the above categories but is relevant to the document."
    return entity_types


def entity_types_as_md_table(entity_types: dict[str, str]) -> str:
    table = "| Type | Description |\n|------|-------------|\n"
    for type_, description in entity_types.items():
        table += f"| {type_} | {description} |\n"
    return table


def extract_entities(doc: str, entity_types: set, md_entity_table: str, agent: Agent) -> list:
    trial = 0
    thread = Thread()
    thread.addSystem(Prompt["entity_extractor_system"])
    thread.addUser(Prompt["entity_extractor_user"].format(md_entity_table=md_entity_table, doc=doc))

    NewEntities = Entities(types=entity_types)

    while trial < TRIALS:
        response = agent.parse(
            messages=thread,
            response_schema=NewEntities,
            temperature=EXTRACTION_TEMPERATURE,
        )
        if isinstance(response, AgentParseSuccessResult):
            return response.response.entities
        elif isinstance(response, AgentParseEmptyResult):
            print("empty response")
            logger.warning("No entities extracted for document. Trying again.")
            thread.addAssistant(response.message)
        elif isinstance(response, AgentParseErrorResult):
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
            response_schema=Schema.Relations,
            temperature=EXTRACTION_TEMPERATURE,
        )
        if isinstance(response, AgentParseSuccessResult):
            return response.response.relations
        elif isinstance(response, AgentParseEmptyResult):
            logger.warning("No relations extracted for document, trying again.")
            thread.addAssistant(response.message)
        elif isinstance(response, AgentParseErrorResult):
            logger.error("Error extracting relations for document with error: %s, trying again.", response.error)
            thread.addAssistant(response.message)
            thread.addUser(response.error)
        trial += 1
    return []


def extract_knowledge_graph(doc: str) -> Schema.KnowledgeGraph:
    agent = Agent()
    entity_types = set(get_entity_types().keys())
    md_entity_table = entity_types_as_md_table(get_entity_types())
    entities = extract_entities(doc, entity_types, md_entity_table, agent)
    if len(entities) > 1:
        relations = extract_relations(doc, [entity.name for entity in entities], agent)
    else:
        relations = []
    return entities, relations


def ingest_knowledge_graph_for_chunk(
    doc,
    chunk: DocumentChunk,
    entities,
    relations,
) -> tuple[bool, list, list]:
    entity_ids: list = []
    relation_ids: list = []

    try:
        KnowledgeRelation.objects.filter(chunk=chunk).delete()
        KnowledgeEntity.objects.filter(chunk=chunk).delete()

        entity_by_name: dict[str, KnowledgeEntity] = {}
        for entity in entities:
            row = KnowledgeEntity.objects.create(
                document=doc,
                chunk=chunk,
                name=entity.name,
                entity_type=entity.type if entity.type != "OTHER" else entity.newtype,
                attributes=entity.attributes or {},
            )
            entity_by_name[entity.name] = row
            entity_ids.append(row.id)

        for relation in relations:
            source = entity_by_name.get(relation.source)
            target = entity_by_name.get(relation.target)
            if source is None or target is None:
                logger.warning(
                    "Skipping relation %s -> %s: missing entity for chunk %s",
                    relation.source,
                    relation.target,
                    chunk.id,
                )
                continue
            row = KnowledgeRelation.objects.create(
                document=doc,
                chunk=chunk,
                source=source,
                target=target,
                type_description=relation.type_description,
                description=relation.description,
            )
            relation_ids.append(row.id)

        return True, entity_ids, relation_ids

    except Exception:
        logger.exception("Failed to ingest knowledge graph for chunk %s", chunk.id)
        return False, entity_ids, relation_ids


def ingest_knowledge_graph(doc, entities, relations) -> tuple[bool, list, list]:
    """Legacy whole-document ingest (deletes all KG rows for the document)."""
    entity_ids: list = []
    relation_ids: list = []

    try:
        KnowledgeRelation.objects.filter(document=doc).delete()
        KnowledgeEntity.objects.filter(document=doc).delete()

        entity_by_name: dict[str, KnowledgeEntity] = {}
        for entity in entities:
            row = KnowledgeEntity.objects.create(
                document=doc,
                name=entity.name,
                entity_type=entity.type if entity.type != "OTHER" else entity.newtype,
                attributes=entity.attributes or {},
            )
            entity_by_name[entity.name] = row
            entity_ids.append(row.id)

        for relation in relations:
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
