# ---
# MongoManager_SwaggerUI.py
# ---
# 
# This script provides a Swagger front-end for the methods contained in the MongoManager class.
# The methods, functions, and data structures in the file are used to provide functionality to each
#    of the methods in the MongoManager class, with calls to those functions to provide access to
#    perform MongoDB management functions and data storage/retrieval.
#
# There are several python modules that must exist on the system to allow this script to function.  The
#    script uses FastAPI to provide a web based UI, and running the UI is accomplished by using uvicorn
#    to handle process control.  The pydantic module is also required to provide data structures used by FastAPI.
#    Several standard python modules are used including json, typing, and functools.  Finally, a connection is required
#    to a MongoDB data repository.  Authentication will allow specifying the connection URI, so an existing database
#    account with appropriate access can be used, or the community of MongoDB is available at 
#    https://www.mongodb.com/products/self-managed/community-edition so that a local database instance can be installed and started.
# ---
# Version History:
#    v0.1 - May 24, 2026 - After additional methods were added to the AAC_CRUD_Operations.py in that served as the base
#        for a management class for MongoDB, this file was created to provide a user accessible interface for testing and
#        using the methods in that resulting python module and class, MongoManager.
# ---
# Mark Woodford
# SNHU CS499 Computer Science Capstone
# May 24, 2026
# ---

from fastapi import FastAPI, HTTPException, Depends, Request, UploadFile, File, Form, APIRouter
from fastapi.responses import HTMLResponse
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field, ConfigDict
from typing import Any, Dict, List, Optional
from functools import lru_cache
from pathlib import Path
import uvicorn
import json
import os

from MongoManager import MongoManager
from SQLtoMongo import SQLToMongoTranslator

# Get the path from which the script is being executed in case we need locally stored files nearby...  
home_dir = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(
    title="Mongo Manager API",
    description="Full-featured Swagger UI for MongoManager operations",
    version="1.0.0"
)

router = APIRouter()
translator = SQLToMongoTranslator()

# Ensure that the MongoManager is single-threaded.  We don't want to have to manage logins
# multiple times or deal with threads that can't figure out that they're not currently
# authenticated.  Threading is find when dealing with certain operations, but the a functions 
# called from the manager is not one of them.
@lru_cache()
def get_manager():
    return MongoManager()
manager = get_manager()

# ---
# About Endpoint
# ---
# Provide a test function to display information about the current version
# ---

@app.get("/", tags=["About"], summary="About this API")
def about():
    return {
        "message": "MongoManager API",
        "version": "1.0.0",
        "connected": manager.authenticated,
        "active_database": getattr(manager.mm_database, 'name', "None"),
        "active_collection": getattr(manager.mm_collection, 'name', "None")
    }

# ---
# Authentication Models
# ---
# Below is the mechanism to ensure that Swagger UI is authenticated.  Provides a standard function for authentication
#    as well as there being a form to display to fill in login information.  This section defines the structure for the 
#    authentication information as well as providing the interface to make the authentication call which should be 
#    valid for the duration of the Swagger session.  The call to the authenticate function from the API is executed in this
#    section here as well.
# ---
class Credentials(BaseModel):
    username: Optional[str] = Field(None)
    password: Optional[str] = Field(None)
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=27017)
    database: Optional[str] = Field(None)
    timeout: int = Field(default=3000)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "username": "admin",
                    "password": "secret",
                    "host": "127.0.0.1",
                    "port": 27017,
                    "database": "testdb",
                    "timeout": 3000
                }
            ]
        }
    )

# ---
# Authentication Logic
# ---
# Try to authenticate and return the result.
# ---
def require_auth(credentials: Credentials):
    try:
        manager.authenticate(
            username=credentials.username,
            password=credentials.password,
            host=credentials.host,
            port=credentials.port,
            database=credentials.database,
            timeout=credentials.timeout
        )
        return manager
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

@app.post("/authenticate", tags=["Authentication"], summary="Authenticate and connect to MongoDB")
def authenticate(credentials: Credentials):
    require_auth(credentials)
    secured = bool(credentials.username and credentials.password)
    return {
        "status": "authenticated",
        "host": credentials.host,
        "port": credentials.port,
        "database": credentials.database,
        "secure": secured
    }

