# ---
# MongoManager
# ---
#  
# This class provides a full-featured Mongo database management library.  
# This functionality offered by this class provides methods for:
#
#    > Authentication and connection to any Mongo instance
#    > Database creation, deletion, listing, switching
#    > Collection creation, deletion, listing, renaming
#    > CRUD operations for any collection (which was the inital purpose of the class from which this derived)
#    > Index creation, listing, deletion
#    > Aggregation pipelines
#    > Schema validation helpers
#    > Backup/restore helpers (simple JSON-based)
#    > Flexible function signatures with optional parameters
#        
# In order to function properly, access for and connection to an existing mongo installation is required.  A routine
# to manage authentication and connection is available within the library.
# ---
# Version History:
# +++
#     v0.1 - May 24, 2026 - Incorporated and updated AAC_CRUD_Operations.py written for CS340 adding more comprehensive set
#                               of methods that are geared towards full database utility rather than only CRUD operations.
# +++
#     v1.0 - June 20, 2026 - Nearing final updates prior to finishing out the Capstone project for SNHU course CS-499.
#                               Added enhancements to facilitate presenting the class as API routines on a Swagger endpoint, then
#                               continued to add updates as new work was incorporated that presents a SQL to Mongo translation
#                               feature.  Existing operations were maintained, with the base CRUD routines and the added 
#                               management features providing a stable back-end for the Swagger API interface.
# ---
# Mark Woodford
# SNHU CS499 Computer Science Capstone
# June 20, 2026
# ---

# ---
# Python modules necessary for functionality.  The class needs to be able to operate separately from the python artifact
#     module that's using it.
# ---
from pymongo import MongoClient
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError
from bson.objectid import ObjectId
from datetime import datetime
from pathlib import Path
import uuid
import os, gzip
import json

