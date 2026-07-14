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
# +++
#    v0.1 - May 24, 2026 - After additional methods were added to the AAC_CRUD_Operations.py in that served as the base
#        for a management class for MongoDB, this file was created to provide a user accessible interface for testing and
#        using the methods in that resulting python module and class, MongoManager.
# +++
#    v1.0 - June 20, 2026 - Nearing final updates prior to finishing out the Capstone project for SNHU course CS-499
#        with endpoints and functions added to cover many management functions for Mongo including authentication, CRUD
#        operations on Mongo documents, collection management, database management, index management, backup and restore 
#        operations, aggregations, and a SQL Helper option to allow a substantial number of SQL features to be translated
#        to the functional equivalents in Mongo.  
# ---
# Mark Woodford
# SNHU CS499 Computer Science Capstone
# June 20, 2026
# ---

# ---
# Import external python modules to take advantage of existing interfaces to do some of the heavy
#    lifting so as not to reinvent the wheel when public-domain options are available.
# ---
from fastapi import FastAPI, HTTPException, Depends, Request, UploadFile, File, Form, APIRouter
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field, ConfigDict
from typing import Any, Dict, List, Optional
from functools import lru_cache
from pathlib import Path
from pymongo.errors import PyMongoError
import uvicorn
import json
import os

# ---
# Import custom classes that implement enhanced Mongo operations and SQL to Mongo translations features
# ---
from MongoManager import MongoManager
from SQLtoMongo import SQLToMongoTranslator

# ---
# Get the path from which the script is being executed in case we need locally stored files nearby...  
# ---
home_dir = os.path.dirname(os.path.abspath(__file__))

# ---
# FastAPI to manage WebUI operations and provide Swagger functionality
# ---
app = FastAPI(
    title="Mongo Manager API",
    description="Full-featured Swagger UI for MongoManager operations",
    version="1.0.0",
    docs_url=None,
    redoc_url=None
)

# ---
# APIRouter will provide the gateway to the SQL Helper features
# ---
router = APIRouter()

# ---
# The features implemented are designed to be single-threaded on the Swagger UI so we need to
#    ensure that the MongoManager is single-threaded.  We don't want to have to manage logins
#    multiple times or deal with threads that can't figure out that they're not currently
#    authenticated.  Threading is fine when dealing with certain operations, but the applied 
#    functions called from the manager UI is not one of them.
# ---
@lru_cache()                # Provide caching of function operations, allowing data retention of prior function uses 

def get_manager():          # simple function to return a new MongoManager instance
    return MongoManager()   
manager = get_manager()     # create a manager for our Swagger UI use

translator = SQLToMongoTranslator(manager)  # This will allow passing the manager instance to the SQL helper class

# ---
# Exception handlers
# ---
# These handlers should catch most errors without impeding application loading and performance.  It won't handle everything,
#     but should handle most errors common to the features used in this application
# ---
@app.exception_handler(PyMongoError)
async def pymongo_exception_handler(request, exc: PyMongoError):
    msg = str(exc)
    status = 403 if "not authorized" in msg.lower() else 500
    return JSONResponse(
        status_code=status,
        content={"detail": msg}
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()}
    )

@app.middleware("http")
async def catch_all_exceptions(request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc)}
        )
    
@app.exception_handler(Exception)
async def global_exception_handler(request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)}
    )
# ---
# About Endpoint
# ---
# Provide a simple function to demonstrate Swagger interface features including a "Try it Out" option which
#     will display information about the current version.
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
#    as well as there being a form to display to fill in login information.  This section defines the data structure for the 
#    authentication information as well as providing the interface to make the authentication call which should be 
#    valid for the duration of the Swagger session.  The call to the authenticate function from the API is executed in this
#    section here as well.
# ---
class Credentials(BaseModel):
    username: Optional[str] = Form(None)
    password: Optional[str] = Form(None, format="password")
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
# Authentication Logic and web interface endpoint
# ---
# Try to authenticate using a dataclass specific to the function and return the result.
# ---
def mongo_requires_auth():
    try:
        # Try to authenticate with empty credentials
        test = MongoManager()
        ok = test.authenticate(
            username=None,
            password=None,
            host="127.0.0.1",
            port=27017,
            database="admin"
        )
        # If authenticate() returns True, no-auth mode
        return not ok
    except:
        # Any failure means secure mode
        return True