# ---
# Authentication Form Page
# ---
#@app.get("/auth/form", tags=["Authentication"], response_class=HTMLResponse)
def auth_form():
    # If functions detect that authentication hasn't been obtained, they should direct here to allow
    #    for authentication so functions don't fail on authentication.  This is not yet implemented
    #    as the method of forcing FastAPI to use a separate window is under investigation.
    return """
    <html>
    <head><title>MongoDB Authentication</title></head>
    <body>
        <h2>MongoDB Authentication</h2>
        <form action="/authenticate" method="post">
            <label>Username:</label><br>
            <input type="text" name="username"><br>
            <label>Password:</label><br>
            <input type="password" name="password"><br>
            <label>Host:</label><br>
            <input type="text" name="host" value="127.0.0.1"><br>
            <label>Port:</label><br>
            <input type="number" name="port" value="27017"><br>
            <label>Database:</label><br>
            <input type="text" name="database"><br><br>
            <label>Timeout (ms):</label><br>
            <input type="number" name="timeout" value="3000"><br>
            <button type="submit">Authenticate</button>
        </form>
    </body>
    </html>
    """

# ---
# Database Models
# ---
# These are the endpoints for database list/create/drop/use.  Each section simply
#    executes the associated function and reports results.  A class with the database
#    request format is included at the start of the section.
# ---
class DatabaseRequest(BaseModel):
    #Pydantic model definition for a database request
    database_name: str = Field(...)

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"database_name": "testdb"}]}
    )

# ---
# Database Endpoints
# ---
# These endpoints and accompanying functions will allow database list,create,drop, and use operations
# ---
@app.get("/database/list", tags=["Database"], summary="List all defined databases")
def list_databases():
    return manager.list_databases()

@app.post("/database/create", tags=["Database"], summary="Create a database")
# The define function will basically ignore the request if the db requested exists, though the class method still gets called.
def create_database(req: DatabaseRequest):
    return manager.create_database(req.database_name)

@app.delete("/database/drop", tags=["Database"], summary="Drop a database. WARNING: Does not prompt for verification")
def drop_database(req: DatabaseRequest):
    return manager.drop_database(req.database_name)

@app.post("/database/use", tags=["Database"], summary="Select active database.  Not necessary if db was specified during authentication.")
def use_database(req: DatabaseRequest):
    return manager.use_database(req.database_name)

# ---
# Collection Models
# ---
# In the same fashion of the database methods above, this is the section that provides
#    endpoints for collections along with a configuration format for collection requests at the
#    start of the section.
# ---
# Pydantic models for collection requests, with a separate model for renames.
class CollectionRequest(BaseModel):
    collection_name: str = Field(...)
    schema_specs: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"collection_name": "users", "schema_specs": None}
            ]
        }
    )

class RenameRequest(BaseModel):
    old_name: str = Field(...)
    new_name: str = Field(...)

# ---
# Collection Endpoints
# ---
# Colletion list,create,drop, and select endpoints.
# ===
@app.get("/collection/list", tags=["Collection"], summary="List collections")
def list_collections():
    return manager.list_collections()

@app.post("/collection/create", tags=["Collection"], summary="Create a collection")
def create_collection(req: CollectionRequest):
    return manager.create_collection(req.collection_name, req.schema_specs)

@app.delete("/collection/drop", tags=["Collection"], summary="Drop a collection")
def drop_collection(req: CollectionRequest):
    return manager.drop_collection(req.collection_name)

@app.put("/collection/rename", tags=["Collection"], summary="Rename a collection")
def rename_collection(req: RenameRequest):
    return manager.rename_collection(req.old_name, req.new_name)

@app.post("/collection/set", tags=["Collection"], summary="Set active collection")
def set_collection(req: CollectionRequest):
    return manager.set_collection(req.collection_name)

# ---
# CRUD Models
# ---
# These are the functions whieh were the inspiration for the class and user interface.  They
#    provide a set of definitions for database request structure detail along with the functions to 
#    execute create/read/update/delete from the connected database.  An additional function has been
#    incorporated to allow adding multiple records in bulk as well.
# ---
# First, the pydantic models for dealing with documents.  Create accepts a json Dict, CreateMany is a list json Dicts, and 
#    Query is used for read (find) operations.
class DocumentCreate(BaseModel):
    data: Dict[str, Any]

class DocumentCreateMany(BaseModel):
    records: List[Dict[str, Any]]

class DocumentQuery(BaseModel):
    # Both fields being 'Optional' causes Swagger to send a garbage field 'additionalProp1' which is default and causes an error
    #    Added extra schema parameters to negate the default setting. 
    filter: Optional[Dict[str, Any]] = Field(None, json_schema_extra={"additionalProperties": False})
    field_select: Optional[Dict[str, Any]] = Field(None, json_schema_extra={"additionalProperties": False})

