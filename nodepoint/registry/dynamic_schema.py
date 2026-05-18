from typing import Optional, Literal, List, Any
from pydantic import create_model, Field

def Entities(types: set):

    EntityType = Literal[*types]

    Entity = create_model(
        "Entity",
        name=(
            str, 
            Field(..., description="The name of the entity.")
        ),
        type=(
            EntityType, 
            Field(..., description="The type of the entity.")
        ),
        newtype=(
            str, 
            Field(default="", description="name of new type of entity, only if type=OTHER")
        ),
        attributes=(
            dict[str, Any],
            Field(..., description="Attributes of the entity.")
        ),
    )

    NewType = create_model(
        "NewType",
        newtype=(
            str,
            Field(..., description="The name of the new entity type.")
        ),
        definition=(
            str,
            Field(..., description="Definition of the new entity type.")
        ),
    )

    Entities = create_model(
        "Entities",
        entities=(
            List[Entity],
            Field(
                ...,
                description="List of extracted entities."
            )
        ),
        newtypes=(
            List[Optional[NewType]],
            Field(
                None,
                description="List of new entity types to be added to the schema."
            )
        )
    )
    return Entities