@app.get("/docs", include_in_schema=False)
async def custom_docs():
    secure_mode = True

    html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>MongoManager API</title>
    <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist/swagger-ui.css" />
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 0;
        }}
        .tabs {{
            display: flex;
            background: #2c3e50;
            padding: 10px;
        }}
        .tab {{
            color: white;
            padding: 10px 20px;
            cursor: pointer;
            border-right: 1px solid #1a252f;
        }}
        .tab.active {{
            background: #34495e;
            font-weight: bold;
        }}
        #login-panel, #api-panel {{
            padding: 20px;
        }}
        .login-box {{
            max-width: 400px;
            margin: 40px auto;
            padding: 20px;
            border: 1px solid #ccc;
            border-radius: 8px;
            background: #fafafa;
        }}
        .login-box input {{
            width: 100%;
            padding: 8px;
            margin-bottom: 12px;
            border-radius: 4px;
            border: 1px solid #bbb;
        }}
        .login-box button {{
            width: 100%;
            padding: 10px;
            background: #2c3e50;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
        }}
        .login-box button:hover {{
            background: #1a252f;
        }}
    </style>
</head>

<body>

    <div class="tabs">
        <div id="tab-login" class="tab active">Login</div>
        <div id="tab-api" class="tab" style="display:{'none' if secure_mode else 'block'};">API</div>
    </div>

    <div id="login-panel" style="display:{'block' if secure_mode else 'none'};">
        <div class="login-box">
            <h3>MongoDB Authentication</h3>
            <input id="username" type="text" placeholder="Username (optional)">
            <input id="password" type="password" placeholder="Password (optional)">
            <input id="host" type="text" value="127.0.0.1" placeholder="Host">
            <input id="port" type="number" value="27017" placeholder="Port">
            <input id="database" type="text" placeholder="Database (optional)">
            <button onclick="submitAuth()">Authenticate</button>
        </div>
    </div>

    <div id="api-panel" style="display:{'none' if secure_mode else 'block'};">
        <div id="swagger-ui"></div>
    </div>

    <script src="https://unpkg.com/swagger-ui-dist/swagger-ui-bundle.js"></script>

    <script>
        let swaggerLoaded = false;

        function showError(message) {{
            alert("Error: " + message);
        }}

        function loadSwagger() {{
            if (swaggerLoaded) return;
            swaggerLoaded = true;

            SwaggerUIBundle({{
                url: "/openapi.json",
                dom_id: "#swagger-ui",
                layout: "BaseLayout",

                requestInterceptor: (req) => {{
                    const token = localStorage.getItem("authToken");
                    if (token) {{
                        req.headers["X-Auth-Token"] = token;
                    }}
                    return req;
                }},

                responseInterceptor: (res) => {{
                    if (res.status >= 400) {{
                        let msg = "Unknown error";

                        if (res.data && res.data.detail) {{
                            msg = res.data.detail;
                        }} else if (res.statusText) {{
                            msg = res.statusText;
                        }}

                        if (res.status === 401) {{
                            switchToLogin(msg);
                            return res;
                        }}

                        if (res.status === 403) {{
                            showError("Not authorized: " + msg);
                            return res;
                        }}

                        showError(msg);
                        return res;
                    }}

                    if (res.data && res.data.error) {{
                        showError(res.data.error);
                    }}

                    return res;
                }}
            }});
        }}

        function switchToLogin(message) {{
            alert(message || "Authentication required");
            document.getElementById("login-panel").style.display = "block";
            document.getElementById("api-panel").style.display = "none";
            document.getElementById("tab-login").classList.add("active");
            document.getElementById("tab-api").classList.remove("active");
            document.getElementById("password").value = "";
        }}

        document.getElementById("tab-login").onclick = () => {{
            document.getElementById("login-panel").style.display = "block";
            document.getElementById("api-panel").style.display = "none";
            document.getElementById("tab-login").classList.add("active");
            document.getElementById("tab-api").classList.remove("active");
        }};

        document.getElementById("tab-api").onclick = () => {{
            document.getElementById("login-panel").style.display = "none";
            document.getElementById("api-panel").style.display = "block";
            document.getElementById("tab-api").classList.add("active");
            document.getElementById("tab-login").classList.remove("active");
            loadSwagger();
        }};

        function submitAuth() {{
            fetch("/authenticate", {{
                method: "POST",
                headers: {{ "Content-Type": "application/json" }},
                body: JSON.stringify({{
                    username: document.getElementById("username").value,
                    password: document.getElementById("password").value,
                    host: document.getElementById("host").value,
                    port: parseInt(document.getElementById("port").value),
                    database: document.getElementById("database").value
                }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.status === "authenticated") {{

                    if (!data.token) {{
                        showError("Authentication failed: missing token");
                        return;
                    }}

                    localStorage.setItem("authToken", data.token);

                    const user = document.getElementById("username").value || "user";
                    const db = document.getElementById("database").value || "db";

                    document.getElementById("tab-api").innerText = "API (" + user + "@" + db + ")";
                    document.getElementById("tab-api").style.display = "block";
                    document.getElementById("tab-api").click();
                    loadSwagger();

                }} else {{
                    showError("Authentication failed");
                }}
            }})
            .catch(err => {{
                showError("Unexpected error: " + err);
            }});
        }}
    </script>

</body>
</html>
"""

    return HTMLResponse(html)
    
def require_auth(request: Request):
    token = request.headers.get("X-Auth-Token")

    if not manager.active_token:
        raise HTTPException(status_code=401, detail="Authentication required")

    if token != manager.active_token:
        raise HTTPException(status_code=401, detail="Authentication required")

    return manager

@app.post("/authenticate", tags=["Authentication"], summary="Authenticate and connect to MongoDB")
async def authenticate(credentials: Credentials):
    try:
        authRet = manager.authenticate(
            username=credentials.username,
            password=credentials.password,
            host=credentials.host,
            port=credentials.port,
            database=credentials.database,
            timeout=credentials.timeout
        )

        secured = bool(credentials.username and credentials.password)

        return {
            "status": authRet.get("status"),
            "token": authRet.get("token"),
            "host": credentials.host,
            "port": credentials.port,
            "database": credentials.database,
            "secure": secured
        }

    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))
# ---
# Endpoint and functional interface to determine whether read-only features are active or not
# ---
@app.post("/readonly", tags=["Authentication"], summary="Check to see if authenticated account is in read-only mode")
def check_readonly():
    return {"readonly": manager.is_read_only()}

# ---
# Database Models
# ---
# These are the custom data classes for use with the database endpoints.  
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
# These endpoints and accompanying functions will allow database list, create, drop, and use operations
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
#    data classes and sample values along with the endpoints for managing collections.
# ---
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
# Colletion list, create, drop, and select endpoints.
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
#    execute create/read/update/delete from the connected database.  Additional functions have been
#    incorporated to allow adding multiple records in bulk as well.
# ---
# First, the pydantic models for dealing with documents.  Create accepts a json Dict, CreateMany is a list json Dicts, and 
#    Query is used for read (find) operations.
class DocumentCreate(BaseModel):
    data: Dict[str, Any] = Field(
        default_factory=dict,
        json_schema_extra={
            "example": {
                "date": "2020-01-01",
                "song": "Some Song Name",
                "artist": "Some Singer",
                "this_week": 1,
                "last_week": 2,
                "peak_position": 1,
                "weeks_on_chart": 2
            }
        }
    )

class DocumentCreateMany(BaseModel):
    records: List[Dict[str, Optional[Any]]] = Field(
        default_factory=list,
        json_schema_extra={
            "example": [
                {
                    "date": "2020-01-01",
                    "song": "Some Song Name",
                    "artist": "Some Singer",
                    "this_week": 1,
                    "last_week": 2,
                    "peak_position": 1,
                    "weeks_on_chart": 2
                },
                {
                    "date": "2020-01-01",
                    "song": "Some Other Song Name",
                    "artist": "Some Other Singer",
                    "this_week": 2,
                    "last_week": 3,
                    "peak_position": 2,
                    "weeks_on_chart": 2
                }
            ],
            "additionalProperties": False
        }
    )

class DocumentQuery(BaseModel):
    # Both fields being 'Optional' causes Swagger to send a garbage field 'additionalProp1' which is default and causes an error
    #    Added extra schema parameters to negate the default setting. 
    filter: Optional[Dict[str, Any]] = Field(None, json_schema_extra={"additionalProperties": False})
    field_select: Optional[Dict[str, Any]] = Field(None, json_schema_extra={"additionalProperties": False})

class DocumentUpdate(BaseModel):
    filter: Dict[str, Any]
    update_dict: Dict[str, Any]

    model_config = {
        "json_schema_extra": {
            "example": {
                "filter": {
                    "artist": "Some Singer",
                    "song": "Some Song Name"
                },
                "update": {
                    "$set": {
                        "weeks_on_chart": 1001,
                        "peak_position": 1
                    }
                }
            }
        }
    }
# ---
# Now the CRUD Endpoints.
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
        pass  # Fall through to newline delimited JSON parsing

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

# ---
# Endpoints to allow index list, create, and drop operations.
# ---
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
    pipeline: List[Dict[str, Any]] = Field(
        default_factory=dict,
        json_schema_extra={
            "example": [
                {
                    "$match": {
                        "artist": "Some Singer",
                        "weeks_on_chart": { "$gte": 10 }
                    }
                },
                {
                    "$group": {
                        "_id": "$artist",
                        "total_weeks": { "$sum": "$weeks_on_chart" },
                    }
                },
                {
                    "$limit": 5
                }
            ]
        }
    )

@app.post("/aggregation/run", tags=["Aggregation"], summary="Run aggregation pipeline")
def run_aggregation(req: AggregationRequest):
    return manager.aggregate(req.pipeline)

# ---
# Backups and restores initially allowed only in-app use... that is, the data was returned to the UI and displayed and though
#    it could be copied and pasted to an external file there was no way given to do that automatically.  The problem with that
#    is the overhead incurred was slowing down the behind-the-scenes operations, so file-base backups and restores were
#    added.  Both file-based and UI based options are currently presented, and will operated against the current collection
#    (selectable using the "Collections -> Set" endpoint).
# ---
# Data classes and functions for Backup / Restore (existing in-memory endpoints)
# ---
class RestoreRequest(BaseModel):
    documents: List[Dict[str, Any]]
    drop_first: bool = False

@app.get("/backup/collection", tags=["Backup"], summary="Backup collection (in-memory JSON)")
def backup_collection():
    return manager.backup_collection()

@app.post("/restore/collection", tags=["Restore"], summary="Restore collection (from JSON payload)")
def restore_collection(req: RestoreRequest):
    return manager.restore_collection(req.documents, req.drop_first)

# ---
# Data classes and functions for Backup / Restore (File-based, JSON + GZIP)
# ---

class FileBackupRequest(BaseModel):
    compress: bool = Field(
        default=False,
        description="If true, backup will be stored as gzipped JSON (.json.gz)."
    )
    preserve_ids: bool = Field(
        default = False,
        description="If true, object ids will be preserved as part of each record.  This may cause issues with duplicate keys on restore."
    )

class FileRestoreRequest(BaseModel):
    filename: str = Field(..., description="Backup filename to restore from (as listed by /backup/collection/list).")
    drop_first: bool = Field(
        default=False,
        description="If true, drop and recreate the collection before restoring."
    )
    preserve_ids: bool = Field(
        default=False,
        description="Ids contained in the backup file will be created in the restored record where possible."
    )

class FileDeleteRequest(BaseModel):
    filename: str = Field(..., description="Backup filename to delete.")

@app.post("/backup/collection/to-file", tags=["Backup"], summary="Backup collection to file (JSON or GZIP)")
def backup_collection_to_file(req: FileBackupRequest):
    try:
        info = manager.backup_to_file(compress=req.compress, preserve_ids=req.preserve_ids)
        return info
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/backup/collection/list", tags=["Backup"], summary="List backup files")
def list_backup_files():
    try:
        return manager.list_backup_files()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/backup/collection/download", tags=["Backup"], summary="Download a backup file")
def download_backup_file(filename: str):
    try:
        backup_path = manager.backup_dir / filename
        if not backup_path.exists():
            raise HTTPException(status_code=404, detail="Backup file not found.")
        return FileResponse(
            path=str(backup_path),
            filename=filename,
            media_type="application/octet-stream"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/restore/collection/from-file", tags=["Restore"], summary="Restore collection from backup file")
def restore_collection_from_file(req: FileRestoreRequest):
    try:
        result = manager.restore_from_file(req.filename, drop_first=req.drop_first, preserve_ids=req.preserve_ids)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/backup/collection/delete", tags=["Backup"], summary="Delete a backup file")
def delete_backup_file(req: FileDeleteRequest):
    try:
        result = manager.delete_backup_file(req.filename)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ---
# The below section implements the SQL translation piece of the UI.  The return from this
#    translation is rather flexible.  The idea is to include both the given SQL query string and the resulting Mongo
#    translation, or at least a representation of what the Mongo logic applied will be.  The result is then appended to 
#    the results of the executed query.  The actual translation mechanics are implemented in the included SQLtoMongo class.
# ---

class SQLExecutionResponse(BaseModel):
    sql: str
    translation: dict
    result: Any

@router.post("/sql/execute", tags=["SQL Helper"], response_model=SQLExecutionResponse)
def execute_sql_query(sql_query: str):
    # Translator returns:
    #   { "sql": ..., "mongo_plan": ..., "result": ... }
    translation = translator.translate_sql(sql_query)

    sql_string = translation.get("sql")
    mongo_plan = translation.get("mongo_plan")
    result = translation.get("result")

    # --- Normalize mongo_plan so it is ALWAYS a dict ---
    # If translator returned a string, wrap it.
    # If translator returned None, replace with empty dict.
    if isinstance(mongo_plan, str):
        mongo_plan = {"info": mongo_plan}
    elif mongo_plan is None:
        mongo_plan = {}
    # If it's already a dict, leave it alone.

    # For backward compatibility with your UI:
    #   translation -> mongo_plan
    #   result      -> result
    #   sql         -> sql
    return SQLExecutionResponse(
        sql=sql_string,
        translation=mongo_plan,
        result=result
    )

# ---
# Include the router (required to execute the SQL translation) in the app definition before starting the app.
# ---
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
    if Path(keyfile).exists() and Path(certfile).exists():
        uvicorn.run("MongoManager_SwaggerUI:app", 
            host="0.0.0.0", 
            port=8000, 
            reload=True, 
            ssl_keyfile=keyfile,
            ssl_certfile=certfile
        )
    else:
        uvicorn.run("MongoManager_SwaggerUI:app", host="0.0.0.0", port=8000, reload=True)
