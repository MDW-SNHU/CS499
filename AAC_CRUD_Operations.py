from pymongo import MongoClient
from bson.objectid import ObjectId

class CRUD(object):
    """ CRUD Operations for Animal collection in MongoDB """
    
    def __new__(CRUD, *args, **kwargs):
        print("__new__ called, creating new AnimalShelter instance.")
        instance = super().__new__(CRUD)
        return instance
    
    def __init__(self, username, password, port, database):
        # Initialize the MongoClient to allow access to databases
        # and collections.
        #print(username, password, port)
        self.client = MongoClient('mongodb://%s:%s@localhost:%s' % (username, password, port),
                                     serverSelectionTimeoutMS = 30)
        try:
            self.client.server_info()
        except:
            raise Exception("Can\'t get server info.")
            
        self.database = self.client[database]
                       
    # Create method (Putting the C in CRUD)
    # The data parameter is a dict of field_id, value pairs
    def create(self, data):
        if data is not None:
            if self.collection is not None:
                insert_command = "self.database.%s.insert(data)" % self.collection
                eval(insert_command)
                return True
            else:
                raise Exception("Error on create(): No collection name has been set.")
        else:
            raise Exception("Error on create(): No data supplied, data cannot be empty.")

        return False

    # Read method (Putting the R in CRUD)
    # Function has been modified from the one used in the last few weeks by adding a "selectDict" parameter
    #     This parameter will allow fields to be included or excluded specifically by specifying an entries
    #     in the Dict that look something like this: {_id: 0, animal_type: 1}
    def read(self, fieldDict = None, selectDict = None):
        if self.collection is not None:
            if fieldDict is not None:
                find_command = "self.database.%s.find(%s, %s)" % (self.collection, fieldDict, selectDict)
            else:
                if not selectDict == None:
                    find_command = "self.database.%s.find({}, %s)" % (self.collection, selectDict)
                else:
                    find_command = "self.database.%s.find()" % self.collection
            return eval(find_command)
        else:
            raise Exception("Error during read(): No collection name has been set.")
            
    # Update method (Putting the U in CRUD)
    # First parameter (findDict) is the search criteria for locating the record, the second parameter (updateDict) 
    #     specifies the fields that will be updated, and the new values for those fields.
    def update(self, findDict, updateDict):
        if self.collection is not None:
            if findDict is None or updateDict is None:
                raise Exception("Error in call to update(): A dict{} of find criteria and update criteria are required.")
            else:
                update_command = "self.database.%s.update_many(%s, {\"$set\": %s})" % (self.collection, findDict, updateDict)
                return eval(update_command)
        else:
            raise Exception("Error during update(): No collection name has been set.")
    
    # Delete method (Putting the D in CRUD)
    # findDict is a dictionary of field/value pairs that establish the criteria of the records
    #     to be deleted.
    def delete(self, findDict):
        if self.collection is not None:
            if findDict is None:
                raise Exception("Error in call to delete(): A dict() of find criteria for records to remove is required.")
            else:
                delete_command = "self.database.%s.delete_many(%s)" % (self.collection, findDict)
                return eval(delete_command)
        else:
            raise Exception("Error during delete(): No collection name has been set.")
   
    # This function is used to set the collection name that holds the records that will be dealt with.
    # Withoug a collection set, the CRUD operations will all throw an exception
    def set_collection(self, collectionName):
        self.collection = collectionName

    # this function will return the name of the database being used    
    def get_database(self):
        return self.database
    
    # this function returns the list of indexes that exist in the database
    def indexes(self):
        if self.database is not None:
            if self.collection is not None:
                indexCommand = "self.%s.list_indexes()" % self.collection
                return eval(indexCommand)

    # this function returns the list of collections available in the database being used
    def collections(self):
        if self.database is not None:
            return self.database.list_collection_names()