class DocumentUpdate(BaseModel):
    filter: Dict[str, Any]
    update_dict: Dict[str, Any]

# ---
# CRUD Endpoints
# ---
# These routines will perform create (in its various forms, read, update, and delete operations.  With the 
#    read (find), update (put), and delete (drop) requests they just call the corresponding method from the
#    MongoManager class, as they do with standard create and create_many.  The only special case is the create
#    from file operation, which needs to do some checking to ensure the data is valid and what kind of data is there.
# ---
@app.post("/document/create", tags=["Documents"], summary="Insert one document")
def create_document(req: DocumentCreate):
    return manager.create(req.data)

@app.post("/document/create-many", tags=["Documents"], summary="Insert many documents")
def create_many(req: DocumentCreateMany):
    return manager.create_many(req.records)

@app.post("/document/create-from-file", tags=["Documents"])
def create_from_file(json_file: UploadFile = File(...)):
    # Create_from_file to more easily facilitate testing for document functions.  Creating records
    #    one at a time was repetitive, and for searching data and index operations large amounts
    #    of data were necessary and adding them via "create_many" was problematic with the confusion
    #    of json data in the input field.  This function takes a file of json or newline-delimited json
    #    then reads and stores the records.
    manager._check_auth()

    if manager.mm_database is None:
        return {"error": "No database selected. Call /database/set first."}

    if manager.mm_collection is None:
        return {"error": "No collection selected. Call /collection/set first."}

    raw_data = json_file.file.read().decode("utf-8").strip()

    # Try standard JSON first.  This would be just a long string of json formatted data.
    try:
        #
        json_data = json.loads(raw_data)
        if isinstance(json_data, dict):
            json_data = [json_data]
        else:
            if not isinstance(json_data, list):
                return {"error": "Invalid JSON data found.  JSON must be an object or array"}
        records_inserted = manager.create_many(json_data)
        return {"inserted": records_inserted}
    except json.JSONDecodeError:
        pass  # Fall through to Newline Delimited JSON parsing

    # Newline Delimited JSON fallback
    new_docs = []
    for line in raw_data.splitlines():
        line = line.strip()
        # Make certain that blank lines are skipped...
        if not line:
            continue
        try:
            new_docs.append(json.loads(line))
        except Exception as e:
            return {"error": f"Invalid newline delimited JSON line: {str(e)}"}

    if not new_docs:
        return {"error": "No valid JSON documents found"}

    records_inserted = manager.create_many(new_docs)
    return {"inserted": records_inserted}

@app.post("/document/read", tags=["Documents"], summary="Read documents")
def read_documents(req: DocumentQuery):
    return manager.read(req.filter, req.field_select)

@app.put("/document/update", tags=["Documents"], summary="Update documents")
def update_documents(req: DocumentUpdate):
    return manager.update(req.filter, req.update_dict)

@app.delete("/document/delete", tags=["Documents"], summary="Delete documents")
def delete_documents(req: DocumentQuery):
    return manager.delete(req.filter)

# ---
# Index Management
# ---
# Data structure classes and endpoints for indexes.  Allow for index creation, removal, and listing.
# ---
# Pydantic models for the three available operations.  The pydantic models make the sample data on the Swagger UI
#    fit the various functions better, so although python types could have probably been use the pydantic option
#    seemed to be more user/code-manager friendly at runtime.
# ---
class IndexCreate(BaseModel):
    fields: List[List[Any]]
    unique: bool = False

class IndexField(BaseModel):
    name: str
    direction: str

class IndexRequest(BaseModel):
    fields: list[IndexField]
    unique: bool = False

class IndexDrop(BaseModel):
    index_name: str

@app.get("/index/list", tags=["Indexes"], summary="List indexes")
def list_indexes():
    return manager.list_indexes()

@app.post("/index/create", tags=["Indexes"], summary="Create an index")
def create_index(req: IndexRequest):
    # Tried this a bunch of different ways trying to get fancy with pydantic, but it broke consistently
    #    using pydantic types which ended up converted to a tuple that got misinterpreted at the receiving end.
    #    to recover, data has been converted to normal python types with a plain dict getting constructed 
    #    before calling MongoManager
    fields = []
    for f in req.fields:
        fields.append({"name": f.name, "direction": f.direction}) 
    payload = {
        "fields": fields,
        "unique": req.unique,
    }
    return manager.create_index(payload)

