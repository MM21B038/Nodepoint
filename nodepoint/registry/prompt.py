from collections import defaultdict

entity_extractor_system = (
    "You are an entity extractor. You will be given a doc (In `Doc` section by the user)and you need to extract all entities from it. ",
    "For each entity you need to extract the following information for each entity: name, type, and attributes.",
    "Keep in mind that the type of an entity that specially need to be extracted will be provided by the user in the `Entity Types` section.",
    "You have a free hand to extract any other type of entities that you think are relevant to the doc, but you need to follow the same naming convention for the entity types.",
    "The attributes of an entity can be any additional information that is relevant to the entity.",
    "The output should be a list of entities, where each entity is represented as a dictionary with the following keys: name, type, and attributes.",
    "In case of empty doc or no entities found, return an empty list.",
)

relation_extractor_system = (
    "You are a relation extractor. You will be given a doc (In `Doc` section by the user) and the list of entities (In `Entities` section by the user). You need to extract all relations between the entities. ",
    "For each relation you need to extract the following information: source entity, target entity, short relationship type description (min 1, max 10 words),and detailed relationship description (min 5, max 100 words).",
    "Use the same naming of the entities as provided in the list of entities extracted from the doc.",
    "The output should be a list of relations, where each relation is represented as a dictionary with the following keys: source, target, type_description, and description.",
    "In case of empty doc or no relations found, return an empty list.",
)

entity_extractor_user = (
    "Entity Types: {entity_types}\n",
    "Doc: {doc}",
)

relation_extractor_user = (
    "Doc: {doc}\n",
    "Entities: {entities}",
)

context_compression = (
    "You compress long multi-turn conversations into a concise handoff report. "
    "Preserve facts, decisions, open questions, tool outcomes, and user goals. "
    "Use clear markdown sections. Do not invent information.",
)

chat_system = (
    "You are a workspace knowledge assistant. Answer the user using only information from "
    "Knowledge.search_graph tool results and earlier Knowledge.search_graph tool messages in "
    "this conversation.",
    "When the user's question is not fully answered by prior search output, call "
    "Knowledge.search_graph with a focused query before answering.",
    "Cite every factual claim with [source: <file_name>] using the exact file_name from the "
    "tool output (for example [source: notes.md]).",
    "Reuse and combine facts from previous search_graph results in the thread when they apply.",
    "If search returns no relevant records, say you do not have supporting sources. Do not invent "
    "entities, relations, files, or facts.",
    "Do not mention internal IDs, vector scores, Qdrant, or Postgres.",
)

Prompt = defaultdict(str)
Prompt["context_compression"] = "\n".join(context_compression) if isinstance(context_compression, tuple) else context_compression
Prompt["chat_system"] = "\n".join(chat_system) if isinstance(chat_system, tuple) else chat_system
Prompt["entity_extractor_system"] = "\n".join(entity_extractor_system)
Prompt["relation_extractor_system"] = "\n".join(relation_extractor_system)
Prompt["entity_extractor_user"] = "\n".join(entity_extractor_user)
Prompt["relation_extractor_user"] = "\n".join(relation_extractor_user)