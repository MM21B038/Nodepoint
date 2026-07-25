from collections import defaultdict
from pydantic import BaseModel, Field
from typing import List, Optional, Union, Any, Dict

class Entity(BaseModel):
    name: str = Field(..., description="The name of the entity.")
    type: str = Field(..., description="The type of the entity.")
    attributes: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Attributes of the entity.")

class Entities(BaseModel):
    entities: List[Entity] = Field(..., description="A list of extracted entities.")

class Relation(BaseModel):
    source: str = Field(..., description="The name of the source entity.")
    target: str = Field(..., description="The name of the target entity.")
    type_description: str = Field(..., min_length=1, max_length=100, description="A short description of the relationship type.")
    description: str = Field(..., min_length=5, max_length=500, description="Detailed description of the relationship.")

class Relations(BaseModel):
    relations: List[Relation] = Field(..., description="A list of relations between entities.")

class KnowledgeGraph(BaseModel):
    entities: List[Entity] = Field(..., description="A list of entities in the knowledge graph.")
    relations: List[Relation] = Field(..., description="A list of relations in the knowledge graph.")

Schema = defaultdict(BaseModel)
Schema["Entity"] = Entity
Schema["Entities"] = Entities
Schema["Relation"] = Relation
Schema["Relations"] = Relations
Schema["KnowledgeGraph"] = KnowledgeGraph