@app.delete("/index/drop", tags=["Indexes"], summary="Drop an index")
def drop_index(req: IndexDrop):
    return manager.drop_index(req.index_name)

# ---
# Aggregation
# ---
# Aggregate is a comprehensive function to manage data analysis tasks.  The number of functions
#    available is difficult to incorporate into comprehesive endpoints, but the provided endpoint allows 
#    entering a list of aggregation options for execution in sequence.
# ---
class AggregationRequest(BaseModel):
    pipeline: List[Dict[str, Any]]

@app.post("/aggregation/run", tags=["Aggregation"], summary="Run aggregation pipeline")
def run_aggregation(req: AggregationRequest):
    return manager.aggregate(req.pipeline)

# ---
# Backup / Restore
# ---
# Backup and restore are available to provide for a more complete set of DB management tools.
#    Backup will return a list of records for a specified table while restore will provide two choices: 
#    restore records completely (from a specified set of records) after deleting and recreating a table, or
#    restore a set of records into a collection.  Id fields will be removed from the records so that there
#    are no conflicting records in the provided list.
# ---
class RestoreRequest(BaseModel):
    documents: List[Dict[str, Any]]
    drop_first: bool = False

# These functions are very basic, simply calling the corresping methods in the MongoManager class.  The functions may be
#    extended in the future to allow for backup and restore to external files, as has been implemented for create_from_file in the
#    CRUD routines.
@app.get("/backup/collection", tags=["Backup"], summary="Backup collection")
def backup_collection():
    return manager.backup_collection()

@app.post("/restore/collection", tags=["Backup"], summary="Restore collection")
def restore_collection(req: RestoreRequest):
    return manager.restore_collection(req.documents, req.drop_first)

@router.post("/sql/execute", tags=["SQL Helper"])
def execute_sql_query(sql_query: str):
    func_to_execute = False

    translation = translator.translate_sql(sql_query)

    operation = translation["operation"]
    collection = translation["collection"]
    manager.set_collection(collection)

    if operation == "read":
        options_dict = {
            "sort": translation.get("sort"),
            "limit": translation.get("limit"),
        }
        func_to_execute = True
        retval = (manager.read( 
            translation["filter"],
            translation["projection"],
            options_dict
        ))

    if operation == "create":
        func_to_execute = True
        retval = (manager.create(translation["document"]))

    if operation == "update":
        func_to_execute = True
        retval = (manager.update(
            translation["filter"],
            translation["update"]
        ))

    if operation == "delete":
        func_to_execute = True
        retval = (manager.delete(translation["filter"]))

    if operation == "aggregate":
        func_to_execute = True
        retval = (manager.aggregate(translation["pipeline"]))

    if func_to_execute:
        sql_string = "SQL Query: " + sql_query
        ret_string = "Mongo Translation: db." + collection + "." + operation
        if translation["filter"]:
            ret_string += str(translation["filter"])
        if translation["projection"]:
            ret_string += str(translation["projection"])
        if translation["sort"] or translation["limit"]:
            ret_string += "{"
            if translation["sort"]:
                ret_string += str(translation.get("sort"))
            if translation["limit"]:
                ret_string += str(translation.get("limit"))
            ret_string += "}"
        if operation == "update":
            if translation["update"]:
                ret_string += str(translation["update"])
        return [sql_string, ret_string, retval]
    else: 
        return {"error": "Unsupported operation"}

app.include_router(router)

# Now that app is fully defined (with all the appropriate URI info incorporated and with the SQL translator installed
#    we're ready to actually run the code.  This circumvents the need to start the application using the uvicorn command line
#
# Note, --reload is used to track changed files in the python code from the directory where MongoManager_SwaggerUI lives. It's 
#    mostly intended if development work on the scripts.
#
if __name__ == "__main__":
    keyfile = home_dir + "/security/key.pem"
    certfile = home_dir + "/security/cert.pem"
    if Path(keyfile).exists() and Path(certfile).exits():
        uvicorn.run("MongoManager_SwaggerUI:app", 
            host="0.0.0.0", 
            port=8000, 
            reload=True, 
            ssl_keyfile=keyfile,
            ssl_certfile=certfile
        )
    else:
        uvicorn.run("MongoManager_SwaggerUI:app", host="0.0.0.0", port=8000, reload=True)