class MongoManager(object):

    def __new__(cls, *args, **kwargs):
        # Create a new instance of MongoManager, which then will pick up necessary class variables from __init__
        instance = super().__new__(cls)
        return instance

    def __init__(self):
        # The init routine defines necessary class variables.  Values are provided and updated
        #     by class functions but initialized as appropriate here.
        self.mm_client = None
        self.mm_database = None
        self.mm_collection = None
        self.authenticated = False
        self.active_token = None
        # Backup directory (Option 3: env override, default ./backups)
        self.backup_dir = Path(
            os.getenv("BACKUP_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups"))
        )
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    # ---
    # Authentication
    # ---
    def authenticate(self, username=None, password=None, host="127.0.0.1", port=27017, database=None, timeout=3000):

        username = username or None
        password = password or None

        try:
            # Always authenticate against admin
            if not username or not password:
                self.mm_client = MongoClient(
                    host=host,
                    port=port,
                    serverSelectionTimeoutMS=timeout
                )
            else:
                self.mm_client = MongoClient(
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    authSource="admin",
                    authMechanism="SCRAM-SHA-256",
                    serverSelectionTimeoutMS=timeout
                )

            # Force authentication immediately
            self.mm_client.admin.command("ping")

            # Working DB (user-selected or default)
            dbname = database or "admin"
            self.mm_database = self.mm_client[dbname]

            # Mark authenticated
            self.authenticated = True
            self.current_user = username
            self.current_db = dbname
            self.active_token = str(uuid.uuid4())
            return {"status": "authenticated", "token": self.active_token}
    
        except OperationFailure as e:
            msg = str(e).lower()
            self.authenticated = False
            self.active_token = None

            # Wrong username/password
            if "authentication failed" in msg or "auth failed" in msg:
                return {"status": "unauthenticated", "token": None}

            # User is authenticated but lacks privileges
            if "not authorized" in msg:
                raise Exception("Not authorized for this operation")

            raise

        except ServerSelectionTimeoutError:
            self.authenticated = False
            self.active_token = None
            raise Exception("Timed out trying to reach server")

        except Exception as e:
            self.authenticated = False
            self.active_token = None
            raise Exception(f"Authentication failed: {str(e)}")
    # ---
    # This method is used to detect whether the authenticated account has only read access or whether
    #    it can create temporary databases for nested searches and more complex operations.  It was 
    #    added specifically for the SQLtoMongo functionality as SQL statements can become complex and
    #    when translated to Mongo methods using temporary storage for nested selects or complex 
    #    where clauses becomes a near necessity to protect against exceeding memory constraints.
    # ---
    def is_read_only(self):
        try:
            status = self.mm_database.command("connectionStatus")
            privileges = status["authInfo"]["authenticatedUserPrivileges"]

            write_actions = {
                "insert", "update", "remove",
                "createCollection", "dropCollection", "dropDatabase"
            }

            for priv in privileges:
                actions = set(priv.get("actions", []))
                if actions & write_actions:
                    return False  # user has at least one write privilege

            return True  # no write privileges found

        except Exception:
            # If we cannot determine, assume we're not read-only.  Commonly this occurs when there is no authentication required.
            return False

    # ---
    # Database Management
    # ---
    # The functions in this section manage database creation, listing, removal, and use.
    # ---
    def list_databases(self):
        self._check_auth()
        return self.mm_client.list_database_names()

    def create_database(self, database_name=None):
        # Since MongoDB is indifferent about database creation, and will create database on the fly,
        # We can just create a collection that exists in the new database and put something marginally useful it.
        # The collection can later be dropped if it isn't desired.
        self._check_auth()
        if not database_name:
            raise Exception("A database name required in order to create it.")
        # Check to see if DB already exists and, if so, simply return True.  If not, create a collection and stick the 
        #    creation date in there.
        if database_name in self.mm_client.list_database_names():
            return True 
        else:
            db = self.mm_client[database_name]
            db["creation_date"].insert_one({"created": datetime.utcnow()})
        return True

    def drop_database(self, database_name=None):
        self._check_auth()
        if not database_name:
            raise Exception("Database name required.")
        self.mm_client.drop_database(database_name)
        return True

    def use_database(self, database_name=None):
        # This routine will set the active database name for use with non-database functions 
        self._check_auth()
        if not database_name:
            raise Exception("Database name required.")
        self.mm_database = self.mm_client[database_name]
        return True

    # ---
    # Collection Management
    # ---
    # The functions in this section will assist in collection management.  Operations include
    #     listing, creating, renaming, removing, and using.
    # ---
    def list_collections(self):
        self._check_auth()
        return self.mm_database.list_collection_names()

    def create_collection(self, collection_name=None, schema_specs=None):
        # Collection creation, like database creation, is very much ad-hoc in MongoDB.
        #  the optional variable schema_specs allows specifying a JSON schema for validating the collection
        self._check_auth()
        if not collection_name:
            raise Exception("Collection name required.")

        create_options = {}
        if schema_specs:
            create_options["schema_specs"] = schema_specs

        self.mm_database.create_collection(collection_name, **create_options)
        return True

    def drop_collection(self, collection_name=None):
        # There is no validation performed to determine if dropping a collection is really desired, the expectation
        #    is that if any validation is desired it will be added to the application using the class.
        self._check_auth()
        if not collection_name:
            raise Exception("A collection name is required to determine what to drop.")
        self.mm_database.drop_collection(collection_name)
        return True

    def rename_collection(self, old_name=None, new_name=None):
        self._check_auth()
        if not old_name or not new_name:
            raise Exception("Old and new collection names are required for rename operation.")
        self.mm_database[old_name].rename(new_name)
        return True

    def set_collection(self, collection_name=None):
        # This function is used to set the collection name for use by other class functions.
        self._check_auth()
        if not collection_name:
            raise Exception("Collection name was not specified for set operation.")
        self.mm_collection = self.mm_database[collection_name]
        return True


    # ---
    # CRUD Operations
    # ---
    # The functions below are 'per collection' operations.  They provide the ability to create, read, write, or delete from
    #     a specific collction.  If the collection has not been specified, operations will return an error.
    # ---

    def create(self, data=None):
        # Create method (Putting the C in CRUD)
        # returns the object id of the created record.
        self._verify_collection()
        if not data:
            raise Exception("Data required for create().")
        result = self.mm_collection.insert_one(data)
        return str(result.inserted_id)

    def create_many(self, records=None):
        # Create Many method (Bulk Insert) -- This is a new addition to the CRUD routines to allow multiple records
        #    from a list of JSON documents to be inserted at the same time. 
        #    Any "_id" fields found in the incoming documents will be removed to avoid duplicate key errors.
        # Return will be a count of records inserted.

        # First ensure that there is collection to insert into
        self._verify_collection()

        # Raise an exception if records were not supplied or if the supplied records aren't a list.
        if records is None:
            raise Exception("Error in create_many(): A list of records must be supplied.")

        if not isinstance(records, list):
            raise Exception("Error in create_many(): The records parameter must be a python list of JSON documents.")

        # Remove any "_id" fields to avoid duplicate key conflicts and verify records in the list are of type dict
        cleaned_records = []
        for doc in records:
            if not isinstance(doc, dict):
                raise Exception("Error in create_many(): Each record must be a dictionary.")
            cleaned = {}
            for k, v in doc.items():
                if k != "_id":
                    cleaned[k] = v
            cleaned_records.append(cleaned)

        # Perform the bulk insert. 
        ### Note: there is no verification that the fields are consistent across records.
        ###    Mongo won't take steps to verify that the fields are consistent with expected or existing
        ###    fields in a collection as a SQL based language would.  Any field given will simply be created
        ###    as it is encountered, and missing fields will not be noticed without specific configuration added to the
        ###    collection to validate records. 
        result = self.mm_collection.insert_many(cleaned_records)
        return len(result.inserted_ids)

    def read(self, filter_dict=None, selection_dict=None, options_dict=None):
        # Read method (Adding the R to CRUD)
        # Returns the object ids of the documents retrieved by the find.
        self._verify_collection()
        filter_dict = filter_dict or {}
        options_dict = options_dict or {}
        cursor = self.mm_collection.find(filter_dict, selection_dict)
        return self._apply_options(cursor, options_dict)

    def update(self, filter_dict=None, update_dict=None):
        # Update method (Can't spell CRUD without U)
        # Returns a count of records updated.
        self._verify_collection()
        if not filter_dict or not update_dict:
            raise Exception("filter_dict and update_dict required.")
        result = self.mm_collection.update_many(filter_dict, {"$set": update_dict})
        return result.modified_count

    def delete(self, filter_dict=None):
        # Delete method (finishing off the CRUD)
        # The delete option doesn't provide any verification as to whether the records really are the ones to be deleted.
        #    It is expected that the application that includes the class will provide for any verification desired.
        # Returns a count of records deleted.
        self._verify_collection()
        if not filter_dict:
            raise Exception("filter_dict is required for delete in order that the target records can be determined.")
        result = self.mm_collection.delete_many(filter_dict)
        return result.deleted_count


    # ---
    # Index Management
    # ---
    # Functions in this section allow for creation, deletion, and listing of indexes for the currently selected collection.
    # ---

    def list_indexes(self):
        self._verify_collection()
        return list(self.mm_collection.list_indexes())

    def create_index(self, req: dict):
        self._check_auth()

        fields = req.get("fields", [])
        unique = req.get("unique", False)

        if len(fields) == 0:
            raise("create_index requires a field name for the index and a direction (asc/desc)")

        index_spec = []

        for field in fields:
            name = field.get("name")
            direction = field.get("direction", "asc")

            if not name:
                raise ValueError("Index field missing 'name'")

            if direction == "asc":
                dir_value = 1
            else: 
                dir_value = -1
            index_spec.append((name, dir_value))

        return self.mm_collection.create_index(index_spec, unique=unique)

    def drop_index(self, index_name=None):
        # Dropping an index isn't as destructive as dropping a db or record, but it still should be noted
        #    that there is no verification for dropping the index.  If verification is desired it should be handled
        #    in the application that includes the class.
        self._verify_collection()
        if not index_name:
            raise Exception("The index name is necessary in order to perform drop operation.")
        self.mm_collection.drop_index(index_name)
        return True


    # ---
    # Aggregation
    # ---
    # The aggregate function will allow for operations to be 'piped' together for use in data grouping and analysis.  The parameter
    #     'pipeline' is expected to be a list of dicts which specify the operations to be used, in the order they should be executed in.
    #     Example:
    #         pipeline = [{"$match": {"job_status": "full-time"}}, {"$group": {"_id": null, total: {"$sum": salary}}}] 
    #     It is left to the application including the class to compose the desired aggregation pipeline.
    # ---
    def aggregate(self, pipeline=None):
        self._verify_collection()
        if not pipeline:
            raise Exception("In order to perform aggregation a pipeline is required.")
        cursor = self.mm_collection.aggregate(pipeline)
        return [self._id_to_string(doc) for doc in cursor]

    # ---
    # Backup / Restore (Simple JSON-based)
    # ---
    # Backup and restore operations for collections.  These operations were added to enhance the management aspects of the class
    #    but aren't yet thoroughly tested.  
    # ---
     # ---
    # Backup / Restore (Simple JSON-based, in-memory)
    # ---
    def backup_collection(self):
        self._verify_collection()
        cursor = self.mm_collection.find({})
        cleaned = []
        for doc in cursor:
            cleaned.append(self._convert_ids_deep(doc))
        return cleaned

    def restore_collection(self, documents=None, drop_first=False):
        self._verify_collection()
        if drop_first:
            self.mm_collection.drop()
            self.mm_database.create_collection(self.mm_collection.name)

        if not documents:
            return 0

        clean_docs = []
        for doc in documents:
            cleaned = {}
            for k, v in doc.items():
                if k != "_id":
                    cleaned[k] = v
            clean_docs.append(cleaned)
        result = self.mm_collection.insert_many(clean_docs)
        return len(result.inserted_ids)

    # ---
    # Backup / Restore to/from files (JSON + GZIP)
    # ---

    def backup_to_file(self, compress: bool = False, preserve_ids: bool = False) -> dict:
        """
        Backup current collection to a file in backup_dir.
        Returns metadata about the created backup.
        """
        self._verify_collection()

        db_name = self.mm_database.name
        coll_name = self.mm_collection.name

        # Fetch documents and strip _id
        docs = []
        cursor = self.mm_collection.find({})
        for doc in cursor:
            cleaned = {}
            for k, v in doc.items():
                if k == "_id":
                    if preserve_ids:
                        cleaned["_id"] = str(v)
                    continue
                cleaned[k] = self._convert_ids_deep(v)
            docs.append(cleaned)

        count = len(docs)
        timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        ts_for_filename = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

        base_name = f"{db_name}_{coll_name}_{ts_for_filename}"
        if compress:
            filename = base_name + ".json.gz"
            format_type = "json.gz"
        else:
            filename = base_name + ".json"
            format_type = "json"

        backup_path = self.backup_dir / filename

        payload = {
            "_meta": {
                "database": db_name,
                "collection": coll_name,
                "count": count,
                "timestamp": timestamp,
                "format": format_type,
                "preserve_ids": preserve_ids,
            },
            "documents": docs,
        }

        if compress:
            with gzip.open(backup_path, "wt", encoding="utf-8") as f:
                json.dump(payload, f)
        else:
            with backup_path.open("w", encoding="utf-8") as f:
                json.dump(payload, f)

        size = backup_path.stat().st_size

        return {
            "filename": filename,
            "size": size,
            "count": count,
            "timestamp": timestamp,
            "format": format_type,
        }

    def restore_from_file(self, filename: str, drop_first: bool = False, preserve_ids: bool = False) -> dict:
        """
        Restore collection from a backup file in backup_dir.
        Validates database/collection metadata and inserts documents.
        """
        self._verify_collection()

        backup_path = self.backup_dir / filename
        if not backup_path.exists():
            raise Exception(f"Backup file not found: {filename}")

        # Detect compression
        is_gzip = filename.endswith(".gz")

        if is_gzip:
            with gzip.open(backup_path, "rt", encoding="utf-8") as f:
                payload = json.load(f)
        else:
            with backup_path.open("r", encoding="utf-8") as f:
                payload = json.load(f)

        meta = payload.get("_meta", {})
        docs = payload.get("documents", [])

        db_name = meta.get("database")
        coll_name = meta.get("collection")

        # Validate database/collection match
        if db_name != self.mm_database.name:
            raise Exception(
                f"Backup database '{db_name}' does not match active database '{self.mm_database.name}'."
            )
        if coll_name != self.mm_collection.name:
            raise Exception(
                f"Backup collection '{coll_name}' does not match active collection '{self.mm_collection.name}'."
            )

        # If backup was created with preserve_ids=True, default to preserving
        meta_preserve = meta.get("preserve_ids", False)
        preserve_ids = preserve_ids or meta_preserve

        # Drop the existing collection if requested
        if drop_first:
            self.mm_collection.drop()
            self.mm_database.create_collection(self.mm_collection.name)

        # Clean docs (remove _id if present and preserve_ids not active)
        clean_docs = []
        for doc in docs:
            cleaned = {}
            for k, v in doc.items():
                if k == "_id":
                    if preserve_ids:
                        try:
                            cleaned["_id"] = ObjectId(v)
                        except Exception:
                            cleaned["_id"] = v
                    continue
                cleaned[k] = v
            clean_docs.append(cleaned)

        # Insert the docs that we've collected
        if not clean_docs:
            return {"restored": 0}

        result = self.mm_collection.insert_many(clean_docs)
        return {"restored": len(result.inserted_ids)}

    def list_backup_files(self) -> list:
        """
        List backup files in backup_dir with basic metadata.
        """
        files_info = []
        for path in self.backup_dir.iterdir():
            if not path.is_file():
                continue
            if not (path.name.endswith(".json") or path.name.endswith(".json.gz")):
                continue

            # Try to read minimal metadata
            try:
                is_gzip = path.name.endswith(".gz")
                if is_gzip:
                    with gzip.open(path, "rt", encoding="utf-8") as f:
                        payload = json.load(f)
                else:
                    with path.open("r", encoding="utf-8") as f:
                        payload = json.load(f)

                meta = payload.get("_meta", {})
                files_info.append({
                    "filename": path.name,
                    "size": path.stat().st_size,
                    "timestamp": meta.get("timestamp"),
                    "database": meta.get("database"),
                    "collection": meta.get("collection"),
                    "count": meta.get("count"),
                    "format": meta.get("format"),
                })
            except Exception:
                # If metadata can't be read, still list the file with minimal info
                files_info.append({
                    "filename": path.name,
                    "size": path.stat().st_size,
                    "timestamp": None,
                    "database": None,
                    "collection": None,
                    "count": None,
                    "format": None,
                })

        return files_info

    def delete_backup_file(self, filename: str) -> dict:
        """
        Delete a backup file from backup_dir.
        """
        backup_path = self.backup_dir / filename
        if not backup_path.exists():
            raise Exception(f"Backup file not found: {filename}")
        backup_path.unlink()
        return {"deleted": filename} 
    
    # ---
    # Helper functions.  Perform operations necessary for stable operations
    # ---
    # The functions in this section are used internally in the class to simplify the class functions and make
    #    the code cleaner and easier to understand.
    # ---
    def _check_auth(self):
        if not self.authenticated:
            raise Exception("Authentication required. Call authenticate().")

    def _verify_collection(self):
        self._check_auth()
        if self.mm_collection is None:
            raise Exception("No collection selected. Call set_collection().")

    def _id_to_string(self, doc):
        # simple function to convert ObjectId to string for processing
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return doc

    def _apply_options(self, cursor, options_dict):
        """
        Applies sort, limit, skip, and other future options to a MongoDB cursor,
        then converts ObjectId fields to strings before returning results.
        """

        # Sorting
        if "sort" in options_dict and options_dict["sort"]:
            sort_dict = options_dict["sort"]
            sort_list = list(sort_dict.items())
            cursor = cursor.sort(sort_list)
    
        # Limit
        if "limit" in options_dict and options_dict["limit"]:
            cursor = cursor.limit(options_dict["limit"])
    
        # Skip (OFFSET)
        if "skip" in options_dict and options_dict["skip"]:
            cursor = cursor.skip(options_dict["skip"])
    
        # Distinct (future feature)
        if "distinct" in options_dict and options_dict["distinct"]:
            field_name = options_dict["distinct"]
            distinct_values = cursor.distinct(field_name)
            return [self._convert_id_if_needed(value) for value in distinct_values]
    
        # Convert all documents
        cleaned_docs = []
        for document in cursor:
            cleaned_docs.append(self._id_to_string(document))

        return cleaned_docs

    def _convert_ids_deep(self, value):
        from bson.objectid import ObjectId

        if isinstance(value, ObjectId):
            return str(value)

        if isinstance(value, dict):
            new_doc = {}
            for k, v in value.items():
                new_doc[k] = self._convert_ids_deep(v)
            return new_doc

        if isinstance(value, list):
            new_list = []
            for item in value:
                new_list.append(self._convert_ids_deep(item))
            return new_list

        return value
