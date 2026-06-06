from fastapi import FastAPI
from pydantic import BaseModel
import pymongo
from AAC_CRUD_Operations import CRUD

app = FastAPI(
    title="AAC CRUD Operations API",
    description="Swagger-enabled API for testing and documenting AAC CRUD routines",
    version="1.0.0"
)

crud = CRUD

class Credentials(BaseModel):
    username: str
    password: str


def auth(credentials: Credentials):
    if not crud.authenticate(credentials.username, credentials.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password"
        )
    return True

# Example Pydantic model for request bodies
class Item(BaseModel):
    id: int
    name: str
    description: str | None = None

@app.get("/", summary="About", tags=["Base"])
def root():
    return {"message": "CRUD Swagger version 1.0. Visit /docs for Swagger UI."}

@app.post("Authenticate User", summary="Connect", tags=["Authenticate"])
def login(credentials: Credentials):
    if crud.authenticate(credentials.username, credentials.password):
        return {"status": "authenticated"}
    raise HTTPException(status_code=401, detail="Invalid credentials")
 
@app.post("Create()", summary="Create an item", tags=["Items"])
def api_create_item(item: Item):
    """Calls the create_item() routine from AAC_CRUD_Operations."""
    return crud.create_item(item.dict())

@app.get("Read()", summary="Read an item", tags=["Items"])
def api_read_item(item_id: int):
    """Calls the read_item() routine."""
    return crud.read_item(item_id)

@app.put("Update()", summary="Update an item", tags=["Items"])
def api_update_item(item_id: int, item: Item):
    """Calls the update_item() routine."""
    return crud.update_item(item_id, item.dict())


@app.delete("Delete()", summary="Delete an item", tags=["Items"])
def api_delete_item(item_id: int):
    """Calls the delete_item() routine."""
    return crud.delete_item(item_id)
