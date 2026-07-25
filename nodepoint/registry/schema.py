from pydantic import BaseModel, ConfigDict, Field
from typing import List, Any, Dict

class Entity(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = Field(..., description="The name of the entity.")
    type: str = Field(..., description="The type of the entity.")
    attributes: Dict[str, Any] = Field(..., description="Attributes of the entity.")

class Entities(BaseModel):
    model_config = ConfigDict(extra="allow")
    entities: List[Entity] = Field(..., description="A list of extracted entities.")

class Relation(BaseModel):
    model_config = ConfigDict(extra="allow")
    source: str = Field(..., description="The name of the source entity.")
    target: str = Field(..., description="The name of the target entity.")
    type_description: str = Field(..., min_length=1, max_length=100, description="A short description of the relationship type.")
    description: str = Field(..., min_length=5, max_length=500, description="Detailed description of the relationship.")

class Relations(BaseModel):
    model_config = ConfigDict(extra="allow")
    relations: List[Relation] = Field(..., description="A list of relations between entities.")

class Schema:
    Entity = Entity
    Entities = Entities
    Relation = Relation
    Relations = Relations
